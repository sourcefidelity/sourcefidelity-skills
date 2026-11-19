# Install the SourceFidelity skills into a coding harness skills directory.
#
#   .\install.ps1 [-Target DIR] [-DryRun] [-NoVenv]
#
# Copies the three skill folders, checks for Python 3.12, and builds the .venv
# they share unless -NoVenv is given. It never installs Python or OCR tools
# silently.
param(
    [string]$Target = "",
    [switch]$DryRun,
    [switch]$NoVenv
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Skills = @("sourcefidelity-review", "sourcefidelity-batch", "sourcefidelity-reappraise")

# Native commands write to stderr and raise NativeCommandError under the Stop
# preference this script sets, so every probe and pip call runs under Continue
# and reports through the exit code instead.
function Test-SkillVenv([string]$VenvPython) {
    if (-not (Test-Path $VenvPython)) { return $false }
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPython -c "import pypdf, pymupdf" 2>$null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $Previous
    }
}

function Install-SkillVenv([string]$Interpreter, [string[]]$InterpreterArgs, [string]$Venv) {
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Interpreter @($InterpreterArgs + @("-m", "venv", $Venv))
        & (Join-Path $Venv "Scripts\python.exe") -m pip install --no-input pypdf pymupdf
    } finally {
        $ErrorActionPreference = $Previous
    }
}

if (-not $Target) {
    foreach ($candidate in @("$HOME\.claude\skills", "$HOME\.agents\skills",
                             "$HOME\.codex\skills", "$HOME\.cursor\skills",
                             "$HOME\.dsh\skills")) {
        if (Test-Path $candidate) { $Target = $candidate; break }
    }
}
if (-not $Target) {
    Write-Error "No harness skills directory found. Pass one, for example: .\install.ps1 -Target `"$HOME\.agents\skills`""
    exit 1
}

$Python = $null
$PythonArgs = @()
$Check = "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
$Probes = @(
    @("py", @("-3.12")),
    @("py", @("-3")),
    @("py", @()),
    @("python", @()),
    @("python3", @())
)
foreach ($probe in $Probes) {
    $candidate = $probe[0]
    $prefix = $probe[1]
    try {
        & $candidate @($prefix + @("-c", $Check)) 2>$null
        if ($LASTEXITCODE -eq 0) { $Python = $candidate; $PythonArgs = $prefix; break }
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
    Write-Host "Python 3.12 or newer was not found. The skills still run in prompt-only mode:"
    Write-Host "  counts, page mapping and verbatim checks are unavailable."
    Write-Host "  Python: https://www.python.org/downloads/"
    Write-Host "  Then re-run: .\install.ps1 -Target `"$Target`""
    exit 0
}

Write-Host "Python found: $((@($Python) + $PythonArgs) -join ' ')"
$Incomplete = $false
if (-not $NoVenv) {
    # One venv serves all three skills: it is built beside sourcefidelity-review
    # and every script resolves it (an existing per-skill venv still wins).
    $venv = Join-Path $Target (Join-Path "sourcefidelity-review" ".venv")
    $venvPython = Join-Path $venv "Scripts\python.exe"
    if ($DryRun) {
        Write-Host "would create: $venv (pypdf, pymupdf, shared by all three skills)"
    } elseif (Test-SkillVenv $venvPython) {
        Write-Host "venv ready (already complete): sourcefidelity-review"
    } else {
        Write-Host "building the shared venv in sourcefidelity-review - pip downloads pypdf and pymupdf, so a first run takes a few minutes"
        Install-SkillVenv $Python $PythonArgs $venv
        if (Test-SkillVenv $venvPython) {
            Write-Host "venv ready: sourcefidelity-review"
        } else {
            Write-Host "venv INCOMPLETE: sourcefidelity-review"
            $Incomplete = $true
        }
    }
}
Write-Host ""
if ($Incomplete) {
    Write-Host "The shared venv is incomplete."
    Write-Host "Re-run this installer to finish it: .\install.ps1 -Target `"$Target`""
    exit 1
}
Write-Host "Next: relaunch your harness and ask for a source-use review."
