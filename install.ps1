# Install the SourceFidelity skills into a coding harness skills directory.
#
#   .\install.ps1 [-Target DIR] [-DryRun] [-NoVenv]
#                 [-Check] [-Update] [-Channel NAME] [-Version TAG]
#
# Copies the three skill folders, checks for Python 3.12, builds the .venv
# they share unless -NoVenv is given, and records the installed version.
# -Check asks whether a newer release exists; -Update downloads and verifies
# the release named in latest.json (requires tar on Windows 10+). It never
# installs Python or OCR tools silently.
param(
    [string]$Target = "",
    [switch]$DryRun,
    [switch]$NoVenv,
    [string]$Channel = "dev",
    [string]$Version = "",
    [switch]$Check,
    [switch]$Update,
    [switch]$AllowUnverified
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Skills = @("sourcefidelity-review", "sourcefidelity-batch", "sourcefidelity-reappraise")

function Test-VersionNewer([string]$Have, [string]$New) {
    try { return ([version]$New -gt [version]$Have) } catch { return $false }
}

# Native commands write to stderr and raise NativeCommandError under the Stop
# preference this script sets, so every probe and pip call runs under Continue
# and reports through the exit code instead.
function Test-SkillVenv([string]$VenvPython) {
    if (-not (Test-Path $VenvPython)) { return $false }
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPython -c "import pypdf, pymupdf, zstandard" 2>$null
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
        & (Join-Path $Venv "Scripts\python.exe") -m pip install --no-input pypdf pymupdf zstandard
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
$PythonCheck = "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
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
        & $candidate @($prefix + @("-c", $PythonCheck)) 2>$null
        if ($LASTEXITCODE -eq 0) { $Python = $candidate; $PythonArgs = $prefix; break }
    } catch { }
}

Write-Host "SourceFidelity skills installer"
Write-Host "  target: $Target"
if ($DryRun) { Write-Host "  mode:   dry run (nothing is written)" }
Write-Host ""
if ($Version) {
    $Want = $Version -replace '^v', ''
    $Have = (Get-Content (Join-Path $Here "VERSION") -Raw).Trim()
    if ($Want -ne $Have) {
        Write-Error "this checkout is $Have, not $Want; check out tag v$Want first"
        exit 1
    }
    $Channel = "tag:$Version"
}

if ($Check) {
    $Checker = Join-Path $Target "sourcefidelity-review\scripts\update_check.py"
    if (-not (Test-Path $Checker)) {
        Write-Error "no installed skill found at $Target; run the installer first"
        exit 1
    }
    if (-not $Python) {
        Write-Error "Python 3.12 is required for -Check"
        exit 1
    }
    $Raw = & $Python @($PythonArgs + @("-I", $Checker, "--check", "--json"))
    if ($LASTEXITCODE -ne 0 -or -not $Raw) {
        Write-Host "Could not check for updates (offline or latest.json unavailable)."
        exit 1
    }
    $Result = $Raw | ConvertFrom-Json
    if ($Result.notice) { Write-Host $Result.notice }
    elseif ($Result.fetch_failed) { Write-Host "Could not check for updates (offline or latest.json unavailable)." }
    else { Write-Host "SourceFidelity is up to date." }
    exit 0
}

if ($Update) {
    if (-not $Python) { Write-Error "Python 3.12 is required for -Update"; exit 1 }
    if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        Write-Error "tar was not found; -Update needs the Windows 10 tar command (or use install.sh under WSL)"
        exit 1
    }
    $LatestUrl = $env:SOURCEFIDELITY_LATEST_URL
    if (-not $LatestUrl) { $LatestUrl = "https://raw.githubusercontent.com/sourcefidelity/sourcefidelity-skills/main/latest.json" }
    $Temp = Join-Path ([IO.Path]::GetTempPath()) ("sf-update-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $Temp | Out-Null
    try {
        $Latest = Invoke-RestMethod -Uri $LatestUrl -TimeoutSec 15
        $InstalledFile = Join-Path $Target ".sourcefidelity\installed.json"
        $Have = ""
        if (Test-Path $InstalledFile) { $Have = (Get-Content $InstalledFile -Raw | ConvertFrom-Json).version }
        if ($Have -and -not (Test-VersionNewer $Have $Latest.version)) {
            Write-Host "SourceFidelity $Have is up to date (latest $($Latest.version))."
            exit 0
        }
        if (-not $Latest.version -or -not $Latest.url) { Write-Error "latest.json is missing version or url"; exit 1 }
        Write-Host "downloading SourceFidelity $($Latest.version)"
        $Pkg = Join-Path $Temp "pkg.tar.gz"
        Invoke-WebRequest -Uri $Latest.url -OutFile $Pkg -UseBasicParsing -TimeoutSec 120
        if ($Latest.sha256) {
            $Got = (Get-FileHash -Algorithm SHA256 -Path $Pkg).Hash.ToLower()
            if ($Got -ne $Latest.sha256.ToLower()) { Write-Error "checksum mismatch for $($Latest.url)"; exit 1 }
        } elseif (-not $AllowUnverified) {
            Write-Error "latest.json has no sha256; refusing an unverified update (pass -AllowUnverified deliberately)"
            exit 1
        }
        $Src = Join-Path $Temp "src"
        New-Item -ItemType Directory -Force -Path $Src | Out-Null
        & tar -xzf $Pkg -C $Src --strip-components=1
        if ($LASTEXITCODE -ne 0) { Write-Error "could not extract the release archive"; exit 1 }
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src "install.ps1") -Target $Target -NoVenv -Channel "stable"
        exit $LASTEXITCODE
    } finally {
        Remove-Item -Recurse -Force -Path $Temp -ErrorAction SilentlyContinue
    }
}

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

if (-not $DryRun) {
    $VersionFile = Join-Path $Here "VERSION"
    $Ver = if (Test-Path $VersionFile) { (Get-Content $VersionFile -Raw).Trim() } else { "unknown" }
    $Commit = ""
    try { $Commit = (& git -C $Here rev-parse --short HEAD 2>$null) } catch { }
    $StateDir = Join-Path $Target ".sourcefidelity"
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    [ordered]@{
        version = $Ver
        commit = $Commit
        channel = $Channel
        source = $Here
        installed_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    } | ConvertTo-Json | Set-Content -Path (Join-Path $StateDir "installed.json") -Encoding utf8
    Write-Host "recorded: v$Ver ($Channel)"
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
        Write-Host "would create: $venv (pypdf, pymupdf, zstandard, shared by all three skills)"
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
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Host "Tesseract OCR not found: image-only PDFs will be recorded as limitations."
    Write-Host "  Install it from https://github.com/tesseract-ocr/tesseract"
    $hintPython = "python"
    if ($venv) {
        # A real run that reached this line has a complete venv (an incomplete
        # one exits above), so the planned path is the one to show.
        $hintPython = Join-Path $venv "Scripts\python.exe"
    }
    Write-Host "  Then add language data, for example:"
    Write-Host "    `"$hintPython`" -I scripts/pdf_quality.py languages --install chi_sim"
}
Write-Host "Next: relaunch your harness and ask for a source-use review."

Write-Host "Optional: store a contact email and free API keys with"
Write-Host "  $VenvPython -I scripts/user_settings.py --prompt"
