@echo off
chcp 65001 >nul
"%~dp0runtime\python.exe" -E -s -X utf8 "%~dp0launcher.py" diagnose
pause
