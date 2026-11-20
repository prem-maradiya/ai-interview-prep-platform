#!/usr/bin/env sh
# Restore a backup made by backup.sh. This REPLACES the current database contents.
#
#   ./scripts/restore.sh backups/prepforge-20261120T020000Z.sql.gz
set -eu

cd "$(dirname "$0")/.."
COMPOSE="${COMPOSE:-docker compose}"
file="${1:-}"
if [ -z "$file" ] || [ ! -f "$file" ]; then
  echo "Usage: $0 <backup.sql.gz>" >&2
  exit 2
fi
gzip -t "$file"

if [ "${FORCE:-}" != "1" ]; then
  printf "This replaces ALL data in the PrepForge database with %s. Type 'restore' to continue: " "$file"
  read -r answer
  [ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }
fi

echo "Stopping the API and worker..."
$COMPOSE stop backend worker

# The app's row-level-security role must exist before its grants are restored (it's cluster-wide,
# so a fresh server doesn't have it yet).
$COMPOSE exec -T db psql -U prep -d prep -v ON_ERROR_STOP=1 -q -c "
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'prepforge_app') THEN CREATE ROLE prepforge_app NOLOGIN; END IF;
  GRANT prepforge_app TO CURRENT_USER;
END \$\$;"

echo "Restoring..."
gunzip -c "$file" | $COMPOSE exec -T db psql -U prep -d prep -v ON_ERROR_STOP=1 -q

echo "Starting the API and worker..."
$COMPOSE start backend worker
echo "Restored from $file"
