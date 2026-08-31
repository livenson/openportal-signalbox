"""signalbox — shared access to a running OpenPortal network.

Everything here goes through the bridge's signed API using the official
``openportal`` module: ``health()`` for the agent tree and
``diagnostics(destination)`` for one agent's jobs, warnings and recent log.

Nothing in here submits a job or changes state.
"""

import contextlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

INVITE = os.getenv("OPENPORTAL_BRIDGE_INVITE", "/inv/bridge-invite.toml")
CONFIG = os.getenv("SIGNALBOX_CONFIG", "")
INVITES = os.getenv("SIGNALBOX_INVITES", "")
HERE = Path(__file__).parent


@dataclass(frozen=True)
class Source:
    """One deployment: a bridge invite, and a name to hang its agents on.

    A deployment is one portal's estate. Two of them are two bridges and two
    invites, because a portal will not report another portal's health - see
    ``links()``.
    """

    name: str
    invite: str
    label: str = ""

    def display(self):
        return self.label or self.name


@dataclass(frozen=True)
class Link:
    """A peer connection between two deployments, taken from configuration.

    OpenPortal refuses portal-to-portal health checks on purpose (handler.rs:
    "portals do not share health with other portals"), so a link between two
    deployments is never reported by either end. An operator who holds both
    invites knows it exists; this is where they say so.
    """

    source: str
    target: str
    source_agent: str = ""
    target_agent: str = ""
    zone: str = ""


DEFAULT_SOURCE_NAME = "default"


def _load_toml(path):
    """TOML for the config file, without adding a runtime dependency.

    tomllib is stdlib from 3.11; the 3.10 floor (what the openportal wheels
    target) has to install tomli or use SIGNALBOX_INVITES instead.
    """
    try:
        import tomllib as toml_reader
    except ModuleNotFoundError:  # Python 3.10
        try:
            import tomli as toml_reader
        except ModuleNotFoundError:
            raise RuntimeError(
                f"Reading {path} needs a TOML parser: install tomli, run on "
                "Python 3.11+, or list the deployments in SIGNALBOX_INVITES "
                "instead (name=/path,name=/path)."
            ) from None
    with open(path, "rb") as handle:
        return toml_reader.load(handle)


def _config_path():
    if CONFIG:
        return Path(CONFIG)
    default = HERE / "signalbox.toml"
    return default if default.exists() else None


_resolved = None


def _resolve():
    """Deployments and their links, in precedence order.

    A single deployment - the only shape there was before - resolves to one
    unnamed source, so every existing caller and every existing payload keeps
    working without knowing that sources exist.
    """
    path = _config_path()
    if path is not None:
        if not Path(path).exists():
            raise RuntimeError(f"signalbox config not found at {path}.")
        document = _load_toml(path)
        sources, links = [], []
        for entry in document.get("deployment") or []:
            name = str(entry.get("name") or "").strip()
            invite = str(entry.get("invite") or "").strip()
            if not name or not invite:
                raise RuntimeError(f"{path}: every [[deployment]] needs a name and an invite.")
            sources.append(Source(name, invite, str(entry.get("label") or "")))
        for entry in document.get("link") or []:
            links.append(
                Link(
                    str(entry.get("from") or ""),
                    str(entry.get("to") or ""),
                    str(entry.get("from_agent") or ""),
                    str(entry.get("to_agent") or ""),
                    str(entry.get("zone") or ""),
                )
            )
        if not sources:
            raise RuntimeError(f"{path}: no [[deployment]] entries.")
        return sources, links

    if INVITES:
        sources = []
        for item in INVITES.split(","):
            item = item.strip()
            if not item:
                continue
            name, _, invite = item.partition("=")
            if not invite:
                raise RuntimeError(
                    "SIGNALBOX_INVITES is name=/path,name=/path; "
                    f"could not read a name and a path from {item!r}."
                )
            sources.append(Source(name.strip(), invite.strip()))
        if sources:
            return sources, []

    return [Source(DEFAULT_SOURCE_NAME, INVITE)], []


def sources():
    global _resolved
    if _resolved is None:
        _resolved = _resolve()
    return _resolved[0]


def links():
    if _resolved is None:
        sources()
    return _resolved[1]


def source_named(name):
    """The source a request is about; the first one when it does not say."""
    known = sources()
    if not name:
        return known[0]
    for source in known:
        if source.name == name:
            return source
    raise RuntimeError(f"Unknown deployment {name!r}.")


# The client keeps its bridge config in a process-wide singleton
# (python/src/lib.rs: SINGLETON_CONFIG), so this process can address one bridge
# at a time and load_config overwrites whatever the last caller chose. server.py
# is threaded, so every call is made under this lock, together with the switch
# that selects its deployment - otherwise one thread answers with another
# thread's bridge.
_BRIDGE_LOCK = threading.RLock()
_loaded = None


@contextlib.contextmanager
def use(source=None):
    """Hold the bridge for one deployment for the duration of a call."""
    source = source if isinstance(source, Source) else source_named(source)
    with _BRIDGE_LOCK:
        yield _select(source)


def _select(source):
    global _loaded
    import openportal

    if _loaded == source.invite and openportal.is_config_loaded():
        return openportal

    # A config someone else loaded is honoured while there is only one
    # deployment to speak to: the client is a singleton, and re-reading the
    # invite would only prove we can read the same file again. With several
    # deployments the invite always decides, because the singleton is exactly
    # what has to be switched.
    if _loaded is None and openportal.is_config_loaded() and len(sources()) == 1:
        _loaded = source.invite
        return openportal

    if not Path(source.invite).exists():
        raise RuntimeError(
            f"Bridge invite not found at {source.invite}. "
            "Set OPENPORTAL_BRIDGE_INVITE, SIGNALBOX_INVITES or SIGNALBOX_CONFIG."
        )
    openportal.load_config(source.invite)
    _loaded = source.invite
    return openportal


def bridge(source=None):
    """The client, pointed at one deployment.

    Callers that then make a call should use ``use()`` instead: holding the lock
    across the call is what stops a second thread switching the singleton
    underneath them.
    """
    with use(source) as op:
        return op


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


def key_of(source, node):
    """A handle that stays unique when two deployments are drawn together.

    ``id`` is the diagnostics path and is only unique inside one estate - two
    deployments routinely both have an agent called ``portal`` or ``bridge`` -
    so the front ends key on this instead, and pass ``source`` back when they
    ask about an agent.
    """
    return f"{source.name}:{node['id'] or node['name']}"


def version_mismatch(source=None):
    """Client release vs the release the bridge reports, when they differ.

    A native deployment may run a different release than the pin. That surfaces
    as an authentication failure rather than a version error, because from
    0.91.0 the client signs the V2 canonical string and an older bridge
    verifies the V1 one and rejects it. Saying so plainly costs one health call.
    """
    with use(source) as op:
        client = str(getattr(op, "__version__", "") or "")
        detail = attr(op.health(), "detail")
    theirs = str(attr(detail, "version", "") or "")
    if not client or not theirs or client == theirs:
        return None
    return f"client {client} against a bridge reporting {theirs}"


def source_topology(source):
    """One deployment's agents, tagged and keyed for a shared canvas."""
    with use(source) as op:
        health = op.health()
        root = attr(health, "detail")
        if root is None:
            raise RuntimeError("Bridge returned no health detail")
        nodes, edges = [], []
        walk(root, "", 0, nodes, edges, set())

    for node in nodes:
        node["source"] = source.name
        node["key"] = key_of(source, node)
    # walk() speaks agent names, which only the tree it came from can resolve;
    # the front ends see keys, so the mapping happens here rather than in each
    # of them.
    by_name = {node["name"]: node["key"] for node in nodes}
    keyed = []
    for edge in edges:
        if edge["source"] in by_name and edge["target"] in by_name:
            keyed.append(
                {
                    "source": by_name[edge["source"]],
                    "target": by_name[edge["target"]],
                }
            )
    return {
        "healthy": bool(attr(health, "is_healthy", False)),
        "nodes": nodes,
        "edges": keyed,
    }


def client_endpoint(source):
    """The bridge's other end: the HTTP door this tool came in through.

    A bridge does not join two sites - it joins the agent protocol to
    everything outside it. Its openportal end peers with its own portal and is
    reported by health(); its other end is a signed HTTP endpoint, and nothing
    on the wire mentions it. HealthInfo carries no addresses for any agent, so
    this is read from the invite we already hold rather than asked for. The
    Waldur side of that endpoint (the bridge's signal_url) stays invisible: it
    lives only in the bridge's own config.
    """
    try:
        return str(_load_toml(source.invite).get("url") or "") or None
    except Exception:
        return None


def _client_of(nodes, source):
    """A node for the endpoint behind this deployment's bridge, if it has one."""
    bridges = [n for n in nodes if n["source"] == source.name and n["type"] == "bridge"]
    url = client_endpoint(source)
    if not bridges or not url:
        return None
    return {
        "key": f"{source.name}:clients",
        "source": source.name,
        "url": url,
        "bridge": bridges[0]["key"],
    }


def _portal_key(nodes, source_name, agent_name=""):
    """The end of a configured link: a named agent, else the portal."""
    candidates = [n for n in nodes if n["source"] == source_name]
    if agent_name:
        named = [n for n in candidates if n["name"] == agent_name]
        return named[0]["key"] if named else None
    portals = [n for n in candidates if n["type"] == "portal"]
    return portals[0]["key"] if portals else None


def topology():
    """Every configured deployment in one graph.

    A deployment that cannot be reached is reported in ``sources`` rather than
    raising: watching two portals is most useful exactly when one of them is
    having a bad day.
    """
    nodes, edges, reported, clients = [], [], [], []
    healthy = True
    for source in sources():
        entry = {"name": source.name, "label": source.display(), "ok": True, "error": ""}
        try:
            part = source_topology(source)
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
            healthy = False
            reported.append(entry)
            continue
        nodes.extend(part["nodes"])
        edges.extend(part["edges"])
        healthy = healthy and part["healthy"]
        reported.append(entry)
        # Kept out of "nodes", which is what health() reported: everything that
        # walks the agents - diagnostics, the log timeline, the TUI table -
        # would otherwise have to special-case a node no agent knows about.
        client = _client_of(part["nodes"], source)
        if client:
            clients.append(client)

    for link in links():
        left = _portal_key(nodes, link.source, link.source_agent)
        right = _portal_key(nodes, link.target, link.target_agent)
        if left and right:
            edges.append({"source": left, "target": right, "inferred": True, "zone": link.zone})

    if not any(entry["ok"] for entry in reported):
        # Every bridge failed: say so the way a single-source failure used to,
        # so the front ends keep their one error path.
        raise RuntimeError("; ".join(e["error"] for e in reported if e["error"]))

    return {
        "ok": True,
        "healthy": healthy,
        "nodes": nodes,
        "edges": edges,
        "clients": clients,
        "sources": reported,
    }


def agent_detail(path, log_lines=200, include_self=False, level=None, search=None, source=None):
    with use(source) as op:
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
        "source": (source.name if isinstance(source, Source) else source) or "",
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
    rows = []
    # Grouped by deployment so the bridge is switched once per estate rather
    # than once per agent.
    by_source = {}
    for entry in paths:
        path, name, *rest = entry
        by_source.setdefault(rest[0] if rest else None, []).append((path, name))

    for source, agents in by_source.items():
        try:
            with use(source) as op:
                for path, name in agents:
                    try:
                        report = attr(op.diagnostics(path), "detail")
                    except Exception:
                        continue
                    if report is None:
                        continue
                    for row in log_entries(report, per_agent, level, search, include_self):
                        row["agent"] = name
                        row["source"] = source or ""
                        rows.append(row)
        except Exception:
            continue

    rows.sort(key=lambda r: r["sort"])
    return rows


def offerings(source=None):
    """Offerings this portal has registered, as agent destinations.

    A read: ``get_offerings`` is a plain GET through the bridge. Worth having
    beside a portal-to-portal link, because an offering is registered as a
    *virtual agent* in the pair's zone, and an award only arrives if it is
    there — so this is the payload the zone exists to carry.
    """
    try:
        with use(source) as op:
            return sorted(str(x) for x in op.get_offerings())
    except Exception:
        return []


# --- write path ---------------------------------------------------------
#
# Everything above only reads. Submitting an instruction is the one thing here
# that changes the system, so it is kept apart and is refused outright when
# SIGNALBOX_READONLY is set.

READONLY = os.getenv("SIGNALBOX_READONLY", "").lower() in ("1", "true", "yes")


def run_command(command, timeout_ms=30000, source=None):
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

    started = time.monotonic()
    try:
        with use(source) as op:
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


def sync_offering(name, source=None):
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

    try:
        with use(source) as op:
            portal = str(op.get_portal())
            destination = f"{name}.{portal}.provider"
            op.sync_offerings([op.Destination(destination)])
            offerings = [str(x) for x in op.get_offerings()]
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "offering": destination,
        "portal": portal,
        "address": f"{portal}.{name}",
        "offerings": offerings,
    }
