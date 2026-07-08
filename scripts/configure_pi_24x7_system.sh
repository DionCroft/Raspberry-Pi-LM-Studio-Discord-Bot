#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  printf 'Run this with sudo. It writes system-wide journald settings.\n' >&2
  exit 1
fi

install -d -m 2755 -o root -g systemd-journal /var/log/journal
install -d -m 0755 /etc/systemd/journald.conf.d

cat >/etc/systemd/journald.conf.d/90-pi-bot-persistent.conf <<'EOF'
[Journal]
Storage=persistent
Compress=yes
SystemMaxUse=256M
SystemKeepFree=512M
MaxRetentionSec=14day
EOF

systemctl restart systemd-journald.service

printf 'Persistent journald storage enabled with a 256M cap and 14 day retention.\n'
printf 'zram swap is already active on this Pi; tune it through the OS zram package if needed.\n'
