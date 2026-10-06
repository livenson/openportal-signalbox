# A network to point it at

`./stack.sh up` builds a throwaway eight-agent network — the same shape as a
real deployment, with no portal software behind it — and `run.sh` and `tui.sh`
find it exactly as they find anything else.

```bash
./stack.sh up                    # under a minute, cold
./tui.sh                         # or ./run.sh
./stack.sh status                # what the bridge sees
./stack.sh topologies            # what shapes are available
./stack.sh up multi-allocator    # a harder one
./stack.sh down                  # and its volumes
```

## Topologies

**`chain`** (default) — one allocator, one cluster. The shape the front ends
were designed against:

```
op-bridge → waldur (portal) → provider → clusters (platform)
  → cluster (instance) → filesystem + slurm + localaccount
```

**`zoned`** — two estates on one host, separated by zone. Nothing is shared,
and the separation is total: a bridge sees its own zone and the other estate is
**absent from the health report**, not merely unreachable in it. So one
signalbox shows one zone, and a zone-separated host needs one instance per
zone. Point the second one at the other estate with
`OPENPORTAL_BRIDGE_INVITE=/inv/bridge2-invite.toml ./run.sh`.

**`multi-allocator`** — two allocators, each with its own portal and bridge,
both allocating onto two shared clusters through one provider. Their traffic
crosses on every shared hop, and `waldur.provider.clusters.cluster2` and
`hpcportal.provider.clusters.cluster1` are both live routes:

```
op-bridge  → waldur    (portal) ─┐
                                  ├→ provider → clusters ─┬→ cluster1 → fs1/slurm1/acct1
op-bridge2 → hpcportal (portal) ─┘                        └→ cluster2 → fs2/slurm2/acct2
```

The allocators do not talk to each other, and cannot: a Portal holds a key pair
to its Provider and to nothing else, and zones exist so "portal A cannot receive
messages intended for portal B". Point signalbox at the second allocator with
`OPENPORTAL_BRIDGE_INVITE=/inv/bridge2-invite.toml ./run.sh`.

## How it is built

All eight agents come from upstream's release binaries — statically linked,
~8 MB each — dropped into one image. Upstream also publishes per-agent OCI
images, but not for `op-localaccount`, and they are the wrong shape here
anyway: the leaf agents shell out (`op-localaccount` to `useradd`, `op-slurm`
to `sacctmgr`), which a distroless image has nothing to run.

The three leaves share one container, because op-localaccount creates the Unix
group and op-filesystem chowns to it — split apart, every `add_project` fails
on a group the filesystem agent cannot see. They keep separate identities and
ports, so signalbox shows them as the three agents they are.

This is a development stack in the fullest sense: the invite it mints is a
full-control credential, and the console will create real users and groups
inside it. That is what it is for.

## Two allocators on one estate

The multi-allocator topology is the shape that used to be drawn wrong, and it
is worth understanding because a national service sold through more than one
allocation route is exactly this picture.

Reaching the second allocator means going *through* the provider both of them
share, so a plain walk hands it `waldur.provider.hpcportal` — a path that reads
as a downstream agent of waldur's. That path is not nothing: diagnostics is
routed hop by hop across the peer graph, so it genuinely reaches hpcportal and
the inspector opens on it. What it is not is a destination. Instructions are
addressed `<portal>.<agent>...` from the portal that *owns* the agent, and
there is no such route from here — a job sent there is never refused, it simply
never lands.

So the two questions are answered separately. Every agent carries:

- **`id`** — how to ask about it through this bridge. Works for the whole
  graph, including the other allocator's half.
- **`route`** — where to send it an instruction, or `null` when there is no
  route from here. The console only ever offers agents that have one.

![two allocators sharing a provider, the second marked as unaddressable](multi-allocator.png)

Above: `./stack.sh up multi-allocator`. Work flows through both clusters —
either allocator can allocate onto either — while `hpcportal` and its `bridge2`
are drawn back, dashed, as another allocator's. Click them and the inspector
still opens; the console will not aim at them.

The health report is what makes this possible: every agent reports its
`agent_type`, and a portal reached below the first hop is by construction
somebody else's, because OpenPortal roots every route at a portal and forbids a
portal from querying another.
