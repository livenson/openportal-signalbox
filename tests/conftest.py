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
        self.version = "0.90.0"
        self._peers = peers or {}

    # A method, not a property — exactly like the real bindings.
    def peers(self):
        return self._peers


class FakeJob:
    def __init__(self, state, result=""):
        self.state = state
        self.result = result


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

    # config
    def is_config_loaded(self):
        return self.loaded

    def load_config(self, path):
        self.loaded = True

    # reads
    def health(self):
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
    return fake


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
