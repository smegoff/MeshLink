#!/usr/bin/env bash
set -euo pipefail
systemctl disable --now meshmini.service || true
rm -f /etc/systemd/system/meshmini.service
systemctl daemon-reload
rm -rf /opt/meshmini
echo "MeshMini removed."
