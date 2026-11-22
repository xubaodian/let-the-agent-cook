$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$project = $root
$managed = Join-Path $root ".agent-cook\tools"
$uvVersion = (Get-Content -LiteralPath (Join-Path $project ".uv-version") -Raw).Trim()
$env:UV_PROJECT_ENVIRONMENT = Join-Path $project ".venv"
$python = Join-Path $env:UV_PROJECT_ENVIRONMENT "Scripts\python.exe"

function Find-Uv {
    $onPath = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue
    $previous = Join-Path (Split-Path $root -Parent) ".agent-cook\tools\uv.exe"
    foreach ($candidate in @($env:AGENT_COOK_UV, (Join-Path $managed "uv.exe"), $previous, $onPath.Source)) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $version = & $candidate --version
            if ($LASTEXITCODE -eq 0 -and $version -match '^uv\s+(\d+\.\d+\.\d+)(\s|$)') {
                $installed = [Version]$Matches[1]
                $minimum = [Version]$uvVersion
                if ($installed.Major -eq $minimum.Major -and $installed -ge $minimum) {
                    return $candidate
                }
            }
        }
    }
}

function Install-Environment {
    $uv = Find-Uv
    if (!$uv) {
        Write-Host "Installing uv $uvVersion ..."
        $installer = Join-Path ([IO.Path]::GetTempPath()) ("agent-cook-uv-" + [Guid]::NewGuid() + ".ps1")
        $previous = $env:UV_UNMANAGED_INSTALL
        try {
            New-Item -ItemType Directory -Path $managed -Force | Out-Null
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Uri "https://astral.sh/uv/$uvVersion/install.ps1" -OutFile $installer
            $env:UV_UNMANAGED_INSTALL = $managed
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
            if ($LASTEXITCODE -ne 0) { throw "The uv installer failed." }
        } finally {
            $env:UV_UNMANAGED_INSTALL = $previous
            Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue
        }
        $uv = Find-Uv
        if (!$uv) { throw "Could not find uv after installation." }
    }
    $pythonVersion = (Get-Content -LiteralPath (Join-Path $project ".python-version") -Raw).Trim()
    Write-Host "Installing Agent Cook with Python $pythonVersion ..."
    & $uv sync --project $project --locked --python $pythonVersion --extra examples --extra server --extra e2b
    if ($LASTEXITCODE -ne 0) { throw "Could not install the locked Python dependencies for '$project'. See the uv error above." }
    $env:AGENT_COOK_UV = $uv
}

$words = [System.Collections.Generic.List[string]]::new()
$words.AddRange([string[]]$args)
try {
    if ($words.Count -and $words[0] -eq "setup") {
        $words.RemoveAt(0)
        if ($words.Count -and $words[0] -eq "--launch") {
            $words.RemoveAt(0)
            Install-Environment
            if ($words.Count -and $words[0] -eq "--global") {
                $words.RemoveAt(0)
                & $python -I -m agent_cook install-global @words
                exit $LASTEXITCODE
            }
            $runtimeArgs = @()
            if ($words.Count -and $words[0] -eq "--with-office") {
                $runtimeArgs = @($words[0])
                $words.RemoveAt(0)
            }
            & $python -I -m agent_cook setup-runtime @runtimeArgs
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } else {
            if ($words.Count -gt 1 -or ($words.Count -eq 1 -and $words[0] -notin @("--docker", "--with-office"))) {
                throw "Usage: cook.cmd setup [--docker|--with-office]"
            }
            Install-Environment
            if ($words.Count -and $words[0] -eq "--docker") {
                $words[0] = "docker"
            } else {
                & $python -I -m agent_cook setup-runtime @words
                exit $LASTEXITCODE
            }
        }
    }
    if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Run install.cmd from '$project' to set up Agent Cook."
    }
    $entry = @("-m", "agent_cook")
    if ($words.Count -and $words[0] -eq "run") {
        $words[0] = "exec"
    } elseif ($words.Count -and $words[0] -eq "demo") {
        $words.RemoveAt(0)
        if (!$words.Count) { $words.Add("quickstart") }
        $entry = @((Join-Path $root "examples\run_demo.py"))
    }
    $envFile = Join-Path $root ".env"
    if (!(Test-Path -LiteralPath $envFile -PathType Leaf)) {
        $envFile = Join-Path (Split-Path $root -Parent) ".env"
    }
    if (Test-Path -LiteralPath $envFile -PathType Leaf) {
        $uv = Find-Uv
        if (!$uv) { throw "Run install.cmd from '$project' to restore uv for .env loading." }
        $env:AGENT_COOK_UV = $uv
        & $uv run --project $project --no-sync --env-file $envFile $python -I @entry @words
    } else {
        & $python -I @entry @words
    }
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
