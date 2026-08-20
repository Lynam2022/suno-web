#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="suno-web-api"
WORK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER="${SUDO_USER:-$(whoami)}"
USER_HOME="$(eval echo ~${USER})"

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Suno Web API
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${WORK_DIR}
# 啟動前殺掉其他佔用 profile 的 process（避免搶 session）
ExecStartPre=/usr/bin/bash -c 'pkill -f "[s]uno-web serve" || true; pkill -f "[c]hrome.*suno-web/profiles" || true; sleep 1'
ExecStart=${WORK_DIR}/.venv/bin/suno-web serve
Restart=on-failure
RestartSec=10
EnvironmentFile=-${WORK_DIR}/.env
Environment=HEADLESS=true
Environment=HOME=${USER_HOME}
Environment=PLAYWRIGHT_BROWSERS_PATH=${USER_HOME}/.cache/ms-playwright

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}
echo "${SERVICE_NAME} 服務已安裝並啟動（port 見 .env，預設 8071）"
