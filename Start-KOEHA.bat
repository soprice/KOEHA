@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
  echo Install uv from https://docs.astral.sh/uv/getting-started/installation/ then run again.
  pause
  exit /b 1
)
uv sync --locked
if errorlevel 1 (
  pause
  exit /b 1
)
uv run --no-sync koeha
if errorlevel 1 pause
