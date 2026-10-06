@echo off
chcp 65001 >nul
title T8 Index Translate - By T8star
echo T8 Index Translate
echo By T8star
echo 正在打开启动器窗口；关闭启动器会释放模型并停止服务。
"%~dp0runtime\python.exe" -E -s -X utf8 "%~dp0update.py" auto
"%~dp0runtime\python.exe" -E -s -X utf8 "%~dp0launcher.py" desktop %*
if errorlevel 1 pause
