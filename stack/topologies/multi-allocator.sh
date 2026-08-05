# shellcheck shell=bash
# Two allocators, two clusters, and traffic that crosses.
#
# Each allocator is a portal with its own bridge — its own portal software,
# its own API entry point — and both allocate onto the *same* clusters through
# a shared provider:
#
#   op-bridge  -> waldur    (portal) --\
#                                       >-- provider -> clusters (platform)
#   op-bridge2 -> hpcportal (portal) --/                  |          |
#                                                    cluster1    cluster2
#                                                    fs1/slurm1/ fs2/slurm2/
#                                                    acct1       acct2
#
# "Cross sending" is the point: both allocators send projects to both clusters,
# so `waldur.provider.clusters.cluster2` and
# `hpcportal.provider.clusters.cluster1` are both live routes and the two
# allocators' work meets on every shared hop. What does *not* happen is the
# portals talking to each other — the trust topology gives a Portal a key pair
# to its Provider and to nothing else, and zones exist specifically so that
# "portal A cannot receive messages intended for portal B"
# (docs/specifications/security-model.md §6).
#
# Everything is left in the default zone here. Zones are what an operator would
# add to enforce the separation; leaving them off keeps the shared hops visible,
# which is the thing worth looking at.
#
# Two bridges because two allocators means two portal deployments. signalbox
# discovers `op-bridge` and therefore shows the estate as *waldur* sees it;
# point it at the other allocator with
#
#     OPENPORTAL_BRIDGE_INVITE=/inv/bridge2-invite.toml ./run.sh
#
AGENTS=(
    "bridge:bridge:8044:op-bridge"
    "bridge2:bridge2:8054:op-bridge"
    "portal:waldur:8040:op-portal"
    "portal2:hpcportal:8050:op-portal"
    "provider:provider:8041:op-provider"
    "clusters:clusters:8045:op-clusters"
    "cluster:cluster1:8046:op-cluster"
    "cluster2:cluster2:8056:op-cluster"
    "filesystem:fs1:8047:op-filesystem"
    "slurm:slurm1:8048:op-slurm"
    "localaccount:acct1:8049:op-localaccount"
    "filesystem2:fs2:8057:op-filesystem"
    "slurm2:slurm2:8058:op-slurm"
    "localaccount2:acct2:8059:op-localaccount"
)

WIRES=(
    "portal:bridge"
    "portal2:bridge2"
    # The shared hop. Both allocators dial the one provider, which is what makes
    # every agent below this line reachable from either of them.
    "provider:portal"
    "provider:portal2"
    "clusters:provider"
    "cluster:clusters"
    "cluster2:clusters"
    "filesystem:cluster"
    "slurm:cluster"
    "localaccount:cluster"
    "filesystem2:cluster2"
    "slurm2:cluster2"
    "localaccount2:cluster2"
)
