@echo off
rem 兼容别名（原「启动课本教练」入口）：等价于 start-qisi.bat
rem 桌面上「启思教育agent（苹果风UI）」快捷方式最终也是走 start-qisi.bat
call "%~dp0start-qisi.bat" %*
