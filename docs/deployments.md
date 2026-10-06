# Watching real deployments

With a Docker Compose deployment, `run.sh` and `tui.sh` find the bridge by
themselves. This page covers the other cases: agents running natively on a
host, and several deployments drawn in one graph.

## Native deployment

Not every network runs under Compose. When the agents are processes on the
host there is no container to discover, so point the launchers at the bridge
invite instead and they run the tool here rather than in a container:

```bash
OPENPORTAL_BRIDGE_INVITE=/path/to/bridge-invite.toml ./run.sh
```

That is the whole switch — an invite that exists means native mode, no invite
means Docker discovery, exactly as before. The launcher builds a
`.venv-signalbox` beside the scripts and installs the pinned client into it.
If you already have an interpreter with a matching `openportal` (the portal's
own virtualenv, usually), hand it over and nothing is installed:

```bash
SIGNALBOX_PYTHON=/srv/portal/.venv/bin/python ./tui.sh
```

The pin matters here in a way it does not under Docker, where one version
drives both halves of the stack. A native deployment may be running a
different release, and from 0.91.0 the client signs the V2 canonical string
while an older bridge verifies the V1 one — so a mismatch surfaces as an
authentication failure rather than a version error. signalbox compares the two
after connecting and says so:

```text
WARNING rp: client 0.93.0 against a bridge reporting 0.91.0
```

## Several deployments in one graph

A review portal and a site portal are two deployments, two bridges, two
invites — and one picture worth having, since the interesting failures are
between them. List them in a config file:

```toml
# signalbox.toml, beside the scripts (or point SIGNALBOX_CONFIG at it)
[[deployment]]
name = "rp"
invite = "/path/to/rp-invite.toml"
label = "Review portal"

[[deployment]]
name = "efp"
invite = "/path/to/efp-invite.toml"
label = "Site portal"

[[link]]
from = "rp"
to = "efp"
zone = "rp>efp"
```

Every agent is then tagged with the deployment it belongs to, and addressed by
a qualified key — two portals both having an agent called `portal` is the
normal case, not an edge case. Diagnostics, the log timeline and the console
all follow the selected agent's deployment, so an instruction is never written
against agents its bridge cannot reach. A deployment that is down is reported
next to the summary; the other one still draws.

`SIGNALBOX_INVITES="rp=/a.toml,efp=/b.toml"` does the same without a file, for
a shell that has no TOML parser to hand.

A `signalbox.toml` beside the scripts wins over the [test stack](test-stack.md),
and `./live.sh` would run its writing tests against the deployments it lists.
`SIGNALBOX_CONFIG=none` sets it aside for one command:
`SIGNALBOX_CONFIG=none ./live.sh` reaches the stack again.

### What `zone` is

Half of an agent's identity: peers are `name@zone` (`Peer { name, zone }`),
and every connect, watchdog, job and diagnostics hop carries it — the same
agent name can appear in more than one zone and they are different peers. A
bridge sits in its portal's `default` zone; between two portals the convention
is `<awarding-portal>><site-portal>`, so `rp>efp` reads "awards flow from rp to
efp". It is not decoration: `sync_offerings` registers each offering as a
virtual agent in that zone, so the link between the two portal agents has to
carry exactly it or no award ever arrives. Put the zone from your own wiring in
`[[link]]` and the graph will show it on the edge.

### Clicking the link

It opens what neither portal can show you alone: both ends' logs about each
other in one timeline, each fetched through its own bridge; the offerings each
portal has registered, since an offering is a virtual agent in that zone and an
award only arrives while the registration stands; whether each end's log
mentions the zone at all, which is how a mis-zoned link shows itself; and the
two engine versions side by side, because cross-site skew is the failure that
presents as an authentication error rather than a version one.

### The bridge's other end

`op-bridge` does not join two sites — it joins the agent protocol to
everything outside it. Its openportal end peers with its own portal and is
reported by `health()`; its other end is a signed HTTP endpoint that Waldur and
this tool call in on, and nothing on the wire mentions it (`HealthInfo` carries
no addresses for any agent). So it is drawn from the invite you already hold,
dashed and labelled *you are here*, as the one node that is not part of the
reported estate. The Waldur side of it — the bridge's `signal_url` — stays
invisible; it lives only in the bridge's own config.

### Why the link is configured rather than discovered

It is not an omission: OpenPortal refuses portal-to-portal health and
diagnostics *on purpose*. The responder ignores a health check whose sender is
a portal — `handler.rs`, "prevent information leakage between sites" — and the
requester strips portal peers out of the cascade before asking
(`health.rs::collect_health_inner`). So neither portal will ever report the
other, no matter which bridge you ask. An operator holding both invites already
knows the link exists; `[[link]]` is where they say so, and it is drawn dashed
and unlabelled by traffic to keep the distinction visible.
