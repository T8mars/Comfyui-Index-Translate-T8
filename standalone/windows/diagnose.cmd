@echo off
chcp 65001 >nul
"%~dp0runtime\python.exe" "%~dp0launcher.py" diagnose
pause
