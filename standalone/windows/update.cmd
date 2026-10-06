@echo off
chcp 65001 >nul
"%~dp0runtime\python.exe" -E -s -X utf8 "%~dp0update.py" apply
if errorlevel 1 pause
