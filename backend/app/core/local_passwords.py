"""Password handling and bounded throttling for development accounts only."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict

LOCAL_ISSUER = "urn:deployforge:development"
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
_SCRYPT_N = 32768
_SCRYPT_R = 8
_SCRYPT_P = 1


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )


def hash_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH or not password.strip():
        raise ValueError("Password must contain 12–128 characters and cannot be blank")
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return f"scrypt$v1${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password(password: str, encoded: str | None) -> bool:
    """Do the same expensive derivation for missing/invalid credentials."""
    valid_format = False
    try:
        algorithm, version, n, r, p, salt_hex, digest_hex = (encoded or "").split("$")
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        valid_format = (
            (algorithm, version, n, r, p) == ("scrypt", "v1", "32768", "8", "1")
            and len(salt) == 16
            and len(expected) == 32
        )
    except (ValueError, TypeError):
        pass
    if not valid_format:
        _, _, _, _, _, salt_hex, digest_hex = _DUMMY_HASH.split("$")
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    # Request validation bounds API input; this also bounds callers outside the API.
    bounded_password = password if len(password) <= PASSWORD_MAX_LENGTH else ""
    matches = hmac.compare_digest(_derive(bounded_password, salt), expected)
    return valid_format and 1 <= len(password) <= PASSWORD_MAX_LENGTH and matches


class LocalLoginLimiter:
    """Thread-safe, bounded, per-process protection for a local development API.

    Reservations happen before password derivation, so concurrent attempts also
    count. Production uses OIDC and never exposes this development endpoint.
    """

    def __init__(self, *, capacity: int = 4096, window_seconds: int = 300):
        self.capacity = capacity
        self.window_seconds = window_seconds
        self._entries: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def account_key(email: str) -> str:
        return "account:" + hashlib.sha256(email.encode("utf-8")).hexdigest()

    def reserve(self, email: str, address: str) -> int:
        now = time.monotonic()
        limits = ((self.account_key(email), 5), (f"address:{address}", 60))
        with self._lock:
            for key in list(self._entries):
                if self._entries[key][0] <= now:
                    del self._entries[key]
            for key, limit in limits:
                expires, count = self._entries.get(key, (now + self.window_seconds, 0))
                if count >= limit:
                    return max(1, int(expires - now) + 1)
            for key, _limit in limits:
                expires, count = self._entries.get(key, (now + self.window_seconds, 0))
                self._entries[key] = (expires, count + 1)
                self._entries.move_to_end(key)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)
        return 0

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._entries.pop(self.account_key(email), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


local_login_limiter = LocalLoginLimiter()
