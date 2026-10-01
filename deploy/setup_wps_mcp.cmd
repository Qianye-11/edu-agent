@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  WPS Office MCP ������װ�������� wps-skills��װ�ڲֿ��⣩
echo ============================================================
echo  �ֿ���ֻ�ű���Ŀ������㣻������Դ���� node_modules һ�ɲ���⡣
echo.

set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"

set "DEPS=%EDU_DEPS_DIR%"
if "%DEPS%"=="" set "DEPS=%LOCALAPPDATA%\edu-agent\deps"
if not "%~1"=="" set "DEPS=%~1"
set "DIR=%DEPS%\wps-skills"

echo  �ֿ�      : %REPO%
echo  ��װλ��  : %DIR%
echo.

where git >nul 2>nul || (echo [����] δ�ҵ� git & pause & exit /b 1)
where node >nul 2>nul || (echo [����] δ�ҵ� node���� Node 18+�� & pause & exit /b 1)
where npm >nul 2>nul || (echo [����] δ�ҵ� npm & pause & exit /b 1)
where powershell >nul 2>nul || (echo [����] δ�ҵ� powershell��COM ����Ҫ�� & pause & exit /b 1)

if exist "%DIR%\wps-office-mcp\package.json" (
  echo [1/5] �Ѵ��ڣ�������¡
) else (
  echo [1/5] ��¡ wps-skills ���ֿ��� ...
  if not exist "%DEPS%" mkdir "%DEPS%"
  git clone --depth 1 https://github.com/lc2panda/wps-skills "%DIR%"
  if errorlevel 1 (echo [����] ��¡ʧ�ܣ������������ȿ� GitHub �����������ô��� & pause & exit /b 1)
)

pushd "%DIR%\wps-office-mcp"
echo [2/5] ��װ���� npm ci ...
call npm ci
if errorlevel 1 (echo [����] npm ci ʧ�� & popd & pause & exit /b 1)

echo [3/5] ���� npm run build ...
call npm run build
if errorlevel 1 (echo [����] npm run build ʧ�� & popd & pause & exit /b 1)
popd

if not exist "%DIR%\wps-office-mcp\dist\index.js" (
  echo [����] ������ɵ�δ���� dist\index.js & pause & exit /b 1
)

echo [4/5] �򱾵ز�����WPS ���ڿɼ��� / Word ���̸�ʽ / ��ĸ���䣩...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patch_wps_mcp.ps1" -Root "%DIR%"
if errorlevel 1 (echo [����] ����ʧ�ܣ����˹��˶� scripts\wps-com.ps1 & pause & exit /b 1)

echo [5/5] д����ʱ���� %REPO%\data\wps_mcp.json ...
if not exist "%REPO%\data" mkdir "%REPO%\data"
set "COMMIT="
for /f "delims=" %%C in ('git -C "%DIR%" rev-parse HEAD 2^>nul') do set "COMMIT=%%C"
powershell -NoProfile -Command "$j=[ordered]@{dir='%DIR%';entry='%DIR%\wps-office-mcp\dist\index.js';skills_dir='%DIR%\skills';commit='%COMMIT%';installed_at=(Get-Date).ToString('s')}; [IO.File]::WriteAllText('%REPO%\data\wps_mcp.json', ($j | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))"
if errorlevel 1 (
  echo [����] д data\wps_mcp.json ʧ�ܣ��ɸ��û������� WPS_MCP_DIR ָ�� %DIR%
) else (
  type "%REPO%\data\wps_mcp.json"
)

echo.
echo ��ɡ�·���������ȼ����������� WPS_MCP_DIR ^> data\wps_mcp.json ^> Ĭ�� %LOCALAPPDATA%\edu-agent\deps\wps-skills
echo ע��Windows �� PowerShell COM �ţ�scripts\wps-com.ps1����**���谲װ WPS ������**������ macOS/Linux ��ѯģʽ��Ҫ����
echo �������󵽡�MCP ����ҳ�ɿ��� WPS Office �����״̬�빤����������Լ 250 ������
pause
