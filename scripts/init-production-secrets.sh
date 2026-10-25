#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
secret_dir="$repo_root/secrets"
mkdir -p "$secret_dir"
chmod 700 "$secret_dir"

write_random_secret() {
    destination=$1
    if [ ! -s "$destination" ]; then
        openssl rand -hex 32 > "$destination.tmp"
        mv "$destination.tmp" "$destination"
    fi
}

write_random_secret "$secret_dir/postgres_password"
write_random_secret "$secret_dir/session_secret"
write_random_secret "$secret_dir/build_callback_secret"

new_app_database_password=0
if [ ! -s "$secret_dir/app_database_password" ]; then
    openssl rand -hex 32 > "$secret_dir/app_database_password.tmp"
    mv "$secret_dir/app_database_password.tmp" "$secret_dir/app_database_password"
    new_app_database_password=1
fi

if [ ! -s "$secret_dir/database_url" ] || [ "$new_app_database_password" -eq 1 ]; then
    app_database_password=$(cat "$secret_dir/app_database_password")
    printf 'postgresql+psycopg://deployforge:%s@postgres:5432/deployforge\n' \
        "$app_database_password" > "$secret_dir/database_url.tmp"
    mv "$secret_dir/database_url.tmp" "$secret_dir/database_url"
fi

# These are mounted only into the API or worker when configured. Fill OIDC before startup.
for optional_secret in oidc_client_secret github_actions_token argocd_auth_token; do
    if [ ! -e "$secret_dir/$optional_secret" ]; then
        : > "$secret_dir/$optional_secret"
    fi
done

# Compose mounts file-backed secrets read-only. The private directory protects the host files;
# readable file mode allows the non-root API, worker, and PostgreSQL users to consume their mounts.
chmod 0444 "$secret_dir"/*
printf 'Created protected secret files in %s\n' "$secret_dir"
printf 'Set the OIDC client secret in secrets/oidc_client_secret before starting production Compose.\n'
