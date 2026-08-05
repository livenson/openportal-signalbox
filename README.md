# signalbox

Development tools for watching — and poking at — a running
[OpenPortal](https://github.com/isambard-sc/openportal) agent network. Answers
"which hop is broken, and what did it say?" without reading several containers'
logs side by side.

Named for the railway signal box, which is where you watch and route traffic
across a network. OpenPortal's own crates are Great Western Railway references
(`paddington`, `templemeads`, `greatwestern`), so it keeps the convention.

Two front ends over one shared data layer (`opdata.py`):

| | |
|---|---|
| `./tui.sh` | **Terminal UI** — for actually watching a system |
| `./run.sh` | **Graph view** — the shape of the network, live traffic, per-link history, and a console |

Both find the running bridge by themselves; you only need Docker.

```bash
git clone https://github.com/livenson/openportal-signalbox
cd openportal-signalbox
./tui.sh          # or ./run.sh, then open http://localhost:8900
```

No agent network to hand? `./stack.sh up` builds a throwaway eight-agent one —
see [A network to point it at](#a-network-to-point-it-at).

Everything comes from the bridge's signed HTTP API — `health()` for the agent
tree, `diagnostics(destination)` for one agent's jobs, warnings and log — so no
agent needs changing and nothing is installed alongside them.

## Terminal UI

![agents table with per-agent detail](docs/tui-agents.svg)

**Agents** (`1`) lists every agent with state, uptime, workers, jobs in flight
and failures. Moving the cursor loads that agent's diagnostics on the right:
its running, failed and slowest jobs, warnings, and the tail of its log.

**Logs** (`2`) is one timeline across every agent, which is the view that is
genuinely awkward otherwise — each agent keeps its own buffer, so following a
single job means reading several containers at once. Filter by level (`INFO`,
`WARN+`, `ERROR`) and by substring.

![merged cross-agent log timeline](docs/tui-logs.svg)

Keys: `1`/`2` switch view, `r` refresh, `p` pause auto-refresh, `n` show the
tool's own polling chatter, `q` quit.

## Graph view

![topology graph with per-agent metrics and live traffic](docs/flow.png)

Built with [React Flow](https://reactflow.dev) laid out by dagre, and — despite
React Flow normally meaning React, JSX and a bundler — with **no build step**:
`htm` replaces JSX with tagged templates and everything loads from esm.sh as
plain ES modules. One HTML file, no npm.

- **Cards, not dots.** Each agent shows the numbers you triage on — jobs
  running, failures, mean job time — so nothing needs a click. An icon per role
  is drawn in the muted colour: the icon carries *identity*, the left border
  carries *status*, and keeping those on separate channels keeps both readable.
- **Traffic flows along the edges.** Dots travel each hop that carried work,
  labelled with the count. There is no per-edge counter in the bridge, so this
  is the rise in an agent's completed count between polls, pushed up its own
  route — work executed on a leaf must have crossed every hop above it, and
  routers never increment a counter of their own.
- **Click an agent** for its jobs, warnings and log; **click a link** for that
  peer connection: both ends' engine versions, what it carried, and the
  connection log reconstructed from *both* endpoints — handshakes, watchdog
  timeouts, reconnects — which otherwise lives in two containers.

![link inspector showing both ends of a peer connection](docs/edge.png)

- **Filter messages** by level and substring. The filter is applied by the
  agent itself, so narrowing asks for less rather than hiding rows locally.
- **Run instructions** from the console, aimed at whichever agent is selected.

## The console, and learning the protocol

The preset chips walk the instruction grammar in order — `add_project`,
`get_project_mapping`, `add_user`, `get_usage_report`, `get_limit`, plus a
deliberately broken destination so you can watch a routing failure. They share
one demo project, so running them top to bottom is a working tour.

Presets are **role-aware**. Routers (provider, platform) and the bridge forward
instructions rather than executing them, so a preset aimed at one is guaranteed
to fail; each preset knows which agent role can answer it and targets
accordingly. Nothing is hardcoded — the portal names itself through the API.

### "register offering"

`get_projects` is answered by the *portal software* (e.g. Waldur), not by an
agent, and the bridge accepts job submissions only from **virtual** agents — a
same-process stand-in that re-injects the message into the portal's own
handler. Those come from `sync_offerings`, and each becomes addressable as
`<portal>.<offering>`.

So `<portal>.<some-agent>` is not a route and simply errors. The console
registers an offering for you when a preset needs one.

### The console writes

Everything else in signalbox only reads. The console submits jobs, so it can
change the system — it creates real projects and users on real agents. That is
the point of it, but it means this is a tool for development stacks.

```bash
SIGNALBOX_READONLY=1 ./run.sh    # console disabled, viewer unchanged
```

A job still pending when the wait expires is reported as a failure rather than
a success — an instruction aimed at an unroutable agent is never rejected, it
simply never lands, and calling that "ok" would read as a pass.

## A network to point it at

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

**`chain`** (default) — one allocator, one cluster. The shape the front ends
were designed against:

```
op-bridge → waldur (portal) → provider → clusters (platform)
  → cluster (instance) → filesystem + slurm + localaccount
```

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

## Three things that look broken but are not

**`Dropping notification … after 3 failed signal attempts`** in agent logs. If
the bridge was initialised without `--notification-url` it keeps its default of
`http://localhost/notification`, which nothing serves, so every notification is
retried three times and dropped. Portal software that implements no
notification receiver (Waldur, today) loses nothing by this — and `stack.sh`,
which has no portal software at all, logs it constantly.

**`<portal>.<agent> get_projects <portal>` errors.** See "register offering"
above — that address has to be a registered offering, not an agent name.

**`401 Unauthorized … Date is outside acceptable time window`.** Not the
invite. Every request is signed with a `Date`, and the bridge rejects one more
than **five seconds** from its own clock — so this is clock skew between
wherever signalbox runs and wherever the bridge runs, and under Docker Desktop
it also shows up on its own after the VM's clock jumps. Retrying works; a
container restart fixes it for good. Worth knowing because a 401 otherwise
reads as a bad key.

## What signalbox gets wrong on a shared estate

The multi-allocator topology exists because it is the shape signalbox draws
**incorrectly**, and it is better to be able to reproduce that than to describe
it. Two allocators sharing a provider is a real deployment; a national service
sold through more than one allocation route is exactly this picture.

The health report *does* say what that peer is — every agent carries
`agent_type`, and `hpcportal` reports `portal`. signalbox reads it, and the
card in the picture below is drawn with the portal icon and badge because of
it. What `opdata.walk()` does not do is *act* on it: every peer extends the
path it was reached by, whatever its type. So walking out from one bridge
reaches the other allocator through the shared provider and hands it a path
that reads as a downstream agent:

```
waldur.provider.hpcportal            ← the other allocator's portal
waldur.provider.hpcportal.bridge2    ← and its bridge
```

![two allocators sharing a provider, the second drawn as a subordinate branch](docs/multi-allocator.png)

Above: `./stack.sh up multi-allocator`. Work flows through both clusters, and
`hpcportal` with its `bridge2` hangs off `provider` on a dead link at the
bottom — the second allocator drawn as if it reported to the first.

Neither is a route an instruction can use, and the fix is not blocked on
missing information: a portal reached below the root's own portal is another
allocator, because OpenPortal roots every route at a portal and forbids a
portal from querying another. The trap is that they are not
obviously wrong: `diagnostics()` on those paths **works**, so the node opens in
the inspector and looks legitimate, while an instruction aimed at it never
lands — it just sits non-terminal until the wait expires. The hierarchy is also
inverted: the second allocator is drawn below the first one's provider, as if
subordinate to it.

Multi-cluster is fine. Two clusters under one platform render correctly, both
are addressable from either allocator, and the portal name namespaces the local
group, so `xsend.waldur` and `xsend.hpcportal` do not collide on a shared
cluster.

## Limitations

- **Logs are recent-only.** Each agent keeps an in-memory ring buffer that dies
  with the process, so this is a live-debugging tool, not post-mortem.
- **Everything routes through the bridge.** If the bridge is down you get an
  error and nothing else — the one case where you must fall back to
  `docker logs` and the per-agent healthcheck ports.
- **The invite is a credential.** Holding it means full control of the agent
  network, not read-only access, whatever these tools choose to call. Keep it
  to development stacks.
- **Polling, not streaming.** Five-second refresh; the bridge does not push.
- Traffic counts are per refresh interval, not a per-job trace. A job that
  starts and finishes between two polls is counted, but never seen moving.
- The graph view loads React Flow and dagre from a CDN, so it needs network
  access on first load. The TUI has no such dependency.

## Development

```bash
pip install pytest && python -m pytest      # 68 tests, ~5s, no network
ruff check . && ruff format --check .

./stack.sh up && ./live.sh                  # 22 more, against real agents
```

The offline suite fakes the compiled `openportal` module rather than installing
it — the real one is useless without a live agent network. The fake
deliberately keeps the bindings' quirks, most notably that `HealthInfo` exposes
`peers` as a method while its neighbours are properties; smoothing that over
would test the wrong thing.

Those tests are weighted towards what actually broke while building this: the
property-vs-method inconsistency, peer links that turn a tree walk into a loop,
filtering that ate its own limit, a job reported as successful when it had not
finished, and a cache keyed without its filters. `tests/test_stack.py` adds the
contract between the stack and the launchers — the compose service and volume
names discovery matches on, and the version pin, which has to be one release
across client and agents because a mismatch fails as an authentication error
rather than a version warning.

`tests/test_live.py` closes the gap the fake cannot: it asserts the same
documented constraints against agents that actually exist — that `peers` really
is a method, that a router really does refuse to execute, that polling really
does log itself, that an unroutable destination really does come back
unfinished rather than rejected. A fake is only ever as correct as our reading
of the bindings, and this is what checks the reading. Run it with `./live.sh`,
which puts pytest inside the agent network the same way `run.sh` puts the proxy
there; on its own, `python -m pytest` deselects it.

CI runs the offline suite on every push, and the live one nightly and on
demand — the agents are pinned to a commit, but the client comes from PyPI and
the base images move under both.

## Status

A working prototype, developed against OpenPortal 0.91.0 and a Waldur portal.

**Not covered by tests:** the viewer's browser behaviour beyond parsing, and
anything needing portal software behind the portal agent — `stack.sh` has none,
so the incoming direction is exercised only as far as registering an offering.
Issues and patches welcome.

## Licence

MIT — see [LICENSE](LICENSE).
