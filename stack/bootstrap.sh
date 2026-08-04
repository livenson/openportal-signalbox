#!/usr/bin/env bash
# Initialises every agent in the test network and exchanges the invitations
# that let them connect, then writes the bridge invite that signalbox loads.
#
# Runs once as a one-shot compose service; the agents start afterwards and only
# ever read what this wrote.
#
#   op-bridge -> op-portal("waldur") -> op-provider -> op-clusters (platform)
#     -> op-cluster (instance) -> op-filesystem + op-slurm + op-localaccount
#
# That is the eight-agent shape signalbox was built against: a bridge, a
# portal, two routers, an instance that actually executes, and three leaves.
# tests/test_stack.py re-derives the tree from the `wire` calls below and
# checks opdata.walk() turns it into the destinations the front ends use, so
# changing the chain here without changing that test will fail the suite.
#
set -euo pipefail

CONFIG_ROOT="${CONFIG_ROOT:-/op-config}"
INVITE_DIR="${INVITE_DIR:-/openportal-invite}"
# Clients are allow-listed by IP. Pinning the compose subnet would stop two
# stacks coming up on one host, and every peer here is confined to the compose
# network anyway, so the list is left open.
CIDR="${OPENPORTAL_CLIENT_CIDR:-0.0.0.0/0}"
STAMP="${CONFIG_ROOT}/.bootstrapped"

if [ -f "$STAMP" ]; then
    echo "Agents already bootstrapped; nothing to do."
    exit 0
fi

# agent-dir : service-name : listen-port
AGENTS=(
    "portal:waldur:8040"
    "provider:provider:8041"
    "bridge:bridge:8044"
    "clusters:clusters:8045"
    "cluster:cluster:8046"
    "filesystem:filesystem:8047"
    "slurm:slurm:8048"
    "localaccount:localaccount:8049"
)

field() { echo "$1" | cut -d: -f"$2"; }

# Every agent's compose service is named after its binary, so peers reach each
# other on ws://op-<dir>:<port>.
conf() { echo "${CONFIG_ROOT}/$1/config.toml"; }
host() { echo "op-$1"; }

svc_name() {
    local dir=$1 entry
    for entry in "${AGENTS[@]}"; do
        [ "$(field "$entry" 1)" = "$dir" ] && { field "$entry" 2; return; }
    done
    echo "unknown-agent-$dir" >&2
    return 1
}

echo "==> Initialising agent configs"
for entry in "${AGENTS[@]}"; do
    dir=$(field "$entry" 1)
    name=$(field "$entry" 2)
    port=$(field "$entry" 3)
    mkdir -p "${CONFIG_ROOT}/${dir}"

    extra_args=()
    if [ "$dir" = "bridge" ]; then
        # The bridge additionally runs the HTTP API, which is the only way in:
        # signalbox holds the invite and signs every call to this endpoint.
        # No --signal-url and no --notification-url are given, because there is
        # no portal software behind this stack to receive either. The agents
        # then keep their default notification URL, which nothing serves, and
        # log "Dropping notification ... after 3 failed signal attempts" — the
        # noise the README calls out as harmless is therefore visible here too,
        # which is the point of a stack you can reproduce reports against.
        extra_args=(
            --bridge-url "http://op-bridge:3000"
            --bridge-ip "0.0.0.0"
            --bridge-port 3000
        )
    fi

    "op-${dir}" -c "$(conf "$dir")" init \
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
# No --type is passed. 0.91.0 added it, and declaring a peer `type = "portal"`
# switches on portal route discovery — agents then derive the route to each
# portal and refuse traffic that does not match it. That is worth having in a
# deployment and is exactly the wrong thing in a tool whose job is to show
# broken routing, so peers are left unchecked, as they were before 0.91.0.
wire() {
    local listener=$1 dialer=$2 dialer_name listener_name workdir
    dialer_name=$(svc_name "$dialer")
    listener_name=$(svc_name "$listener")
    workdir=$(mktemp -d)
    echo "==> Wiring ${dialer} -> ${listener} (dials in as '${dialer_name}')"
    (
        cd "$workdir"
        "op-${listener}" -c "$(conf "$listener")" client --add "$dialer_name" --ip "$CIDR"
        "op-${dialer}" -c "$(conf "$dialer")" server --add "invite_${listener_name}_default.toml"
    )
    rm -rf "$workdir"
}

# Each agent dials the one below it, and the cluster dials its own leaves.
# Only reachability depends on the direction — peers register by agent type
# once connected — so flipping a pair here is the fix if a link never comes up.
wire portal bridge
wire provider portal
wire clusters provider
wire cluster clusters
wire filesystem cluster
wire slurm cluster
wire localaccount cluster

echo "==> Applying agent-specific options"
# REST mode. The emulator's sacctmgr accepts `--json` but answers in table
# format, which op-slurm cannot parse, so every account and limit lookup fails
# with "Unknown command: --json". slurmrestd-emulator, from the same package,
# speaks the API the agent expects; it runs beside op-slurm inside op-node, so
# the server is on localhost. The emulator does not check the bearer token.
"op-slurm" -c "$(conf slurm)" extra --key slurm-server \
    --value "http://localhost:${SLURMRESTD_PORT:-6820}"
"op-slurm" -c "$(conf slurm)" extra --key slurm-user --value root
"op-slurm" -c "$(conf slurm)" extra --key token-command --value "echo signalbox-token"
"op-slurm" -c "$(conf slurm)" extra --key slurm-default-node \
    --value '{"cpus":4,"gpus":0,"mem":16000,"billing":4}'
"op-slurm" -c "$(conf slurm)" extra --key parent-account --value root

# op-localaccount runs the shadow-utils directly in its own container, so the
# command names need no prefix; only the managed group is pinned, so the agent
# can tell the accounts it created from system ones. 0.91.0 also refuses to add
# an account to a privileged system group, which is why this must not name one.
"op-localaccount" -c "$(conf localaccount)" extra --key managed-group --value openportal

# op-filesystem takes typed volume config rather than extras.
cat /opt/openportal/filesystem-volumes.toml >>"$(conf filesystem)"

echo "==> Writing bridge invite"
"op-bridge" -c "$(conf bridge)" bridge --config "${INVITE_DIR}/bridge-invite.toml"

# Deliberately no chmod here. 0.91.0 writes configs and the bridge invite
# owner-only and atomically, because both carry key material; widening them
# again would undo that. Nothing needs it — every container in this stack runs
# as root, and so does the signalbox container that mounts the invite volume.
touch "$STAMP"
echo "==> Bootstrap complete"
