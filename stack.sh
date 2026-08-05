#!/usr/bin/env bash
# Bring up a throwaway OpenPortal agent network for signalbox to point at.
#
#   ./stack.sh up [topology]   start a network and wait for it to connect
#   ./stack.sh down            stop it and delete its volumes
#   ./stack.sh status          what the bridge currently sees
#   ./stack.sh logs            follow every agent
#   ./stack.sh topologies      what shapes are available
#
# Topologies live in stack/topologies/. The default, `chain`, is one allocator
# and one cluster. `multi-allocator` is two of each, sharing a provider, which
# is where signalbox stops being able to draw the truth — see the README.
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

ACTION="${1:-up}"
# A topology is only chosen at `up`; afterwards it is whatever is running, and
# the compose profile has to match or `down` would leave containers behind.
if [ "$ACTION" = "up" ]; then
    STACK_TOPOLOGY="${2:-${STACK_TOPOLOGY:-chain}}"
    if [ ! -f "${HERE}/stack/topologies/${STACK_TOPOLOGY}.sh" ]; then
        echo "No such topology '${STACK_TOPOLOGY}'. Available:" >&2
        "$0" topologies >&2
        exit 1
    fi
    echo "$STACK_TOPOLOGY" >"${HERE}/.stack-topology"
elif [ -f "${HERE}/.stack-topology" ]; then
    STACK_TOPOLOGY=$(cat "${HERE}/.stack-topology")
else
    STACK_TOPOLOGY="${STACK_TOPOLOGY:-chain}"
fi
export STACK_TOPOLOGY

COMPOSE=(docker compose -f "${HERE}/stack/docker-compose.yml"
         --profile "$STACK_TOPOLOGY")
AGENTS=(op-portal op-provider op-clusters op-cluster op-node op-bridge)
case "$STACK_TOPOLOGY" in
    multi-allocator) AGENTS+=(op-portal2 op-bridge2 op-cluster2 op-node2) ;;
    zoned) AGENTS+=(op-portal2 op-bridge2 op-cluster2 op-node2
                    op-provider2 op-clusters2) ;;
esac

case "$ACTION" in
    up)
        echo "topology: ${STACK_TOPOLOGY}"
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
        rm -f "${HERE}/.stack-topology"
        ;;
    topologies)
        for f in "${HERE}"/stack/topologies/*.sh; do
            name=$(basename "$f" .sh)
            printf '  %-16s %s\n' "$name" "$(sed -n '2s/^# //p' "$f")"
        done
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
        echo "usage: $0 {up [topology]|down|status|logs|topologies}" >&2
        exit 1
        ;;
esac
