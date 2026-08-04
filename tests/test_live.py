"""Tests against a real agent network.

Everything else in this suite runs against the fake in conftest.py, which is
the right trade for CI but leaves a gap: the fake is only ever as correct as
our reading of the bindings. These tests close that gap by asserting the same
claims against agents that actually exist —

  - that ``HealthInfo.peers`` really is a method while its neighbours are
    properties, which is the reason ``opdata.attr`` exists at all;
  - that peer links really do come back reciprocal, which is what makes a naive
    walk loop;
  - that asking for diagnostics really does write a log line about itself,
    which is what ``SELF_CHATTER`` filters;
  - that a router really does refuse to execute, and that the refusal arrives
    as an unfinished job rather than an error.

Each of those is a documented constraint that signalbox is built around. If one
stops being true, the fake is wrong and the front ends are wrong with it, and
nothing in the offline suite would say so.

Run them with ``./live.sh`` against a stack from ``./stack.sh up`` — inside the
agent network, because op-bridge listens nowhere else. They skip themselves
otherwise. The write tests create real projects and users; ``SIGNALBOX_READONLY=1``
skips those and keeps the rest.
"""

import os
import uuid

import pytest

import opdata

pytestmark = pytest.mark.live

# The instance agent, addressed by the path opdata.walk derives for it. Routers
# above it forward; only this one and its leaves execute.
INSTANCE = "waldur.provider.clusters.cluster"

EXPECTED_PATHS = {
    "bridge": "bridge",
    "waldur": "waldur",
    "provider": "waldur.provider",
    "clusters": "waldur.provider.clusters",
    "cluster": "waldur.provider.clusters.cluster",
    "filesystem": "waldur.provider.clusters.cluster.filesystem",
    "slurm": "waldur.provider.clusters.cluster.slurm",
    "localaccount": "waldur.provider.clusters.cluster.localaccount",
}


@pytest.fixture(scope="session", autouse=True)
def live_bridge():
    """Skip the whole module unless there is a bridge to talk to.

    Deliberately actionable: the usual reason this skips is running pytest on
    the host, where the agent network is not reachable by design.
    """
    if not os.getenv("SIGNALBOX_LIVE"):
        pytest.skip("live tests are opt-in — run ./live.sh (after ./stack.sh up)")
    try:
        import openportal  # noqa: F401
    except ImportError:
        pytest.skip("the compiled 'openportal' module is not installed here")
    try:
        return opdata.bridge()
    except Exception as exc:
        pytest.skip(f"no usable bridge invite: {exc}")


@pytest.fixture(scope="session")
def live_topology(live_bridge):
    return opdata.topology()


@pytest.fixture(scope="session")
def writable():
    if opdata.READONLY:
        pytest.skip("SIGNALBOX_READONLY is set; skipping the tests that submit jobs")


@pytest.fixture(scope="session")
def project(writable):
    """One project on the cluster, shared by the tests below.

    Named uniquely because op-localaccount creates a real Unix group for it,
    and a second run against the same stack would collide.
    """
    name = f"sbx{uuid.uuid4().hex[:8]}.waldur"
    result = opdata.run_command(f"{INSTANCE} add_project {name}", 90_000)
    assert result["ok"], result
    return name


# --- the bindings, as they actually are ----------------------------------


def test_peers_is_a_method_on_the_real_bindings(live_bridge):
    """The inconsistency opdata.attr exists to absorb.

    `name` is a property and `peers` is a method on the same object. The fake
    reproduces this on purpose; this is the test that says it is not a fiction.
    """
    root = opdata.attr(live_bridge.health(), "detail")

    assert not callable(root.name)
    assert callable(root.peers), "peers is no longer a method — opdata.attr can be simplified"
    # HealthInfo has no __eq__, so compare what the call returned, not identity.
    assert sorted(opdata.attr(root, "peers")) == sorted(root.peers())


def test_the_health_tree_arrives_already_deduplicated(live_bridge):
    """Each agent's peers exclude the one that asked.

    Worth pinning because it is *not* what the tree looks like from an agent's
    own config, where every link is mutual: the bridge peers with the portal and
    the portal peers with the bridge. The health cascade drops the requester and
    everything already visited, so what arrives over the API is a tree.

    opdata.walk's seen set therefore does not fire on this shape. It stays,
    because it is also what pins each agent to its shortest path — and because
    a walk that only terminates while the far end keeps deduplicating for it is
    a walk waiting for a topology that does not.
    """
    root = opdata.attr(live_bridge.health(), "detail")
    peers = opdata.attr(root, "peers", {})
    assert peers, "the bridge reports no peers"

    for name, peer in peers.items():
        back = opdata.attr(peer, "peers", {}) or {}
        assert root.name not in back, (
            f"{name} reports {root.name} back — the cascade no longer excludes "
            "the requester, so the tree can now contain cycles"
        )


# --- topology ------------------------------------------------------------


def test_every_agent_in_the_stack_is_reported(live_topology):
    assert live_topology["ok"] is True
    names = {node["name"] for node in live_topology["nodes"]}
    assert names == set(EXPECTED_PATHS), names


def test_the_walk_terminates_and_visits_each_agent_once(live_topology):
    names = [node["name"] for node in live_topology["nodes"]]
    assert len(names) == len(set(names)), names


def test_paths_are_the_destinations_the_agents_answer_on(live_topology):
    """The dotted path opdata.walk builds is a real route, not a label.

    Proved by addressing every agent by its derived path in the next test —
    here just that the shape is what the stack wires.
    """
    paths = {node["name"]: node["id"] for node in live_topology["nodes"]}
    assert paths == EXPECTED_PATHS


def test_every_derived_path_is_routable(live_topology):
    """Each path returns that agent's own diagnostics, not someone else's."""
    for node in live_topology["nodes"]:
        detail = opdata.agent_detail(node["id"], log_lines=5)
        assert detail["ok"], (node["id"], detail)
        assert detail["agent"] == node["name"], detail


def test_agents_report_the_version_the_launchers_installed(live_topology):
    """Client and agents are one release, and this is where drift shows.

    A client ahead of the bridge cannot authenticate at all, so reaching this
    assertion already proves a lot; it stays because a stack rebuilt from a
    stale cache is the one way the two can differ while still talking.
    """
    pinned = os.environ["OPENPORTAL_VERSION"]
    versions = {node["name"]: node["version"] for node in live_topology["nodes"]}
    assert set(versions.values()) == {pinned}, versions


def test_nodes_carry_the_counters_the_views_render(live_topology):
    for node in live_topology["nodes"]:
        assert node["type"], node
        assert node["connected"] is True, f"{node['name']} is not connected"
        assert set(node["jobs"]) == {
            "active",
            "pending",
            "running",
            "completed",
            "errored",
            "expired",
        }
        assert isinstance(node["uptime_seconds"], (int, float))


# --- logs ----------------------------------------------------------------


def test_asking_for_diagnostics_logs_itself(live_bridge):
    """The chatter SELF_CHATTER filters is real, and it is ours.

    It lands on the *bridge*, not on the agent asked about: the bridge is the
    process that receives the API call, and it logs one line per request
    regardless of which destination the request names. So this is the bridge's
    log view and the merged timeline that fill up, and both are views a front
    end refreshing every five seconds is usually sitting on.

    Two calls, so the first one's line is in the buffer the second reads.
    """
    opdata.agent_detail("bridge", log_lines=50, include_self=True)
    detail = opdata.agent_detail("bridge", log_lines=200, include_self=True)

    messages = [row["message"] for row in detail["logs"]]
    assert any(opdata.SELF_CHATTER in message for message in messages), messages[-10:]


def test_the_chatter_is_confined_to_the_bridge(live_bridge):
    """Polling an agent does not put a line in that agent's own log."""
    detail = opdata.agent_detail(INSTANCE, log_lines=200, include_self=True)
    assert all(opdata.SELF_CHATTER not in row["message"] for row in detail["logs"])


def test_the_chatter_filter_removes_it(live_bridge):
    detail = opdata.agent_detail("bridge", log_lines=200)
    assert all(opdata.SELF_CHATTER not in row["message"] for row in detail["logs"])


def test_the_limit_survives_the_filter(live_bridge):
    """Over-fetch then trim: dropped chatter must not eat the caller's budget.

    Only meaningful against a buffer holding more than the limit and some
    chatter, which the bridge and the calls above have guaranteed.
    """
    rows = opdata.agent_detail("bridge", log_lines=5)["logs"]
    assert len(rows) == 5, f"asked for 5 lines, got {len(rows)}"


def test_merged_logs_interleave_the_whole_network(live_topology):
    paths = [(node["id"], node["name"]) for node in live_topology["nodes"]]
    rows = opdata.merged_logs(paths, per_agent=40)

    assert rows, "no log lines from any agent"
    assert len({row["agent"] for row in rows}) > 1, "only one agent produced logs"
    assert rows == sorted(rows, key=lambda row: row["sort"])


def test_the_level_filter_is_applied_by_the_agent(live_bridge):
    rows = opdata.agent_detail(INSTANCE, log_lines=100, level="WARN+")["logs"]
    assert all(row["level"] in ("WARN", "ERROR") for row in rows), rows


# --- the write path ------------------------------------------------------


def test_the_instance_agent_executes(project):
    """add_project reaches a leaf and comes back with a real mapping."""
    result = opdata.run_command(f"{INSTANCE} get_project_mapping {project}", 90_000)
    assert result["ok"], result
    assert project.split(".")[0] in result["result"], result


def test_adding_a_user_maps_it_locally(project):
    result = opdata.run_command(f"{INSTANCE} add_user sbxuser.{project}", 90_000)
    assert result["ok"], result
    # user:local-user:local-group
    assert result["result"].count(":") >= 2, result


@pytest.mark.parametrize("router", ["waldur.provider", "waldur.provider.clusters"])
def test_routers_do_not_execute(writable, router):
    """Provider and platform agents forward; they do not run project work.

    The presets encode this by targeting a role rather than an agent, and this
    is the behaviour they encode.
    """
    result = opdata.run_command(f"{router} add_project sbxrouter.waldur", 30_000)
    assert result["ok"] is False, result


def test_an_unroutable_destination_is_not_a_success(writable):
    """Nothing rejects it — it simply never lands.

    The job stays non-terminal until the wait expires, and run_command has to
    call that a failure; reporting the state it came back in would read as a
    pass. This is the single most misleading thing the bridge does.
    """
    result = opdata.run_command(
        "waldur.provider.clusters.nosuchcluster add_project sbxnowhere.waldur", 20_000
    )
    assert result["ok"] is False, result
    assert result["state"] != "complete", result
    assert result["error"], result


def test_an_unknown_project_comes_back_as_an_error(writable):
    """A refusal arrives as the agent's own message, which is the useful part."""
    result = opdata.run_command(f"{INSTANCE} get_project_mapping neverexisted.waldur", 30_000)
    assert result["ok"] is False, result
    assert result["error"], result


# --- offerings -----------------------------------------------------------


def test_registering_an_offering_creates_a_virtual_agent(writable):
    """The only way to address the portal software.

    The bridge accepts submissions from virtual agents only, so a portal-
    answered instruction has nothing to talk to until this runs.
    """
    name = f"sbx{uuid.uuid4().hex[:6]}"
    result = opdata.sync_offering(name)

    assert result["ok"], result
    assert result["portal"] == "waldur", result
    assert result["address"] == f"waldur.{name}", result
    assert result["offering"] in result["offerings"], result


def test_an_agent_name_is_not_an_offering_address(writable):
    """`<portal>.<agent>` is not a route, which is the usual first mistake.

    It looks exactly like the offering address that does work, and the error it
    produces does not say so — hence the note in the README.
    """
    result = opdata.run_command("waldur.provider get_projects waldur", 20_000)
    assert result["ok"] is False, result
