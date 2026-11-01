#!/usr/bin/env bash
#
# KPI Rover JupyterHub: install, build and run.
# Usage: ./run.sh <command> [args...]
# Settings live in .env (created from .env.example on first run).

set -euo pipefail

ROOT="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
readonly ROOT
readonly PASSWORD_LENGTH=12
readonly PASSWORD_CHARS='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#%*_+=?'

readonly -a COMMANDS=(
  "setup|install → reboot → check-host → build → check-image → start"
  "status|sessions, CPU/RAM, GPU"
  "start|start the hub (creates missing accounts in users)"
  "stop|stop the hub"
  "restart|apply .env and hub config (sessions keep running)"
  "logs|hub logs (Ctrl+C to exit)"
  "limit|<user> <cpus> [memory] — change limits of a running session"
  "arena|start [world] [args] | stop | logs — shared Gazebo for kpi-arena"
  "build|build session and hub images"
  "check-host|driver, nvidia runtime, GPU in a container"
  "check-image|GPU and EGL in the session image"
  "install|Docker + NVIDIA Container Toolkit (sudo)"
  "clean|remove containers, optionally user volumes and images"
)

die() {
  echo "run.sh: $*" >&2
  exit 1
}

usage_error() {
  echo "Usage: ./run.sh $*" >&2
  exit 2
}

confirm() {
  local answer
  read -r -p "$1 [y/N] " answer
  [[ "${answer}" == [yY] ]]
}

require_docker() {
  docker info >/dev/null 2>&1 || die "no Docker access. Run ./run.sh install, then reboot."
}

gen_password() {
  local password='' i
  for (( i = 0; i < PASSWORD_LENGTH; i++ )); do
    password+="${PASSWORD_CHARS:SRANDOM % ${#PASSWORD_CHARS}:1}"
  done
  printf '%s' "${password}"
}

ensure_users() {
  local -a admins students
  local user
  IFS=, read -r -a admins <<< "${ADMIN_USERS}"
  mapfile -t students < <(seq -f "${USER_PREFIX}%02g" "${USER_COUNT}")
  (umask 077 && touch users)
  for user in "${admins[@]}" "${students[@]}"; do
    user="${user// /}"
    [[ -n "${user}" ]] || continue
    grep -q -- "^${user} " users || printf '%s %s\n' "${user}" "$(gen_password)" >> users
  done
}

usage() {
  local entry
  echo "Usage: ./run.sh <command> [args...]   Settings: .env"
  echo
  for entry in "${COMMANDS[@]}"; do
    printf '  %-12s %s\n' "${entry%%|*}" "${entry#*|}"
  done
  cat <<EOF

Hub: ${URL} — logins and passwords in the users file, one "<user> <password>" per line.
Session: Launcher → Desktop → KPI Rover. Code: ~/ros_ws (kept between sessions).
  First login clones and builds the code (~2 min, log: ~/work/kpi-setup.log).
  Start over or switch the kpi_rover branch: kpi-setup [branch].
  SSH / VS Code Remote-SSH from your PC: kpi-ssh "<public key>" in a session terminal.
  Update: cd ~/ros_ws/src/kpi_rover && git pull; colcon build only after C++ changes.
  Arena: ./run.sh arena start garden; in a session: KPI Rover → Shared arena, or kpi-arena (one robot for now).
  3D apps from a terminal: vglrun -d egl <app>. GPU check: gpu-test in the Desktop terminal.
Troubleshooting:
  check-host FAIL       sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
  session won't start   ./run.sh logs; docker logs kpi-rover-session-<user>
  low fps               keep the Gazebo window off; ./run.sh limit <user> 10
  colcon "existing path cannot be removed"   rm -rf build install log && colcon build
EOF
}


cmd_install() {
  command -v nvidia-smi >/dev/null || die "install the NVIDIA driver and reboot."
  if ! command -v docker >/dev/null; then
    local codename
    # shellcheck source=/dev/null
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  if ! command -v nvidia-ctk >/dev/null; then
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | sudo gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit
  fi
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  sudo usermod -aG docker "${USER}"
  echo "Done. Reboot, then run ./run.sh setup again."
}

cmd_check_host() {
  require_docker
  local failed=0
  nvidia-smi -L || failed=1
  if [[ "$(docker info --format '{{json .Runtimes}}')" == *nvidia* ]]; then
    echo "nvidia runtime: OK"
  else
    echo "nvidia runtime: FAIL"
    failed=1
  fi
  docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L || failed=1
  (( failed == 0 )) || die "check-host: FAIL"
  echo "check-host: OK"
}

cmd_build() {
  require_docker
  docker build -t "${SESSION_IMG}" \
    --build-arg BUILD_TIME="$(date +%s)" \
    --build-arg JUPYTERHUB_VERSION --build-arg ROS_WS_REPO --build-arg KPI_ROVER_BRANCH --build-arg GUI_REPO \
    session
  docker compose build
}

cmd_check_image() {
  require_docker
  docker run --rm --gpus all -e NVIDIA_DRIVER_CAPABILITIES=all "${SESSION_IMG}" gpu-test
}

cmd_start() {
  require_docker
  ensure_users
  docker compose up -d
  docker compose ps
  echo "Open: ${URL}"
}

cmd_stop() {
  docker compose down
}

cmd_restart() {
  ensure_users
  docker compose up -d --force-recreate
}

cmd_logs() {
  docker compose logs -f --tail 100 hub
}

cmd_status() {
  docker ps --filter name=kpi-rover --format 'table {{.Names}}\t{{.Status}}'
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | grep -E 'NAME|kpi-rover' || true
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv
}

cmd_limit() {
  (( $# >= 2 )) && [[ "$2" =~ ^[0-9]+$ ]] || usage_error "limit <user> <cpus> [memory, e.g. 8g]"
  local -a memory=()
  [[ -z "${3:-}" ]] || memory=(--memory "$3" --memory-swap "$3")
  docker update --cpu-quota $(( $2 * 100000 )) "${memory[@]}" "kpi-rover-session-$1" >/dev/null
  echo "kpi-rover-session-$1: $2 cpus${3:+, $3} (until the session stops)"
}

# Shared Gazebo server that robots from sessions join (session/kpi-arena).
# Same image as the sessions, no limits, its own copy of the code in a volume.
cmd_arena() {
  require_docker
  case "${1:-}" in
    start)
      shift
      docker network inspect kpi-rover-hub >/dev/null 2>&1 || die "start the hub first: ./run.sh start"
      local world="${1:-garden}"
      (( $# == 0 )) || shift
      docker rm -f "${ARENA}" >/dev/null 2>&1 || true
      docker run -d --name "${ARENA}" --network kpi-rover-hub \
        --gpus all -e NVIDIA_DRIVER_CAPABILITIES=all --shm-size 1g \
        -e ROS_WS_REPO -e KPI_ROVER_BRANCH -e GUI_REPO \
        -v kpi-rover-arena-work:/home/jovyan/work \
        "${SESSION_IMG}" kpi-arena server world:="${world}" "$@" >/dev/null
      echo "${ARENA}: world ${world} (first start builds the code and the world, a few minutes). Logs: ./run.sh arena logs"
      ;;
    stop)
      docker rm -f "${ARENA}" >/dev/null
      echo "${ARENA}: stopped"
      ;;
    logs)
      docker logs -f --tail 100 "${ARENA}"
      ;;
    *)
      usage_error "arena start [world] [launch_sim args] | stop | logs"
      ;;
  esac
}

cmd_setup() {
  if ! docker info >/dev/null 2>&1; then
    cmd_install
    return
  fi
  cmd_check_host
  cmd_build
  cmd_check_image
  cmd_start
}

cmd_clean() {
  docker ps -aq --filter "name=^kpi-rover-session-" --filter "name=^${ARENA}\$" | xargs -r docker rm -f
  docker compose down -v
  if confirm "Delete user volumes (kpi-rover-work-*, their code) and the arena code?"; then
    docker volume ls -q --filter "name=^kpi-rover-work-" --filter "name=^kpi-rover-arena-" | xargs -r docker volume rm
  fi
  if confirm "Delete images (~11 GB)?"; then
    docker rmi -f "${SESSION_IMG}" "${HUB_IMG}"
  fi
}

main() {
  cd -- "${ROOT}"
  [[ -f .env ]] || cp .env.example .env
  set -a
  # shellcheck source=.env.example
  . ./.env
  set +a
  readonly SESSION_IMG="kpi-rover-session:${VERSION}"
  readonly HUB_IMG="kpi-rover-hub:${VERSION}"
  readonly ARENA=kpi-rover-arena
  readonly URL="${HUB_URL}"

  local command="${1:-}"
  case "${command}" in
    "")
      usage >&2
      exit 2
      ;;
    help | -h | --help)
      usage
      ;;
    *)
      local handler="cmd_${command//-/_}"
      declare -F "${handler}" >/dev/null || { echo "run.sh: unknown command: ${command}" >&2; usage >&2; exit 2; }
      shift
      "${handler}" "$@"
      ;;
  esac
}

main "$@"
