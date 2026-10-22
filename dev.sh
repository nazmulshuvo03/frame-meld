#!/usr/bin/env bash
# Start, stop and inspect the local FrameMeld stack (Git Bash on Windows, or Linux/macOS).
#
#   ./dev.sh start                 everything: infra + migrate + render, api, worker, web
#   ./dev.sh stop                  everything: the four services, then Postgres + Redis
#   ./dev.sh up                    same as start (up/down take service names and --infra)
#   ./dev.sh status
#   ./dev.sh restart api worker    after editing .env
#   ./dev.sh logs worker
#   ./dev.sh migrate
#   ./dev.sh down [--infra]        --infra also stops Postgres and Redis
#
# Postgres + Redis run in Docker; the four services run in the background with PIDs
# and logs in var/dev/ (gitignored). Stop kills the whole process tree: `conda run`
# and `npm` wrap the real process, so killing the wrapper alone leaves a stale
# uvicorn/arq running on the old .env (see README.md).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT=$PWD
RUN=$ROOT/var/dev
mkdir -p "$RUN"

CONDA="conda run --no-capture-output -n framemeld"
ORDER=(render api worker web)   # start order; stop is the reverse

svc_dir()  { case $1 in render) echo services/render;; api|worker) echo services/backend;; web) echo apps/web;; esac; }
svc_port() { case $1 in render) echo 8790;; api) echo 8000;; web) echo 3000;; esac; }
svc_cmd()  {
  case $1 in
    render) echo "npm start";;
    api)    echo "$CONDA uvicorn framemeld.api_main:app --reload --port 8000";;
    worker) echo "$CONDA arq framemeld.worker.WorkerSettings";;
    web)    echo "npm run dev";;
  esac
}

pidfile() { echo "$RUN/$1.pid"; }
logfile() { echo "$RUN/$1.log"; }

# Prints the pid if the service is running; drops a stale pidfile otherwise.
svc_pid() {
  local f; f=$(pidfile "$1")
  [[ -f $f ]] || return 1
  local pid; pid=$(<"$f")
  if kill -0 "$pid" 2>/dev/null; then echo "$pid"; else rm -f "$f"; return 1; fi
}

port_up() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# Kill a process and everything under it. Git Bash's kill cannot reach Windows
# children, so go through taskkill with the Windows pid.
kill_tree() {
  local pid=$1
  if command -v taskkill >/dev/null 2>&1; then
    local win; win=$(cat "/proc/$pid/winpid" 2>/dev/null || echo "$pid")
    taskkill //F //T //PID "$win" >/dev/null 2>&1 || true
  else
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
  fi
}

start_svc() {
  local svc=$1 port pid
  if pid=$(svc_pid "$svc"); then echo "$svc: already running (pid $pid)"; return; fi
  port=$(svc_port "$svc")
  if [[ -n $port ]] && port_up "$port"; then
    echo "$svc: port $port is in use by something this script did not start. Skipping." >&2
    return
  fi
  ( cd "$(svc_dir "$svc")" && PYTHONUNBUFFERED=1 exec nohup $(svc_cmd "$svc") ) \
    >"$(logfile "$svc")" 2>&1 </dev/null &
  echo $! >"$(pidfile "$svc")"
  echo "$svc: started (pid $!), log var/dev/$svc.log"
  if [[ -n $port ]]; then wait_port "$svc" "$port"; fi
}

wait_port() {
  local svc=$1 port=$2 i
  for ((i = 0; i < 240; i++)); do   # 120 s; render's first start downloads Chrome
    if port_up "$port"; then echo "$svc: listening on $port"; return; fi
    svc_pid "$svc" >/dev/null || { echo "$svc exited during startup. See: ./dev.sh logs $svc" >&2; exit 1; }
    sleep 0.5
  done
  echo "$svc: port $port not up after 120s. See: ./dev.sh logs $svc" >&2
  exit 1
}

stop_svc() {
  local pid
  if pid=$(svc_pid "$1"); then kill_tree "$pid"; echo "$1: stopped (pid $pid)"; fi
  rm -f "$(pidfile "$1")"
}

# Anything started by hand, or missed by the tree kill.
stop_strays() {
  if command -v taskkill >/dev/null 2>&1; then
    for n in uvicorn arq; do
      taskkill //F //T //IM "$n.exe" >/dev/null 2>&1 && echo "killed stray $n" || true
    done
  else
    pkill -f 'uvicorn framemeld|arq framemeld' 2>/dev/null && echo "killed strays" || true
  fi
}

migrate() { (cd services/backend && $CONDA alembic upgrade head); }

status() {
  printf '%-8s %-8s %-8s %s\n' SERVICE STATE PID PORT
  local svc pid port
  for svc in "${ORDER[@]}"; do
    pid=$(svc_pid "$svc") && state=running || { pid=-; state=stopped; }
    port=$(svc_port "$svc")
    if [[ -z $port ]]; then port=-; elif port_up "$port"; then port="$port up"; else port="$port down"; fi
    printf '%-8s %-8s %-8s %s\n' "$svc" "$state" "$pid" "$port"
  done
  echo
  docker compose ps postgres redis
}

# Resolve the service names given on the command line, in canonical order.
selected() {
  local s n
  for n in "$@"; do
    [[ " ${ORDER[*]} " == *" $n "* ]] || { echo "Unknown service '$n'. Choose from: ${ORDER[*]}" >&2; exit 1; }
  done
  for s in "${ORDER[@]}"; do
    if (($# == 0)) || [[ " $* " == *" $s "* ]]; then echo "$s"; fi
  done
}

cmd=${1:-}; shift || true
infra=0 names=()
[[ $cmd == start ]] && cmd=up
[[ $cmd == stop ]] && { cmd=down; infra=1; }
for a in "$@"; do
  if [[ $a == --infra ]]; then infra=1; else names+=("$a"); fi
done

case $cmd in
  up)
    docker compose up -d --wait || { echo "docker compose up failed. Is Docker Desktop running?" >&2; exit 1; }
    migrate
    for s in $(selected "${names[@]}"); do start_svc "$s"; done
    echo; echo "App: http://localhost:3000   API docs: http://localhost:8000/docs"
    ;;
  down | restart)
    sel=$(selected "${names[@]}")
    for s in $(echo "$sel" | tac); do stop_svc "$s"; done
    ((${#names[@]})) || stop_strays   # full stop/restart: a leftover worker keeps the old .env
    if [[ $cmd == down ]]; then
      if ((infra)); then docker compose stop postgres redis; fi
    else
      for s in $sel; do start_svc "$s"; done
    fi
    ;;
  status) status ;;
  migrate) migrate ;;
  logs)
    sel=$(selected "${names[@]}")
    [[ $(echo "$sel" | wc -w) -eq 1 ]] || { echo "logs needs exactly one service name." >&2; exit 1; }
    tail -n 50 -f "$(logfile "$sel")"
    ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
