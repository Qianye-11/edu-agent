# 启思教育 agent（苹果风 UI）· PowerShell 启动器
#   powershell -ExecutionPolicy Bypass -File run_ui.ps1
# ASCII-only on purpose: keeps this file readable no matter the console codepage.
$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = Join-Path $root ".venv\Scripts\python.exe"
if (-not $env:EDU_WEB_PORT) { $env:EDU_WEB_PORT = "5181" }

Write-Output ("ROOT   " + $root)
Write-Output ("PYTHON " + $py)
Write-Output ("URL    http://127.0.0.1:" + $env:EDU_WEB_PORT)

if (-not (Test-Path $py)) {
    Write-Output "[ERROR] venv not found. Run setup.cmd first (creates .venv and installs requirements.txt)."
    exit 1
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Output "[WARN] .env not found: copy .env.example to .env and set DEEPSEEK_API_KEY."
}

& $py -u (Join-Path $root "src\edu_agent\web_server.py")
Write-Output ("PYEXIT " + $LASTEXITCODE)
