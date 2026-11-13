@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-KOEHA.ps1"
if errorlevel 1 pause
