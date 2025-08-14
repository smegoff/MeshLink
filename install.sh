#!/usr/bin/env bash
set -euo pipefail

USER_NAME=""
DEVICE="auto"
NAME="MeshLink BBS"
ADMINS=""
PEERS=""

usage() {
  cat <<EOF
Usage: sudo bash install.sh --user <username> [--device auto|/dev/ttyACM0] [--name "MeshLink BBS"] [--admins "!deadbeef,!cafef00d"] [--peers "!11111111,!22222222"]
Installs MeshMini to /opt/meshmini and sets up a systemd service (with /etc/meshmini/meshmini.env).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)   USER_NAME="${2}"; shift 2;;
    --device) DEVICE="${2}"; shift 2;;
    --name)   NAME="${2}"; shift 2;;
    --admins) ADMINS="${2}"; shift 2;;
    --peers)  PEERS="${2}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

if [[ -z "${USER_NAME}" ]]; then
  echo "Missing --user"; usage; exit 1
fi

APP_DIR=/opt/meshmini
VENV=${APP_DIR}/venv
PY=${VENV}/bin/python
PIP=${VENV}/bin/pip

echo "[1/6] Creating app dir and copying script…"
install -d -o "${USER_NAME}" -g "${USER_NAME}" "${APP_DIR}"
install -m 0755 meshmini.py "${APP_DIR}/meshmini.py"
chown "${USER_NAME}:${USER_NAME}" "${APP_DIR}/meshmini.py"

echo "[2/6] Python venv + packages…"
python3 -m venv "${VENV}" || true
"${PIP}" install --upgrade pip
"${PIP}" install meshtastic pypubsub

echo "[3/6] Add ${USER_NAME} to 'dialout' (serial)…"
id "${USER_NAME}" &>/dev/null && usermod -aG dialout "${USER_NAME}" || true

echo "[4/6] Environment file…"
install -d /etc/meshmini
ENVF=/etc/meshmini/meshmini.env
cat > "${ENVF}" <<EOF
PYTHONUNBUFFERED=1
MMB_DB=/opt/meshmini/board.db
MMB_DEVICE=${DEVICE}
MMB_NAME=${NAME}
MMB_ADMINS=${ADMINS}
MMB_MAX_TEXT=140
MMB_RATE=2
MMB_HEALTH_PUBLIC=1
MMB_DEBUG=0
MMB_SYNC=1
MMB_PEERS=${PEERS}
MMB_SYNC_PERIOD=300
MMB_SYNC_INV=15
MMB_SYNC_CHUNK=160
MMB_RX_STALE_SEC=240
MMB_WATCH_TICK=10
EOF

echo "[5/6] systemd unit…"
SERVICE=/etc/systemd/system/meshmini.service
cat > "${SERVICE}" <<UNIT
[Unit]
Description=MeshMini - minimal Meshtastic BBS
After=network.target

[Service]
Type=simple
User=${USER_NAME}
Group=${USER_NAME}
WorkingDirectory=/opt/meshmini
EnvironmentFile=/etc/meshmini/meshmini.env
ExecStart=/opt/meshmini/venv/bin/python /opt/meshmini/meshmini.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable meshmini.service
systemctl restart meshmini.service

echo "Done. Tail logs: sudo journalctl -u meshmini -f --no-pager"
