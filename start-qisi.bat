@echo off
rem ============================================================
rem  QISI - Apple-style UI build.  One-click launcher.
rem  启思教育 agent（苹果风 UI）· 一键启动
rem
rem  端口 5181（原版 5174 / ark 5180 各占一个端口，三套界面可同时打开对比）
rem  可移植：全部路径基于 %~dp0，clone 到任何目录都能直接跑
rem  编码：UTF-8 + chcp 65001；行尾由 .gitattributes 强制 CRLF
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=D:\edu-agent\.venv\Scripts\python.exe"
set "PORT=5181"
set "URL=http://127.0.0.1:%PORT%"

rem --- 已经在跑？直接开界面 ---
netstat -ano | findstr /c:":%PORT%" | findstr /c:"LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo 启思教育 agent 已在运行，正在打开 %URL% ...
  start "" "%URL%"
  exit /b 0
)

if not exist "%PY%" (
  echo [ERROR] 找不到虚拟环境: %PY%
  echo         请先双击运行 setup.cmd 一键准备环境（建 .venv + 装依赖 + 生成 .env）
  echo.
  pause
  exit /b 1
)

if not exist "%~dp0.env" (
  echo [WARN] 未找到 .env —— AI 问答会提示「DEEPSEEK_API_KEY 未就绪」。
  echo        请复制 .env.example 为 .env 并填入自己的 DEEPSEEK_API_KEY。
  echo.
)

echo [1/3] 检查本机 Ollama（embedding 服务 localhost:11434）...
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri http://localhost:11434/api/tags -UseBasicParsing -TimeoutSec 3).StatusCode } catch { 'DOWN' }" > "%TEMP%\qisi_ollama.txt"
set /p OLLAMA=<"%TEMP%\qisi_ollama.txt"
if "%OLLAMA%"=="DOWN" (
  echo       [WARN] Ollama 未运行 —— 教材向量检索会失败。
  echo              请先执行 ollama pull bge-m3，再启动 Ollama 桌面端。
) else (
  echo       [OK] Ollama 就绪
)

echo [2/3] 启动启思教育 agent（苹果风 UI）: %URL%
set "EDU_PORT=%PORT%"
start "" /b cmd /c "ping -n 5 127.0.0.1 >nul & start %URL%"
"%PY%" -u "src\edu_agent\web_server.py"

echo.
echo [3/3] 服务已停止。按任意键关闭本窗口。
pause >nul
