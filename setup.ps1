# 启思教育 agent（苹果风 UI）· 环境准备（PowerShell 外包装）
# 真正的逻辑在 setup.cmd 里，这里只是给 PowerShell 用户一个入口：
#     powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $root 'setup.cmd')
exit $LASTEXITCODE
