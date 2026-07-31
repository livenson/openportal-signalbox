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

## Two things that look broken but are not

**`Dropping notification … after 3 failed signal attempts`** in agent logs. If
the bridge was initialised without `--notification-url` it keeps its default of
`http://localhost/notification`, which nothing serves, so every notification is
retried three times and dropped. Portal software that implements no
notification receiver (Waldur, today) loses nothing by this.

**`<portal>.<agent> get_projects <portal>` errors.** See "register offering"
above — that address has to be a registered offering, not an agent name.

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

## Status

A working prototype, developed against OpenPortal 0.90.0 and a Waldur portal.
It has no tests and is packaged as `docker run` wrappers rather than a proper
distribution. Issues and patches welcome.

## Licence

MIT — see [LICENSE](LICENSE).
