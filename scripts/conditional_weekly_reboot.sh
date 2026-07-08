#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/home/cadmus/Projects/lm-studio-discord-bot}"
ENV_FILE="${ENV_FILE:-$BOT_DIR/.env}"
LOG_DIR="${LOG_DIR:-$BOT_DIR/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/conditional-weekly-reboot.log}"

mkdir -p "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*"
}

read_env_value() {
  local key="$1"
  local default="$2"
  local line=""

  if [[ -f "$ENV_FILE" ]]; then
    line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  fi

  if [[ -n "$line" ]]; then
    line="${line#*=}"
    line="${line%\"}"
    line="${line#\"}"
    line="${line%\'}"
    line="${line#\'}"
    printf '%s\n' "$line"
  else
    printf '%s\n' "$default"
  fi
}

swap_used_percent() {
  awk '
    /^SwapTotal:/ {total=$2}
    /^SwapFree:/ {free=$2}
    END {
      if (total > 0) {
        printf "%d\n", ((total - free) * 100) / total
      } else {
        print 0
      }
    }
  ' /proc/meminfo
}

enabled="$(read_env_value WEEKLY_REBOOT_ENABLED false)"
swap_threshold="$(read_env_value WEEKLY_REBOOT_SWAP_THRESHOLD_PERCENT 95)"

log "Conditional weekly reboot check starting"

if [[ "$enabled" != "true" ]]; then
  log "Weekly reboot disabled. Set WEEKLY_REBOOT_ENABLED=true in .env to allow it."
  exit 0
fi

swap_percent="$(swap_used_percent)"
if [[ "$swap_percent" =~ ^[0-9]+$ ]] && (( swap_percent >= swap_threshold )); then
  log "Swap is ${swap_percent}% used, threshold is ${swap_threshold}%; rebooting"
  systemctl reboot
fi

log "No reboot needed; swap is ${swap_percent:-unknown}% used"
