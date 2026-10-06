@echo off
rem Run the fishing plugin (PC window mode by default; see README for phone usage)
cd /d "%~dp0"
python -m fishing_plugin %*
