#!/usr/bin/env bash
# Run the live suite against a running OpenPortal agent network.
#
#   ./stack.sh up && ./live.sh     # against the throwaway stack
#   ./live.sh                      # against whatever bridge is running
#   NETWORK=... INVITE_VOLUME=... ./live.sh
#
# Same discovery and the same container shape as run.sh and tui.sh, for the
# same reason: op-bridge listens only inside the agent network and every call
# to it is signed with the invite key, so the tests cannot run on the host.
#
# These tests submit jobs — they create real projects and users on the agents
# they point at. That is the point of them, and it is also why this is aimed at
# a throwaway stack. SIGNALBOX_READONLY=1 skips the write tests.
#
set -euo pipefail

# shellcheck source=detect.sh
. "$(cd "$(dirname "$0")" && pwd)/detect.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"

if resolve_native; then
    PYTHON="$(native_python "$HERE" pytest)"
    cd "$HERE"
    exec env SIGNALBOX_LIVE=1 SIGNALBOX_READONLY="${SIGNALBOX_READONLY:-}" \
        OPENPORTAL_VERSION="$OPENPORTAL_VERSION" \
        "$PYTHON" -m pytest tests/test_live.py -v -m live -p no:cacheprovider "${@:-}"
fi

resolve_target

exec docker run --rm \
    --name signalbox-live \
    --network "$NETWORK" \
    -e SIGNALBOX_LIVE=1 \
    -e SIGNALBOX_CONFIG=none \
    -e SIGNALBOX_READONLY="${SIGNALBOX_READONLY:-}" \
    -e OPENPORTAL_VERSION="$OPENPORTAL_VERSION" \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "${INVITE_VOLUME}:/inv:ro" \
    -v "${HERE}:/app:ro" \
    -w /app \
    python:3.12-slim \
    sh -c "pip install --quiet --disable-pip-version-check \
             openportal==${OPENPORTAL_VERSION} pytest \
           && python -m pytest tests/test_live.py -v -m live -p no:cacheprovider ${*:-}"
