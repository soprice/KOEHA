$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    uv sync --locked --extra dev
    if ($LASTEXITCODE -ne 0) { throw 'Dependency sync failed' }
    uv run --no-sync python -m ruff check src tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
    uv run --no-sync python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    uv build --wheel
    if ($LASTEXITCODE -ne 0) { throw 'Wheel build failed' }
} finally {
    Pop-Location
}
