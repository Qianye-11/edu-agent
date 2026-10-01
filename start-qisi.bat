@echo off
rem ============================================================
rem  QISI - Apple-style UI build.  One-click launcher.
rem  Independent port 5181 (original 5174 / ark 5180 untouched).
rem  ASCII-only on purpose: cmd.exe needs plain bytes here.
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
set "PORT=5181"
set "URL=http://127.0.0.1:%PORT%"

rem --- already running? just open the UI ---
netstat -ano | findstr /c:":%PORT%" | findstr /c:"LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo QISI already running, opening %URL% ...
  start "" "%URL%"
  exit /b 0
)

if not exist "%PY%" (
  echo [ERROR] python runtime not found: %PY%
  echo         run setup.cmd first: it creates .venv and installs requirements.txt
  pause
  exit /b 1
)

if not exist "%~dp0.env" (
  echo [WARN] .env not found - AI answers will report DEEPSEEK_API_KEY not ready.
  echo        copy .env.example to .env and fill in your own DEEPSEEK_API_KEY.
  echo.
)

echo [1/3] checking local Ollama (embedding service, localhost:11434) ...
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri http://localhost:11434/api/tags -UseBasicParsing -TimeoutSec 3).StatusCode } catch { 'DOWN' }" > "%TEMP%\qisi_ollama.txt"
set /p OLLAMA=<"%TEMP%\qisi_ollama.txt"
if "%OLLAMA%"=="DOWN" (
  echo       [WARN] Ollama is not running - textbook vector search may fail.
) else (
  echo       [OK] Ollama ready
)

echo [2/3] starting QISI on %URL% ...
set "EDU_WEB_PORT=%PORT%"
start "" /b cmd /c "ping -n 5 127.0.0.1 >nul & start %URL%"
"%PY%" -u "src\edu_agent\web_server.py"

echo.
echo [3/3] Service stopped. Press any key to close.
pause >nul
