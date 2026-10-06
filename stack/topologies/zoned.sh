# shellcheck shell=bash
# Two estates on one host, separated by zone.
#
# A zone is a named trust domain. It must match on both sides of a connection
# and is checked again on every message, so two deployments can share physical
# infrastructure with nothing able to cross between them — the case
# docs/specifications/security-model.md §6 gives as production beside test, or
# two operators on the same hardware.
#
#   zone alpha:  op-bridge  -> waldur    -> provider  -> clusters  -> cluster
#                              -> filesystem + slurm + localaccount
#   zone beta:   op-bridge2 -> hpcportal -> provider2 -> clusters2 -> cluster2
#                              -> filesystem2 + slurm2 + localaccount2
#
# Nothing is shared. That is the difference from multi-allocator.sh, where two
# allocators share a provider and can therefore reach each other's clusters:
# here they cannot reach anything of each other's, by construction.
#
# What this is for is finding out what one signalbox sees of such a host, which
# is not obvious before you run it — see docs/test-stack.md.
AGENTS=(
    "bridge:bridge:8044:op-bridge"
    "portal:waldur:8040:op-portal"
    "provider:provider:8041:op-provider"
    "clusters:clusters:8045:op-clusters"
    "cluster:cluster:8046:op-cluster"
    "filesystem:filesystem:8047:op-filesystem"
    "slurm:slurm:8048:op-slurm"
    "localaccount:localaccount:8049:op-localaccount"

    "bridge2:bridge2:8054:op-bridge"
    "portal2:hpcportal:8050:op-portal"
    "provider2:provider2:8061:op-provider"
    "clusters2:clusters2:8065:op-clusters"
    "cluster2:cluster2:8056:op-cluster"
    "filesystem2:fs2:8057:op-filesystem"
    "slurm2:slurm2:8058:op-slurm"
    "localaccount2:acct2:8059:op-localaccount"
)

# Third field is the zone. Every link in an estate carries that estate's zone,
# because a zone is a property of the connection and a message is checked
# against it at every hop.
WIRES=(
    "portal:bridge:alpha"
    "provider:portal:alpha"
    "clusters:provider:alpha"
    "cluster:clusters:alpha"
    "filesystem:cluster:alpha"
    "slurm:cluster:alpha"
    "localaccount:cluster:alpha"

    "portal2:bridge2:beta"
    "provider2:portal2:beta"
    "clusters2:provider2:beta"
    "cluster2:clusters2:beta"
    "filesystem2:cluster2:beta"
    "slurm2:cluster2:beta"
    "localaccount2:cluster2:beta"
)
