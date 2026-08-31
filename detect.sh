#!/usr/bin/env bash
# shellcheck shell=bash
# Shared discovery for the launcher scripts.
#
# signalbox has to run inside the agent network, because op-bridge listens only
# there and every call to it is signed with the key from the invite file. Rather
# than make everyone name their compose project, find the running bridge and
# take its network and invite volume from it.
#
# Override either by exporting NETWORK / INVITE_VOLUME.

# The client version comes from openportal.env, which the test stack pulls its
# agents from too — a client and a bridge on different releases fail to
# authenticate rather than reporting a mismatch, so they are pinned together in
# one file. Exported so `docker compose` substitution picks it up.
# shellcheck source=openportal.env
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/openportal.env"
export OPENPORTAL_VERSION

detect_bridge_containers() {
    docker ps --filter "label=com.docker.compose.service=op-bridge" \
              --format '{{.Names}}'
}

detect_bridge_container() {
    detect_bridge_containers | head -1
}

detect_network() {
    local container=$1
    [ -n "$container" ] || return 1
    docker inspect "$container" \
        --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' \
        2>/dev/null | head -1
}

detect_invite_volume() {
    # Prefer the volume mounted into the bridge we already picked, so the key
    # and the network always come from the same stack. Resolving them
    # independently is fine until a second stack is running — ./stack.sh makes
    # that normal — and then it can pair one stack's network with another's
    # invite, which fails as a signature error and reads as a corrupt invite.
    local container=$1
    if [ -n "$container" ]; then
        docker inspect "$container" --format '{{range .Mounts}}{{.Name}}{{"\n"}}{{end}}' \
            2>/dev/null | grep -E 'openportal-invite$' | head -1 && return 0
    fi
    # Deployments that do not mount the invite into the bridge itself still
    # work: fall back to the conventional suffix, project prefix unknown.
    docker volume ls --format '{{.Name}}' | grep -E 'openportal-invite$' | head -1
}

# A deployment that does not run under compose - agents started by hand, or by
# systemd - has no container to discover. What signalbox actually needs is the
# invite and a route to the bridge, so when the invite is named, take it and
# skip discovery entirely rather than failing on the absence of Docker.
# shellcheck disable=SC2034  # NATIVE is read by the launchers that source this
resolve_native() {
    local config="${SIGNALBOX_CONFIG:-}"
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [ -z "$config" ] && [ -f "$here/signalbox.toml" ]; then
        config="$here/signalbox.toml"
    fi

    if [ -n "$config" ]; then
        if [ ! -f "$config" ]; then
            echo "signalbox config not found: $config" >&2
            return 1
        fi
        NATIVE=1
        echo "config: $config" >&2
        return 0
    fi

    if [ -n "${SIGNALBOX_INVITES:-}" ]; then
        NATIVE=1
        echo "deployments: ${SIGNALBOX_INVITES}" >&2
        return 0
    fi

    if [ -n "${OPENPORTAL_BRIDGE_INVITE:-}" ] && [ -f "${OPENPORTAL_BRIDGE_INVITE}" ]; then
        NATIVE=1
        echo "invite: ${OPENPORTAL_BRIDGE_INVITE}" >&2
        return 0
    fi

    return 1
}

# The interpreter a native run uses. SIGNALBOX_PYTHON is taken as it stands -
# that is how an environment which already carries a matching client is reused,
# and the only way to run against agents on a release other than the pin.
# Otherwise a venv beside the scripts gets the pinned client, so there is still
# exactly one version knob.
native_python() {
    local here=$1
    shift
    local extras=("$@")

    if [ -n "${SIGNALBOX_PYTHON:-}" ]; then
        echo "$SIGNALBOX_PYTHON"
        return 0
    fi

    local venv="$here/.venv-signalbox"
    if [ ! -x "$venv/bin/python" ]; then
        echo "creating $venv" >&2
        python3 -m venv "$venv" >&2
    fi
    if ! "$venv/bin/python" -c "import openportal" 2>/dev/null; then
        echo "installing openportal==${OPENPORTAL_VERSION} into $venv" >&2
        "$venv/bin/pip" install --quiet --disable-pip-version-check \
            "openportal==${OPENPORTAL_VERSION}" "${extras[@]}" >&2
    fi
    echo "$venv/bin/python"
}

resolve_target() {
    local container
    container=$(detect_bridge_container)

    if [ "$(detect_bridge_containers | wc -l | tr -d ' ')" -gt 1 ]; then
        echo "More than one OpenPortal stack is running; using '${container}'." >&2
        echo "Set NETWORK and INVITE_VOLUME to pick a different one." >&2
    fi

    NETWORK="${NETWORK:-$(detect_network "$container" || true)}"
    INVITE_VOLUME="${INVITE_VOLUME:-$(detect_invite_volume "$container" || true)}"

    if [ -z "${NETWORK:-}" ]; then
        cat >&2 <<'EOF'
Could not find a running op-bridge container.

signalbox talks to a live OpenPortal network, so start one first, or name it:

    NETWORK=<docker network> INVITE_VOLUME=<volume with bridge-invite.toml> ...

Agents running natively rather than under compose have no container to find.
Point signalbox at the invite instead, and it will run on the host:

    OPENPORTAL_BRIDGE_INVITE=/path/to/bridge-invite.toml ...
    SIGNALBOX_INVITES=rp=/path/rp-invite.toml,efp=/path/efp-invite.toml ...
    SIGNALBOX_CONFIG=/path/to/signalbox.toml ...
EOF
        return 1
    fi

    if [ -z "${INVITE_VOLUME:-}" ]; then
        echo "Found network '$NETWORK' but no *openportal-invite volume." >&2
        echo "Set INVITE_VOLUME to the volume holding bridge-invite.toml." >&2
        return 1
    fi

    echo "network: $NETWORK   invite: $INVITE_VOLUME" >&2
}
