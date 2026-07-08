#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/home/cadmus/Projects/lm-studio-discord-bot}"
BACKUP_DIR="${BACKUP_DIR:-$BOT_DIR/backups}"
KEEP_BACKUPS="${KEEP_BACKUPS:-14}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$BACKUP_DIR/lm-studio-discord-bot-runtime-$timestamp.tar.gz"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

tar -czf "$archive" \
  -C "$BOT_DIR" \
  .env \
  state.json \
  README.md \
  run.sh \
  scripts \
  systemd

chmod 600 "$archive"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lm-studio-discord-bot-runtime-*.tar.gz' \
  | sort -r \
  | awk -v keep="$KEEP_BACKUPS" 'NR > keep { print }' \
  | xargs -r rm -f

printf '[%s] Wrote %s\n' "$(date -Is)" "$archive"
