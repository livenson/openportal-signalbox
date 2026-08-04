#!/usr/bin/env bash
# Runs the three leaf agents that together stand in for a cluster login node:
# op-localaccount (Unix users and groups), op-filesystem (home and project
# directories) and op-slurm (accounting).
#
# They share one container on purpose. op-localaccount creates real Unix groups
# and op-filesystem then chowns project directories to them — split across
# containers the filesystem agent cannot resolve a group the account agent made
# in another container's /etc/group, and every add_project fails with "Could
# not find a group called <project>".
#
# Each agent keeps its own identity, config and port; the compose service
# carries a network alias per agent so peers still dial them by name. In
# signalbox they appear as three separate leaves, which is what they are.
set -euo pipefail

CONFIG_ROOT="${CONFIG_ROOT:-/op-config}"
SLURMRESTD_PORT="${SLURMRESTD_PORT:-6820}"

# op-slurm talks to slurmrestd rather than shelling out to sacctmgr, and exits
# on startup if the REST server is unreachable, so it comes up first.
echo "==> Starting slurmrestd-emulator on 127.0.0.1:${SLURMRESTD_PORT}"
slurmrestd-emulator --host 127.0.0.1 --port "${SLURMRESTD_PORT}" >/tmp/slurmrestd.log 2>&1 &
restd_pid=$!

for _ in $(seq 1 60); do
    if curl -sf -o /dev/null "http://127.0.0.1:${SLURMRESTD_PORT}/openapi.json"; then
        echo "==> slurmrestd-emulator is ready"
        break
    fi
    if ! kill -0 "$restd_pid" 2>/dev/null; then
        echo "slurmrestd-emulator died on startup:" >&2
        cat /tmp/slurmrestd.log >&2
        exit 1
    fi
    sleep 1
done

pids=("$restd_pid")
for agent in localaccount filesystem slurm; do
    echo "==> Starting op-${agent}"
    "op-${agent}" -c "${CONFIG_ROOT}/${agent}/config.toml" run &
    pids+=("$!")
done

# If any one agent dies the node is broken, so take the container down with it
# rather than leaving a half-working cluster that signalbox would show as three
# healthy leaves.
wait -n "${pids[@]}"
echo "A node agent exited; stopping the node." >&2
kill "${pids[@]}" 2>/dev/null || true
exit 1
