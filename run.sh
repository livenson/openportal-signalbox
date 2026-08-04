#!/usr/bin/env bash
# Start signalbox (graph view) against a running OpenPortal agent stack.
#
# The bridge only listens inside the compose network and every call to it must
# be signed with the invite key, so the proxy runs as a container on that
# network with the invite volume mounted, and publishes only its own port to
# the host.
#
#   ./run.sh                       # finds the running bridge by itself
#   NETWORK=... INVITE_VOLUME=... ./run.sh
#
set -euo pipefail

# shellcheck source=detect.sh
. "$(cd "$(dirname "$0")" && pwd)/detect.sh"
resolve_target
PORT="${PORT:-8900}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "signalbox on http://localhost:${PORT} (ctrl-c to stop)"

# Interactive when run from a terminal, still works when piped or backgrounded.
# A plain string rather than an array: bash 3.2 (macOS) treats an empty array
# as unbound under `set -u`.
TTY_FLAGS=""
if [ -t 0 ] && [ -t 1 ]; then TTY_FLAGS="-it"; fi

# shellcheck disable=SC2086  # intentional word splitting of the optional flag
exec docker run --rm $TTY_FLAGS \
    --name signalbox \
    --network "$NETWORK" \
    -p "${PORT}:${PORT}" \
    -e PORT="$PORT" \
    -e SIGNALBOX_READONLY="${SIGNALBOX_READONLY:-}" \
    -v "${INVITE_VOLUME}:/inv:ro" \
    -v "${HERE}:/app:ro" \
    python:3.10-slim \
    sh -c "pip install --quiet --disable-pip-version-check openportal==${OPENPORTAL_VERSION} && python /app/server.py"
