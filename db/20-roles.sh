#!/bin/sh
# Roles for the Metabase dashboard (docker-compose `metabase`, profile "dashboard"):
#   metabase   owns the `metabase` database, where Metabase keeps its own settings, users and dashboards
#   dashboard  read-only access to the call records, used by Metabase to show bookings and calls
# Runs on first start of a new db volume. Safe to re-run on an existing one:
#   docker exec voice-db sh /docker-entrypoint-initdb.d/20-roles.sh
set -eu

psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-voice}" -d "${POSTGRES_DB:-voice}" \
  -v metabase_pw="$METABASE_DB_PASSWORD" -v dashboard_pw="$DASHBOARD_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE metabase LOGIN PASSWORD %L', :'metabase_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metabase') \gexec
SELECT format('CREATE ROLE dashboard LOGIN PASSWORD %L', :'dashboard_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dashboard') \gexec
SELECT 'CREATE DATABASE metabase OWNER metabase'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'metabase') \gexec

GRANT USAGE ON SCHEMA public TO dashboard;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dashboard;
SQL
