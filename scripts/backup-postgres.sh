#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${1:-"$repo_root/backups"}
case "$backup_dir" in
    /*) ;;
    *) backup_dir="$(pwd)/$backup_dir" ;;
esac
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
umask 077

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_file="$backup_dir/deployforge-$timestamp.dump"
partial_file="$backup_file.partial"
trap 'rm -f "$partial_file"' EXIT HUP INT TERM

cd "$repo_root"
docker compose --env-file .env.production \
    -f docker-compose.yml -f docker-compose.production.yml \
    exec -T postgres sh -eu -c '
        export PGPASSWORD="$(cat /run/secrets/postgres_password)"
        exec pg_dump --host=127.0.0.1 --username=deployforge_bootstrap \
            --dbname=deployforge --format=custom
    ' > "$partial_file"

docker compose --env-file .env.production \
    -f docker-compose.yml -f docker-compose.production.yml \
    exec -T postgres pg_restore --list - < "$partial_file" > /dev/null
mv "$partial_file" "$backup_file"
trap - EXIT HUP INT TERM
printf 'PostgreSQL backup created: %s\n' "$backup_file"
