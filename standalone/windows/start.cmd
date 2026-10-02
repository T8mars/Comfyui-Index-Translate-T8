@echo off
chcp 65001 >nul
"%~dp0runtime\python.exe" "%~dp0update.py" auto
"%~dp0runtime\python.exe" "%~dp0launcher.py" start
if errorlevel 1 pause
