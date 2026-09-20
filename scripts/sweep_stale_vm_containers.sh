#!/usr/bin/env bash
# Delete dead OSWorld VM containers left behind by interrupted CoAct runs.
#
# Why they exist: a SIGHUP/SIGKILL skips DesktopEnv.close(), so neither `docker stop`
# nor the volume cleanup in stop_emulator() ever runs. The container then stays Up,
# holding 4 vCPU + 4G RAM and its ports, and pins its anonymous ~34GB /storage volume.
#
# Safety: a container is removed only when BOTH hold
#   1. it is already stopped (status=exited)  -> no live experiment can be using it
#   2. it bind-mounts VM_PATH                  -> that file lives under /data1/gjy, which
#      is mode 0750 gjy:gjy, so no other user's container can possibly reference it
# `docker rm -v` also drops the container's anonymous volumes. Named volumes and images
# are never pruned: this daemon is shared with another user, and `docker volume prune`
# would delete their unreferenced objects too.
#
# usage: scripts/sweep_stale_vm_containers.sh [--dry-run] [path_to_qcow2]
set -o pipefail

VM_PATH="/data1/gjy/coact-runtime/vm/Ubuntu.qcow2"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) VM_PATH="$arg" ;;
  esac
done

if [ ! -f "$VM_PATH" ]; then
  echo "VM image not found: $VM_PATH" >&2
  exit 1
fi

removed=0
for id in $(docker ps -aq --filter status=exited --filter ancestor=happysixd/osworld-docker); do
  if docker inspect -f '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' "$id" | grep -qx "$VM_PATH"; then
    name=$(docker inspect -f '{{.Name}}' "$id")
    vol=$(docker inspect -f '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{end}}{{end}}' "$id")
    echo "removing $id $name (volume ${vol:-none})"
    if [ "$DRY_RUN" = "0" ]; then
      docker rm -v "$id" >/dev/null || echo "  failed to remove $id" >&2
    fi
    removed=$((removed+1))
  fi
done

if [ "$DRY_RUN" = "1" ]; then
  echo "dry run: $removed container(s) would be removed"
else
  echo "removed $removed container(s)"
fi

echo "containers left: $(docker ps -aq | wc -l)   volumes left: $(docker volume ls -q | wc -l)"
df -h / | tail -1
