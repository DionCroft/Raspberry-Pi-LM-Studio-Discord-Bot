#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/home/cadmus/Projects/lm-studio-discord-bot}"
ENV_FILE="${ENV_FILE:-$BOT_DIR/.env}"
LOG_DIR="${LOG_DIR:-$BOT_DIR/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/lm-studio-model.log}"

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

LMS="$(read_env_value LMS_BINARY /home/cadmus/.lmstudio/bin/lms)"
MODEL="$(read_env_value LM_STUDIO_MODEL qwen3.5-0.8b)"
MODEL_SPEC="$(read_env_value LM_STUDIO_MODEL_SPEC "$MODEL")"
CONTEXT_LENGTH="$(read_env_value LMS_DEFAULT_CONTEXT_LENGTH 16384)"
TTL_SECONDS="$(read_env_value LMS_DEFAULT_TTL_SECONDS 0)"
LM_STUDIO_BASE_URL="$(read_env_value LM_STUDIO_BASE_URL http://127.0.0.1:1234/v1)"

mkdir -p "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1

echo "[$(date -Is)] Ensuring LM Studio server and model $MODEL"

"$LMS" server start || true

for _attempt in {1..12}; do
  if "$LMS" ls >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

"$LMS" ls >/dev/null

api_models_url="${LM_STUDIO_BASE_URL%/}/models"
for _attempt in {1..24}; do
  if curl --fail --silent --show-error --max-time 5 "$api_models_url" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

curl --fail --silent --show-error --max-time 10 "$api_models_url" >/dev/null

loaded_models() {
  "$LMS" ls | awk '
    /LOADED/ {
      line=$0
      sub(/^[[:space:]]*/, "", line)
      split(line, columns, /[[:space:]][[:space:]]+/)
      gsub(/[[:space:]]+\(1 variant\)$/, "", columns[1])
      print columns[1]
    }
  '
}

mapfile -t loaded < <(loaded_models)

for identifier in "${loaded[@]}"; do
  if [[ "$identifier" != "$MODEL" ]]; then
    echo "[$(date -Is)] Unloading extra model $identifier"
    "$LMS" unload "$identifier"
  fi
done

mapfile -t loaded < <(loaded_models)
for identifier in "${loaded[@]}"; do
  if [[ "$identifier" == "$MODEL" ]]; then
    echo "[$(date -Is)] Model $MODEL is already loaded"
    exit 0
  fi
done

load_args=(
  load "$MODEL_SPEC"
  --context-length "$CONTEXT_LENGTH"
  --parallel 1
  --identifier "$MODEL"
  -y
)

if [[ "$TTL_SECONDS" =~ ^[0-9]+$ ]] && (( TTL_SECONDS > 0 )); then
  load_args+=(--ttl "$TTL_SECONDS")
else
  echo "[$(date -Is)] Loading without TTL so the model stays resident"
fi

"$LMS" "${load_args[@]}"

for _attempt in {1..24}; do
  if curl --fail --silent --show-error --max-time 5 "$api_models_url" | grep -q "\"$MODEL\""; then
    break
  fi
  sleep 5
done

curl --fail --silent --show-error --max-time 10 "$api_models_url" | grep -q "\"$MODEL\""
echo "[$(date -Is)] LM Studio model ready: $MODEL"
