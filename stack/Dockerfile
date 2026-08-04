# One image carrying every OpenPortal agent binary the test network needs.
#
# Upstream publishes each agent three ways, and this takes the third:
#
#   OCI images (ghcr.io/isambard-sc/op-*) exist for seven of the eight, but not
#   for op-localaccount — 0.91.0 ships that one, op-cloudaccount, op-cloudportal
#   and op-proxy as binaries only. They are also the wrong shape here: the leaf
#   agents shell out (op-localaccount to useradd/groupadd, op-slurm to sacctmgr)
#   and a distroless image has neither, and the three leaves have to share one
#   container anyway so the Unix groups one creates are visible to another.
#
#   Building from source works and was what this did first, but it compiles
#   eight Rust crates for a stack that is thrown away after a test run.
#
#   Release binaries are statically linked, ~8 MB each, published for x86_64 and
#   aarch64 with attested SBOMs. They drop straight into any base image, which
#   is what lets all eight sit in one container-shaped image with the shell
#   tools and the Python client beside them.
#
# The trade-off taken: this pins the release *tag* rather than a commit, so a
# retagged release would change what the stack runs. `gh attestation verify` is
# the rigorous check if that ever matters.
FROM python:3.12-slim-bookworm
ARG OPENPORTAL_VERSION
# Supplied by BuildKit: amd64 on a hosted runner, arm64 on an Apple Silicon dev
# machine. Upstream suffixes the aarch64 assets and leaves x86_64 bare.
ARG TARGETARCH

# passwd -> useradd/groupadd/usermod/gpasswd, which op-localaccount invokes;
# curl to fetch the agents and for the node's readiness probe; procps for
# poking at a container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends passwd procps curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) suffix="" ;; \
        arm64) suffix="-aarch64" ;; \
        *) echo "no OpenPortal release binaries for arch '${TARGETARCH}'" >&2; exit 1 ;; \
    esac; \
    base="https://github.com/isambard-sc/openportal/releases/download/${OPENPORTAL_VERSION:?}"; \
    for agent in op-bridge op-portal op-provider op-clusters \
                 op-cluster op-filesystem op-slurm op-localaccount; do \
        curl -fsSL -o "/usr/local/bin/${agent}" "${base}/${agent}${suffix}"; \
        chmod +x "/usr/local/bin/${agent}"; \
    done; \
    # The tag is what was asked for; this is what actually arrived. A release
    # retagged under the same name shows up here rather than as agents that
    # cannot authenticate against the pinned client.
    op-bridge --version 2>/dev/null | grep -qw "${OPENPORTAL_VERSION}"

# slurm-emulator supplies the sacctmgr/sacct/scancel fakes op-slurm shells out
# to, and the slurmrestd-emulator it actually talks to (see node.sh).
#
# The client goes in beside the agents so probe.py can ask the bridge the same
# way signalbox does, without a pip install at run time. Installing it from the
# same pin the agents came from is also the check itself: these two have to be
# one release, since a 0.91.0 client signs the V2 canonical string and a 0.90.0
# bridge verifies the V1 one and rejects it.
RUN pip install --no-cache-dir slurm-emulator "openportal==${OPENPORTAL_VERSION:?}"

COPY bootstrap.sh /usr/local/bin/op-bootstrap
COPY node.sh /usr/local/bin/op-node
COPY probe.py /usr/local/bin/op-probe
COPY filesystem-volumes.toml /opt/openportal/filesystem-volumes.toml
COPY topologies/ /opt/openportal/topologies/
RUN chmod +x /usr/local/bin/op-bootstrap /usr/local/bin/op-node /usr/local/bin/op-probe
