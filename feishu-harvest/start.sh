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

# 前端构建产物不入库: clone 后首次运行自动构建 (需要 Node.js 16+)
if [ ! -f "frontend/dist/index.html" ]; then
  if ! command -v node >/dev/null; then
    echo "[!] 未找到 Node.js, 无法构建前端界面。"
    echo "    请安装 Node.js 16+ 后重新运行, 后端仍将以纯 API 模式启动。"
    echo "    API 地址: http://127.0.0.1:19527/docs"
  else
    if [ ! -d "frontend/node_modules" ]; then
      echo "[i] 首次运行, 正在安装前端依赖 (约 1-2 分钟)..."
      (cd frontend && npm install --no-audit --no-fund)
    fi
    echo "[i] 正在构建前端界面..."
    (cd frontend && npm run build)
  fi
fi

echo "[i] 控制台: http://127.0.0.1:19527"
( sleep 2; xdg-open http://127.0.0.1:19527 2>/dev/null || open http://127.0.0.1:19527 2>/dev/null || true ) &

cd backend
exec python3 -m harvest
