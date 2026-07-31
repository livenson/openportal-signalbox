"""Tests for the data layer.

Weighted towards the things that actually went wrong while building this: the
property-vs-method inconsistency in the bindings, the reciprocal peer links
that turn a tree walk into a loop, filtering that ate its own limit, and a
job reported as successful when it had not finished.
"""

import pytest
from conftest import FakeHealthInfo, FakeJob, FakeLogEntry, FakeReport

import opdata

# --- attr: the bindings are not consistent -------------------------------


def test_attr_reads_a_property():
    obj = FakeHealthInfo("a", "bridge")
    assert opdata.attr(obj, "name") == "a"


def test_attr_calls_a_method():
    """`peers` is a method while its neighbours are properties."""
    peer = FakeHealthInfo("b", "portal")
    obj = FakeHealthInfo("a", "bridge", peers={"b": peer})
    assert opdata.attr(obj, "peers") == {"b": peer}


def test_attr_falls_back_when_missing():
    assert opdata.attr(object(), "nope", "fallback") == "fallback"


def test_attr_falls_back_when_the_call_raises():
    class Boom:
        def explode(self):
            raise RuntimeError("no")

    assert opdata.attr(Boom(), "explode", "fallback") == "fallback"


# --- topology ------------------------------------------------------------


def test_topology_visits_every_agent_once(chain):
    """Reciprocal peer links would otherwise walk in circles."""
    names = [n["name"] for n in opdata.topology()["nodes"]]
    assert len(names) == len(set(names)), f"an agent was visited twice: {names}"
    assert set(names) == {"bridge", "waldur", "provider", "cluster", "slurm"}
    # Tree order, not alphabetical — the views rely on it for layout.
    assert names[0] == "bridge"


def test_topology_builds_routable_paths(chain):
    """The path doubles as the destination used for diagnostics."""
    paths = {n["name"]: n["id"] for n in opdata.topology()["nodes"]}
    assert paths["bridge"] == "bridge"
    assert paths["waldur"] == "waldur"
    assert paths["provider"] == "waldur.provider"
    assert paths["cluster"] == "waldur.provider.cluster"
    assert paths["slurm"] == "waldur.provider.cluster.slurm"


def test_topology_records_depth_per_hop(chain):
    depths = {n["name"]: n["depth"] for n in opdata.topology()["nodes"]}
    assert depths == {
        "bridge": 0,
        "waldur": 1,
        "provider": 2,
        "cluster": 3,
        "slurm": 4,
    }


def test_topology_edges_follow_the_chain(chain):
    edges = {(e["source"], e["target"]) for e in opdata.topology()["edges"]}
    assert ("bridge", "waldur") in edges
    assert ("waldur", "provider") in edges
    assert ("provider", "cluster") in edges


def test_node_carries_the_counters_the_views_use(chain):
    cluster = next(n for n in opdata.topology()["nodes"] if n["name"] == "cluster")
    assert cluster["totals"]["failed"] == 2
    assert cluster["type"] == "instance"
    assert cluster["engine"] == "templemeads"


def test_topology_raises_without_health_detail(fake_openportal):
    fake_openportal.health_root = None
    with pytest.raises(RuntimeError, match="no health detail"):
        opdata.topology()


# --- logs ----------------------------------------------------------------


def _report():
    return FakeReport(
        "cluster",
        logs=[
            FakeLogEntry(0, "INFO", "bridge_server", "Diagnostics request - destination: x"),
            FakeLogEntry(1, "INFO", "connection", "Handshake complete"),
            FakeLogEntry(2, "WARN", "connection", "Disconnecting peer provider"),
            FakeLogEntry(3, "ERROR", "exchange", "Connection provider not found"),
        ],
    )


def test_polling_chatter_is_dropped_by_default():
    rows = opdata.log_entries(_report())
    assert all("Diagnostics request" not in r["message"] for r in rows)
    assert len(rows) == 3


def test_polling_chatter_can_be_asked_for():
    rows = opdata.log_entries(_report(), include_self=True)
    assert any("Diagnostics request" in r["message"] for r in rows)


def test_limit_applies_after_filtering():
    """The chatter must not eat the caller's budget.

    Over-fetching then trimming is why this works; a naive implementation asks
    for `limit` rows, throws some away, and returns fewer than requested.
    """
    rows = opdata.log_entries(_report(), limit=3)
    assert len(rows) == 3
    assert all("Diagnostics request" not in r["message"] for r in rows)


def test_log_entries_are_structured():
    row = opdata.log_entries(_report())[0]
    assert set(row) >= {"time", "sort", "level", "target", "message"}
    assert row["level"] == "INFO"


def test_level_filter_is_passed_through():
    rows = opdata.log_entries(_report(), level="WARN+")
    assert {r["level"] for r in rows} == {"WARN", "ERROR"}


def test_merged_logs_interleave_agents_by_time(fake_openportal):
    fake_openportal.reports = {
        "a": FakeReport(
            "a", logs=[FakeLogEntry(0, "INFO", "t", "first"), FakeLogEntry(4, "INFO", "t", "last")]
        ),
        "b": FakeReport("b", logs=[FakeLogEntry(2, "INFO", "t", "middle")]),
    }
    rows = opdata.merged_logs([("a", "a"), ("b", "b")])
    assert [r["message"] for r in rows] == ["first", "middle", "last"]
    assert [r["agent"] for r in rows] == ["a", "b", "a"]


def test_merged_logs_skips_an_agent_that_cannot_answer(fake_openportal):
    fake_openportal.reports = {"a": FakeReport("a", logs=[FakeLogEntry(0, "INFO", "t", "x")])}
    rows = opdata.merged_logs([("a", "a"), ("missing", "missing")])
    assert len(rows) == 1


# --- agent detail --------------------------------------------------------


def test_agent_detail_narrows_job_lists_by_the_same_search(fake_openportal):
    fake_openportal.reports = {
        "c": FakeReport("c", failed=["add_project alpha failed", "add_user beta failed"])
    }
    detail = opdata.agent_detail("c", search="alpha")
    assert detail["failed_jobs"] == ["add_project alpha failed"]


def test_agent_detail_reports_a_missing_report(fake_openportal):
    fake_openportal.reports = {}
    assert opdata.agent_detail("nope")["ok"] is False


# --- run: the write path -------------------------------------------------


def test_run_returns_the_agents_result(fake_openportal):
    fake_openportal.next_job = FakeJob("complete", "proj:group")
    out = opdata.run_command("dest add_project p", 5000)
    assert out["ok"] is True
    assert out["result"] == "proj:group"
    assert fake_openportal.runs == [("dest add_project p", 5000)]


def test_unfinished_job_is_not_reported_as_success(fake_openportal):
    """A job still running when the wait expires has not succeeded.

    This is the usual shape of an instruction aimed at an agent that cannot be
    routed to: nothing rejects it, it simply never lands.
    """
    fake_openportal.next_job = FakeJob("running", "")
    out = opdata.run_command("dest add_project p")
    assert out["ok"] is False
    assert "did not complete" in out["error"]
    assert out["state"] == "running"


def test_run_surfaces_the_agents_error(fake_openportal):
    fake_openportal.next_job = OSError("Project does not exist")
    out = opdata.run_command("dest get_project_mapping nope")
    assert out["ok"] is False
    assert "Project does not exist" in out["error"]


def test_run_rejects_an_empty_command(fake_openportal):
    assert opdata.run_command("   ")["ok"] is False
    assert fake_openportal.runs == []


def test_run_is_refused_when_read_only(fake_openportal, monkeypatch):
    monkeypatch.setattr(opdata, "READONLY", True)
    out = opdata.run_command("dest add_project p")
    assert out["ok"] is False
    assert "read-only" in out["error"]
    assert fake_openportal.runs == []


# --- offerings -----------------------------------------------------------


def test_sync_offering_registers_a_virtual_agent(fake_openportal):
    out = opdata.sync_offering("demo")
    assert out["ok"] is True
    assert out["offering"] == "demo.waldur.provider"
    # The address a portal-answered instruction has to use.
    assert out["address"] == "waldur.demo"
    assert fake_openportal.synced == [["demo.waldur.provider"]]


def test_sync_offering_is_refused_when_read_only(fake_openportal, monkeypatch):
    monkeypatch.setattr(opdata, "READONLY", True)
    assert opdata.sync_offering("demo")["ok"] is False
    assert fake_openportal.synced == []
