#!/usr/bin/env bash
set -euo pipefail

USER_NAME=""
DEVICE="auto"
NAME="MeshLink BBS"
ADMINS=""
PEERS=""

usage() {
  cat <<EOF
Usage:
  sudo bash install.sh --user <username> [--device auto|/dev/ttyACM0] [--name "MeshLink BBS"] \
    [--admins "!deadbeef,!cafef00d"] [--peers "!11111111,!22222222"]

Installs MeshMini to /opt/meshmini and sets up a systemd service + /etc/meshmini/meshmini.env
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)   USER_NAME="$2"; shift 2;;
    --device) DEVICE="$2"; shift 2;;
    --name)   NAME="$2"; shift 2;;
    --admins) ADMINS="$2"; shift 2;;
    --peers)  PEERS="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

[[ -n "$USER_NAME" ]] || { echo "Missing --user"; usage; exit 1; }

APP_DIR=/opt/meshmini
VENV=${APP_DIR}/venv
SERVICE=/etc/systemd/system/meshmini.service
ENV_DIR=/etc/meshmini
ENV_FILE=${ENV_DIR}/meshmini.env

echo "[1/6] OS deps"
apt-get update -y
apt-get install -y python3-venv

echo "[2/6] App dir + venv"
install -d -o "$USER_NAME" -g "$USER_NAME" "$APP_DIR"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install meshtastic pypubsub pytz

echo "[3/6] Copy meshmini.py"
install -m 0755 -o "$USER_NAME" -g "$USER_NAME" meshmini.py "$APP_DIR/meshmini.py"

echo "[4/6] Env file"
install -d -m 0755 "$ENV_DIR"
cat >"$ENV_FILE" <<ENV
# MeshMini runtime env
MMB_DB=${APP_DIR}/board.db
MMB_DEVICE=${DEVICE}
MMB_NAME=${NAME}
MMB_ADMINS=${ADMINS}
MMB_PEERS=${PEERS}
MMB_RATE=2
MMB_MAX_TEXT=140
MMB_HEALTH_PUBLIC=0
MMB_DEBUG=0
MMB_SYNC=1
MMB_SYNC_PERIOD=300
MMB_SYNC_INV=15
MMB_SYNC_CHUNK=160
MMB_RX_STALE_SEC=240
MMB_WATCH_TICK=10
MMB_TZ=Pacific/Auckland
# Optional notice expiry (choose one)
# MMB_INFO_TTL_MIN=180
# MMB_INFO_UNTIL=2025-08-20 18:00
# Unknown reply behavior
MMB_UNKNOWN_REPLY=1
ENV
chmod 0644 "$ENV_FILE"

echo "[5/6] Systemd unit"
cat >"$SERVICE" <<UNIT
[Unit]
Description=MeshMini - minimal Meshtastic BBS
After=network.target

[Service]
Type=simple
User=${USER_NAME}
Group=${USER_NAME}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV}/bin/python ${APP_DIR}/meshmini.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

echo "[6/6] Enable service"
systemctl daemon-reload
systemctl enable --now meshmini

echo
echo "Done. Logs:   sudo journalctl -u meshmini -f"
echo "Edit env:     sudo nano ${ENV_FILE}  (then: sudo systemctl restart meshmini)"
