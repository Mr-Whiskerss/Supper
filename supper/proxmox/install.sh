#!/usr/bin/env bash
# Installs (or updates) Supper inside a Debian/Ubuntu LXC container.
# Safe to run again: your recipes and plans in /var/lib/supper are kept.
#
#   bash proxmox/install.sh            # from the unzipped supper folder, inside the container
#   PORT=80 bash proxmox/install.sh    # serve on a different port
set -euo pipefail

APP_DIR=/opt/supper
DATA_DIR=/var/lib/supper
PORT="${PORT:-8080}"
SRC_DIR="${SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this as root inside the container."
[ -f "$SRC_DIR/app.py" ] || die "Can't find app.py in $SRC_DIR. Run this from the unzipped supper folder."
command -v apt-get >/dev/null || die "This installer needs a Debian or Ubuntu container."

info "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ca-certificates curl >/dev/null

info "Creating the supper service user"
id supper >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin supper

info "Copying the app to $APP_DIR"
mkdir -p "$APP_DIR" "$DATA_DIR"
rm -rf "$APP_DIR/static"
cp "$SRC_DIR/app.py" "$SRC_DIR/seed_recipes.py" "$SRC_DIR/requirements.txt" "$APP_DIR/"
cp -r "$SRC_DIR/static" "$APP_DIR/static"

info "Installing Python dependencies"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

chown -R root:root "$APP_DIR"
chown -R supper:supper "$DATA_DIR"
chmod 750 "$DATA_DIR"

info "Setting up the systemd service"
cat > /etc/systemd/system/supper.service <<UNIT
[Unit]
Description=Supper meal planner
After=network-online.target
Wants=network-online.target

[Service]
User=supper
Group=supper
WorkingDirectory=$APP_DIR
Environment=SUPPER_DB=$DATA_DIR/supper.db
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$APP_DIR/venv/bin/gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 app:app
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable supper >/dev/null 2>&1
systemctl restart supper

info "Waiting for Supper to start"
for _ in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null; then
    IP=$(hostname -I | awk '{print $1}')
    info "Supper is running: http://${IP:-<container-ip>}:$PORT"
    exit 0
  fi
  sleep 1
done
die "Supper didn't start. See: journalctl -u supper -n 50"
