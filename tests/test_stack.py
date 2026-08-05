"""Tests for the throwaway agent network in stack/.

These need no Docker and no network: they read the stack's own files and check
the things that break silently.

Two kinds of breakage are worth a test here. The first is **discovery**:
run.sh and tui.sh find a stack by a compose service literally named `op-bridge`
and a volume whose name ends `openportal-invite`, so renaming either leaves the
launchers reporting "could not find a running op-bridge container" against a
stack that is up and healthy. The second is **drift**: the client the launchers
install and the agents the stack builds have to be one release, and a mismatch
is an authentication failure rather than a version warning, so the pin is
checked rather than trusted.

Every topology in stack/topologies/ is re-derived here from its own AGENTS and
WIRES and pushed through opdata.walk(), because the destinations the front ends
address agents by *are* that shape. That also pins one picture signalbox gets
wrong — see test_the_other_allocator_is_rendered_below_this_ones_provider.
"""

import re
from pathlib import Path

import pytest
from conftest import FakeHealthInfo

import opdata

ROOT = Path(__file__).resolve().parent.parent
STACK = ROOT / "stack"

COMPOSE = (STACK / "docker-compose.yml").read_text()
BOOTSTRAP = (STACK / "bootstrap.sh").read_text()

# The bootstrap explains at length what it deliberately does *not* do, so the
# tests below that assert on an absence have to read the commands alone.
BOOTSTRAP_CODE = "\n".join(
    line for line in BOOTSTRAP.splitlines() if not line.lstrip().startswith("#")
)


# --- the version pin -----------------------------------------------------


def openportal_env():
    """openportal.env parsed as a dict — it is sourced by shell, so no parser."""
    values = {}
    for line in (ROOT / "openportal.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


def test_the_pinned_version_is_a_release():
    """One value, and it has to be a release name three ways over.

    It is the PyPI version of the client, the release the agent binaries are
    downloaded from, and the version those binaries then have to report.
    """
    env = openportal_env()
    assert list(env) == ["OPENPORTAL_VERSION"], "the pin has grown a second knob"
    assert re.fullmatch(r"\d+\.\d+\.\d+", env["OPENPORTAL_VERSION"])


def test_the_launchers_take_the_version_from_the_pin():
    """One pin, not three.

    The client and the agents have to be the same release — from 0.91.0 the
    client signs the V2 canonical string and a 0.90.0 bridge verifies the V1
    one and rejects it — so nothing may carry its own default.
    """
    for name in ("run.sh", "tui.sh", "live.sh"):
        script = (ROOT / name).read_text()
        assert "openportal==${OPENPORTAL_VERSION}" in script, name
        assert re.search(r"OPENPORTAL_VERSION=\"?\$\{OPENPORTAL_VERSION:-", script) is None, (
            f"{name} carries its own version default; it must come from openportal.env"
        )

    assert ". " in (ROOT / "detect.sh").read_text().split("openportal.env")[0]


def test_the_stack_runs_the_version_the_launchers_install():
    """The agent image installs the same client the front ends do.

    stack/Dockerfile pulls the agent binaries from the pinned release and
    pip-installs the pinned client beside them, so a stack that comes up has
    already proved the two can talk. That only holds while the build arg is
    actually passed, and while one version drives both halves.
    """
    dockerfile = (STACK / "Dockerfile").read_text()
    assert 'OPENPORTAL_VERSION: "${OPENPORTAL_VERSION:?' in COMPOSE
    assert "openportal==${OPENPORTAL_VERSION:?}" in dockerfile
    assert "releases/download/${OPENPORTAL_VERSION:?}" in dockerfile


def test_the_downloaded_binaries_are_checked_against_the_pin():
    """Pulling by tag rather than by commit is the trade for not compiling.

    This is what keeps it honest: a release retagged under the same name fails
    the build rather than turning up as agents that cannot authenticate against
    the client sitting next to them.
    """
    dockerfile = (STACK / "Dockerfile").read_text()
    assert "op-bridge --version" in dockerfile
    assert 'grep -qw "${OPENPORTAL_VERSION}"' in dockerfile


def test_both_runner_architectures_are_covered():
    """arm64 on a dev machine, amd64 on a hosted runner.

    Upstream suffixes the aarch64 assets and leaves x86_64 bare, so the mapping
    is not derivable from TARGETARCH alone — and an unknown arch has to fail
    loudly rather than download nothing and produce an image with no agents.
    """
    dockerfile = (STACK / "Dockerfile").read_text()
    assert "amd64)" in dockerfile and "arm64)" in dockerfile
    assert "-aarch64" in dockerfile
    assert "exit 1" in dockerfile.split("TARGETARCH")[-1]


# --- discovery: what detect.sh matches on --------------------------------


def test_the_bridge_service_is_named_for_discovery():
    """detect.sh filters on `com.docker.compose.service=op-bridge`.

    Compose derives that label from the service key, so the name is the API.
    """
    detect = (ROOT / "detect.sh").read_text()
    assert "com.docker.compose.service=op-bridge" in detect
    assert re.search(r"^  op-bridge:$", COMPOSE, re.MULTILINE)


def test_the_invite_volume_matches_the_suffix_detect_greps_for():
    """detect.sh takes the first volume ending `openportal-invite`.

    Compose prefixes volume names with the project, so the declared name is the
    suffix; anything else and the launchers find a network but no invite.
    """
    detect = (ROOT / "detect.sh").read_text()
    suffix = re.search(r"grep -E '([^']+)'", detect).group(1)
    assert suffix == "openportal-invite$"

    volumes = COMPOSE.split("\nvolumes:\n")[1]
    declared = re.findall(r"^  ([a-z0-9-]+):$", volumes, re.MULTILINE)
    assert any(re.search(suffix, name) for name in declared), declared


def test_the_invite_volume_is_mounted_into_the_bridge():
    """detect.sh derives the invite from the bridge container it picked.

    Two stacks at once is now the normal case — this one plus whatever is being
    debugged — and resolving the network and the invite independently can pair
    one stack's network with another's key. That fails as a signature error,
    which reads as a corrupt invite rather than as the wrong stack. The
    derivation only works while the bridge actually mounts the volume.
    """
    bridge = COMPOSE.split("\n  op-bridge:")[1]
    anchor = COMPOSE.split("x-agent: &agent")[1].split("\n\n")[0]
    assert "openportal-invite:/openportal-invite" in bridge + anchor


def test_the_discoverable_bridge_writes_the_invite_the_launchers_mount():
    """The filename has to be the one opdata expects under the mount point.

    run.sh mounts the invite volume at /inv and opdata defaults to
    /inv/bridge-invite.toml, so the bridge that `detect.sh` finds — the compose
    service literally named `op-bridge`, config directory `bridge` — has to be
    the one whose invite gets that name. A topology with a second allocator
    writes a second invite beside it, which is opt-in via
    OPENPORTAL_BRIDGE_INVITE rather than something discovery could pick.
    """
    template = re.search(r'bridge --config "\$\{INVITE_DIR\}/([^"]+)"', BOOTSTRAP).group(1)
    assert opdata.INVITE.endswith("/" + template.replace("${dir}", "bridge"))


# --- the topologies ------------------------------------------------------
#
# Each stack/topologies/*.sh declares AGENTS (dir:name:port:binary) and WIRES
# (listener:dialer). Everything below is generic over those two arrays, so a
# new topology is covered the moment the file exists.

TOPOLOGIES = sorted(p.stem for p in (STACK / "topologies").glob("*.sh"))


def topology(name):
    """A topology file's AGENTS and WIRES, parsed."""
    text = (STACK / "topologies" / f"{name}.sh").read_text()

    def array(key):
        body = text.split(f"{key}=(", 1)[1].split("\n)", 1)[0]
        return re.findall(r'^\s*"([^"]+)"', body, re.MULTILINE)

    agents = {}
    for entry in array("AGENTS"):
        directory, agent, port, binary = entry.split(":")
        agents[directory] = {"name": agent, "port": port, "binary": binary}
    wires = []
    for entry in array("WIRES"):
        parts = entry.split(":")
        # listener:dialer[:zone] — zone defaults to the literal "default".
        wires.append((parts[0], parts[1], parts[2] if len(parts) > 2 else "default"))
    return agents, wires


def test_there_is_more_than_one_topology():
    """Guards the parametrisation itself: a glob that matches nothing passes."""
    assert "chain" in TOPOLOGIES
    assert len(TOPOLOGIES) > 1


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_every_agent_is_reachable_at_the_url_it_advertises(name):
    """Each agent's peers dial it on ws://op-<dir>:<port>.

    So every agent needs its own compose service or a network alias on the
    service it shares; an agent wired but not addressable connects to nothing
    and shows up as a missing leaf.
    """
    addressable = set(re.findall(r"^  (op-[a-z0-9]+):$", COMPOSE, re.MULTILINE))
    addressable |= set(re.findall(r"^          - (op-[a-z0-9]+)$", COMPOSE, re.MULTILINE))

    agents, _ = topology(name)
    for directory in agents:
        assert f"op-{directory}" in addressable, f"op-{directory} is wired but unreachable"


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_agent_ports_and_names_are_unique(name):
    """Leaves share a container, so a port clash is a real collision.

    Names have to be unique for a different reason: the viewer maps edges onto
    nodes by agent name (index.html), and a destination is a dotted list of
    them, so two agents answering to one name is ambiguous on the wire as well
    as on screen.
    """
    agents, _ = topology(name)
    ports = [a["port"] for a in agents.values()]
    names = [a["name"] for a in agents.values()]
    assert len(ports) == len(set(ports)), ports
    assert len(names) == len(set(names)), names


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_every_wired_agent_has_a_binary_the_image_carries(name):
    """A topology may run the same agent twice, so dir and binary differ.

    op-filesystem2 is a config directory, not a program; the image only ever
    downloads the eight real ones.
    """
    fetched = set(
        re.findall(r"\bop-[a-z]+\b", (STACK / "Dockerfile").read_text().split("for agent in")[1])
    )
    agents, _ = topology(name)
    for directory, agent in agents.items():
        assert agent["binary"] in fetched, f"{directory} wants {agent['binary']}, not downloaded"


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_wires_only_reference_declared_agents(name):
    agents, wires = topology(name)
    for listener, dialer, _zone in wires:
        assert listener in agents, f"{name}: wire to undeclared '{listener}'"
        assert dialer in agents, f"{name}: wire from undeclared '{dialer}'"


# The agent type each binary reports, which is what opdata.walk keys the
# portal rule off. Faking them all as one type would let a topology test pass
# while the rule it is meant to exercise never fires.
AGENT_TYPES = {
    "op-portal": "portal",
    "op-provider": "provider",
    "op-clusters": "platform",
    "op-cluster": "instance",
    "op-bridge": "bridge",
    "op-filesystem": "filesystem",
    "op-slurm": "scheduler",
    "op-localaccount": "account",
}


def peer_graph(name):
    """The peer graph a topology builds, as FakeHealthInfo objects.

    Links are added in both directions because that is how the agents hold
    them — each side keeps a key for the other. What the *health API* returns
    is narrower; see test_live.py.
    """
    agents, wires = topology(name)
    nodes = {d: FakeHealthInfo(a["name"], AGENT_TYPES[a["binary"]]) for d, a in agents.items()}
    for listener, dialer, _zone in wires:
        nodes[listener]._peers[agents[dialer]["name"]] = nodes[dialer]
        nodes[dialer]._peers[agents[listener]["name"]] = nodes[listener]
    return agents, nodes


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_each_bridge_sees_an_acyclic_estate_and_together_they_cover_it(name):
    """Every agent reachable from some bridge, and none wired twice.

    OpenPortal requires acyclicity: portal route discovery derives each agent's
    route from its peers and treats a second route to the same portal as an
    impostor, "because the topology is single-pathed and acyclic". A stray extra
    wire is not a nicety — it is a denial of service at the far end.

    Coverage is per *bridge* rather than for the graph as a whole, because a
    zone-separated host has one estate per zone and a bridge sees only its own.
    Every agent must still be somebody's.
    """
    agents, nodes = peer_graph(name)
    bridges = [d for d, a in agents.items() if a["binary"] == "op-bridge"]
    assert bridges, f"{name} has no bridge, so signalbox cannot see it at all"

    covered = set()
    for bridge in bridges:
        walked, edges = [], []
        opdata.walk(nodes[bridge], "", 0, walked, edges, set())
        assert len(edges) == len(walked) - 1, f"{name}: a shortcut or a second route"
        covered |= {node["name"] for node in walked}

    assert covered == {a["name"] for a in agents.values()}, (
        f"{name}: an agent is unreachable from any bridge"
    )


def test_the_chain_gives_the_destinations_the_front_ends_address():
    """The default topology produces exactly the routes signalbox expects."""
    _, nodes = peer_graph("chain")
    walked, edges = [], []
    opdata.walk(nodes["bridge"], "", 0, walked, edges, set())

    assert {n["name"]: n["id"] for n in walked} == {
        "bridge": "bridge",
        "waldur": "waldur",
        "provider": "waldur.provider",
        "clusters": "waldur.provider.clusters",
        "cluster": "waldur.provider.clusters.cluster",
        "filesystem": "waldur.provider.clusters.cluster.filesystem",
        "slurm": "waldur.provider.clusters.cluster.slurm",
        "localaccount": "waldur.provider.clusters.cluster.localaccount",
    }


def test_both_clusters_are_addressable_from_either_allocator():
    """The point of multi-allocator: work crosses into both clusters.

    Each allocator's own portal name roots the route, and the shared provider
    is what makes the other allocator's clusters reachable at all.
    """
    _, nodes = peer_graph("multi-allocator")
    for bridge, portal in (("bridge", "waldur"), ("bridge2", "hpcportal")):
        walked, edges = [], []
        opdata.walk(nodes[bridge], "", 0, walked, edges, set())
        paths = {n["name"]: n["id"] for n in walked}
        for cluster in ("cluster1", "cluster2"):
            assert paths[cluster] == f"{portal}.provider.clusters.{cluster}", paths[cluster]


def test_a_peer_allocators_agents_have_no_route():
    """Reachable to ask about, not addressable to instruct.

    Two different questions that look like one. Diagnostics is routed hop by
    hop across the peer graph, so the traversal path reaches the other
    allocator and `id` keeps it — the inspector works, which is worth having on
    a shared estate. An *instruction* is addressed <portal>.<agent>... rooted
    at the portal that owns the agent, and there is no such route from here, so
    `route` is None rather than a destination that would silently never land.

    Checked live too, both halves: see test_live.py.
    """
    _, nodes = peer_graph("multi-allocator")
    walked, edges = [], []
    opdata.walk(nodes["bridge"], "", 0, walked, edges, set())
    by_name = {n["name"]: n for n in walked}

    for name in ("hpcportal", "bridge2"):
        assert by_name[name]["route"] is None, name
        assert by_name[name]["allocator"] == "peer", name

    # Still reachable to ask about, and still drawn attached to the shared hop.
    assert by_name["hpcportal"]["id"] == "waldur.provider.hpcportal"
    assert ("provider", "hpcportal") in {(e["source"], e["target"]) for e in edges}


def test_our_own_agents_keep_a_route_equal_to_their_path():
    """Marking the peer estate must not disturb the one we can drive.

    Everything the bridge's own portal owns — including the clusters the two
    allocators share, which we reach through our own portal — keeps a route,
    and it is the same dotted path the inspector uses.
    """
    _, nodes = peer_graph("multi-allocator")
    walked, edges = [], []
    opdata.walk(nodes["bridge"], "", 0, walked, edges, set())
    by_name = {n["name"]: n for n in walked}

    for name in ("bridge", "waldur", "provider", "clusters", "cluster1", "cluster2", "fs1"):
        node = by_name[name]
        assert node["route"] == node["id"], name
        assert node["allocator"] == "own", name

    assert by_name["cluster2"]["route"] == "waldur.provider.clusters.cluster2"


# What the discoverable bridge should find it cannot instruct. Only an estate
# shared with another allocator has any: separate zones do not share a graph at
# all, so the other estate never appears to be marked.
UNROUTABLE = {
    "chain": set(),
    "multi-allocator": {"hpcportal", "bridge2"},
    "zoned": set(),
}


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_only_a_shared_estate_has_unroutable_agents(name):
    """The mark must fire where allocators share, and nowhere else."""
    assert name in UNROUTABLE, f"{name} is new — say what it should mark"
    _, nodes = peer_graph(name)
    walked, edges = [], []
    opdata.walk(nodes["bridge"], "", 0, walked, edges, set())

    assert {n["name"] for n in walked if n["route"] is None} == UNROUTABLE[name]


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_every_instance_has_all_three_leaf_roles(name):
    """op-cluster refuses project and user work without all three.

    Which is why they are the minimum per cluster rather than a nicety: drop
    one and every add_project fails on the missing dependency, not on the job.
    """
    agents, nodes = peer_graph(name)
    instances = [d for d, a in agents.items() if a["binary"] == "op-cluster"]
    assert instances, f"{name} has no instance agent, so nothing executes"

    for instance in instances:
        roles = {agents[d]["binary"] for d in agents if agents[d]["name"] in nodes[instance]._peers}
        assert {"op-filesystem", "op-slurm", "op-localaccount"} <= roles, (
            f"{name}: {instance} is missing a leaf role, so it cannot run jobs"
        )


def test_the_leaves_share_a_container_per_cluster():
    """Split apart, add_project fails on a group the filesystem cannot see.

    op-localaccount creates the Unix group and op-filesystem chowns to it, so
    they need one /etc/group — and a second cluster needs a second container,
    not more aliases on the first, because the two are separate machines.
    """
    for service, agents in (
        ("op-node", ("op-filesystem", "op-slurm", "op-localaccount")),
        ("op-node2", ("op-filesystem2", "op-slurm2", "op-localaccount2")),
    ):
        block = COMPOSE.split(f"\n  {service}:")[1].split("\n  op-", 1)[0]
        for agent in agents:
            assert agent in block, f"{agent} is not part of {service}"


# --- things 0.91.0 changed -----------------------------------------------


def test_the_bootstrap_does_not_widen_permissions_on_key_material():
    """0.91.0 writes configs and the invite owner-only, deliberately.

    Every container in the stack runs as root, and so does the signalbox
    container that mounts the invite, so nothing needs them widened — and
    chmod-ing them back would undo the fix.
    """
    assert "chmod" not in BOOTSTRAP_CODE


def test_peers_are_left_untyped():
    """`--type portal` switches on portal route discovery.

    Agents then derive the route to each portal and refuse traffic that does
    not match. That is what an operator deploying this would want, and it is
    wrong in a tool whose job is to show broken routing.
    """
    assert "--type" not in BOOTSTRAP_CODE


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_a_zone_is_only_used_where_a_topology_asks_for_one(name):
    """Zones are opt-in per wire, and the default has to stay `default`.

    A zone must match on both sides and is re-checked on every message, so an
    accidental zone on one link severs it — and severs it invisibly, since the
    far side then does not appear in the health report at all.
    """
    _, wires = topology(name)
    zones = {zone for _, _, zone in wires}
    if name == "zoned":
        assert zones == {"alpha", "beta"}, zones
    else:
        assert zones == {"default"}, zones


@pytest.mark.parametrize("script", ["bootstrap.sh", "node.sh"])
def test_stack_scripts_fail_loudly(script):
    """A half-bootstrapped chain is worse than one that did not start."""
    assert "set -euo pipefail" in (STACK / script).read_text()


@pytest.mark.parametrize("name", TOPOLOGIES)
def test_every_topology_keeps_a_discoverable_bridge(name):
    """`detect.sh` matches the service name exactly, so one must be `op-bridge`.

    A topology whose bridges were all called something else would come up
    healthy and be invisible to run.sh and tui.sh.
    """
    agents, _ = topology(name)
    assert "bridge" in agents and agents["bridge"]["binary"] == "op-bridge"
