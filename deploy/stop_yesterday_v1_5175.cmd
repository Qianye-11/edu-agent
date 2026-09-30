@echo off
setlocal
chcp 65001 >nul

for /f %%P in ('powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 5175 -State Listen -ErrorAction SilentlyContinue; if($c){$c[0].OwningProcess}"') do set "EDU_OLD_PID=%%P"

if not defined EDU_OLD_PID (
  echo 昨日第一版未在 5175 端口运行。
  exit /b 0
)

powershell -NoProfile -Command "Stop-Process -Id %EDU_OLD_PID% -ErrorAction Stop"
echo 已停止昨日第一版，进程 PID=%EDU_OLD_PID%。
endlocal
