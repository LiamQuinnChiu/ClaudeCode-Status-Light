@echo off
chcp 65001 >nul
echo.
echo   ============================================
echo     CursorLight — Claude Code 状态灯 安装
echo     ESP32 BLE 实体灯  + 桌面任务栏圆点
echo   ============================================
echo.
echo   即将安装到: %USERPROFILE%\.cursor\hooks\cursor-light\
echo   Claude Code 配置: %USERPROFILE%\.claude\settings.json
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
