"""Watching more than one deployment at once.

A portal will not report another portal's health — openportal refuses it on
purpose — so two estates are two bridges, two invites, and a link the operator
states rather than one either end discloses. These tests cover the resolution
of that configuration, the qualification that keeps two estates' agents apart,
and the serialisation the client's singleton config forces on us.
"""

import threading

import pytest

import opdata


def write_config(tmp_path, body):
    path = tmp_path / "signalbox.toml"
    path.write_text(body)
    return str(path)


def test_a_lone_invite_is_one_unnamed_deployment(monkeypatch):
    monkeypatch.setattr(opdata, "CONFIG", "")
    monkeypatch.setattr(opdata, "INVITES", "")
    monkeypatch.setattr(opdata, "INVITE", "/inv/bridge-invite.toml")
    monkeypatch.setattr(opdata, "_resolved", None)
    monkeypatch.setattr(opdata, "_config_path", lambda: None)

    [source] = opdata.sources()
    assert source.invite == "/inv/bridge-invite.toml"
    assert opdata.links() == []


def test_the_env_list_names_several_deployments(monkeypatch):
    monkeypatch.setattr(opdata, "CONFIG", "")
    monkeypatch.setattr(opdata, "INVITES", "rp=/tmp/rp.toml, efp=/tmp/efp.toml")
    monkeypatch.setattr(opdata, "_resolved", None)
    monkeypatch.setattr(opdata, "_config_path", lambda: None)

    assert [(s.name, s.invite) for s in opdata.sources()] == [
        ("rp", "/tmp/rp.toml"),
        ("efp", "/tmp/efp.toml"),
    ]


def test_the_env_list_says_what_it_wanted(monkeypatch):
    monkeypatch.setattr(opdata, "CONFIG", "")
    monkeypatch.setattr(opdata, "INVITES", "/tmp/rp.toml")
    monkeypatch.setattr(opdata, "_resolved", None)
    monkeypatch.setattr(opdata, "_config_path", lambda: None)

    with pytest.raises(RuntimeError, match="name=/path"):
        opdata.sources()


def test_the_config_file_carries_deployments_and_links(tmp_path, monkeypatch):
    pytest.importorskip(
        "tomllib", reason="the config file needs a TOML parser; 3.10 uses SIGNALBOX_INVITES"
    )
    path = write_config(
        tmp_path,
        """
        [[deployment]]
        name = "rp"
        invite = "/tmp/rp.toml"
        label = "Review portal"

        [[deployment]]
        name = "efp"
        invite = "/tmp/efp.toml"

        [[link]]
        from = "rp"
        to = "efp"
        zone = "rp>efp"
        """,
    )
    monkeypatch.setattr(opdata, "CONFIG", path)
    monkeypatch.setattr(opdata, "_resolved", None)

    rp, efp = opdata.sources()
    assert (rp.name, rp.display()) == ("rp", "Review portal")
    assert efp.display() == "efp"
    [link] = opdata.links()
    assert (link.source, link.target, link.zone) == ("rp", "efp", "rp>efp")


def test_a_deployment_without_an_invite_is_refused(tmp_path, monkeypatch):
    pytest.importorskip("tomllib")
    path = write_config(tmp_path, '[[deployment]]\nname = "rp"\n')
    monkeypatch.setattr(opdata, "CONFIG", path)
    monkeypatch.setattr(opdata, "_resolved", None)

    with pytest.raises(RuntimeError, match="name and an invite"):
        opdata.sources()


def test_agents_of_two_deployments_do_not_collide(two_deployments):
    data = opdata.topology()

    assert {n["key"] for n in data["nodes"]} == {
        "rp:bridge",
        "rp:rp",
        "efp:bridge",
        "efp:efp",
    }
    # Both estates really do have an agent called "bridge"; only the key parts
    # them, which is why the front ends key on it.
    assert [n["name"] for n in data["nodes"]].count("bridge") == 2
    assert {n["source"] for n in data["nodes"]} == {"rp", "efp"}


def test_the_configured_link_joins_the_two_portals(two_deployments):
    data = opdata.topology()

    inferred = [e for e in data["edges"] if e.get("inferred")]
    assert inferred == [
        {"source": "rp:rp", "target": "efp:efp", "inferred": True, "zone": "rp>efp"}
    ]


def test_a_deployment_that_is_down_does_not_blank_the_other(two_deployments, monkeypatch):
    fake, invites = two_deployments
    real_health = fake.health

    def health():
        if fake.invite == invites["efp"]:
            raise RuntimeError("bridge unreachable")
        return real_health()

    monkeypatch.setattr(fake, "health", health)
    data = opdata.topology()

    assert {n["source"] for n in data["nodes"]} == {"rp"}
    reported = {s["name"]: s for s in data["sources"]}
    assert reported["rp"]["ok"] is True
    assert reported["efp"]["ok"] is False
    assert "unreachable" in reported["efp"]["error"]
    assert data["healthy"] is False


def test_every_deployment_down_still_raises(two_deployments, monkeypatch):
    fake, _ = two_deployments
    fake.fails = "bridge unreachable"

    with pytest.raises(RuntimeError, match="unreachable"):
        opdata.topology()


def test_a_call_is_never_answered_by_another_deployments_bridge(two_deployments):
    """The client's config is a process-wide singleton.

    load_config overwrites it, so a read that is not made under the same lock
    as its switch can be answered by whichever deployment another thread
    selected. Two threads polling at once must still produce alternating
    load/health pairs, never load-load-health-health.
    """
    fake, _ = two_deployments
    errors = []

    def poll():
        try:
            for _ in range(10):
                opdata.topology()
        except Exception as exc:  # pragma: no cover - only on a real race
            errors.append(exc)

    threads = [threading.Thread(target=poll) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    for (first, invite), (second, read_invite) in zip(fake.calls, fake.calls[1:], strict=False):
        if first == "load" and second == "health":
            assert invite == read_invite
    reads = [invite for kind, invite in fake.calls if kind == "health"]
    loads = [invite for kind, invite in fake.calls if kind == "load"]
    assert reads and loads
    assert set(reads) == set(loads)


def test_agent_detail_reaches_the_named_deployment(two_deployments):
    fake, invites = two_deployments
    fake.reports["efp"] = FakeReportStub("efp")

    detail = opdata.agent_detail("efp", source="efp")

    assert detail["ok"] is True
    assert detail["source"] == "efp"
    assert fake.invite == invites["efp"]


def test_an_unknown_deployment_is_named_in_the_error(two_deployments):
    with pytest.raises(RuntimeError, match="nope"):
        opdata.agent_detail("x", source="nope")


class FakeReportStub:
    def __init__(self, name):
        self.agent_name = name
        self.running_jobs = []
        self.failed_jobs = []
        self.slowest_jobs = []
        self.expired_jobs = []
        self.warnings = []

    def logs(self, limit=0, level=None, search=None):
        return []
