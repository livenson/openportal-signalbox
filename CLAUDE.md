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
python -m pytest                                   # 56 tests, ~5s
python -m pytest tests/test_opdata.py::test_attr_calls_a_method
python -m pytest -k read_only                      # note the underscores
ruff check . && ruff format --check .

./stack.sh up                                      # a real agent network to use
./live.sh                                          # 22 tests against it
./stack.sh down

./tui.sh                                           # terminal UI
./run.sh                                           # graph view -> http://localhost:8900
SIGNALBOX_READONLY=1 ./run.sh                      # console disabled
NETWORK=... INVITE_VOLUME=... ./run.sh             # override auto-detection
```

The default suite needs no OpenPortal network — it fakes the module (see
Testing). `live.sh`, `tui.sh` and `run.sh` need a real one, which `stack.sh`
will build.

Environment read by the code: `OPENPORTAL_BRIDGE_INVITE`, `SIGNALBOX_READONLY`,
`PORT`, `CACHE_TTL`, `SIGNALBOX_LIVE`.

## The version pin

`openportal.env` holds the release everything is built and installed from, and
is the only place it appears: `detect.sh` sources it for `run.sh`/`tui.sh`/
`live.sh`, `stack.sh` exports it into compose, and `tests/test_stack.py` fails
if anything grows its own default.

That is not tidiness. **Client and agents must be the same release.** From
0.91.0 the Python client signs the V2 canonical string and declares it with
`X-OpenPortal-Signature-Version`; a 0.90.0 bridge has no such header, verifies
the V1 form, and rejects the call. Bumping one side alone turns every read into
a 401, with nothing anywhere naming a version.

One value drives all of it: the PyPI version of the client, the GitHub release
the agent binaries are pulled from, and the version those binaries must then
report. Because that is a tag rather than a commit, the image build asserts
`op-bridge --version` matches it — a retagged release fails there instead of
becoming agents that cannot authenticate.

Bumping means: `OPENPORTAL_VERSION` in `openportal.env`, the fake's `version`
in `tests/conftest.py`, then `./stack.sh down && ./stack.sh up && ./live.sh` —
`test_agents_report_the_version_the_launchers_installed` is what notices a
stale image.

## Why the launchers use Docker

`op-bridge` listens only inside the agent network, and every call to it is
signed with the HMAC key from the invite file. So the tools cannot run on the
host: `run.sh`, `tui.sh` and `live.sh` start a container *on that network* with
the invite volume mounted. `detect.sh` finds both by locating a running
container labelled `com.docker.compose.service=op-bridge` and taking the invite
volume *from that same container's mounts* — resolving the two independently
pairs one stack's network with another's key when more than one is up, which
`stack.sh` makes a normal situation, and that fails as a signature error rather
than as anything naming the wrong stack. `stack/docker-compose.yml` is
therefore held to those two names by `tests/test_stack.py`.

Consequence when editing: the Python files are bind-mounted read-only into the
container, so an edit needs `docker restart signalbox` (graph view) or a
relaunch (TUI) — not a rebuild.

## The test stack

`stack/` is a complete agent network with no portal software behind it. The
agents are upstream's **release binaries** — statically linked, ~8 MB each,
published per release for x86_64 and aarch64 — dropped into one image. Do not
"upgrade" this to the published per-agent OCI images: there is none for
`op-localaccount`, and the leaves shell out to `useradd` and `sacctmgr`, which
a distroless image cannot run. Do not go back to compiling from source either;
that is where this started and it cost ten minutes a run for nothing.

Read `stack/bootstrap.sh` before changing the chain: it wires the peers, and
`tests/test_stack.py` re-derives the tree from its `wire` calls and pushes it
through `opdata.walk()`, so the destinations the front ends address are pinned
to what the stack actually builds.

Three things it has to get right, each of which cost a debugging session in
`waldur-integration-testing!117`, where this stack comes from:

- **The three leaves share one container.** op-localaccount creates the Unix
  group and op-filesystem chowns to it; split apart, every `add_project` fails
  with `Could not find a group called <project>`. Network aliases keep them
  individually dialable.
- **op-slurm talks to slurmrestd, not `sacctmgr`.** The emulator's `sacctmgr`
  accepts `--json` and answers in table format, which the agent cannot parse.
- **Invite files are named after the issuing agent**, not the client they
  admit — the upstream worked example had this backwards until 0.91.0.

Two things 0.91.0 changed that the bootstrap depends on: configs and the invite
are written **owner-only**, so it must not `chmod` them back (everything here
runs as root, including the signalbox container); and `client --add --type`
would switch on portal route discovery, which is exactly wrong in a tool for
looking at broken routing, so peers are left untyped.

## Domain constraints that are not visible in the code

These cost real debugging time and are the usual cause of "the tool is broken"
reports. Most are properties of OpenPortal, not of signalbox.

**The bindings are inconsistent.** `HealthInfo` exposes `peers` as a *method*
while every neighbouring field is a property. `opdata.attr()` exists solely to
resolve either form; do not "simplify" it away.

**Peer links are reciprocal in config, but not in the health report.** Each
agent peers with the one above it and the one below, so `bridge -> portal` is
also `portal -> bridge`. What arrives over the API is not that graph: the
health cascade drops the requester and everything already visited, so a
single-zone stack answers with a tree — verified against 0.91.0 by
`test_the_health_tree_arrives_already_deduplicated`. `opdata.walk()` keeps its
`seen` set regardless, because it is also what pins each agent to its shortest
path, and that dotted path *is* the routable destination used for
`diagnostics()`. Do not remove it on the strength of one topology.

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

**Polling is itself logged, on the bridge.** Asking for diagnostics writes a
`Diagnostics request` line into the *bridge's* log, not into the log of the
agent named in the request — the bridge is the process that receives the API
call. So it is the bridge's log view and the merged timeline that fill up, and
both are views a tool refreshing every few seconds tends to be sitting on.
`opdata.SELF_CHATTER` filters those by default (`n` in the TUI re-enables). The
filter over-fetches then trims, so dropped chatter does not eat the caller's
limit.

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

`tests/test_live.py` runs the same claims against real agents, which is the
only thing that can check them — a fake is only ever as correct as our reading
of the bindings, and reading them wrong is how three of the constraints above
got written down inaccurately in the first place. It is marked `live` and
deselected by `addopts`, so `python -m pytest` stays the fast offline suite;
`./live.sh` selects it and puts pytest inside the agent network.

When adding one, prefer a claim the fake *cannot* settle — `peers` really being
a method, a router really refusing to execute, an unroutable job really coming
back unfinished rather than rejected. Anything the fake can settle belongs in
the offline suite, which everyone runs.

Not covered: browser behaviour beyond parsing, and the incoming direction past
registering an offering — `stack.sh` has no portal software to answer it.

## Conventions

Comments explain *why* — usually a protocol constraint or a bug that has
already happened once. Do not add comments restating what the next line does.

`opdata.py` is shared by both front ends; keep protocol knowledge there rather
than duplicating it into `server.py` or `tui.py`.
