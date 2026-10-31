@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Feishu Harvest — 飞书知识库采撷

where python >nul 2>nul
if errorlevel 1 (
  echo [!] 未找到 Python, 请先安装 Python 3.10+ 并勾选 Add to PATH
  pause & exit /b 1
)

python -c "import fastapi, playwright, bs4, lxml, markdown" >nul 2>nul
if errorlevel 1 (
  echo [i] 首次运行, 正在安装后端依赖...
  python -m pip install -r backend\requirements.txt -q
  python -m playwright install chromium
)

rem 前端构建产物不入库: clone 后首次运行自动构建 (需要 Node.js 16+)
if not exist "frontend\dist\index.html" (
  where node >nul 2>nul
  if errorlevel 1 (
    echo [!] 未找到 Node.js, 无法构建前端界面。
    echo     请安装 Node.js 16+ 后重新运行, 后端仍将以纯 API 模式启动。
    echo     API 地址: http://127.0.0.1:19527/docs
  ) else (
    if not exist "frontend\node_modules" (
      echo [i] 首次运行, 正在安装前端依赖 (约 1-2 分钟)...
      pushd frontend
      call npm install --no-audit --no-fund
      popd
    )
    echo [i] 正在构建前端界面...
    pushd frontend
    call npm run build
    popd
  )
)

echo [i] 打开控制台 http://127.0.0.1:19527 ...
start "" http://127.0.0.1:19527

cd backend
python -m harvest
pause
