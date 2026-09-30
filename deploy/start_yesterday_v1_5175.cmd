@echo off
setlocal
chcp 65001 >nul

set "YESTERDAY_V1=C:\Users\h1582\.codex\worktrees\yesterday-v1\教育agent"
set "EDU_PYTHON=D:\教育agent\.venv\Scripts\python.exe"
set "EDU_URL=http://127.0.0.1:5175"

if not exist "%YESTERDAY_V1%\src\edu_agent\web_server.py" (
  echo [错误] 昨日第一版工作区不存在：%YESTERDAY_V1%
  pause
  exit /b 1
)

if not exist "%EDU_PYTHON%" (
  echo [错误] Python 环境不存在：%EDU_PYTHON%
  pause
  exit /b 1
)

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5175 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%EDU_PYTHON%' -ArgumentList @('-m','uvicorn','edu_agent.web_server:app','--app-dir','src','--host','127.0.0.1','--port','5175','--log-level','warning') -WorkingDirectory '%YESTERDAY_V1%' -WindowStyle Hidden"
  timeout /t 2 /nobreak >nul
)

start "" "%EDU_URL%"
echo 昨日第一版已打开：%EDU_URL%
endlocal
