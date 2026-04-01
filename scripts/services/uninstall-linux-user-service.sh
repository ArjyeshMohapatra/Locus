#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="locus-backend.service"
SERVICE_FILE="${HOME}/.config/systemd/user/${SERVICE_NAME}"

systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
rm -f "$SERVICE_FILE"
systemctl --user daemon-reload
systemctl --user reset-failed

echo "Removed ${SERVICE_NAME}."
