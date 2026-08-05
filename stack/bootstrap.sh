#!/usr/bin/env bash
# Initialises every agent in the chosen topology and exchanges the invitations
# that let them connect, then writes the bridge invite that signalbox loads.
#
# Runs once as a one-shot compose service; the agents start afterwards and only
# ever read what this wrote.
#
# The shape comes from topologies/<STACK_TOPOLOGY>.sh, which defines AGENTS and
# WIRES; everything below is generic over those. tests/test_stack.py re-derives
# each topology's tree from the same two arrays and checks opdata.walk() turns
# it into the destinations the front ends use, so changing a topology without
# changing what signalbox expects to address will fail the suite.
#
set -euo pipefail

CONFIG_ROOT="${CONFIG_ROOT:-/op-config}"
INVITE_DIR="${INVITE_DIR:-/openportal-invite}"
TOPOLOGY="${STACK_TOPOLOGY:-chain}"
# Clients are allow-listed by IP. Pinning the compose subnet would stop two
# stacks coming up on one host, and every peer here is confined to the compose
# network anyway, so the list is left open.
CIDR="${OPENPORTAL_CLIENT_CIDR:-0.0.0.0/0}"
STAMP="${CONFIG_ROOT}/.bootstrapped"

TOPOLOGY_FILE="/opt/openportal/topologies/${TOPOLOGY}.sh"
if [ ! -f "$TOPOLOGY_FILE" ]; then
    echo "No such topology '${TOPOLOGY}'. Available:" >&2
    for available in /opt/openportal/topologies/*.sh; do
        basename "$available" .sh >&2
    done
    exit 1
fi
# The file is chosen at run time; chain.sh stands in for the shape of them all.
# shellcheck source-path=SCRIPTDIR source=topologies/chain.sh
. "$TOPOLOGY_FILE"

if [ -f "$STAMP" ]; then
    echo "Agents already bootstrapped; nothing to do."
    exit 0
fi

echo "==> Topology: ${TOPOLOGY} (${#AGENTS[@]} agents)"

field() { echo "$1" | cut -d: -f"$2"; }

conf() { echo "${CONFIG_ROOT}/$1/config.toml"; }
# Peers dial an agent at ws://op-<dir>:<port>, so the directory name is also the
# compose service name or one of its network aliases.
host() { echo "op-$1"; }

lookup() {
    local dir=$1 want=$2 entry
    for entry in "${AGENTS[@]}"; do
        [ "$(field "$entry" 1)" = "$dir" ] && { field "$entry" "$want"; return; }
    done
    echo "no agent '${dir}' in topology ${TOPOLOGY}" >&2
    return 1
}
svc_name() { lookup "$1" 2; }
binary() { lookup "$1" 4; }

echo "==> Initialising agent configs"
for entry in "${AGENTS[@]}"; do
    dir=$(field "$entry" 1)
    name=$(field "$entry" 2)
    port=$(field "$entry" 3)
    bin=$(field "$entry" 4)
    mkdir -p "${CONFIG_ROOT}/${dir}"

    extra_args=()
    if [ "$bin" = "op-bridge" ]; then
        # A bridge additionally runs the HTTP API, which is the only way in:
        # signalbox holds the invite and signs every call to this endpoint.
        # No --signal-url and no --notification-url are given, because there is
        # no portal software behind this stack to receive either. The agents
        # then keep their default notification URL, which nothing serves, and
        # log "Dropping notification ... after 3 failed signal attempts" — the
        # noise the README calls out as harmless is therefore visible here too,
        # which is the point of a stack you can reproduce reports against.
        extra_args=(
            --bridge-url "http://$(host "$dir"):3000"
            --bridge-ip "0.0.0.0"
            --bridge-port 3000
        )
    fi

    "$bin" -c "$(conf "$dir")" init \
        --service "$name" \
        --url "ws://$(host "$dir"):${port}" \
        --ip "0.0.0.0" \
        --port "$port" \
        --force \
        "${extra_args[@]}"
done

# wire <listener-dir> <dialer-dir>
#
# The listener admits the dialer (`client --add`, which mints an invite file)
# and the dialer imports that invite as an outbound peer (`server --add`). The
# invite lands in ./invite_<listener-name>_<zone>.toml — named after the agent
# that issued it, not the client it admits, which the upstream worked example
# had the wrong way round until 0.91.0 (isambard-sc/openportal#21). Each pair
# runs in its own directory so concurrent invites cannot tread on one another.
#
# Neither --type nor --zone is passed. 0.91.0 added both: --type declares what
# an agent must present itself as, and declaring a peer `type = "portal"`
# switches on portal route discovery, where agents derive the route to each
# portal and refuse traffic that does not match. --zone is what an operator
# would use to stop two allocators' messages meeting at all. Both are exactly
# wrong in a tool for looking at broken routing, so peers are left unchecked
# and everything shares the default zone.
wire() {
    local listener=$1 dialer=$2 dialer_name listener_name workdir
    dialer_name=$(svc_name "$dialer")
    listener_name=$(svc_name "$listener")
    workdir=$(mktemp -d)
    echo "==> Wiring ${dialer} -> ${listener} (dials in as '${dialer_name}')"
    (
        cd "$workdir"
        "$(binary "$listener")" -c "$(conf "$listener")" \
            client --add "$dialer_name" --ip "$CIDR"
        "$(binary "$dialer")" -c "$(conf "$dialer")" \
            server --add "invite_${listener_name}_default.toml"
    )
    rm -rf "$workdir"
}

for pair in "${WIRES[@]}"; do
    wire "$(field "$pair" 1)" "$(field "$pair" 2)"
done

echo "==> Applying agent-specific options"
for entry in "${AGENTS[@]}"; do
    dir=$(field "$entry" 1)
    bin=$(field "$entry" 4)
    case "$bin" in
        op-slurm)
            # REST mode. The emulator's sacctmgr accepts `--json` but answers in
            # table format, which op-slurm cannot parse, so every account and
            # limit lookup fails with "Unknown command: --json".
            # slurmrestd-emulator, from the same package, speaks the API the
            # agent expects; it runs beside op-slurm inside the node container,
            # so the server is on localhost. It does not check the bearer token.
            "$bin" -c "$(conf "$dir")" extra --key slurm-server \
                --value "http://localhost:${SLURMRESTD_PORT:-6820}"
            "$bin" -c "$(conf "$dir")" extra --key slurm-user --value root
            "$bin" -c "$(conf "$dir")" extra --key token-command \
                --value "echo signalbox-token"
            "$bin" -c "$(conf "$dir")" extra --key slurm-default-node \
                --value '{"cpus":4,"gpus":0,"mem":16000,"billing":4}'
            "$bin" -c "$(conf "$dir")" extra --key parent-account --value root
            ;;
        op-localaccount)
            # Runs the shadow-utils directly in its own container, so the
            # command names need no prefix; only the managed group is pinned, so
            # the agent can tell the accounts it created from system ones.
            # 0.91.0 also refuses to add an account to a privileged system
            # group, which is why this must not name one.
            "$bin" -c "$(conf "$dir")" extra --key managed-group --value openportal
            ;;
        op-filesystem)
            # Takes typed volume config rather than extras.
            cat /opt/openportal/filesystem-volumes.toml >>"$(conf "$dir")"
            ;;
    esac
done

echo "==> Writing bridge invites"
# One per bridge, named after it. A topology with two allocators has two, and
# which one signalbox loads is which allocator's view of the estate it shows.
for entry in "${AGENTS[@]}"; do
    dir=$(field "$entry" 1)
    [ "$(field "$entry" 4)" = "op-bridge" ] || continue
    "op-bridge" -c "$(conf "$dir")" bridge --config "${INVITE_DIR}/${dir}-invite.toml"
    echo "    ${INVITE_DIR}/${dir}-invite.toml"
done

# What the readiness probe should wait for. Written here rather than hardcoded
# in probe.py, because the answer is whatever topology was selected.
for entry in "${AGENTS[@]}"; do field "$entry" 2; done >"${INVITE_DIR}/expected-agents.txt"

# Deliberately no chmod. 0.91.0 writes configs and the bridge invite owner-only
# and atomically, because both carry key material; widening them again would
# undo that. Nothing needs it — every container in this stack runs as root, and
# so does the signalbox container that mounts the invite volume.
touch "$STAMP"
echo "==> Bootstrap complete"
