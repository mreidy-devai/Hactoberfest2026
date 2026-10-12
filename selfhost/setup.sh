#!/usr/bin/env bash
# One-time setup: download an OpenStreetMap extract and build a foot-routing graph for OSRM.
# Usage: ./setup.sh [extract-url]     (default: New Jersey)
# Needs Docker and ~1-2 GB free disk for a state-sized extract.
set -euo pipefail

URL="${1:-https://download.geofabrik.de/north-america/us/new-jersey-latest.osm.pbf}"
IMAGE="ghcr.io/project-osrm/osrm-backend"
cd "$(dirname "$0")"
mkdir -p data
FILE="$(basename "$URL")"
BASE="${FILE%.osm.pbf}"

if [ ! -f "data/$FILE" ]; then
  echo "Downloading $URL"
  curl -L --fail -o "data/$FILE" "$URL"
fi

run() { docker run --rm -t -v "$PWD/data:/data" "$IMAGE" "$@"; }

echo "Extracting with the foot profile (a few minutes)..."
run osrm-extract -p /opt/foot.lua "/data/$FILE"
run osrm-partition "/data/$BASE.osrm"
run osrm-customize "/data/$BASE.osrm"

echo "$BASE" > data/.dataset
echo
echo "Done. Start the server with:  ./start.sh"
