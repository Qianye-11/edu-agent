@echo off
rem ============================================================
rem ask.cmd -- ask the LOCAL Ollama a question (edu-agent skill: ollama-qa)
rem
rem   ask.cmd "your question"     one-shot (streams the answer)
rem   ask.cmd                     interactive mode (q to quit)
rem   ask.cmd --selftest          health check
rem   ask.cmd --list-models       show installed models
rem
rem   The platform calls scripts\ask.py directly with the venv python;
rem   this wrapper only exists so a human can run it by hand.
rem
rem   NOTE: keep this file ASCII-only (cmd.exe uses the GBK code page).
rem ============================================================
setlocal
rem %~dp0 = <repo>\skills\ollama-qa\scripts\   -> 3 levels up = repo root
for %%I in ("%~dp0..\..\..") do set "REPO=%%~fI"

set "PY=%REPO%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if "%~1"=="" (
  "%PY%" "%~dp0ask.py" --interactive
) else (
  "%PY%" "%~dp0ask.py" %*
)
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
