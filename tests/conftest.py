"""A stand-in for the compiled ``openportal`` module.

signalbox talks to a live agent network, which CI has no way to provide, so the
module is faked here. The fake deliberately copies the quirks of the real
bindings rather than presenting a tidy interface — most notably that
``HealthInfo`` exposes ``peers`` as a *method* while every other field is a
property. That inconsistency is the reason ``opdata.attr`` exists, so a fake
that smoothed it over would test the wrong thing.
"""

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

BASE = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


class FakeLogEntry:
    def __init__(self, offset, level, target, message):
        self.timestamp = BASE + timedelta(seconds=offset)
        self.level = level
        self.target = target
        self.message = message

    def __str__(self):
        return f"[{self.timestamp}] {self.level} {self.target} - {self.message}"


class FakeReport:
    def __init__(self, agent_name, logs=(), failed=(), slowest=(), warnings=()):
        self.agent_name = agent_name
        self._logs = list(logs)
        self.failed_jobs = list(failed)
        self.slowest_jobs = list(slowest)
        self.expired_jobs = []
        self.running_jobs = []
        self.warnings = list(warnings)

    def logs(self, limit=0, level=None, search=None):
        """Mirrors the real signature: limit 0 means everything."""
        rows = self._logs
        if level == "WARN+":
            rows = [r for r in rows if r.level in ("WARN", "ERROR")]
        elif level:
            rows = [r for r in rows if r.level == level]
        if search:
            rows = [r for r in rows if search.lower() in r.message.lower()]
        return rows[-limit:] if limit else rows


class FakeHealthInfo:
    def __init__(self, name, agent_type, peers=None, **fields):
        self.name = name
        self.agent_type = agent_type
        self.connected = fields.get("connected", True)
        self.uptime_seconds = fields.get("uptime_seconds", 60)
        self.worker_count = fields.get("worker_count", 2)
        self.memory_bytes = fields.get("memory_bytes", 1048576)
        self.cpu_percent = fields.get("cpu_percent", 0.5)
        self.total_completed = fields.get("total_completed", 0)
        self.total_failed = fields.get("total_failed", 0)
        self.engine = "templemeads"
        self.version = "0.93.0"
        self._peers = peers or {}

    # A method, not a property — exactly like the real bindings.
    def peers(self):
        return self._peers


class OpenPortalError(OSError):
    pass


class OpenPortalOtherError(OpenPortalError):
    pass


class ManagedProjectPermissionError(OpenPortalError):
    pass


class ManagedProjectPendingError(ManagedProjectPermissionError):
    pass


class FakeJob:
    """A job as ``run()`` hands it back.

    An agent's refusal does not raise from ``run()``: it comes back as a job in
    state ``error`` whose ``result`` *raises* the typed exception and whose
    ``error`` returns it. Reading ``result`` unguarded is how the agent's own
    message used to get lost, so the fake keeps that trap.
    """

    def __init__(self, state, result="", error=None, error_kind="", result_type=""):
        self.state = state
        self._result = result
        self.error = error
        self.error_kind = error_kind
        self.result_type = result_type

    @property
    def result(self):
        if self.error is not None:
            raise self.error
        return self._result


class FakeOpenPortal(types.ModuleType):
    """Module object injected into sys.modules as ``openportal``."""

    def __init__(self):
        super().__init__("openportal")
        self.loaded = True
        self.reports = {}
        self.health_root = None
        self.offerings = []
        self.next_job = FakeJob("complete", "ok")
        self.runs = []
        self.synced = []
        self.invite = None
        self.calls = []
        # invite path -> health root, for tests that watch two deployments
        self.trees = {}
        self.fails = ""

    # config
    def is_config_loaded(self):
        return self.loaded

    def load_config(self, path):
        self.loaded = True
        # Which invite was loaded last, and the order of every load and read.
        # Switching deployments means overwriting a process-wide singleton in
        # the real client, so the order is the thing worth asserting.
        self.invite = str(path)
        self.calls.append(("load", str(path)))
        if self.trees:
            self.health_root = self.trees.get(str(path), self.health_root)

    # reads
    def health(self):
        self.calls.append(("health", self.invite))
        if self.fails:
            raise RuntimeError(self.fails)
        root = self.health_root
        return types.SimpleNamespace(detail=root, is_healthy=lambda: True)

    def diagnostics(self, destination):
        return types.SimpleNamespace(detail=self.reports.get(destination))

    def get_portal(self):
        return "waldur"

    def get_offerings(self):
        return list(self.offerings)

    # writes
    def run(self, command, timeout_ms=0):
        self.runs.append((command, timeout_ms))
        if isinstance(self.next_job, Exception):
            raise self.next_job
        return self.next_job

    def sync_offerings(self, destinations):
        self.synced.append([str(d) for d in destinations])
        self.offerings = [str(d) for d in destinations]

    def Destination(self, value):  # noqa: N802 - mirrors the real class name
        return value


@pytest.fixture
def fake_openportal(monkeypatch):
    fake = FakeOpenPortal()
    monkeypatch.setitem(sys.modules, "openportal", fake)

    import opdata

    monkeypatch.setattr(opdata, "READONLY", False, raising=False)
    # Both caches are process-wide: the resolved deployment list, and which
    # invite the client last loaded. A test that does not reset them inherits
    # the previous test's estate.
    monkeypatch.setattr(opdata, "_resolved", None, raising=False)
    monkeypatch.setattr(opdata, "_loaded", None, raising=False)
    # An operator's own signalbox.toml sits beside the scripts - the README
    # tells them to put it there - and would otherwise resolve as the estate
    # under test. Tests declare their deployments or get the single default.
    monkeypatch.setattr(opdata, "CONFIG", "", raising=False)
    monkeypatch.setattr(opdata, "INVITES", "", raising=False)
    monkeypatch.setattr(opdata, "_config_path", lambda: None)
    return fake


@pytest.fixture
def two_deployments(fake_openportal, tmp_path, monkeypatch):
    """Two estates whose agents share names, each behind its own invite.

    Both have a ``bridge`` and a ``portal``: that collision is the reason nodes
    carry a source-qualified key.
    """
    import opdata

    invites = {}
    for name in ("rp", "efp"):
        portal = FakeHealthInfo(name, "portal")
        bridge = FakeHealthInfo("bridge", "bridge")
        bridge._peers = {name: portal}
        portal._peers = {"bridge": bridge}
        invite = tmp_path / f"{name}-invite.toml"
        invite.write_text("url = 'http://127.0.0.1/'\n")
        invites[name] = str(invite)
        fake_openportal.trees[str(invite)] = bridge

    monkeypatch.setattr(
        opdata,
        "_resolved",
        (
            [opdata.Source("rp", invites["rp"]), opdata.Source("efp", invites["efp"])],
            [opdata.Link("rp", "efp", zone="rp>efp")],
        ),
        raising=False,
    )
    fake_openportal.health_root = fake_openportal.trees[invites["rp"]]
    return fake_openportal, invites


@pytest.fixture
def chain(fake_openportal):
    """A small agent tree shaped like a real deployment.

    Peer links are reciprocal, which is what makes a naive walk loop forever.
    """
    leaf = FakeHealthInfo("slurm", "scheduler")
    cluster = FakeHealthInfo("cluster", "instance", total_failed=2)
    provider = FakeHealthInfo("provider", "provider")
    portal = FakeHealthInfo("waldur", "portal")
    bridge = FakeHealthInfo("bridge", "bridge")

    bridge._peers = {"waldur": portal}
    portal._peers = {"bridge": bridge, "provider": provider}  # back-link
    provider._peers = {"waldur": portal, "cluster": cluster}  # back-link
    cluster._peers = {"provider": provider, "slurm": leaf}  # back-link
    leaf._peers = {"cluster": cluster}  # back-link

    fake_openportal.health_root = bridge
    return fake_openportal
