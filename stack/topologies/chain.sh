# shellcheck shell=bash
# The default topology: one allocator, one cluster.
#
# The shape signalbox was built against, and the smallest thing that executes
# real work — op-cluster refuses project and user jobs unless a filesystem, a
# scheduler and an account agent are all connected.
#
#   op-bridge -> waldur (portal) -> provider -> clusters (platform)
#     -> cluster (instance) -> filesystem + slurm + localaccount
#
# AGENTS entries are  <dir>:<agent-name>:<port>:<binary>
#
# `dir` is both the config directory and the DNS name peers dial (op-<dir>), so
# it has to match a compose service or one of its network aliases. `binary` is
# separate from `dir` because a topology may run the same agent twice — see
# multi-allocator.sh, where two clusters each get their own op-filesystem.
AGENTS=(
    "portal:waldur:8040:op-portal"
    "provider:provider:8041:op-provider"
    "bridge:bridge:8044:op-bridge"
    "clusters:clusters:8045:op-clusters"
    "cluster:cluster:8046:op-cluster"
    "filesystem:filesystem:8047:op-filesystem"
    "slurm:slurm:8048:op-slurm"
    "localaccount:localaccount:8049:op-localaccount"
)

# WIRES entries are <listener-dir>:<dialer-dir> — the listener admits the
# dialer, and the dialer imports the invite that produces. Each agent dials the
# one below it, and the cluster dials its own leaves. Only reachability depends
# on the direction, so flipping a pair is the fix if a link never comes up.
WIRES=(
    "portal:bridge"
    "provider:portal"
    "clusters:provider"
    "cluster:clusters"
    "filesystem:cluster"
    "slurm:cluster"
    "localaccount:cluster"
)
