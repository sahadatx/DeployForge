#!/bin/sh
set -eu

app_database_password=$(cat /run/secrets/app_database_password)
export DEPLOYFORGE_APP_DATABASE_PASSWORD="$app_database_password"
unset app_database_password

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 <<'SQL'
\getenv app_password DEPLOYFORGE_APP_DATABASE_PASSWORD
CREATE ROLE deployforge LOGIN PASSWORD :'app_password';
ALTER DATABASE deployforge OWNER TO deployforge;
REVOKE ALL ON DATABASE deployforge FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE deployforge TO deployforge;
SQL

unset DEPLOYFORGE_APP_DATABASE_PASSWORD
