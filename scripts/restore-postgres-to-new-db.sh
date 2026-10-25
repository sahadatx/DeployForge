#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
    printf 'Usage: %s BACKUP.dump NEW_DATABASE_NAME\n' "$0" >&2
    printf 'The destination must be a new database name; the active deployforge database is refused.\n' >&2
    exit 2
fi

backup_file=$1
database_name=$2
case "$backup_file" in
    /*) ;;
    *) backup_file="$(pwd)/$backup_file" ;;
esac
case "$database_name" in
    deployforge|""|*[!a-z0-9_]*|[0-9]*)
        printf 'Destination must be a new SQL identifier starting with a letter or underscore.\n' >&2
        exit 2
        ;;
esac
if [ "${#database_name}" -gt 63 ]; then
    printf 'Destination database name must be at most 63 characters.\n' >&2
    exit 2
fi
if [ ! -f "$backup_file" ] || [ ! -r "$backup_file" ]; then
    printf 'Backup file is missing or unreadable: %s\n' "$backup_file" >&2
    exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
# Restore to a new database so validation never overwrites the active application database.
docker compose --env-file .env.production \
    -f docker-compose.yml -f docker-compose.production.yml \
    exec -T postgres pg_restore --list - < "$backup_file" > /dev/null
docker compose --env-file .env.production \
    -f docker-compose.yml -f docker-compose.production.yml \
    exec -T postgres sh -eu -c '
        export PGPASSWORD="$(cat /run/secrets/postgres_password)"
        createdb --host=127.0.0.1 --username=deployforge_bootstrap \
            --owner=deployforge "$1"
        exec pg_restore --exit-on-error --no-owner --role=deployforge \
            --host=127.0.0.1 --username=deployforge_bootstrap --dbname="$1"
    ' sh "$database_name" < "$backup_file"

printf 'Restore completed in database %s. Validate it, then plan a controlled database URL switch.\n' "$database_name"
