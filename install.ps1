# Install the SourceFidelity skills into a coding harness skills directory.
#
#   .\install.ps1 [-Target DIR] [-DryRun] [-NoVenv]
#
# Copies the three skill folders, checks for Python 3.12, and builds each
# skill's .venv unless -NoVenv is given. It never installs Python or OCR tools
# silently.
param(
    [string]$Target = "",
    [switch]$DryRun,
    [switch]$NoVenv
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Skills = @("sourcefidelity-review", "sourcefidelity-batch", "sourcefidelity-reappraise")

if (-not $Target) {
    foreach ($candidate in @("$HOME\.claude\skills", "$HOME\.agents\skills",
                             "$HOME\.codex\skills", "$HOME\.cursor\skills")) {
        if (Test-Path $candidate) { $Target = $candidate; break }
    }
}
if (-not $Target) {
    Write-Error "No harness skills directory found. Pass one, for example: .\install.ps1 -Target `"$HOME\.agents\skills`""
    exit 1
}

$Python = $null
foreach ($candidate in @("py", "python", "python3")) {
    $arguments = if ($candidate -eq "py") { @("-3.12", "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)") } else { @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)") }
    try {
        & $candidate @arguments 2>$null
        if ($LASTEXITCODE -eq 0) { $Python = $candidate; break }
    } catch { }
}

Write-Host "SourceFidelity skills installer"
Write-Host "  target: $Target"
if ($DryRun) { Write-Host "  mode:   dry run (nothing is written)" }
Write-Host ""

foreach ($skill in $Skills) {
    $source = Join-Path $Here $skill
    if (-not (Test-Path $source)) { Write-Error "missing skill folder: $source"; exit 1 }
    if ($DryRun) {
        Write-Host "would install: $skill -> $(Join-Path $Target $skill)"
    } else {
        New-Item -ItemType Directory -Force -Path (Join-Path $Target $skill) | Out-Null
        Copy-Item -Recurse -Force (Join-Path $source "*") (Join-Path $Target $skill)
        Write-Host "installed: $skill"
    }
}

Write-Host ""
if (-not $Python) {
    Write-Host "Python 3.12 was not found. The skills still run in prompt-only mode:"
    Write-Host "  counts, page mapping and verbatim checks are unavailable."
    Write-Host "  Python: https://www.python.org/downloads/"
    Write-Host "  Then re-run: .\install.ps1 -Target `"$Target`""
    exit 0
}

Write-Host "Python found: $Python"
if (-not $NoVenv) {
    foreach ($skill in $Skills) {
        $venv = Join-Path $Target (Join-Path $skill ".venv")
        if ($DryRun) {
            Write-Host "would create: $venv (pypdf, pymupdf)"
            continue
        }
        & $Python -m venv $venv
        & (Join-Path $venv "Scripts\python.exe") -m pip install --quiet pypdf pymupdf
        Write-Host "venv ready: $skill"
    }
}
Write-Host ""
Write-Host "Next: relaunch your harness and ask for a source-use review."
