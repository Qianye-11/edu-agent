@echo off
rem ============================================================
rem  启思教育 agent（苹果风 UI）· 一键环境准备
rem
rem  clone / fork 之后跑一次即可：
rem    1) 找到 Python 3.11+
rem    2) 建虚拟环境 .venv
rem    3) 装 requirements.txt 依赖
rem    4) 由 .env.example 生成 .env（你再填 DEEPSEEK_API_KEY）
rem
rem  不做的事：不下载教材源 PDF（版权文件 182MB，知识已在 chroma_db 里）
rem  编码：UTF-8 + chcp 65001；行尾由 .gitattributes 强制 CRLF
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   启思教育 agent（苹果风 UI）· 环境准备
echo   项目目录: %~dp0
echo ============================================================
echo.

echo [1/4] 查找 Python 3.11+ ...
set "PYCMD="
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3.11"
if not defined PYCMD (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PYCMD=py -3"
)
if not defined PYCMD (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
  echo   [ERROR] 没找到 Python。请先安装 Python 3.11+ 并勾选 "Add python.exe to PATH"。
  echo           下载: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)
echo   [OK] 使用: %PYCMD%
echo.

echo [2/4] 准备虚拟环境 .venv ...
if exist ".venv\Scripts\python.exe" (
  echo   [SKIP] .venv 已存在
) else (
  %PYCMD% -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo   [ERROR] 虚拟环境创建失败。请确认 Python 3.11+ 可用后重试。
    echo.
    pause
    exit /b 1
  )
  echo   [OK] .venv 已创建
)
echo.

echo [3/4] 安装依赖（首次约 2~5 分钟，会联网下载）...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo   [ERROR] 依赖安装失败。若为网络问题，可换国内源重试：
  echo           .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  echo.
  pause
  exit /b 1
)
echo   [OK] 依赖安装完成
echo.

echo [4/4] 生成配置文件 .env ...
if exist ".env" (
  echo   [SKIP] .env 已存在，未覆盖
) else (
  copy /y ".env.example" ".env" >nul
  echo   [OK] 已由 .env.example 生成 .env
)
echo.

echo ============================================================
echo   下一步（三件事）
echo ============================================================
echo   1. 编辑 .env，把 DEEPSEEK_API_KEY 换成你自己的真实 Key
echo      申请: https://platform.deepseek.com/api_keys
echo.
echo   2. 装并启动本机 Ollama 的 embedding 模型（教材检索依赖它）
echo        ollama pull bge-m3
echo        ollama serve        （或直接打开 Ollama 桌面端）
echo.
echo   3. 双击 start-qisi.bat 启动，浏览器自动打开 http://127.0.0.1:5181
echo.
echo   说明: 教材源 PDF 未随仓库分发，因此「翻教材原文页」按钮不可用；
echo         教材知识已预建在 chroma_db 里，答疑/教案功能完整可用。
echo ============================================================
echo.
pause
