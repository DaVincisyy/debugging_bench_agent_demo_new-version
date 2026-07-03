#!/usr/bin/env bash
# ============================================================
#  VLM Agent Service — Linux/macOS 启动脚本
#
#  使用方式：
#    ./run_server.sh              # 默认端口 8000
#    ./run_server.sh 9000         # 指定端口 9000
#    VLM_AGENT_SERVICE_PORT=9000 ./run_server.sh
#
#  生产环境推荐用 systemd / supervisord 管理
# ============================================================

set -euo pipefail
cd "$(dirname "$0")"

# --- .env 检查 --------------------------------------------------
if [ ! -f ".env" ]; then
    echo "[ERROR] .env file not found. Copy .env.example to .env and fill in your VLM credentials."
    exit 1
fi

# --- 端口（命令行参数 > 环境变量 > 默认 8000）-------------------
PORT="${1:-${VLM_AGENT_SERVICE_PORT:-8000}}"
WORKERS="${VLM_AGENT_SERVICE_WORKERS:-1}"
HOST="${VLM_AGENT_SERVICE_HOST:-0.0.0.0}"

echo "============================================================"
echo "  VLM Agent Service"
echo "  Host    : $HOST"
echo "  Port    : $PORT"
echo "  Workers : $WORKERS"
echo "============================================================"

exec python -m uvicorn agent.service:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    --log-level info
