$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    uv sync --locked --extra dev --extra desktop
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build dependency sync failed' }
    uv run --no-sync python scripts/build_desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
} finally {
    Pop-Location
}
