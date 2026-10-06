@echo off
rem Purge local logs/ and debug/ (one-key cleanup)
cd /d "%~dp0"
python -m fishing_plugin --purge
pause
