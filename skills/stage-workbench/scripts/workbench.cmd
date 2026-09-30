@echo off
rem ============================================================
rem workbench.cmd -- personalized workbench config by stage
rem                 (edu-agent skill: stage-workbench)
rem
rem   workbench.cmd --stage 初中            初中专属工作台配置
rem   workbench.cmd --stage 高中            高中专属工作台配置
rem   workbench.cmd --list-stages           支持的学段
rem   workbench.cmd --diff                  两学段差异
rem   workbench.cmd --describe              函数与 description 清单
rem   workbench.cmd --selftest              自检
rem
rem   The platform calls scripts\workbench.py directly with the venv
rem   python; this wrapper is for humans.
rem
rem   NOTE: keep this file ASCII-only (cmd.exe uses the GBK code page).
rem ============================================================
setlocal
rem %~dp0 = <repo>\skills\stage-workbench\scripts\   -> 3 levels up = repo root
for %%I in ("%~dp0..\..\..") do set "REPO=%%~fI"

set "PY=%REPO%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0workbench.py" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
