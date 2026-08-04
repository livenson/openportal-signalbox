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

The chain the bootstrap wires is also re-derived here and pushed through
opdata.walk(), because the destinations the front ends address agents by are
that chain's shape — see test_walking_the_wired_chain_gives_routable_paths.
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


def test_every_wired_agent_is_downloaded():
    """The image must carry a binary for each agent the bootstrap starts."""
    fetched = set(
        re.findall(r"\bop-[a-z]+\b", (STACK / "Dockerfile").read_text().split("for agent in")[1])
    )
    for directory in re.findall(r'^    "([a-z]+):[a-z]+:\d+"$', BOOTSTRAP, re.MULTILINE):
        assert f"op-{directory}" in fetched, f"op-{directory} is wired but never downloaded"


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


def test_the_bridge_writes_the_invite_the_launchers_mount():
    """The filename has to be the one opdata expects under the mount point.

    run.sh mounts the invite volume at /inv, and opdata defaults to
    /inv/bridge-invite.toml; the bootstrap picks the basename.
    """
    written = re.search(r'bridge --config "\$\{INVITE_DIR\}/([^"]+)"', BOOTSTRAP).group(1)
    assert opdata.INVITE.endswith(f"/{written}")


def test_every_agent_is_reachable_at_the_url_it_advertises():
    """Each agent's peers dial it on ws://op-<dir>:<port>.

    So every agent needs either its own compose service or a network alias on
    the service it shares; an agent wired but not addressable connects to
    nothing and shows up as a missing leaf.
    """
    addressable = set(re.findall(r"^  (op-[a-z]+):$", COMPOSE, re.MULTILINE))
    addressable |= set(re.findall(r"^          - (op-[a-z]+)$", COMPOSE, re.MULTILINE))

    for directory in re.findall(r'^    "([a-z]+):[a-z]+:\d+"$', BOOTSTRAP, re.MULTILINE):
        assert f"op-{directory}" in addressable, f"op-{directory} is wired but unreachable"


def test_agent_ports_are_unique():
    """The three leaves share a container, so a clash is a real collision."""
    ports = re.findall(r'^    "[a-z]+:[a-z]+:(\d+)"$', BOOTSTRAP, re.MULTILINE)
    assert len(ports) == len(set(ports)), ports


# --- the shape of the chain ----------------------------------------------


def wired_chain():
    """The peer graph the bootstrap builds, as {agent: {peer: agent}}.

    Read from the `wire <listener> <dialer>` calls and the agent table above
    them. Links are added in both directions because that is how the agents
    report them — which is the whole reason opdata.walk needs a seen set.
    """
    names = dict(re.findall(r'^    "([a-z]+):([a-z]+):\d+"$', BOOTSTRAP, re.MULTILINE))
    agents = {directory: FakeHealthInfo(name, "agent") for directory, name in names.items()}

    body = BOOTSTRAP.split("# Only reachability depends on the direction")[-1]
    pairs = re.findall(r"^wire ([a-z]+) ([a-z]+)$", body, re.MULTILINE)
    assert pairs, "no wire calls found — has bootstrap.sh been restructured?"

    for listener, dialer in pairs:
        agents[listener]._peers[names[dialer]] = agents[dialer]
        agents[dialer]._peers[names[listener]] = agents[listener]
    return agents


def test_the_chain_is_wired_as_one_connected_tree():
    """Every agent reachable from the bridge, and no agent wired twice.

    An extra `wire` is not an error the agents report — it is a second route
    that changes which destination an agent answers on, silently.
    """
    agents = wired_chain()
    nodes, edges = [], []
    opdata.walk(agents["bridge"], "", 0, nodes, edges, set())

    assert len(nodes) == len(agents), "an agent is not reachable from the bridge"
    assert len(edges) == len(agents) - 1, "the chain has a shortcut or a second route"


def test_walking_the_wired_chain_gives_routable_paths():
    """The stack produces exactly the destinations the front ends address.

    opdata.walk turns the peer tree into dotted paths, and those paths *are*
    the destinations diagnostics() and the console use. This pins the two
    together: rewiring the stack without updating what signalbox expects to
    address shows up here rather than as a job that never lands.
    """
    nodes, edges = [], []
    opdata.walk(wired_chain()["bridge"], "", 0, nodes, edges, set())
    paths = {node["name"]: node["id"] for node in nodes}

    assert paths == {
        "bridge": "bridge",
        "waldur": "waldur",
        "provider": "waldur.provider",
        "clusters": "waldur.provider.clusters",
        "cluster": "waldur.provider.clusters.cluster",
        "filesystem": "waldur.provider.clusters.cluster.filesystem",
        "slurm": "waldur.provider.clusters.cluster.slurm",
        "localaccount": "waldur.provider.clusters.cluster.localaccount",
    }


def test_the_instance_agent_has_all_three_leaves():
    """op-cluster refuses project and user work without all three.

    Which is why the node exists at all: drop one and every add_project fails
    with a message about the missing dependency rather than about the job.
    """
    cluster_peers = set(wired_chain()["cluster"]._peers)
    assert {"filesystem", "slurm", "localaccount"} <= cluster_peers


def test_the_leaves_share_one_container():
    """Split apart, add_project fails on a group the filesystem agent cannot see.

    op-localaccount creates the Unix group and op-filesystem chowns to it, so
    they need one /etc/group. The aliases are what keep them individually
    addressable despite that.
    """
    node = COMPOSE.split("\n  op-node:")[1].split("\n  op-bridge:")[0]
    for agent in ("op-filesystem", "op-slurm", "op-localaccount"):
        assert agent in node, f"{agent} is not part of op-node"


# --- things 0.91.0 changed -----------------------------------------------


def test_the_bootstrap_does_not_widen_permissions_on_key_material():
    """0.91.0 writes configs and the invite owner-only, deliberately.

    Every container in the stack runs as root, and so does the signalbox
    container that mounts the invite, so nothing needs them widened — and
    chmod-ing them back would undo the fix.
    """
    assert "chmod" not in BOOTSTRAP_CODE


def test_peers_are_left_untyped():
    """`client --add --type portal` switches on portal route discovery.

    Agents then derive the route to each portal and refuse traffic that does
    not match it. signalbox exists to show broken routing, so the stack keeps
    the pre-0.91.0 behaviour of not declaring peer types.
    """
    assert "--type" not in BOOTSTRAP_CODE


@pytest.mark.parametrize("script", ["bootstrap.sh", "node.sh"])
def test_stack_scripts_fail_loudly(script):
    """A half-bootstrapped chain is worse than one that did not start."""
    assert "set -euo pipefail" in (STACK / script).read_text()
