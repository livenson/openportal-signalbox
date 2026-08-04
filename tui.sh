#!/usr/bin/env bash
# signalbox — terminal view of a running OpenPortal agent network.
#
# Runs inside the agent network because op-bridge only listens there, and every
# call to it must be signed with the invite key.
#
#   ./tui.sh                       # finds the running bridge by itself
#   NETWORK=... INVITE_VOLUME=... ./tui.sh
#
set -euo pipefail

# shellcheck source=detect.sh
. "$(cd "$(dirname "$0")" && pwd)/detect.sh"
resolve_target
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ ! -t 1 ]; then
    echo "This is a terminal UI — run it from a terminal." >&2
    exit 1
fi

exec docker run --rm -it \
    --name signalbox-tui \
    --network "$NETWORK" \
    -e TERM="${TERM:-xterm-256color}" \
    -e COLORTERM="${COLORTERM:-truecolor}" \
    -v "${INVITE_VOLUME}:/inv:ro" \
    -v "${HERE}:/app:ro" \
    -w /app \
    python:3.10-slim \
    sh -c "pip install --quiet --disable-pip-version-check openportal==${OPENPORTAL_VERSION} textual && python tui.py"
