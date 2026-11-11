#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${DEPLOYFORGE_DATABASE_URL:?Set the managed PostgreSQL URL through a secret reference}"
: "${S3_BUCKET:?Set the off-site backup bucket}"
: "${AGE_RECIPIENT:?Set the age public recipient key}"
: "${AWS_REGION:?Set the backup bucket region}"

prefix=${S3_PREFIX:-deployforge/postgres}
object_lock_mode=${S3_OBJECT_LOCK_MODE:-COMPLIANCE}
retention_days=${S3_OBJECT_LOCK_RETENTION_DAYS:-90}
if [[ ! "$retention_days" =~ ^[1-9][0-9]*$ ]]; then
  echo "S3_OBJECT_LOCK_RETENTION_DAYS must be a positive integer." >&2
  exit 2
fi
if [[ "$object_lock_mode" != COMPLIANCE && "$object_lock_mode" != GOVERNANCE ]]; then
  echo "S3_OBJECT_LOCK_MODE must be COMPLIANCE or GOVERNANCE." >&2
  exit 2
fi

temporary_directory=$(mktemp -d)
chmod 700 "$temporary_directory"
cleanup() { rm -rf "$temporary_directory"; }
trap cleanup EXIT HUP INT TERM

# Keep credentials out of pg_dump's arguments. Generate a private libpq .pgpass
# file and shell-quoted connection settings from the secret URL.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$script_dir/lib/postgres-url-to-env.py" \
  "$temporary_directory" DEPLOYFORGE_DATABASE_URL
source "$temporary_directory/connection.env"
unset DEPLOYFORGE_DATABASE_URL

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
plain="$temporary_directory/deployforge-${timestamp}.dump"
encrypted="$plain.age"
key="${prefix%/}/deployforge-${timestamp}.dump.age"
retain_until=$(date -u -d "+${retention_days} days" +%Y-%m-%dT%H:%M:%SZ)

pg_dump --no-password --format=custom --no-owner --no-acl --file="$plain"
pg_restore --list "$plain" >/dev/null
age --recipient "$AGE_RECIPIENT" --output "$encrypted" "$plain"
sha256=$(sha256sum "$encrypted" | awk '{print $1}')

aws_args=(--region "$AWS_REGION")
if [[ -n "${S3_ENDPOINT_URL:-}" ]]; then
  aws_args+=(--endpoint-url "$S3_ENDPOINT_URL")
fi
encryption_args=(--server-side-encryption AES256)
if [[ -n "${S3_SSE_KMS_KEY_ID:-}" ]]; then
  encryption_args=(--server-side-encryption aws:kms --ssekms-key-id "$S3_SSE_KMS_KEY_ID")
fi

aws s3api put-object "${aws_args[@]}" \
  --bucket "$S3_BUCKET" \
  --key "$key" \
  --body "$encrypted" \
  --checksum-algorithm SHA256 \
  --object-lock-mode "$object_lock_mode" \
  --object-lock-retain-until-date "$retain_until" \
  --metadata "sha256=$sha256,format=age-encrypted-postgresql-custom-dump" \
  "${encryption_args[@]}" >/dev/null

printf '%s  %s\n' "$sha256" "$(basename "$key")" > "$temporary_directory/checksum.txt"
aws s3api put-object "${aws_args[@]}" \
  --bucket "$S3_BUCKET" \
  --key "$key.sha256" \
  --body "$temporary_directory/checksum.txt" \
  --checksum-algorithm SHA256 \
  --object-lock-mode "$object_lock_mode" \
  --object-lock-retain-until-date "$retain_until" \
  "${encryption_args[@]}" >/dev/null

echo "Encrypted, validated PostgreSQL backup uploaded: s3://${S3_BUCKET}/${key}"
