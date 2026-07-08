#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/home/cadmus/Projects/lm-studio-discord-bot}"
ENV_FILE="${ENV_FILE:-$BOT_DIR/.env}"
LOG_DIR="${LOG_DIR:-$BOT_DIR/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/service-health.log}"
STATE_FILE="${STATE_FILE:-$LOG_DIR/service-health.state}"
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

read_env_value() {
  local key="$1"
  local default="$2"
  local line=""
  local value=""

  if [[ -f "$ENV_FILE" ]]; then
    line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  fi

  if [[ -n "$line" ]]; then
    value="${line#*=}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf '%s\n' "$value"
  else
    printf '%s\n' "$default"
  fi
}

unit_exists() {
  systemctl --user list-unit-files --no-legend "$1" 2>/dev/null | grep -q "^$1"
}

unit_active() {
  local unit="$1"
  unit_exists "$unit" && systemctl --user is-active --quiet "$unit"
}

state_get() {
  local key="$1"
  if [[ -f "$STATE_FILE" ]]; then
    grep -E "^${key}=" "$STATE_FILE" | tail -n 1 | cut -d= -f2- || true
  fi
}

state_set() {
  local key="$1"
  local value="$2"
  local tmp="${STATE_FILE}.tmp"
  touch "$STATE_FILE"
  grep -Ev "^${key}=" "$STATE_FILE" >"$tmp" || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  mv "$tmp" "$STATE_FILE"
}

increment_failure() {
  local key="$1"
  local current
  current="$(state_get "$key")"
  if [[ ! "$current" =~ ^[0-9]+$ ]]; then
    current=0
  fi
  current=$((current + 1))
  state_set "$key" "$current"
  printf '%s\n' "$current"
}

reset_failure() {
  state_set "$1" 0
}

curl_ok() {
  curl --fail --silent --show-error --max-time 6 "$1" >/dev/null
}

mem_available_mb() {
  awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo
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

restart_lm_stack() {
  log "Repairing LM Studio stack"
  systemctl --user stop lm-studio-discord-bot.service || true
  systemctl --user restart lm-studio-app.service
  systemctl --user restart lm-studio-qwen35-08b.service
  systemctl --user start lm-studio-discord-bot.service
}

LM_STUDIO_BASE_URL="$(read_env_value LM_STUDIO_BASE_URL http://127.0.0.1:1234/v1)"
WIKI_BASE_URL="$(read_env_value WIKI_BASE_URL http://127.0.0.1:8090)"
MIN_FREE_MEMORY_MB="$(read_env_value LMS_MIN_FREE_MEMORY_MB 700)"
FAILURE_THRESHOLD="$(read_env_value HEALTHCHECK_FAILURE_THRESHOLD 2)"
SWAP_WARN_PERCENT="$(read_env_value HEALTHCHECK_SWAP_WARN_PERCENT 90)"

if [[ ! "$FAILURE_THRESHOLD" =~ ^[0-9]+$ ]] || (( FAILURE_THRESHOLD < 1 )); then
  FAILURE_THRESHOLD=2
fi

log "Health check starting"

if unit_active lm-studio-discord-bot.service; then
  log "Discord bot service: ok"
else
  log "Discord bot service: not active; starting"
  systemctl --user start lm-studio-discord-bot.service || true
fi

if curl_ok "${LM_STUDIO_BASE_URL%/}/models"; then
  log "LM Studio API: ok"
  reset_failure lm_api_failures
else
  failures="$(increment_failure lm_api_failures)"
  log "LM Studio API: failed (${failures}/${FAILURE_THRESHOLD})"
  if (( failures >= FAILURE_THRESHOLD )); then
    restart_lm_stack || log "LM Studio stack repair failed"
    reset_failure lm_api_failures
  fi
fi

if curl_ok "${WIKI_BASE_URL%/}/catalog/v2/entries"; then
  log "Kiwix API: ok"
  reset_failure kiwix_failures
else
  failures="$(increment_failure kiwix_failures)"
  log "Kiwix API: failed (${failures}/${FAILURE_THRESHOLD})"
  if (( failures >= FAILURE_THRESHOLD )) && unit_exists kiwix-wikipedia.service; then
    log "Restarting kiwix-wikipedia.service"
    systemctl --user restart kiwix-wikipedia.service || true
    reset_failure kiwix_failures
  fi
fi

available_mb="$(mem_available_mb)"
if [[ "$available_mb" =~ ^[0-9]+$ ]] && (( available_mb < MIN_FREE_MEMORY_MB )); then
  log "RAM warning: ${available_mb} MB available; threshold is ${MIN_FREE_MEMORY_MB} MB"
else
  log "RAM: ok (${available_mb:-unknown} MB available)"
fi

swap_percent="$(swap_used_percent)"
if [[ "$swap_percent" =~ ^[0-9]+$ ]] && (( swap_percent >= SWAP_WARN_PERCENT )); then
  log "Swap warning: ${swap_percent}% used"
else
  log "Swap: ok (${swap_percent:-unknown}% used)"
fi

log "Health check completed"
