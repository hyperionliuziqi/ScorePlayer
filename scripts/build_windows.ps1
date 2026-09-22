$ErrorActionPreference = "Stop"

# On GitHub Actions (PowerShell 7), make failed native commands fail the step.
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $true
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $ProjectRoot ".build"
$Venv = Join-Path $BuildRoot "venv"
$StageHome = Join-Path $BuildRoot "runtime_home"
$Dist = Join-Path $ProjectRoot "dist"

Write-Host "== ScorePlayer v0.8.4 Windows portable build ==" -ForegroundColor Cyan

Remove-Item $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $BuildRoot, $StageHome | Out-Null

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------
py -3.12 -m venv $Venv

$Python = Join-Path $Venv "Scripts\python.exe"
$Pip = Join-Path $Venv "Scripts\pip.exe"

if (!(Test-Path $Python)) {
    throw "Python venv was not created."
}

& $Python -m pip install --upgrade pip wheel setuptools
if ($LASTEXITCODE -ne 0) {
    throw "pip/wheel/setuptools upgrade failed."
}

# IMPORTANT:
# Install HOMR by itself first. HOMR 0.7.0 declares numpy, cv2
# (opencv-python-headless), rapidocr and the inference backend dependencies.
# The previous script installed many packages in one command and then continued
# even when pip failed, which is why cv2 and homr.exe were missing.
Write-Host ""
Write-Host "Installing HOMR CPU runtime..." -ForegroundColor Yellow

& $Pip install "homr[cpu]==0.7.0"
if ($LASTEXITCODE -ne 0) {
    throw "Installing homr[cpu]==0.7.0 failed."
}

Write-Host ""
Write-Host "Installing ScorePlayer build/UI dependencies..." -ForegroundColor Yellow

& $Pip install PySide6 pyinstaller Pillow pytest
if ($LASTEXITCODE -ne 0) {
    throw "Installing ScorePlayer build dependencies failed."
}

# Do not trust package-manager output alone. Verify the exact imports our app
# and HOMR need before continuing.
Write-Host ""
Write-Host "Verifying Python runtime imports..." -ForegroundColor Yellow

$ImportCheck = @'
import sys
import homr
import cv2
import numpy
import onnxruntime
import rapidocr
from PIL import Image
print("Python:", sys.version)
print("HOMR:", getattr(homr, "__version__", "installed"))
print("OpenCV:", cv2.__version__)
print("NumPy:", numpy.__version__)
print("ONNX Runtime:", onnxruntime.__version__)
print("Runtime import check: OK")
'@

$ImportCheckPath = Join-Path $BuildRoot "check_runtime.py"
[System.IO.File]::WriteAllText(
    $ImportCheckPath,
    $ImportCheck,
    (New-Object System.Text.UTF8Encoding($false))
)

& $Python $ImportCheckPath
if ($LASTEXITCODE -ne 0) {
    throw "HOMR runtime import check failed. The log above shows the missing dependency."
}

Write-Host ""
Write-Host "Installed package versions:" -ForegroundColor Cyan
& $Pip show homr numpy opencv-python-headless onnxruntime rapidocr
if ($LASTEXITCODE -ne 0) {
    throw "pip show runtime packages failed."
}

# ---------------------------------------------------------------------------
# Portable model/cache home
# ---------------------------------------------------------------------------
$env:HOME = $StageHome
$env:USERPROFILE = $StageHome
$env:XDG_CACHE_HOME = Join-Path $StageHome ".cache"
$env:HF_HOME = Join-Path $StageHome ".cache\huggingface"
$env:TORCH_HOME = Join-Path $StageHome ".cache\torch"

# ---------------------------------------------------------------------------
# Real public-domain sheet music used only as a CI smoke test
# ---------------------------------------------------------------------------
$WarmupImage = Join-Path $BuildRoot "omr-real-score.png"

$SmokeUrls = @(
    "https://commons.wikimedia.org/wiki/Special:Redirect/file/RondoAllaTurcaMozart.png",
    "https://commons.wikimedia.org/wiki/Special:Redirect/file/Beethoven%20canon%20from%20op%20101.png"
)

$Downloaded = $false

foreach ($Url in $SmokeUrls) {
    try {
        Write-Host ""
        Write-Host "Downloading OMR smoke-test score:" -ForegroundColor Yellow
        Write-Host $Url

        Invoke-WebRequest `
            -Uri $Url `
            -OutFile $WarmupImage `
            -MaximumRedirection 10 `
            -UseBasicParsing

        if ((Test-Path $WarmupImage) -and ((Get-Item $WarmupImage).Length -gt 10000)) {
            $Downloaded = $true
            break
        }
    }
    catch {
        Write-Warning "Smoke image download failed: $($_.Exception.Message)"
    }
}

if (-not $Downloaded) {
    throw "Could not download a valid public-domain sheet-music smoke image."
}

Write-Host "Smoke image size: $((Get-Item $WarmupImage).Length) bytes"

# Validate with Pillow rather than cv2. The import check above already proves
# cv2 exists; Pillow gives us a simple independent check that the downloaded
# file is actually an image rather than an HTML error page.
$ValidateImageCode = @'
from PIL import Image
import sys
p = sys.argv[1]
with Image.open(p) as im:
    im.verify()
with Image.open(p) as im:
    w, h = im.size
    print(f"Decoded smoke image: {w}x{h}, format={im.format}")
    if w < 300 or h < 150:
        raise SystemExit("Smoke image is unexpectedly small")
'@

$ValidateImageScript = Join-Path $BuildRoot "validate_smoke.py"
[System.IO.File]::WriteAllText(
    $ValidateImageScript,
    $ValidateImageCode,
    (New-Object System.Text.UTF8Encoding($false))
)

& $Python $ValidateImageScript $WarmupImage
if ($LASTEXITCODE -ne 0) {
    throw "Downloaded smoke-test file is not a usable image."
}

# ---------------------------------------------------------------------------
# Pre-package HOMR inference
# ---------------------------------------------------------------------------
# Invoke HOMR through Python instead of assuming Scripts\homr.exe exists.
# This avoids console-entry-point/path differences on Windows.
$WarmupWork = Join-Path $BuildRoot "homr-warmup"
New-Item -ItemType Directory -Force -Path $WarmupWork | Out-Null

$WarmupCopy = Join-Path $WarmupWork "smoke.png"
Copy-Item $WarmupImage $WarmupCopy -Force

Push-Location $WarmupWork
try {
    Write-Host ""
    Write-Host "Running plain HOMR on real printed piano score..." -ForegroundColor Yellow

    & $Python -c "from homr.main import main; main()" $WarmupCopy
    $HomrExit = $LASTEXITCODE

    Write-Host "Plain HOMR smoke exit code: $HomrExit"

    if ($HomrExit -ne 0) {
        throw "Plain HOMR failed before packaging with exit code $HomrExit."
    }

    $WarmupXml = Get-ChildItem -Path $WarmupWork -Filter "*.musicxml" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if ($null -eq $WarmupXml) {
        throw "Plain HOMR returned success but produced no MusicXML."
    }

    if ($WarmupXml.Length -lt 100) {
        throw "Plain HOMR produced an unexpectedly small MusicXML file."
    }

    Write-Host "Plain HOMR produced: $($WarmupXml.FullName)" -ForegroundColor Green
    Write-Host "MusicXML size: $($WarmupXml.Length) bytes"
}
finally {
    Pop-Location
}

# Model download/cache must be complete now.
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

# ---------------------------------------------------------------------------
# Build portable EXE
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

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

$Exe = Join-Path $Dist "ScorePlayer\ScorePlayer.exe"

if (!(Test-Path $Exe)) {
    throw "ScorePlayer.exe was not produced."
}

Write-Host "PyInstaller produced ScorePlayer.exe." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Test the actual packaged EXE offline
# ---------------------------------------------------------------------------
$PackagedReport = Join-Path $Dist "packaged-self-test.json"

Write-Host ""
Write-Host "Running packaged EXE offline OMR self-test..." -ForegroundColor Yellow

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
    throw "Packaged ScorePlayer.exe imported HOMR but failed real offline OMR inference."
}

Write-Host ""
Write-Host "Packaged EXE passed real offline OMR inference." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Release portable ZIP
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

if (!(Test-Path $Zip)) {
    throw "Portable ZIP was not created."
}

Write-Host ""
Write-Host "SUCCESS" -ForegroundColor Green
Write-Host "Built: $Zip" -ForegroundColor Green
Write-Host "Unzip it and double-click ScorePlayer.exe." -ForegroundColor Green
