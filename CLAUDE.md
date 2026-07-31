# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

signalbox is a pair of debugging front ends for a running [OpenPortal](https://github.com/isambard-sc/openportal)
agent network: a Textual terminal UI (`tui.py`) and a browser topology graph
(`index.html` served by `server.py`). Both sit on one data layer, `opdata.py`.

Nothing here reimplements the protocol. Every read goes through the compiled
`openportal` Python module against a live `op-bridge`: `health()` for the agent
tree, `diagnostics(destination)` for one agent's jobs, warnings and log.

## Commands

```bash
python -m pytest                                   # 36 tests, ~5s
python -m pytest tests/test_opdata.py::test_attr_calls_a_method
python -m pytest -k read_only                      # note the underscores
ruff check . && ruff format --check .

./tui.sh                                           # terminal UI
./run.sh                                           # graph view -> http://localhost:8900
SIGNALBOX_READONLY=1 ./run.sh                      # console disabled
NETWORK=... INVITE_VOLUME=... ./run.sh             # override auto-detection
```

Tests need no OpenPortal network — they fake the module (see Testing). The two
front ends need a real one.

Environment read by the code: `OPENPORTAL_BRIDGE_INVITE`, `SIGNALBOX_READONLY`,
`PORT`, `CACHE_TTL`.

## Why the launchers use Docker

`op-bridge` listens only inside the agent network, and every call to it is
signed with the HMAC key from the invite file. So the tools cannot run on the
host: `run.sh` and `tui.sh` start a container *on that network* with the invite
volume mounted. `detect.sh` finds both by locating a running container labelled
`com.docker.compose.service=op-bridge` and matching a volume ending
`openportal-invite`.

Consequence when editing: the Python files are bind-mounted read-only into the
container, so an edit needs `docker restart signalbox` (graph view) or a
relaunch (TUI) — not a rebuild.

## Domain constraints that are not visible in the code

These cost real debugging time and are the usual cause of "the tool is broken"
reports. Most are properties of OpenPortal, not of signalbox.

**The bindings are inconsistent.** `HealthInfo` exposes `peers` as a *method*
while every neighbouring field is a property. `opdata.attr()` exists solely to
resolve either form; do not "simplify" it away.

**Peer links are reciprocal.** `bridge -> portal` also appears as
`portal -> bridge`, so a naive tree walk loops forever. `opdata.walk()` keeps a
`seen` set, which also pins each agent to its shortest path — and that dotted
path *is* the routable destination used for `diagnostics()`.

**Routers do not execute.** Provider, platform and bridge agents forward
instructions; only the instance agent (and its leaves) run project and user
work. An instruction aimed at a router fails. The graph's presets encode this
by declaring which agent *role* can answer each one.

**Portal-answered instructions need a registered offering.** `get_projects` is
answered by the portal software (e.g. Waldur), and the bridge accepts job
submissions only from *virtual* agents — same-process stand-ins created by
`sync_offerings`, addressable as `<portal>.<offering>`. `<portal>.<agent-name>`
is not a route and simply errors. `opdata.sync_offering()` registers one.

**A non-terminal job is not a success.** An instruction aimed at an unroutable
agent is never rejected — it just never lands. `run_command()` maps any state
other than `complete` to `ok: False`; reporting otherwise reads as a pass.

**Polling is itself logged.** Asking an agent for diagnostics writes a
`Diagnostics request` line, so a tool refreshing every few seconds floods its
own log views. `opdata.SELF_CHATTER` filters those by default (`n` in the TUI
re-enables). The filter over-fetches then trims, so dropped chatter does not
eat the caller's limit.

**Routers never increment completed counters**, so the graph's edge animation
derives throughput from each agent's completed delta and pushes it *up its own
route* — work executed on a leaf must have crossed every hop above it.
Attributing traffic only to the executing agent leaves the middle of the chain
looking idle.

## Read/write posture

Everything is read-only except the graph view's console, which submits jobs and
creates real projects and users. `SIGNALBOX_READONLY=1` disables it
(`opdata.READONLY`, surfaced to the browser via `/api/config`).

The bridge invite is a **full-control credential**, not a read-only one —
whatever the tool chooses to call. Keep the whole thing to development stacks.

## The viewer has no build step

`index.html` is a single file: `htm` tagged templates instead of JSX, React /
React Flow / dagre from esm.sh via an import map. There is no npm, no bundler,
and CI only parses the inline module with `node --check`.

The whole app is one component, so **declaration order matters** — a
`useCallback` whose dependency array names a `const` declared later throws at
render. This has bitten three times (`registerOffering`, `pickPreset`,
`agents`). Put lookups and helpers above their users.

Layout is dagre `rankdir: LR`; the graph refits when the agent set changes or
the inspector docks, never on a plain refresh, so a refresh does not yank the
camera away from wherever it was dragged.

## Testing

`tests/conftest.py` fakes the `openportal` module and injects it into
`sys.modules`. The fake **deliberately reproduces the bindings' quirks** —
`peers` as a method, `logs(limit, level, search)` with `limit=0` meaning
everything — because a tidy fake would test the wrong thing.

Tests are weighted towards what has actually broken, not towards coverage:
property-vs-method resolution, walk termination, limit-after-filter, unfinished
jobs, the read-only guard, and cache keys that include their filters.

Not covered: anything needing a live network, and browser behaviour beyond
parsing. Both are verified by hand against a real stack.

## Conventions

Comments explain *why* — usually a protocol constraint or a bug that has
already happened once. Do not add comments restating what the next line does.

`opdata.py` is shared by both front ends; keep protocol knowledge there rather
than duplicating it into `server.py` or `tui.py`.
