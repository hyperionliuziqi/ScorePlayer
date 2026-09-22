$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $ProjectRoot ".build"
$Venv = Join-Path $BuildRoot "venv"
$StageHome = Join-Path $BuildRoot "runtime_home"
$Dist = Join-Path $ProjectRoot "dist"

Write-Host "== ScorePlayer v0.8.2 Windows portable build ==" -ForegroundColor Cyan

Remove-Item $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $BuildRoot, $StageHome | Out-Null

py -3.12 -m venv $Venv
$Python = Join-Path $Venv "Scripts\python.exe"
$Pip = Join-Path $Venv "Scripts\pip.exe"
$Homr = Join-Path $Venv "Scripts\homr.exe"

& $Python -m pip install --upgrade pip wheel setuptools

# Pin the HOMR release used by this build so future package updates do not
# unexpectedly change the portable build behavior.
& $Pip install "homr[cpu]==0.7.0" PySide6 pyinstaller numpy opencv-python-headless Pillow pytest

Write-Host ""
Write-Host "Installed runtime versions:" -ForegroundColor Cyan
& $Pip show homr numpy opencv-python-headless onnxruntime

# Force every model/cache download into a directory that will later be bundled.
$env:HOME = $StageHome
$env:USERPROFILE = $StageHome
$env:XDG_CACHE_HOME = Join-Path $StageHome ".cache"
$env:HF_HOME = Join-Path $StageHome ".cache\huggingface"
$env:TORCH_HOME = Join-Path $StageHome ".cache\torch"

# ---------------------------------------------------------------------------
# Real OMR smoke image
# ---------------------------------------------------------------------------
# The old build used an artificial one-staff drawing. HOMR is trained for real
# printed sheet music and that synthetic image could reach an internal
# singleton edge case ("numpy.int32 object is not iterable").
#
# Use HOMR's own public example score instead. This is only used during CI to
# pre-warm models and validate inference; it is not shipped as user content.
$WarmupImage = Join-Path $BuildRoot "homr-official-smoke.jpg"
$OfficialSmokeUrl = "https://raw.githubusercontent.com/liebharc/homr/main/figures/tabi.jpg"

Write-Host ""
Write-Host "Downloading HOMR official smoke-test score..." -ForegroundColor Yellow

try {
    Invoke-WebRequest `
        -Uri $OfficialSmokeUrl `
        -OutFile $WarmupImage `
        -UseBasicParsing
}
catch {
    Write-Warning "Could not download HOMR official smoke image: $($_.Exception.Message)"
    Write-Host "Falling back to locally generated warmup score..." -ForegroundColor Yellow

    & $Python (Join-Path $PSScriptRoot "make_warmup_score.py")
    $WarmupImage = Join-Path $ProjectRoot "fixtures\warmup_score.png"
}

if (!(Test-Path $WarmupImage)) {
    throw "OMR smoke-test image does not exist: $WarmupImage"
}

Write-Host "Smoke image: $WarmupImage"
Write-Host "Smoke image size: $((Get-Item $WarmupImage).Length) bytes"

# ---------------------------------------------------------------------------
# Pre-warm HOMR BEFORE packaging.
# ---------------------------------------------------------------------------
# This serves two purposes:
# 1. Downloads the actual model files into runtime_home.
# 2. Proves the plain HOMR installation can process a real score before we
#    spend several minutes building the EXE.
$WarmupWork = Join-Path $BuildRoot "homr-warmup"
New-Item -ItemType Directory -Force -Path $WarmupWork | Out-Null
$WarmupCopy = Join-Path $WarmupWork "smoke.jpg"
Copy-Item $WarmupImage $WarmupCopy -Force

Push-Location $WarmupWork
try {
    Write-Host ""
    Write-Host "Running HOMR pre-packaging smoke inference..." -ForegroundColor Yellow

    & $Homr $WarmupCopy
    $HomrExit = $LASTEXITCODE

    Write-Host "Plain HOMR smoke exit code: $HomrExit"

    if ($HomrExit -ne 0) {
        throw "Plain HOMR smoke inference failed before packaging with exit code $HomrExit."
    }

    $WarmupXml = Get-ChildItem -Path $WarmupWork -Filter "*.musicxml" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if ($null -eq $WarmupXml) {
        throw "Plain HOMR smoke inference returned success but produced no MusicXML."
    }

    Write-Host "Plain HOMR produced: $($WarmupXml.FullName)" -ForegroundColor Green
}
finally {
    Pop-Location
}

# From here on the build must work offline.
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

# ---------------------------------------------------------------------------
# Build portable EXE.
# ---------------------------------------------------------------------------
Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name ScorePlayer `
        --collect-all homr `
        --collect-all onnxruntime `
        --collect-all rapidocr `
        --collect-all PySide6 `
        --add-data "$StageHome;runtime_home" `
        --hidden-import homr.main `
        --paths $ProjectRoot `
        scoreplayer\main.py
}
finally {
    Pop-Location
}

$Exe = Join-Path $Dist "ScorePlayer\ScorePlayer.exe"

if (!(Test-Path $Exe)) {
    throw "ScorePlayer.exe was not produced."
}

# ---------------------------------------------------------------------------
# Run the ACTUAL packaged EXE and WAIT for it.
# ---------------------------------------------------------------------------
$PackagedReport = Join-Path $Dist "packaged-self-test.json"

Write-Host ""
Write-Host "Running packaged EXE offline self-test using real sheet music..." -ForegroundColor Yellow

if (Test-Path $PackagedReport) {
    Remove-Item $PackagedReport -Force
}

$SelfTestArgs = @(
    "--self-test",
    "--require-engine",
    "--omr-smoke", "`"$WarmupImage`"",
    "--self-test-report", "`"$PackagedReport`""
)

$SelfTestProcess = Start-Process `
    -FilePath $Exe `
    -ArgumentList $SelfTestArgs `
    -WorkingDirectory (Split-Path $Exe -Parent) `
    -Wait `
    -PassThru

Write-Host "Packaged EXE self-test exit code: $($SelfTestProcess.ExitCode)"

if (Test-Path $PackagedReport) {
    Write-Host ""
    Write-Host "Packaged self-test report:" -ForegroundColor Cyan
    Get-Content $PackagedReport
}

if ($SelfTestProcess.ExitCode -ne 0) {
    throw "Packaged ScorePlayer.exe failed offline self-test with exit code $($SelfTestProcess.ExitCode)."
}

if (!(Test-Path $PackagedReport)) {
    throw "Packaged ScorePlayer.exe exited without creating packaged-self-test.json."
}

$PackagedJson = Get-Content $PackagedReport -Raw | ConvertFrom-Json

if (-not $PackagedJson.passed) {
    throw "Packaged ScorePlayer.exe self-test did not pass."
}

if (-not $PackagedJson.checks.omr_engine_import) {
    throw "Packaged ScorePlayer.exe could not import HOMR/ONNX."
}

if (-not $PackagedJson.checks.omr_offline_inference) {
    throw "Packaged ScorePlayer.exe could import HOMR but failed real offline OMR inference."
}

Write-Host ""
Write-Host "Packaged EXE passed real offline OMR inference." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Release files.
# ---------------------------------------------------------------------------
Copy-Item `
    (Join-Path $ProjectRoot "README.md") `
    (Join-Path $Dist "ScorePlayer\README.md") `
    -ErrorAction SilentlyContinue

Copy-Item `
    (Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md") `
    (Join-Path $Dist "ScorePlayer\THIRD_PARTY_NOTICES.md") `
    -ErrorAction SilentlyContinue

Copy-Item `
    (Join-Path $ProjectRoot "SOURCE_OFFER.txt") `
    (Join-Path $Dist "ScorePlayer\SOURCE_OFFER.txt") `
    -ErrorAction SilentlyContinue

$Zip = Join-Path $Dist "ScorePlayer-Windows-x64-portable-v0.8.zip"

if (Test-Path $Zip) {
    Remove-Item $Zip -Force
}

Compress-Archive `
    -Path (Join-Path $Dist "ScorePlayer\*") `
    -DestinationPath $Zip `
    -CompressionLevel Optimal

Write-Host ""
Write-Host "Built: $Zip" -ForegroundColor Green
Write-Host "Unzip it and double-click ScorePlayer.exe." -ForegroundColor Green
