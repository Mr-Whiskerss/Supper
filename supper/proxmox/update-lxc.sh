#!/usr/bin/env bash
# Run on the Proxmox host to update Supper in an existing container.
# Your recipes and plans are kept.
#
#   bash proxmox/update-lxc.sh <container-id>
set -euo pipefail

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

command -v pct >/dev/null || die "Run this on the Proxmox host."
CTID="${1:-}"
[ -n "$CTID" ] || die "Usage: bash proxmox/update-lxc.sh <container-id>"
pct status "$CTID" | grep -q running || die "Container $CTID isn't running. Start it with: pct start $CTID"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Keep the port the container is already using.
PORT=$(pct exec "$CTID" -- sed -n 's/.*--bind 0.0.0.0:\([0-9]*\).*/\1/p' /etc/systemd/system/supper.service 2>/dev/null || true)
PORT="${PORT:-8080}"

info "Backing up the database inside the container"
pct exec "$CTID" -- bash -c 'f=/var/lib/supper/supper.db; [ -f "$f" ] && cp "$f" "$f.bak-$(date +%Y%m%d-%H%M%S)" || true'

info "Copying the new version into container $CTID"
TMP=$(mktemp /tmp/supper-XXXXXX.tgz)
tar czf "$TMP" -C "$SRC_DIR" app.py seed_recipes.py requirements.txt static proxmox
pct push "$CTID" "$TMP" /tmp/supper.tgz
rm -f "$TMP"

pct exec "$CTID" -- bash -c "rm -rf /tmp/supper-src && mkdir -p /tmp/supper-src \
  && tar xzf /tmp/supper.tgz -C /tmp/supper-src \
  && PORT=$PORT bash /tmp/supper-src/proxmox/install.sh \
  && rm -rf /tmp/supper-src /tmp/supper.tgz"
