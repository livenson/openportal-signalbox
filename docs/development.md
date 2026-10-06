# Development

```bash
pip install pytest && python -m pytest      # ~110 tests, ~8s, no network
ruff check . && ruff format --check .

./stack.sh up && ./live.sh                  # ~26 more, against real agents
```

## The offline suite

It fakes the compiled `openportal` module rather than installing it — the real
one is useless without a live agent network. The fake deliberately keeps the
bindings' quirks: `HealthInfo` exposes `peers` as a method while its neighbours
are properties, and a failed job's `result` raises rather than returning the
error. Smoothing either over would test the wrong thing.

Those tests are weighted towards what actually broke while building this: the
property-vs-method inconsistency, peer links that turn a tree walk into a loop,
filtering that ate its own limit, a job reported as successful when it had not
finished, an agent's refusal lost behind "did not complete", and a cache keyed
without its filters. `tests/test_stack.py` adds the contract between the stack
and the launchers — the compose service and volume names discovery matches on,
and the version pin, which has to be one release across client and agents
because a mismatch fails as an authentication error rather than a version
warning.

## The live suite

`tests/test_live.py` closes the gap the fake cannot: it asserts the same
documented constraints against agents that actually exist — that `peers` really
is a method, that a router really does refuse to execute, that polling really
does log itself, that an unroutable destination really does come back
unfinished rather than rejected. A fake is only ever as correct as our reading
of the bindings, and this is what checks the reading.

Run it with `./live.sh`, which puts pytest inside the agent network the same
way `run.sh` puts the server there; on its own, `python -m pytest` deselects
it. The live tests **submit jobs**, so they aim at whatever the launchers find —
a `signalbox.toml` beside the scripts included. `SIGNALBOX_CONFIG=none ./live.sh`
sets that file aside and reaches the stack.

CI runs the offline suite on every push, and the live one against all three
[topologies](test-stack.md#topologies) nightly and on demand — the agents are
pinned to a release, but the client comes from PyPI and the base images move
under both.

## Moving to a new OpenPortal release

The pin lives in one place, `openportal.env`; the test fake's `version` in
`tests/conftest.py` has to match it. Then:

```bash
./stack.sh down && ./stack.sh up && ./live.sh
```

## Regenerating the screenshots

The images in `docs/` show agent versions, so a release bump makes them stale.
`docs/capture.py` regenerates all of them from a live stack — every image is a
real capture, nothing edited:

```bash
./stack.sh down && ./stack.sh up \
  && uv run --no-project --with playwright --with textual python docs/capture.py chain
./stack.sh down && ./stack.sh up multi-allocator \
  && uv run --no-project --with playwright python docs/capture.py multi-allocator
```

Start each topology from a fresh stack: failure counts are cumulative, and the
console shot fails an instruction on purpose. The script's docstring records
the rest — why the load is one sequential loop, and why each shot waits past
the second poll and half a poll clear of the next.
