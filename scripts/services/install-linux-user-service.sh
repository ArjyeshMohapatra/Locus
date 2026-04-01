#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SERVICE_NAME="locus-backend.service"
USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${USER_SYSTEMD_DIR}/${SERVICE_NAME}"

PYTHON_EXE="${PYTHON_EXE:-${REPO_ROOT}/.venv/bin/python}"
SERVICE_ENTRY="${SERVICE_ENTRY:-${REPO_ROOT}/backend/service_entry.py}"
LOCUS_PORT="${LOCUS_PORT:-8000}"
LOCUS_HOST="${LOCUS_HOST:-127.0.0.1}"
LOCUS_DATA_DIR="${LOCUS_DATA_DIR:-${HOME}/.local/share/locus}"

if [[ ! -x "$PYTHON_EXE" ]]; then
  echo "Python executable not found: $PYTHON_EXE" >&2
  exit 1
fi

if [[ ! -f "$SERVICE_ENTRY" ]]; then
  echo "Service entry file not found: $SERVICE_ENTRY" >&2
  exit 1
fi

mkdir -p "$USER_SYSTEMD_DIR"
mkdir -p "$LOCUS_DATA_DIR"

cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=LOCUS Backend API (user service)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_ROOT}
ExecStart=${PYTHON_EXE} ${SERVICE_ENTRY} --host ${LOCUS_HOST} --port ${LOCUS_PORT} --data-dir ${LOCUS_DATA_DIR}
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"
systemctl --user status --no-pager "$SERVICE_NAME" || true

echo "Installed and started ${SERVICE_NAME}."
echo "Check status with: systemctl --user status ${SERVICE_NAME}"
echo "Live logs with: journalctl --user -u ${SERVICE_NAME} -f"
echo "To keep it active after logout: sudo loginctl enable-linger ${USER}"
