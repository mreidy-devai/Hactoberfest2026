#!/usr/bin/env bash
# Start the local foot-routing server on http://localhost:5000
set -euo pipefail
cd "$(dirname "$0")"
[ -f data/.dataset ] || { echo "Run ./setup.sh first."; exit 1; }
BASE="$(cat data/.dataset)"
PORT="${PORT:-5000}"
# Bind to localhost only by default. Set BIND=0.0.0.0 to reach it from your phone on the same Wi-Fi.
BIND="${BIND:-127.0.0.1}"
echo "Routing server: http://$BIND:$PORT  (Ctrl+C to stop)"
exec docker run --rm -t -p "$BIND:$PORT:5000" -v "$PWD/data:/data" \
  ghcr.io/project-osrm/osrm-backend osrm-routed --algorithm mld "/data/$BASE.osrm"
