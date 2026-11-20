#!/usr/bin/env sh
# Back up the PrepForge database to a compressed SQL file, and delete backups older than KEEP_DAYS.
#
#   ./scripts/backup.sh                      # -> backups/prepforge-20261120T020000Z.sql.gz
#   BACKUP_DIR=/mnt/backups KEEP_DAYS=30 ./scripts/backup.sh
#
# Schedule it with cron, e.g. daily at 02:00:
#   0 2 * * * cd /opt/prepforge && ./scripts/backup.sh >> backups/backup.log 2>&1
# Copy backups off the server too (e.g. rclone to object storage): a backup on the same disk
# doesn't survive losing that disk.
set -eu

cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
COMPOSE="${COMPOSE:-docker compose}"

mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/prepforge-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
tmp="$file.partial"

# --clean/--if-exists make the dump restorable over an existing database.
$COMPOSE exec -T db pg_dump -U prep -d prep --clean --if-exists --no-owner | gzip > "$tmp"
gzip -t "$tmp"   # refuse to keep a truncated file
mv "$tmp" "$file"

find "$BACKUP_DIR" -name 'prepforge-*.sql.gz' -type f -mtime +"$KEEP_DAYS" -delete
echo "Backup written: $file ($(du -h "$file" | cut -f1))"
