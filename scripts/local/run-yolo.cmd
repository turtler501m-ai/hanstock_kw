@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-yolo.ps1" -WorkingDirectory "%CD%" %*
