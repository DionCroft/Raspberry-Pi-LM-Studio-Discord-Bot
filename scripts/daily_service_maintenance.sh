#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/home/cadmus/Projects/lm-studio-discord-bot}"
LOG_DIR="${LOG_DIR:-$BOT_DIR/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/daily-service-maintenance.log}"
MAX_LOG_BYTES="${MAX_LOG_BYTES:-262144}"

mkdir -p "$LOG_DIR"

if [[ -f "$LOG_FILE" ]]; then
  size="$(wc -c <"$LOG_FILE" 2>/dev/null || printf '0')"
  if [[ "$size" =~ ^[0-9]+$ ]] && (( size > MAX_LOG_BYTES )); then
    mv "$LOG_FILE" "$LOG_FILE.1"
  fi
fi

exec >>"$LOG_FILE" 2>&1

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*"
}

unit_exists() {
  systemctl --user list-unit-files --no-legend "$1" 2>/dev/null | grep -q "^$1"
}

restart_if_present() {
  local unit="$1"
  if unit_exists "$unit"; then
    log "Restarting $unit"
    systemctl --user restart "$unit"
  else
    log "Skipping $unit; unit is not installed"
  fi
}

start_if_present() {
  local unit="$1"
  if unit_exists "$unit"; then
    log "Starting $unit"
    systemctl --user start "$unit"
  else
    log "Skipping $unit; unit is not installed"
  fi
}

log "Daily service maintenance starting"

if unit_exists lm-studio-discord-bot.service; then
  log "Stopping lm-studio-discord-bot.service before LM Studio maintenance"
  systemctl --user stop lm-studio-discord-bot.service
fi

restart_if_present research-funding-signup.service
restart_if_present kiwix-wikipedia.service
restart_if_present lm-studio-app.service
restart_if_present lm-studio-qwen35-08b.service
start_if_present lm-studio-discord-bot.service

log "Daily service maintenance completed"
