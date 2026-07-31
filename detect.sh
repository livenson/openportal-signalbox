# Shared discovery for the launcher scripts.
#
# signalbox has to run inside the agent network, because op-bridge listens only
# there and every call to it is signed with the key from the invite file. Rather
# than make everyone name their compose project, find the running bridge and
# take its network and invite volume from it.
#
# Override either by exporting NETWORK / INVITE_VOLUME.

detect_bridge_container() {
    docker ps --filter "label=com.docker.compose.service=op-bridge" \
              --format '{{.Names}}' | head -1
}

detect_network() {
    local container
    container=$(detect_bridge_container)
    [ -n "$container" ] || return 1
    docker inspect "$container" \
        --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' \
        2>/dev/null | head -1
}

detect_invite_volume() {
    # The bootstrap writes the invite into a volume the agents share; match on
    # the conventional suffix rather than a fixed project prefix.
    docker volume ls --format '{{.Name}}' | grep -E 'openportal-invite$' | head -1
}

resolve_target() {
    NETWORK="${NETWORK:-$(detect_network || true)}"
    INVITE_VOLUME="${INVITE_VOLUME:-$(detect_invite_volume || true)}"

    if [ -z "${NETWORK:-}" ]; then
        cat >&2 <<'EOF'
Could not find a running op-bridge container.

signalbox talks to a live OpenPortal network, so start one first, or name it:

    NETWORK=<docker network> INVITE_VOLUME=<volume with bridge-invite.toml> ...
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
