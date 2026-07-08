#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEB_DIR="$PROJECT_DIR/vendor/debs"
KIWIX_DIR="$PROJECT_DIR/vendor/kiwix-tools"

mkdir -p "$DEB_DIR" "$KIWIX_DIR"

cd "$DEB_DIR"
apt-get download \
  kiwix-tools \
  libkiwix14 \
  libzim9 \
  libmicrohttpd12t64 \
  libxapian30

for deb in "$DEB_DIR"/*.deb; do
  dpkg -x "$deb" "$KIWIX_DIR"
done

KIWIX_LIB_DIR="$KIWIX_DIR/usr/lib/aarch64-linux-gnu"
KIWIX_SEARCH="$KIWIX_DIR/usr/bin/kiwix-search"

LD_LIBRARY_PATH="$KIWIX_LIB_DIR" "$KIWIX_SEARCH" -V
