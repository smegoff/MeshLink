#!/usr/bin/env bash
set -euo pipefail

USER_NAME=""
DEVICE="auto"
NAME="MeshLink BBS"
ADMINS=""
PYTHON_BIN="python3"
PIP_FLAGS="--no-cache-dir"

usage() {
  cat <<EOF
Usage: sudo bash install.sh [--user <name>] [--device <auto|/dev/...>] [--name "BBS Name"] [--admins "!id,!id"]
EOF
}

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "Please run as root (sudo)." >&2
    exit 1
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --user) USER_NAME="$2"; shift 2;;
      --device) DEVICE="$2"; shift 2;;
      --name) NAME="$2"; shift 2;;
      --admins) ADMINS="$2"; shift 2;;
      -h|--help) usage; exit 0;;
      *) echo "Unknown arg: $1" >&2; usage; exit 1;;
    esac
  done
  if [[ -z "${USER_NAME}" ]]; then
    echo "--user is required" >&2
    exit 1
  fi
}

ensure_user() {
  id -u "$USER_NAME" >/dev/null 2>&1 || { echo "User $USER_NAME not found"; exit 1; }
}

setup_appdir() {
  install -d -o "$USER_NAME" -g "$USER_NAME" /opt/meshmini
  install -m 0755 meshmini.py /opt/meshmini/meshmini.py
  chown "$USER_NAME:$USER_NAME" /opt/meshmini/meshmini.py
}

setup_venv() {
  if [[ ! -d /opt/meshmini/venv ]]; then
    sudo -u "$USER_NAME" $PYTHON_BIN -m venv /opt/meshmini/venv
  fi
  /opt/meshmini/venv/bin/python -m pip install -U pip $PIP_FLAGS
  /opt/meshmini/venv/bin/pip install meshtastic pypubsub $PIP_FLAGS
}

write_unit() {
  UNIT_PATH="/etc/systemd/system/meshmini.service"
  cat > "$UNIT_PATH" <<EOF
[Unit]
Description=MeshMini - minimal Meshtastic BBS
After=network.target

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=/opt/meshmini
Environment="PYTHONUNBUFFERED=1"
Environment="MMB_DB=/opt/meshmini/board.db"
Environment="MMB_DEVICE=$DEVICE"
Environment="MMB_NAME=$NAME"
Environment="MMB_ADMINS=$ADMINS"
Environment="MMB_RATE=8"
Environment="MMB_CH_FALLBACK=0"
Environment="MMB_REPLY_BCAST=0"
Environment="MMB_DIRECT_FALLBACK=0"
Environment="MMB_FALLBACK_SEC=5"
Environment="MMB_MAX_TEXT=110"
Environment="MMB_TX_GAP=1.5"
ExecStart=/opt/meshmini/venv/bin/python /opt/meshmini/meshmini.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

enable_start() {
  systemctl daemon-reload
  systemctl enable meshmini.service
  systemctl restart meshmini.service
  systemctl status meshmini.service --no-pager || true
}

main() {
  require_root
  parse_args "$@"
  ensure_user
  setup_appdir
  setup_venv
  write_unit
  enable_start
  echo
  echo "Done. Send '?' from a node to see the menu."
}
main "$@"
