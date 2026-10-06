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

Both find the running bridge by themselves. With a Docker Compose
deployment you need nothing but Docker; agents running natively on the
host are watched too — see [Watching real deployments](docs/deployments.md).

```bash
git clone https://github.com/livenson/openportal-signalbox
cd openportal-signalbox
./tui.sh          # or ./run.sh, then open http://localhost:8900
```

No agent network to hand? `./stack.sh up` builds a throwaway eight-agent one —
see [the test stack](docs/test-stack.md).

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

## The console

The graph view's console submits instructions and shows what came back: the
result and its type, or the agent's own refusal with its error kind. Its preset
chips walk the instruction grammar against one demo project, including the
`is_user_added` / `is_user_removed` checks that ask every leaf agent whether
the work really happened. It **writes** — real projects, real users — so
`SIGNALBOX_READONLY=1 ./run.sh` disables it. More in
[docs/console.md](docs/console.md).

## A network to point it at

```bash
./stack.sh up                    # a throwaway eight-agent network, under a minute
./stack.sh up multi-allocator    # two allocators sharing a provider
./stack.sh down
```

Three topologies — `chain`, `zoned`, `multi-allocator` — built from upstream's
release binaries. See [docs/test-stack.md](docs/test-stack.md).

## Documentation

| | |
|---|---|
| [The console](docs/console.md) | Presets, reading answers and error kinds, offerings, the write path |
| [The test stack](docs/test-stack.md) | Topologies, how the stack is built, two allocators on one estate |
| [Watching real deployments](docs/deployments.md) | Native agents, several deployments in one graph, zones and links |
| [Troubleshooting](docs/troubleshooting.md) | Log noise, 401s, instructions that never land |
| [Development](docs/development.md) | Tests, release bumps, regenerating these screenshots |

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
- **One bridge at a time, in sequence.** The client keeps its configuration in
  a process-global, so several deployments are polled one after another behind
  a lock rather than in parallel. Fine for a handful; not a fleet view.
- Traffic counts are per refresh interval, not a per-job trace. A job that
  starts and finishes between two polls is counted, but never seen moving.
- The graph view loads React Flow and dagre from a CDN, so it needs network
  access on first load. The TUI has no such dependency.

## Status

A working prototype, developed against OpenPortal 0.91.0–0.93.0 and a Waldur portal.

**Not covered by tests:** the viewer's browser behaviour beyond parsing, and
anything needing portal software behind the portal agent — `stack.sh` has none,
so the incoming direction is exercised only as far as registering an offering.
Issues and patches welcome.

## Licence

MIT — see [LICENSE](LICENSE).
