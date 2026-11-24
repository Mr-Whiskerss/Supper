#!/usr/bin/env bash
# Run on the Proxmox host. Creates a Debian LXC container and installs Supper in it.
#
#   bash proxmox/create-lxc.sh
#
# Optional settings (put them in front of the command):
#   CTID=120              container ID (default: next free ID)
#   CT_HOSTNAME=supper    container hostname
#   STORAGE=local-lvm     storage for the container's disk
#   TEMPLATE_STORAGE=local  storage that holds LXC templates
#   DISK=4                disk size in GB
#   MEMORY=512            RAM in MB
#   CORES=1
#   BRIDGE=vmbr0
#   IP=dhcp               or a static address like 192.168.1.50/24
#   GATEWAY=192.168.1.1   needed with a static IP
#   PORT=8080             port Supper listens on
#   ROOT_PASSWORD=...     set a root password (otherwise use `pct enter <id>`)
#
# Example: CTID=150 IP=192.168.1.50/24 GATEWAY=192.168.1.1 bash proxmox/create-lxc.sh
set -euo pipefail

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

command -v pct >/dev/null || die "Run this on the Proxmox host, not inside a container."
[ "$(id -u)" -eq 0 ] || die "Run this as root."

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$SRC_DIR/app.py" ] || die "Can't find app.py in $SRC_DIR. Keep this script inside the unzipped supper folder."

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_HOSTNAME="${CT_HOSTNAME:-supper}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
DISK="${DISK:-4}"
MEMORY="${MEMORY:-512}"
CORES="${CORES:-1}"
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"
GATEWAY="${GATEWAY:-}"
PORT="${PORT:-8080}"

pct status "$CTID" >/dev/null 2>&1 && die "Container $CTID already exists. Pick another with CTID=..."
pvesm status | awk 'NR>1 {print $1}' | grep -qx "$STORAGE" \
  || die "Storage '$STORAGE' not found. Available: $(pvesm status | awk 'NR>1 {print $1}' | xargs). Set STORAGE=..."
[ "$IP" = "dhcp" ] || [ -n "$GATEWAY" ] || die "A static IP needs GATEWAY=... as well."

ARCH=$(dpkg --print-architecture)

info "Finding the latest Debian template for $ARCH"
pveam update >/dev/null || true
TEMPLATE=$(pveam available --section system | awk '{print $2}' | grep -E "^debian-13-standard_.*_${ARCH}\\.tar" | sort -V | tail -1)
[ -n "$TEMPLATE" ] || TEMPLATE=$(pveam available --section system | awk '{print $2}' | grep -E "^debian-12-standard_.*_${ARCH}\\.tar" | sort -V | tail -1)
[ -n "$TEMPLATE" ] || die "No Debian template found. Check the host's internet connection."
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  info "Downloading $TEMPLATE"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
fi

NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[ -n "$GATEWAY" ] && NET="$NET,gw=$GATEWAY"

info "Creating container $CTID ($CT_HOSTNAME)"
CREATE_ARGS=(
  "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE"
  --arch "$ARCH"
  --hostname "$CT_HOSTNAME"
  --cores "$CORES" --memory "$MEMORY" --swap 512
  --rootfs "$STORAGE:$DISK"
  --net0 "$NET"
  --unprivileged 1 --features nesting=1
  --onboot 1 --tags supper
  --description "Supper meal planner. Web UI on port $PORT. Data: /var/lib/supper"
)
[ -n "${ROOT_PASSWORD:-}" ] && CREATE_ARGS+=(--password "$ROOT_PASSWORD")
pct create "${CREATE_ARGS[@]}" >/dev/null
trap 'echo; echo "Setup stopped. To remove the half-made container: pct destroy $CTID --purge"' ERR
pct start "$CTID"

info "Waiting for the container's network"
for i in $(seq 1 60); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  [ "$i" -eq 60 ] && die "Container $CTID has no internet access. Check BRIDGE/IP/GATEWAY settings."
  sleep 1
done

info "Copying Supper into the container"
TMP=$(mktemp /tmp/supper-XXXXXX.tgz)
tar czf "$TMP" -C "$SRC_DIR" app.py seed_recipes.py requirements.txt static proxmox
pct push "$CTID" "$TMP" /tmp/supper.tgz
rm -f "$TMP"

pct exec "$CTID" -- bash -c "rm -rf /tmp/supper-src && mkdir -p /tmp/supper-src \
  && tar xzf /tmp/supper.tgz -C /tmp/supper-src \
  && PORT=$PORT bash /tmp/supper-src/proxmox/install.sh \
  && rm -rf /tmp/supper-src /tmp/supper.tgz"

CT_IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
echo
info "Done. Open http://${CT_IP}:$PORT"
echo "    Container: $CTID ($CT_HOSTNAME). Shell: pct enter $CTID"
echo "    Update later: bash proxmox/update-lxc.sh $CTID"
