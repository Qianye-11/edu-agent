@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
rem ============================================================
rem  两份外部 skill 的**参考副本**安装脚本（只读参考，不入库）
rem
rem  为什么要有这个脚本：
rem    · ppt-master（MIT）—— 我们**在运行时外部调用**它（svg_to_pptx / pptx_intake /
rem      svg_quality_checker / image_treat / pptx_delivery_check / batch_validate …），
rem      装在 .deps\ppt-master（210 MB，不入库），由 setup_ppt_master.cmd 负责。
rem    · guizang-ppt-skill（AGPL-3.0 ⚠️）—— **一行代码都不进仓库与产品链路**，
rem      只在本地留一份只读参考，用来核对我们借鉴的**数值与规则**（色板、间距阶梯、
rem      版式清单、质量清单、校验阈值）。它必须能被重新拉回来核对，所以也要有安装入口。
rem
rem  本脚本做两件事：
rem    1) 调 setup_ppt_master.cmd 装/修 ppt-master；
rem    2) 把 guizang-ppt-skill 按**钉住的 commit** 克隆到 .deps\refs\guizang-ppt-skill。
rem
rem  用法：deploy\setup_skills.cmd
rem  路径约定：.deps\ 已被 .gitignore 忽略（第三方依赖，不入库）；.deps\refs\ 放"只看不引"的参考副本。
rem ============================================================

set "ROOT=%~dp0.."
set "REFS=%ROOT%\.deps\refs"
set "GUIZANG=%REFS%\guizang-ppt-skill"
set "GZ_REPO=https://github.com/op7418/guizang-ppt-skill.git"
rem  我们文档（docs/22 §2A、docs/29 §1.2）里核对过的 commit；换版本要先重核数值与规则
set "GZ_PIN=c91369c"

echo ============================================================
echo [1/3] ppt-master（MIT，运行时外部调用）
echo ============================================================
call "%~dp0setup_ppt_master.cmd"
rem  setup_ppt_master.cmd 结尾有 pause，这里不再停

echo.
echo ============================================================
echo [2/3] guizang-ppt-skill（AGPL-3.0，只读参考，不入库）
echo ============================================================
if not exist "%REFS%" mkdir "%REFS%"
if exist "%GUIZANG%\.git" (
  echo       已存在：%GUIZANG%
  git -C "%GUIZANG%" fetch --depth 1 origin %GZ_PIN% >nul 2>&1
  git -C "%GUIZANG%" checkout -q %GZ_PIN% >nul 2>&1
) else (
  echo       克隆中（depth=1，钉在 %GZ_PIN%）...
  git clone --depth 1 "%GZ_REPO%" "%GUIZANG%"
  if errorlevel 1 (
    echo       [X] 克隆失败：本机 github.com 可能被 hosts 指到加速器，可改用
    echo           git clone https://gh-proxy.com/%GZ_REPO% "%GUIZANG%"
    goto :check
  )
  git -C "%GUIZANG%" fetch --depth 1 origin %GZ_PIN% >nul 2>&1
  git -C "%GUIZANG%" checkout -q %GZ_PIN% >nul 2>&1
)

:check
echo.
echo ============================================================
echo [3/3] 自检
echo ============================================================
for %%F in (
  "LICENSE"
  "references\themes.md"
  "references\themes-swiss.md"
  "references\layouts.md"
  "references\layouts-swiss.md"
  "references\checklist.md"
  "references\presenter-mode.md"
  "scripts\validate-swiss-deck.mjs"
) do (
  if exist "%GUIZANG%\%%~F" (echo   [OK] %%~F) else (echo   [X ] %%~F 缺)
)
for /f "delims=" %%C in ('git -C "%GUIZANG%" rev-parse --short HEAD 2^>nul') do echo   commit=%%C（文档里核对过的是 %GZ_PIN%）

echo.
echo 说明：
echo   · 这两份都**不入库**（.gitignore：.deps/）；换机器重跑本脚本即可复原。
echo   · guizang 是 AGPL-3.0：只许借"数值与规则"，**模板/脚本/背景图一个字都不许进仓库或产品链路**。
echo   · ppt-master 是 MIT：只做**外部调用**，不许改它目录里的任何文件（它启动有完整性校验）。
echo   · 上游新版本探针（可选）：git clone --depth 1 --filter=blob:none --sparse ^
echo       https://github.com/hugohe3/ppt-master.git .deps\refs\ppt-master-next
echo       然后 git -C .deps\refs\ppt-master-next sparse-checkout set skills/ppt-master docs/zh
echo ============================================================
endlocal
pause
