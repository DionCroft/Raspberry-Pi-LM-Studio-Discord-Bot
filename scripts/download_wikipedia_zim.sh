#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI_DIR="$PROJECT_DIR/data/wikipedia"

ZIM_NAME="${1:-wikipedia_en-simple_all_nopic_2026-06.zim}"
ZIM_URL="${2:-https://download.kiwix.org/zim/wikipedia/$ZIM_NAME}"

mkdir -p "$WIKI_DIR"

curl -L -C - --fail --progress-bar \
  -o "$WIKI_DIR/$ZIM_NAME" \
  "$ZIM_URL"

echo "$WIKI_DIR/$ZIM_NAME"
