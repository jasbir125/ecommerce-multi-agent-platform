#!/bin/sh
# Creates the schema (pgvector extension + tables) and, only when LOAD_SEED_DATA=true, the demo rows.
# Safe to re-run: it stops if the schema already exists.
set -eu
: "${PGHOST:?}" "${PGUSER:?}" "${PGPASSWORD:?}" "${PGDATABASE:?}"

if psql -tAc "SELECT to_regclass('public.workflow_instance')" | grep -q workflow_instance; then
  echo "Schema already present: nothing to do."
  exit 0
fi

MARK='-- ------------------------------------------------------------------ demo data'
awk -v m="$MARK" 'index($0, m) { exit } { print }' /sql/seed.sql > /tmp/schema_part.sql
awk -v m="$MARK" 'found { print } index($0, m) { found = 1 }' /sql/seed.sql > /tmp/demo_part.sql

echo "Creating schema ..."
psql -v ON_ERROR_STOP=1 -f /sql/init.sql
psql -v ON_ERROR_STOP=1 -f /tmp/schema_part.sql

if [ "${LOAD_SEED_DATA:-false}" = "true" ]; then
  echo "Loading demo data ..."
  psql -v ON_ERROR_STOP=1 -f /tmp/demo_part.sql
fi
echo "Done."
