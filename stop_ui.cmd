@echo off
rem ============================================================
rem  停止启思教育 agent（苹果风 UI）
rem  查杀监听 5181 端口的服务进程；端口可用 EDU_PORT 覆盖
rem ============================================================
setlocal
chcp 65001 >nul
if "%EDU_PORT%"=="" set "EDU_PORT=5181"
echo 查找并停止 %EDU_PORT% 端口上的启思教育 agent 进程 ...
set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%EDU_PORT%" ^| findstr "LISTENING"') do (
  taskkill /PID %%p /F >nul 2>&1
  if not errorlevel 1 (
    echo   已停止 PID %%p
    set "FOUND=1"
  )
)
if not defined FOUND echo   未发现监听 %EDU_PORT% 的进程，服务可能已经停止。
echo.
pause
