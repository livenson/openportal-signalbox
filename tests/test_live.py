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

# Nothing here hardcodes a shape. The stack has more than one topology and a
# real deployment has none of them, so the expectations come from the network
# being pointed at: which agents should be there is written into the invite
# volume by the bootstrap, and which agent executes is whatever reports itself
# as an instance.
EXPECTED_AGENTS_FILE = "/inv/expected-agents.txt"


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
def expected_agents():
    """Agent names the running topology wired, written by stack/bootstrap.sh.

    Absent when pointed at someone else's deployment, which is a supported way
    to run this — the tests that need it say so by skipping.
    """
    try:
        with open(EXPECTED_AGENTS_FILE) as handle:
            return {line.strip() for line in handle if line.strip()}
    except OSError:
        pytest.skip(f"{EXPECTED_AGENTS_FILE} not present — not a ./stack.sh network")


@pytest.fixture(scope="session")
def instance(live_topology):
    """The path of an agent that actually executes.

    Routers forward; only an instance agent and its leaves run project and user
    work, so this is what the write tests address. A topology with two clusters
    has two — either will do, and taking the first keeps the run deterministic.
    """
    instances = sorted(node["id"] for node in live_topology["nodes"] if node["type"] == "instance")
    if not instances:
        pytest.skip("no instance agent in this network, so nothing executes")
    return instances[0]


@pytest.fixture(scope="session")
def writable():
    if opdata.READONLY:
        pytest.skip("SIGNALBOX_READONLY is set; skipping the tests that submit jobs")


@pytest.fixture(scope="session")
def project(writable, instance):
    """One project on the cluster, shared by the tests below.

    Named uniquely because op-localaccount creates a real Unix group for it,
    and a second run against the same stack would collide.
    """
    name = f"sbx{uuid.uuid4().hex[:8]}.waldur"
    result = opdata.run_command(f"{instance} add_project {name}", 90_000)
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


def test_every_agent_in_the_stack_is_reported(live_topology, expected_agents):
    assert live_topology["ok"] is True
    names = {node["name"] for node in live_topology["nodes"]}
    assert names == expected_agents, names


def test_the_walk_terminates_and_visits_each_agent_once(live_topology):
    names = [node["name"] for node in live_topology["nodes"]]
    assert len(names) == len(set(names)), names


def test_each_path_extends_its_parents(live_topology):
    """A path is built by walking, so every one is a parent's path plus a name.

    Asserted structurally rather than against a fixed list, because the shape
    depends on the topology this is pointed at. The next test proves the paths
    are routes and not just labels.
    """
    by_id = {node["id"]: node for node in live_topology["nodes"]}
    for node in live_topology["nodes"]:
        if node["depth"] <= 1:
            # The bridge, and whatever it peers directly, are addressed by bare
            # name — a first hop has no prefix to extend.
            assert node["id"] == node["name"], node
            continue
        parent_id, _, last = node["id"].rpartition(".")
        assert last == node["name"], node
        assert parent_id in by_id, f"{node['id']} has no parent node"
        assert by_id[parent_id]["depth"] == node["depth"] - 1, node


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


def test_the_chatter_is_confined_to_the_bridge(live_bridge, instance):
    """Polling an agent does not put a line in that agent's own log."""
    detail = opdata.agent_detail(instance, log_lines=200, include_self=True)
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


def test_the_level_filter_is_applied_by_the_agent(live_bridge, instance):
    rows = opdata.agent_detail(instance, log_lines=100, level="WARN+")["logs"]
    assert all(row["level"] in ("WARN", "ERROR") for row in rows), rows


# --- the write path ------------------------------------------------------


def test_the_instance_agent_executes(project, instance):
    """add_project reaches a leaf and comes back with a real mapping."""
    result = opdata.run_command(f"{instance} get_project_mapping {project}", 90_000)
    assert result["ok"], result
    assert project.split(".")[0] in result["result"], result


def test_adding_a_user_maps_it_locally(project, instance):
    result = opdata.run_command(f"{instance} add_user sbxuser.{project}", 90_000)
    assert result["ok"], result
    # user:local-user:local-group
    assert result["result"].count(":") >= 2, result


@pytest.mark.parametrize("role", ["provider", "platform"])
def test_routers_do_not_execute(writable, live_topology, role):
    """Provider and platform agents forward; they do not run project work.

    The presets encode this by targeting a role rather than an agent, and this
    is the behaviour they encode. Found by role rather than by name, so it
    holds for whatever network this is pointed at.
    """
    routers = [n["id"] for n in live_topology["nodes"] if n["type"] == role]
    if not routers:
        pytest.skip(f"no {role} agent in this network")

    result = opdata.run_command(f"{routers[0]} add_project sbxrouter.waldur", 30_000)
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


def test_an_unknown_project_comes_back_as_an_error(writable, instance):
    """A refusal arrives as the agent's own message, which is the useful part.

    It comes back as a job in state error, not as an exception from run(), and
    reading that job's result raises - so the message has to be taken from
    ``error``. Asserting only that *some* error came back is how losing it went
    unnoticed.
    """
    result = opdata.run_command(f"{instance} get_project_mapping neverexisted.waldur", 30_000)
    assert result["ok"] is False, result
    assert result["state"] == "error", result
    assert "neverexisted" in result["error"], result
    assert result["error_kind"], result
    assert result["error_class"].endswith("Error"), result


def test_a_user_can_be_verified_in_and_out(project, instance):
    """is_user_added asks every leaf, not just the one that answered add_user.

    Before 0.93.0 op-cluster reported success for a scheduler step that had
    failed, so "added" could mean the Slurm account was never made. Against the
    emulator this is the check that the whole chain really did the work - and
    a "no" has to come back as false, not as an empty result.
    """
    user = f"sbxverify.{project}"
    before = opdata.run_command(f"{instance} is_user_added {user}", 30_000)
    assert before["ok"] and before["result"] == "false", before
    assert before["result_type"] == "bool", before

    assert opdata.run_command(f"{instance} add_user {user}", 90_000)["ok"]
    added = opdata.run_command(f"{instance} is_user_added {user}", 30_000)
    assert added["result"] == "true", added

    removed = opdata.run_command(f"{instance} remove_user {user}", 90_000)
    assert removed["ok"], removed
    after = opdata.run_command(f"{instance} is_user_added {user}", 30_000)
    assert after["result"] == "false", after


def test_a_removed_user_is_verified_removed(project, instance):
    """is_user_removed also asks op-slurm whether the user still has jobs.

    It does that with ``sacct --state=PENDING,RUNNING``, which the stack's
    slurm-emulator does not accept - a gap in the emulator, not in OpenPortal,
    and exactly the kind of failure the console now shows rather than hides.
    Marked expected only for that message, so the emulator learning the flag
    turns this into a pass without anyone editing it.
    """
    user = f"sbxgone.{project}"
    assert opdata.run_command(f"{instance} add_user {user}", 90_000)["ok"]
    assert opdata.run_command(f"{instance} remove_user {user}", 90_000)["ok"]

    gone = opdata.run_command(f"{instance} is_user_removed {user}", 30_000)
    if "unrecognized arguments: --state" in gone.get("error", ""):
        pytest.xfail("slurm-emulator's sacct does not accept --state")
    assert gone["result"] == "true", gone


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


def test_a_peer_allocator_is_reachable_but_not_addressable(live_topology):
    """Two different questions, and the live network answers them differently.

    The health report carries `agent_type` for every agent and the peer portal
    reports `portal`, which is what marks its estate. From there:

      - `id` still reaches it. Diagnostics is routed hop by hop across the peer
        graph, so the traversal path works and the inspector opens — worth
        having, since a shared cluster's problems are visible from both sides.
      - `route` is None, because an instruction is addressed from the portal
        that owns the agent and there is no such route from this bridge.

    Both halves are asserted against real agents, because measuring is what
    settled the design: re-rooting the path made it honest and unqueryable at
    the same time.
    """
    portals = [node for node in live_topology["nodes"] if node["type"] == "portal"]
    assert portals, "no portal reported at all"

    peers = [node for node in live_topology["nodes"] if node["allocator"] == "peer"]
    if not peers:
        pytest.skip("single-allocator network — nothing to confuse with a peer")

    for node in peers:
        assert node["route"] is None, node
        # Reachable: the inspector has to keep working on the shared estate.
        detail = opdata.agent_detail(node["id"], log_lines=2)
        assert detail["ok"], (node["id"], detail)
        assert detail["agent"] == node["name"], detail


def test_our_own_agents_are_addressable_at_their_route(live_topology, writable):
    """A route is not just non-None — it has to actually work.

    Cheapest proof that marking the peer estate did not take anything real with
    it: address the executing agent by the route the topology reports.
    """
    ours = [
        node
        for node in live_topology["nodes"]
        if node["allocator"] == "own" and node["type"] == "instance"
    ]
    assert ours, "no instance agent we can drive"

    node = ours[0]
    assert node["route"] == node["id"]
    result = opdata.run_command(f"{node['route']} add_project sbxroute.waldur", 90_000)
    assert result["ok"], result
