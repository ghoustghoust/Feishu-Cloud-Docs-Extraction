#!/usr/bin/env bash
# Feishu Harvest 启动脚本 (macOS / Linux)
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "[!] 未找到 python3"; exit 1; }

python3 -c "import fastapi, playwright, bs4, lxml, markdown" 2>/dev/null || {
  echo "[i] 首次运行, 安装后端依赖..."
  python3 -m pip install -r backend/requirements.txt
  python3 -m playwright install chromium
}

echo "[i] 控制台: http://127.0.0.1:19527"
( sleep 2; xdg-open http://127.0.0.1:19527 2>/dev/null || open http://127.0.0.1:19527 2>/dev/null || true ) &

cd backend
exec python3 -m harvest
