#!/usr/bin/env bash
# Reliable chip-relay bring-up on Hyper-V Docker (clears Created/Exited, waits for /health).
# Run from repo root: ./scripts/chip_relay_up.sh
#
# Optional env (unset is safe with set -u; defaults applied below):
#   CHIP_RELAY_SERVICE      compose service name (default: chip-relay)
#   CHIP_RELAY_UP_TIMEOUT   health wait seconds (default: 300)
#   CHIP_RELAY_PULL         1 to pull image before up (default: 0)
#   CHIP_RELAY_BUILD        1 to build before up (default: 1)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose)
SERVICE="${CHIP_RELAY_SERVICE:-chip-relay}"
TIMEOUT="${CHIP_RELAY_UP_TIMEOUT:-300}"
PULL="${CHIP_RELAY_PULL:-0}"
BUILD="${CHIP_RELAY_BUILD:-1}"

if [[ "${PULL}" == "1" ]]; then
  "${COMPOSE[@]}" pull "$SERVICE" 2>/dev/null || docker pull chip-relay:latest 2>/dev/null || true
fi

if [[ "${BUILD}" == "1" ]]; then
  "${COMPOSE[@]}" build "$SERVICE"
fi

remove_stuck_relay_containers() {
  local ids id state
  ids="$("${COMPOSE[@]}" ps -aq "$SERVICE" 2>/dev/null || true)"
  if [[ -z "${ids}" ]]; then
    return 0
  fi
  while read -r id; do
    [[ -z "${id}" ]] && continue
    state="$(docker inspect -f '{{.State.Status}}' "${id}" 2>/dev/null || echo unknown)"
    case "${state}" in
      created | exited | dead)
        echo "Removing stuck ${SERVICE} container ${id} (state=${state})"
        docker rm -f "${id}" || true
        ;;
    esac
  done <<< "${ids}"
}

remove_stuck_relay_containers

"${COMPOSE[@]}" up -d --force-recreate "$SERVICE"

# Hyper-V quirk: container can remain Created after compose up; one forced recreate cycle.
sleep 3
ids="$("${COMPOSE[@]}" ps -aq "$SERVICE" 2>/dev/null || true)"
if [[ -n "${ids}" ]]; then
  while read -r id; do
    [[ -z "${id}" ]] && continue
    state="$(docker inspect -f '{{.State.Status}}' "${id}")"
    if [[ "${state}" == "created" ]]; then
      echo "chip-relay still Created after compose up; removing ${id} and retrying once"
      docker rm -f "${id}"
      "${COMPOSE[@]}" up -d --force-recreate "$SERVICE"
      break
    fi
  done <<< "${ids}"
fi

export CHIP_RELAY_UP_TIMEOUT="${TIMEOUT}"
python3 "${ROOT}/scripts/chip_relay_deploy.py" --timeout "${TIMEOUT}" -- "${COMPOSE[@]}"

echo "chip-relay is up (GET /health returned 200 inside the container)."
