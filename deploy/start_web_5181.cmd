@echo off
rem ============================================================
rem start_web_5181.cmd —— 演示专用入口：在 5181 端口起 Web UI
rem 默认端口 5174 不动；本脚本通过 EDU_WEB_PORT=5181 指定演示端口，
rem 因此可以和 5174 实例（日常开发）同时存在，互不干扰。
rem 停止：关闭本窗口，或 deploy\stop_ui.cmd（stop 脚本按 5174 找进程，5181 请关窗口）
rem 用法：双击本文件；浏览器自动打开 http://127.0.0.1:5181
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "PY=%~dp0..\.venv\Scripts\python.exe"
set "EDU_WEB_PORT=5181"
set "EDU_URL=http://127.0.0.1:5181"

if not exist "%PY%" (
  echo [错误] 未找到虚拟环境: %PY%
  echo 修复：在仓库根目录执行  uv venv .venv  再  uv pip install -r requirements.txt --python .venv\Scripts\python.exe
  pause
  exit /b 1
)

rem rerank：演示默认不开精排 —— docs/27 里 3~7s 的实测耗时都是在关精排下测的；
rem         要演示精排能力时，把下面一行改成 set RERANK_ON=1（单题会明显变慢，先实测）。
set RERANK_ON=0
echo [rerank] 精排关闭（演示口径：优先速度与稳定）

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5181 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
  echo [提示] 5181 已在监听，直接打开浏览器
  start "" "%EDU_URL%"
  exit /b 0
)

echo 正在启动 Agent Web UI (%EDU_URL%) ...
start "" /min "%PY%" -u "src\edu_agent\web_server.py"
timeout /t 6 >nul
start "" "%EDU_URL%"
echo.
echo 已打开：%EDU_URL%   （关闭本窗口不影响已拉起的服务）
endlocal
