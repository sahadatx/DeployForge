#!/usr/bin/env python3
"""Turn a PostgreSQL URL into a private libpq environment and password file."""

import os
import shlex
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: postgres-url-to-env.py TEMP_DIRECTORY ENVIRONMENT_VARIABLE")
    temporary = Path(sys.argv[1])
    raw = os.environ.get(sys.argv[2], "")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"postgresql", "postgres", "postgresql+psycopg"}:
        raise SystemExit("PostgreSQL URL must use postgresql:// or postgresql+psycopg://")
    if not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
        raise SystemExit("PostgreSQL URL must include host, user, and database")

    host = parsed.hostname
    port = parsed.port or 5432
    database = unquote(parsed.path.lstrip("/"))
    username = unquote(parsed.username)
    password = unquote(parsed.password or "")
    values = (host, str(port), database, username, password)
    if any(any(character in value for character in "\r\n\x00") for value in values):
        raise SystemExit("PostgreSQL URL contains unsupported control characters")

    def escape_pgpass(value: str) -> str:
        return value.replace("\\", "\\\\").replace(":", "\\:")

    pgpass_file = temporary / "pgpass"
    pgpass_file.write_text(
        ":".join(escape_pgpass(value) for value in values) + "\n", encoding="utf-8"
    )
    pgpass_file.chmod(0o600)

    query = parse_qs(parsed.query)
    environment = {
        "PGHOST": host,
        "PGPORT": str(port),
        "PGDATABASE": database,
        "PGUSER": username,
        "PGSSLMODE": os.environ.get(
            "DEPLOYFORGE_DATABASE_SSLMODE", query.get("sslmode", ["require"])[0]
        ),
        "PGPASSFILE": str(pgpass_file),
    }
    sslrootcert = os.environ.get(
        "DEPLOYFORGE_DATABASE_SSLROOTCERT", query.get("sslrootcert", [""])[0]
    )
    if sslrootcert:
        environment["PGSSLROOTCERT"] = sslrootcert
    env_file = temporary / "connection.env"
    env_file.write_text(
        "".join(f"export {key}={shlex.quote(value)}\n" for key, value in environment.items()),
        encoding="utf-8",
    )
    env_file.chmod(0o600)


if __name__ == "__main__":
    main()
