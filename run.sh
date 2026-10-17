#!/usr/bin/env bash
# MediaTool 启动脚本
#
#   ./run.sh              启动服务（后台，默认端口 5001）
#   ./run.sh stop         停止服务
#   ./run.sh restart      重启服务
#   ./run.sh status       查看运行状态
#   ./run.sh logs         实时查看日志（Ctrl+C 退出）
#   ./run.sh debug        调试模式前台运行（默认端口 5002，改代码自动重载）
#   ./run.sh install      安装 / 更新依赖
#   ./run.sh doctor       诊断处理记录与目标库状态（排查重复文件）
#
# 可覆盖的环境变量：
#   PORT=5001  HOST=0.0.0.0  THREADS=8  VENV=.venv  DEBUG_PORT=5002
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV="${VENV:-.venv}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5001}"
THREADS="${THREADS:-8}"
DEBUG_PORT="${DEBUG_PORT:-5002}"
PID_FILE=".run.pid"
LOG_FILE="logs/server.log"

if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_ERR=$'\033[31m'; C_INFO=$'\033[36m'; C_END=$'\033[0m'
else
    C_OK=; C_ERR=; C_INFO=; C_END=
fi

info() { printf '%s==>%s %s\n' "$C_INFO" "$C_END" "$*"; }
ok()   { printf '%s  ✓%s %s\n' "$C_OK" "$C_END" "$*"; }
warn() { printf '%s  !%s %s\n' "$C_ERR" "$C_END" "$*"; }
die()  { printf '%s  ✗ %s%s\n' "$C_ERR" "$*" "$C_END" >&2; exit 1; }

usage() {
    sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------- 依赖准备
ensure_venv() {
    if [ ! -x "$VENV/bin/python" ]; then
        info "创建虚拟环境：$VENV"
        python3 -m venv "$VENV" || die "创建虚拟环境失败（Debian/Ubuntu 需要 apt install python3-venv）"
    fi
}

deps_ready() {
    "$VENV/bin/python" -c "import flask, apscheduler, gunicorn, requests" >/dev/null 2>&1
}

ensure_deps() {
    ensure_venv
    if ! deps_ready; then
        info "首次启动，安装依赖（requirements.txt）..."
        "$VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
        "$VENV/bin/pip" install -q -r requirements.txt || die "依赖安装失败，请检查网络或 pip 源"
        deps_ready || die "依赖安装后仍无法导入，请检查 $VENV"
        ok "依赖安装完成"
    fi
}

# ---------------------------------------------------------------- 状态探测
is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    # 校验确实是本项目的 gunicorn，避免 PID 被系统复用后误判
    if [ -r "/proc/$pid/cmdline" ]; then
        tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q "gunicorn" || return 1
    fi
    return 0
}

rotate_log() {
    [ -f "$LOG_FILE" ] || return 0
    local size
    size="$(wc -c <"$LOG_FILE" 2>/dev/null || echo 0)"
    if [ "${size:-0}" -gt 5242880 ]; then      # 超过 5MB 归档
        mv -f "$LOG_FILE" "$LOG_FILE.1"
    fi
}

port_busy() {
    "$VENV/bin/python" - "$1" <<'PY'
import socket, sys
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(0)          # 已被占用
finally:
    sock.close()
sys.exit(1)
PY
}

probe() {
    "$VENV/bin/python" - "$1" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

wait_ready() {
    local url="$1" i
    for i in $(seq 1 60); do
        probe "$url" && return 0
        is_running || return 1
        sleep 0.25
    done
    return 1
}

show_url() {
    local host="$1"
    [ "$host" = "0.0.0.0" ] && host="127.0.0.1"
    printf '    界面地址： http://%s:%s/\n' "$host" "$PORT"
}

# ---------------------------------------------------------------- 子命令
cmd_start() {
    ensure_deps
    if is_running; then
        ok "已在运行（PID $(cat "$PID_FILE")）"
        show_url "$HOST"
        return 0
    fi
    if port_busy "$PORT"; then
        if [ -f "$PID_FILE" ]; then
            die "端口 $PORT 已被占用；先 ./run.sh stop 再启动，或换端口：PORT=5002 ./run.sh start"
        fi
        die "端口 $PORT 已被其他进程占用（可能是上次未正常退出的实例）。
    查找占用： ss -ltnp | grep :$PORT
    清理旧实例： pkill -f 'gunicorn.*app:app'
    或换端口： PORT=5002 ./run.sh start"
    fi

    mkdir -p logs configs
    rotate_log
    info "启动 MediaTool：$HOST:$PORT（gunicorn 单 worker / $THREADS 线程）"
    nohup "$VENV/bin/gunicorn" \
        -k gthread -w 1 --threads "$THREADS" --timeout 0 \
        --access-logfile - --error-logfile - \
        -b "$HOST:$PORT" app:app >>"$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" >"$PID_FILE"

    if wait_ready "http://127.0.0.1:$PORT/progress"; then
        ok "启动成功（PID $pid）"
        show_url "$HOST"
        printf '    日志文件： %s\n' "$LOG_FILE"
        printf '    停止服务： ./run.sh stop\n'
    else
        warn "启动失败，最近日志："
        tail -n 20 "$LOG_FILE" 2>/dev/null || true
        kill -TERM "$pid" 2>/dev/null || true
        rm -f "$PID_FILE"
        exit 1
    fi
}

cmd_stop() {
    if ! is_running; then
        rm -f "$PID_FILE"
        info "服务未在运行"
        return 0
    fi
    local pid i
    pid="$(cat "$PID_FILE")"
    info "停止服务（PID $pid）..."
    kill -TERM "$pid" 2>/dev/null || true
    for i in $(seq 1 40); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
        warn "进程未响应 TERM，强制结束"
        kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    ok "已停止"
}

cmd_status() {
    if is_running; then
        ok "运行中（PID $(cat "$PID_FILE")，端口 $PORT）"
        if probe "http://127.0.0.1:$PORT/progress"; then
            ok "HTTP 探测通过"
        else
            warn "进程存在但 HTTP 无响应，请查看 $LOG_FILE"
            return 1
        fi
    else
        info "未运行"
        return 1
    fi
}

cmd_restart() {
    cmd_stop
    cmd_start
}

cmd_logs() {
    [ -f "$LOG_FILE" ] || die "还没有日志（$LOG_FILE），先执行 ./run.sh start"
    tail -n 50 -f "$LOG_FILE"
}

cmd_debug() {
    ensure_deps
    if is_running; then
        warn "后台服务正在运行（PID $(cat "$PID_FILE")），调试模式可能与其冲突"
    fi
    info "调试模式（前台运行，Ctrl+C 退出）：http://127.0.0.1:$DEBUG_PORT/"
    exec env PORT="$DEBUG_PORT" "$VENV/bin/python" app.py
}

cmd_doctor() {
    ensure_deps
    "$VENV/bin/python" tools/doctor.py "$@"
}

cmd_install() {
    ensure_venv
    info "安装 / 更新依赖..."
    "$VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
    "$VENV/bin/pip" install -q -r requirements.txt || die "依赖安装失败"
    ok "依赖就绪"
}

# ---------------------------------------------------------------- 入口
COMMAND="${1:-start}"
shift || true
case "$COMMAND" in
    start|run)    cmd_start ;;
    stop)         cmd_stop ;;
    restart)      cmd_restart ;;
    status)       cmd_status ;;
    logs|log)     cmd_logs ;;
    debug|dev)    cmd_debug ;;
    install|deps) cmd_install ;;
    doctor)       cmd_doctor "$@" ;;
    -h|--help|help) usage ;;
    *)
        warn "未知命令：$COMMAND"
        usage
        exit 1
        ;;
esac
