#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ "$#" -ne 1 ]]; then
  echo "Usage: restore-postgres-offsite.sh ENCRYPTED_BACKUP.dump.age" >&2
  exit 2
fi
: "${DEPLOYFORGE_ACTIVE_DATABASE_URL:?Set the active database URL for destination safety checks}"
: "${DEPLOYFORGE_RESTORE_DATABASE_URL:?Set a new, empty restore database URL}"
: "${AGE_IDENTITY_FILE:?Set the path to the age private identity file}"

encrypted_backup=$1
if [[ ! -f "$encrypted_backup" || ! -r "$encrypted_backup" ]]; then
  echo "Encrypted backup file is missing or unreadable." >&2
  exit 2
fi
backup_directory=$(CDPATH= cd -- "$(dirname -- "$encrypted_backup")" && pwd)
backup_name=$(basename -- "$encrypted_backup")
checksum_name="${backup_name}.sha256"
if [[ ! -f "$backup_directory/$checksum_name" || ! -r "$backup_directory/$checksum_name" ]]; then
  echo "Matching SHA-256 checksum sidecar is missing or unreadable." >&2
  exit 2
fi
if ! (cd "$backup_directory" && sha256sum --check --strict --status "$checksum_name"); then
  echo "Backup checksum verification failed." >&2
  exit 2
fi
if [[ ! -f "$AGE_IDENTITY_FILE" || ! -r "$AGE_IDENTITY_FILE" ]]; then
  echo "Age identity file is missing or unreadable." >&2
  exit 2
fi

temporary_directory=$(mktemp -d)
chmod 700 "$temporary_directory"
cleanup() { rm -rf "$temporary_directory"; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

mkdir -m 700 "$temporary_directory/active" "$temporary_directory/restore"
python3 "$script_dir/lib/postgres-url-to-env.py" \
  "$temporary_directory/active" DEPLOYFORGE_ACTIVE_DATABASE_URL
source "$temporary_directory/active/connection.env"
active_host=${PGHOST,,}
active_port=$PGPORT
active_database=$PGDATABASE

python3 "$script_dir/lib/postgres-url-to-env.py" \
  "$temporary_directory/restore" DEPLOYFORGE_RESTORE_DATABASE_URL
source "$temporary_directory/restore/connection.env"
restore_host=${PGHOST,,}
if [[ "$restore_host" == "$active_host" && "$PGPORT" == "$active_port" && "$PGDATABASE" == "$active_database" ]]; then
  echo "Refusing to restore over the active database." >&2
  exit 2
fi
export PGAPPNAME=deployforge-offsite-restore
export PGCONNECT_TIMEOUT=${PGCONNECT_TIMEOUT:-15}

decrypted="$temporary_directory/restore.dump"
age --decrypt --identity "$AGE_IDENTITY_FILE" --output "$decrypted" "$encrypted_backup"
pg_restore --list "$decrypted" >/dev/null

existing_objects=$(psql -XAtq -v ON_ERROR_STOP=1 -c "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','p','v','m','S','f')")
if [[ "$existing_objects" != "0" ]]; then
  echo "Refusing to restore into a database that already contains application objects." >&2
  exit 2
fi

pg_restore --exit-on-error --single-transaction --no-owner --no-acl --dbname="$PGDATABASE" "$decrypted"
psql -XAtq -v ON_ERROR_STOP=1 -c 'SELECT 1' >/dev/null
echo "Restore completed in the new database '$PGDATABASE'. Validate the application against it before any controlled cutover."
