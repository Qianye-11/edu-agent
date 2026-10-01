@echo off
rem 兼容别名：原基座 start_web.cmd（曾硬编码 D:\edu-agent + 端口 5174）。
rem 本仓库统一走 start-qisi.bat（苹果风 UI，端口 5181）。
call "%~dp0start-qisi.bat" %*
