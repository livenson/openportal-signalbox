"""signalbox — shared access to a running OpenPortal network.

Everything here goes through the bridge's signed API using the official
``openportal`` module: ``health()`` for the agent tree and
``diagnostics(destination)`` for one agent's jobs, warnings and recent log.

Nothing in here submits a job or changes state.
"""

import os
import time
from pathlib import Path

INVITE = os.getenv("OPENPORTAL_BRIDGE_INVITE", "/inv/bridge-invite.toml")


def bridge():
    import openportal

    if not openportal.is_config_loaded():
        if not Path(INVITE).exists():
            raise RuntimeError(
                f"Bridge invite not found at {INVITE}. Set OPENPORTAL_BRIDGE_INVITE."
            )
        openportal.load_config(INVITE)
    return openportal


def attr(obj, name, default=None):
    """Read a binding exposed either as a property or as a method.

    HealthInfo mixes the two — most fields are getters but ``peers`` is a
    method — so resolve whichever this build provides rather than guessing.
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


def node_of(info, path, depth, foreign=False):
    def num(name, default=0):
        value = attr(info, name, default)
        return default if value is None else value

    return {
        # How to *ask about* this agent through the bridge we hold an invite
        # for. Diagnostics is routed hop by hop across the peer graph, so this
        # works for every agent the walk reached, including another allocator's.
        "id": path or info.name,
        # Where to *send an instruction*. Not the same question: an instruction
        # is addressed <portal>.<agent>..., rooted at the portal that owns the
        # agent, so there is no route from here to somebody else's estate — the
        # job would sit non-terminal until the wait expired rather than being
        # refused. None says so, instead of offering a destination that cannot
        # work.
        "route": None if foreign else (path or info.name),
        "allocator": "peer" if foreign else "own",
        "name": info.name,
        "type": num("agent_type", "unknown"),
        "depth": depth,
        "connected": bool(num("connected", False)),
        "uptime_seconds": num("uptime_seconds"),
        "workers": num("worker_count"),
        "memory_bytes": num("memory_bytes"),
        "cpu_percent": round(float(num("cpu_percent", 0.0)), 2),
        "jobs": {
            "active": num("active_jobs"),
            "pending": num("pending_jobs"),
            "running": num("running_jobs"),
            "completed": num("completed_jobs"),
            "errored": num("errored_jobs"),
            "expired": num("expired_jobs"),
        },
        "totals": {
            "completed": num("total_completed"),
            "failed": num("total_failed"),
            "expired": num("total_expired"),
            "slow": num("total_slow"),
        },
        "job_time_mean_ms": round(float(num("job_time_mean_ms", 0.0)), 2),
        "engine": num("engine", ""),
        "version": num("version", ""),
    }


def walk(info, path, depth, nodes, edges, seen, foreign=False):
    """Depth-first walk of the peer tree.

    Peer links are reciprocal, so a naive walk would recurse forever; `seen`
    keeps each agent at its first (shortest) path, which is also the routable
    destination for diagnostics.

    ``foreign`` marks the part of the graph that belongs to another allocator —
    reachable to ask about, not addressable to instruct. See ``node_of``.
    """
    if info.name in seen:
        return
    seen.add(info.name)
    nodes.append(node_of(info, path, depth, foreign))

    for name, peer in sorted((attr(info, "peers", {}) or {}).items()):
        if name in seen:
            continue
        child_path = name if depth == 0 else f"{path}.{name}"
        edges.append({"source": info.name, "target": name})
        # A portal below the first hop belongs to another allocator: OpenPortal
        # roots every route at a portal and forbids a portal from querying
        # another, so one reached *through* a shared agent is somebody else's.
        # Everything under it is theirs too.
        walk(
            peer,
            child_path,
            depth + 1,
            nodes,
            edges,
            seen,
            foreign or (depth >= 1 and attr(peer, "agent_type") == "portal"),
        )


def topology():
    op = bridge()
    health = op.health()
    root = attr(health, "detail")
    if root is None:
        raise RuntimeError("Bridge returned no health detail")

    nodes, edges = [], []
    walk(root, "", 0, nodes, edges, set())
    return {
        "ok": True,
        "healthy": bool(attr(health, "is_healthy", False)),
        "nodes": nodes,
        "edges": edges,
    }


def agent_detail(path, log_lines=200, include_self=False, level=None, search=None):
    op = bridge()
    report = attr(op.diagnostics(path), "detail")
    if report is None:
        return {"ok": False, "error": "no diagnostics returned"}

    def entries(name, limit=10):
        """Job/warning lines, narrowed by the same substring as the log."""
        try:
            rows = [str(x) for x in getattr(report, name)]
        except Exception:
            return []
        if search:
            needle = search.lower()
            rows = [r for r in rows if needle in r.lower()]
        return rows[:limit]

    return {
        "ok": True,
        "path": path,
        "agent": report.agent_name,
        "running_jobs": entries("running_jobs"),
        "failed_jobs": entries("failed_jobs"),
        "slowest_jobs": entries("slowest_jobs"),
        "expired_jobs": entries("expired_jobs"),
        "warnings": entries("warnings", 20),
        "logs": log_entries(report, log_lines, level, search, include_self),
    }


# Asking an agent for diagnostics is itself logged, so a tool that polls every
# few seconds drowns its own log view in "Diagnostics request" lines. Dropped
# by default; the caller can ask for them back.
SELF_CHATTER = "Diagnostics request"


def log_entries(report, limit=200, level=None, search=None, include_self=False):
    """Structured log lines, newest last."""
    try:
        # Over-fetch when filtering, so dropped chatter does not eat the limit.
        raw = report.logs(0 if not include_self else limit, level, search)
    except Exception:
        return []

    out = []
    for entry in raw:
        if not include_self and SELF_CHATTER in str(attr(entry, "message", "")):
            continue
        stamp = attr(entry, "timestamp")
        out.append(
            {
                "time": stamp.strftime("%H:%M:%S") if hasattr(stamp, "strftime") else "",
                "sort": stamp.timestamp() if hasattr(stamp, "timestamp") else 0.0,
                "level": str(attr(entry, "level", "") or ""),
                "target": str(attr(entry, "target", "") or ""),
                "message": str(attr(entry, "message", "") or str(entry)),
            }
        )
    return out[-limit:] if limit else out


def merged_logs(paths, level=None, search=None, per_agent=120, include_self=False):
    """One timeline across every agent.

    This is the view that is genuinely awkward otherwise: each agent keeps its
    own buffer, so following a single job today means reading several
    containers side by side.
    """
    op = bridge()
    rows = []
    for path, name in paths:
        try:
            report = attr(op.diagnostics(path), "detail")
        except Exception:
            continue
        if report is None:
            continue
        for row in log_entries(report, per_agent, level, search, include_self):
            row["agent"] = name
            rows.append(row)

    rows.sort(key=lambda r: r["sort"])
    return rows


# --- write path ---------------------------------------------------------
#
# Everything above only reads. Submitting an instruction is the one thing here
# that changes the system, so it is kept apart and is refused outright when
# SIGNALBOX_READONLY is set.

READONLY = os.getenv("SIGNALBOX_READONLY", "").lower() in ("1", "true", "yes")


def run_command(command, timeout_ms=30000):
    """Submit an OpenPortal instruction and wait for it to finish.

    Returns the job's state and result rather than raising, so the caller can
    show a failure as an outcome — a rejected instruction is often exactly what
    you were trying to learn.
    """
    if READONLY:
        return {"ok": False, "error": "signalbox is running read-only"}

    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "empty command"}

    op = bridge()
    started = time.monotonic()
    try:
        job = op.run(command, int(timeout_ms))
    except Exception as exc:
        # The module surfaces a refused or failed job as an exception carrying
        # the agent's own message, which is the interesting part.
        return {
            "ok": False,
            "command": command,
            "state": "error",
            "error": str(exc),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }

    state = str(attr(job, "state", ""))
    out = {
        "ok": state == "complete",
        "command": command,
        "state": state,
        "result": str(attr(job, "result", "") or ""),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
    if not out["ok"]:
        # A job still pending or running when the wait expires has not
        # succeeded — reporting it as ok would read as a pass. This is the
        # usual shape of an instruction aimed at an agent that cannot be
        # routed to: nothing rejects it, it simply never lands.
        out["error"] = f"job did not complete (state: {state or 'unknown'})"
    return out


def sync_offering(name):
    """Register an offering with the portal, creating a virtual agent for it.

    This is the only way to exercise the incoming direction. The bridge accepts
    job submissions from *virtual* agents only, and a virtual agent is
    same-process-only — it re-injects the message into the portal's own
    handler. Waldur does this from its ProjectTemplate rows in
    waldur_openportal.sync_offering_agents; here it is done by hand so the
    portal-answered instructions have something to address.
    """
    if READONLY:
        return {"ok": False, "error": "signalbox is running read-only"}

    op = bridge()
    portal = str(op.get_portal())
    destination = f"{name}.{portal}.provider"
    try:
        op.sync_offerings([op.Destination(destination)])
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "offering": destination,
        "portal": portal,
        "address": f"{portal}.{name}",
        "offerings": [str(x) for x in op.get_offerings()],
    }
