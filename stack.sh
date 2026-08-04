#!/usr/bin/env bash
# Bring up a throwaway OpenPortal agent network for signalbox to point at.
#
#   ./stack.sh up        build the agents and start the chain, wait for it
#   ./stack.sh down      stop it and delete its volumes
#   ./stack.sh status    what the bridge currently sees
#   ./stack.sh logs      follow every agent
#
# Then ./tui.sh or ./run.sh — they discover this stack the same way they
# discover a real one, by the op-bridge container and the invite volume, so
# nothing needs pointing at it.
#
# The first `up` downloads the eight agent binaries from their upstream release
# and builds one small image around them; afterwards the image is cached and
# the chain is up in seconds. See stack/Dockerfile for why the binaries are
# used rather than the published per-agent images.
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# The release both agents and client are pinned to.
# shellcheck source=openportal.env
. "${HERE}/openportal.env"
export OPENPORTAL_VERSION

COMPOSE=(docker compose -f "${HERE}/stack/docker-compose.yml")
AGENTS=(op-portal op-provider op-clusters op-cluster op-node op-bridge)

case "${1:-up}" in
    up)
        "${COMPOSE[@]}" build
        "${COMPOSE[@]}" up -d --wait "${AGENTS[@]}"
        # --wait only proves the bridge's port is open; the chain below it
        # connects afterwards, and a dead leaf leaves every container running.
        # op-probe asks the bridge over the signed API, which is what signalbox
        # will do, and blocks until every agent reports connected.
        echo "Waiting for the agent chain to connect..."
        if ! "${COMPOSE[@]}" exec -T op-bridge op-probe "${STACK_TIMEOUT:-180}"; then
            echo "Try '$0 logs' — a leaf that fails takes op-cluster's jobs with it." >&2
            exit 1
        fi
        echo
        echo "OpenPortal ${OPENPORTAL_VERSION} is up. Now run:  ./tui.sh   or   ./run.sh"
        ;;
    down)
        # -v because the invite and every agent key live in these volumes, and
        # a stale bootstrap stamp would otherwise skip the rewiring next time.
        "${COMPOSE[@]}" down -v --remove-orphans
        ;;
    status)
        "${COMPOSE[@]}" ps
        "${COMPOSE[@]}" exec -T op-bridge op-probe
        ;;
    logs)
        # Follows when asked for bare, so `stack.sh logs` is the watch command;
        # any argument means the caller wants a specific slice and an exit
        # (`stack.sh logs --tail 200`, as CI does on failure).
        shift
        if [ "$#" -eq 0 ]; then
            "${COMPOSE[@]}" logs -f
        else
            "${COMPOSE[@]}" logs "$@"
        fi
        ;;
    *)
        echo "usage: $0 {up|down|status|logs}" >&2
        exit 1
        ;;
esac
