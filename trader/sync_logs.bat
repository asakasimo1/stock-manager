@echo off
cd /d "%~dp0"
echo Log sync started. Press Ctrl+C to stop.
echo.
:loop
git fetch origin --quiet
git checkout origin/main -- cycle_cloud.log profit_buy_cloud.log profit_sell_cloud.log monitor_036030.log 2>nul
echo %time% updated
timeout /t 300 /nobreak > nul
goto loop
