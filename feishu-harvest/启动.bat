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

echo [i] 打开控制台 http://127.0.0.1:19527 ...
start "" http://127.0.0.1:19527

cd backend
python -m harvest
pause
