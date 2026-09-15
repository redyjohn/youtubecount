@echo off
chcp 65001 >nul
cd /d "%~dp0"
python youtube_stats.py --open
echo.
pause
