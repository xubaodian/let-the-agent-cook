$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$python = Get-Content -LiteralPath (Join-Path $root "python") -Encoding UTF8
& $python (Join-Path $root "launcher.py") @args
exit $LASTEXITCODE
