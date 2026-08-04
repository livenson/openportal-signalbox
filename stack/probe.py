#!/usr/bin/env python3
"""Is the agent chain up, as seen through the bridge?

Runs inside the stack (``stack.sh up`` and ``stack.sh status``) and answers the
only question that matters before pointing a front end at it: does a signed
call to the bridge return every agent, connected. Container state does not
answer that — the bridge serves its HTTP API long before the chain below it has
finished connecting, and a leaf that died takes op-cluster's ability to run
jobs with it while leaving both containers up.

Deliberately uses the compiled ``openportal`` module rather than reading
container logs, because that is the path signalbox itself takes: if this
passes, the client and the agents agree on protocol and signature version. A
client one release ahead of the bridge fails here, at startup, rather than as
an unexplained authentication error in the front end later.

Kept independent of opdata.py — this ships inside the agent image, which has no
copy of the repo.
"""

import sys
import time

INVITE = "/openportal-invite/bridge-invite.toml"

EXPECTED = [
    "bridge",
    "waldur",
    "provider",
    "clusters",
    "cluster",
    "filesystem",
    "slurm",
    "localaccount",
]


def attr(obj, name, default=None):
    """Read a binding exposed either as a property or as a method.

    HealthInfo mixes the two — `peers` is a method while its neighbours are
    properties. opdata.attr exists for the same reason; see CLAUDE.md.
    """
    try:
        value = getattr(obj, name)
    except Exception:
        return default
    if callable(value):
        try:
            return value()
        except Exception:
            return default
    return value


def connected(root, found=None):
    """Walk the peer tree, collecting name -> connected?.

    Peer links are reciprocal, so this keeps a seen set; without one the walk
    never terminates.
    """
    found = {} if found is None else found
    name = attr(root, "name")
    if name is None or name in found:
        return found
    found[name] = bool(attr(root, "connected", False))
    for peer in (attr(root, "peers", {}) or {}).values():
        connected(peer, found)
    return found


def snapshot():
    import openportal

    if not openportal.is_config_loaded():
        openportal.load_config(INVITE)
    return connected(attr(openportal.health(), "detail"))


def main():
    deadline = time.monotonic() + (float(sys.argv[1]) if len(sys.argv) > 1 else 0)
    agents, problem = {}, "no health report yet"

    while True:
        try:
            agents = snapshot()
            problem = ", ".join(
                f"{n} ({'not connected' if n in agents else 'missing'})"
                for n in EXPECTED
                if not agents.get(n)
            )
            if not problem:
                break
        except Exception as exc:
            problem = str(exc)
        if time.monotonic() >= deadline:
            break
        time.sleep(3)

    for name in EXPECTED:
        state = "ok" if agents.get(name) else "DOWN"
        print(f"  {name:<14} {state}")

    if problem:
        print(f"\nagent chain incomplete: {problem}", file=sys.stderr)
        return 1
    print(f"\nall {len(EXPECTED)} agents connected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
