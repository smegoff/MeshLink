#!/usr/bin/env bash
set -euo pipefail

# Defaults
USER_NAME=""
DEVICE="auto"
NAME="MeshLink BBS"
ADMINS=""
PEERS=""

usage() {
  cat <<EOF
Usage: sudo bash install.sh --user <username> [--device auto|/dev/ttyACM0] [--name "MeshLink BBS"] [--admins "!deadbeef,!cafef00d"] [--peers "!11111111,!22222222"]

Installs MeshMini to /opt/meshmini and sets up a systemd service.
After install you can override settings in /etc/meshmini/meshmini.env (it wins).
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

if [[ -z "${USER_NAME}" ]]; then
  echo "Missing --user"; usage; exit 1
fi

# Paths
APP_DIR=/opt/meshmini
VENV=${APP_DIR}/venv
PY=${VENV}/bin/python
PIP=${VENV}/bin/pip

# Create app dir and venv
mkdir -p "${APP_DIR}"
install -m 0644 meshmini.py "${APP_DIR}/meshmini.py"  # keep perms stable
python3 -m venv "${VENV}"
"${PIP}" install --upgrade pip
"${PIP}" install meshtastic pypubsub

# Ownership
chown -R "${USER_NAME}:${USER_NAME}" "${APP_DIR}"

# Config dir + env file (env file wins over unit defaults)
install -d -m 0755 /etc/meshmini
cat >/etc/meshmini/meshmini.env <<ENV
# MeshMini overrides (this file is optional; if present it overrides unit defaults)
# Examples:
# MMB_NAME=Menu
# MMB_MENU_LABEL=Menu
# MMB_MAX_TEXT=160
# MMB_ADMINS=!deadbeef,!cafef00d
# MMB_PEERS=!11111111,!22222222
# MMB_DEVICE=/dev/ttyACM0

# Defaults (generated at install time; safe to edit)
MMB_NAME=${NAME}
MMB_DEVICE=${DEVICE}
MMB_ADMINS=${ADMINS}
MMB_PEERS=${PEERS}
ENV
chmod 0644 /etc/meshmini/meshmini.env

# Systemd unit
SERVICE=/etc/systemd/system/meshmini.service
cat >/tmp/meshmini.service <<"UNIT"
[Unit]
Description=MeshMini - minimal Meshtastic BBS (with peer sync)
After=network.target

[Service]
Type=simple
User=__USER__
Group=__USER__
WorkingDirectory=/opt/meshmini

# Built-in defaults (safe fallbacks)
Environment="PYTHONUNBUFFERED=1"
Environment="MMB_DB=/opt/meshmini/board.db"
Environment="MMB_DEVICE=__DEVICE__"
Environment="MMB_NAME=__NAME__"
Environment="MMB_ADMINS=__ADMINS__"
Environment="MMB_PEERS=__PEERS__"
Environment="MMB_SYNC=1"
Environment="MMB_RATE=2"
Environment="MMB_CH_FALLBACK=0"
Environment="MMB_REPLY_BCAST=0"
Environment="MMB_DIRECT_FALLBACK=0"
Environment="MMB_FALLBACK_SEC=5"
Environment="MMB_MAX_TEXT=140"
Environment="MMB_TX_GAP=1.0"

# Load overrides last so they WIN over the above
EnvironmentFile=-/etc/meshmini/meshmini.env

ExecStart=/opt/meshmini/venv/bin/python /opt/meshmini/meshmini.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

# Fill placeholders (escape slashes where needed)
sed -i "s#__USER__#${USER_NAME}#g" /tmp/meshmini.service
sed -i "s#__DEVICE__#${DEVICE//\//\\/}#g" /tmp/meshmini.service
sed -i "s#__NAME__#${NAME//\//\\/}#g" /tmp/meshmini.service
sed -i "s#__ADMINS__#${ADMINS//\//\\/}#g" /tmp/meshmini.service
sed -i "s#__PEERS__#${PEERS//\//\\/}#g" /tmp/meshmini.service

install -m 0644 /tmp/meshmini.service "${SERVICE}"
rm -f /tmp/meshmini.service

# Enable & start
systemctl daemon-reload
systemctl enable meshmini.service
systemctl restart meshmini.service

echo "Installed. Edit /etc/meshmini/meshmini.env to override (e.g. MMB_NAME=Menu), then: sudo systemctl restart meshmini"
echo "Watch logs: sudo journalctl -u meshmini -f --no-pager"
