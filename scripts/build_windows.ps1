$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $ProjectRoot ".build"
$Venv = Join-Path $BuildRoot "venv"
$StageHome = Join-Path $BuildRoot "runtime_home"
$Dist = Join-Path $ProjectRoot "dist"

Write-Host "== ScorePlayer v0.8 Windows portable build ==" -ForegroundColor Cyan

Remove-Item $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $BuildRoot, $StageHome | Out-Null

# Use the Python already present on the Windows build machine.
py -3.12 -m venv $Venv
$Python = Join-Path $Venv "Scripts\python.exe"
$Pip = Join-Path $Venv "Scripts\pip.exe"

& $Python -m pip install --upgrade pip wheel setuptools
& $Pip install "homr[cpu]" PySide6 pyinstaller numpy opencv-python-headless Pillow pytest

# Force model downloads/caches into a folder that will later be packed into the app.
$env:HOME = $StageHome
$env:USERPROFILE = $StageHome
$env:XDG_CACHE_HOME = Join-Path $StageHome ".cache"
$env:HF_HOME = Join-Path $StageHome ".cache\huggingface"
$env:TORCH_HOME = Join-Path $StageHome ".cache\torch"

# Generate a simple staff fixture so the build can warm the actual HOMR model.
& $Python (Join-Path $PSScriptRoot "make_warmup_score.py")

$WarmupImage = Join-Path $ProjectRoot "fixtures\warmup_score.png"
Push-Location $BuildRoot
try {
    Write-Host "Warming HOMR models (network is allowed only at build time)..." -ForegroundColor Yellow
    & $Venv\Scripts\homr.exe $WarmupImage
}
finally {
    Pop-Location
}

# Verify the runtime can now start with network explicitly disabled.
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
& $Python (Join-Path $PSScriptRoot "self_test.py") --require-engine

# Build one-folder portable app. The user only starts ScorePlayer.exe.
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

# Critical release gate: execute the ACTUAL packaged EXE with network disabled.
# This proves that the packaged program can import HOMR/ONNX and run one real
# offline OMR inference using the bundled model cache.
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$PackagedReport = Join-Path $Dist "packaged-self-test.json"

Write-Host "Running packaged EXE offline self-test..." -ForegroundColor Yellow
& $Exe `
    --self-test `
    --require-engine `
    --omr-smoke $WarmupImage `
    --self-test-report $PackagedReport

if ($LASTEXITCODE -ne 0) {
    if (Test-Path $PackagedReport) { Get-Content $PackagedReport }
    throw "Packaged ScorePlayer.exe failed offline OMR self-test."
}

$PackagedJson = Get-Content $PackagedReport -Raw | ConvertFrom-Json
if (-not $PackagedJson.passed -or -not $PackagedJson.checks.omr_offline_inference) {
    Get-Content $PackagedReport
    throw "Packaged ScorePlayer.exe did not pass offline OMR inference."
}

# Copy source/license notice into the portable folder for AGPL compliance.
Copy-Item (Join-Path $ProjectRoot "README.md") (Join-Path $Dist "ScorePlayer\README.md")
Copy-Item (Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md") (Join-Path $Dist "ScorePlayer\THIRD_PARTY_NOTICES.md")
Copy-Item (Join-Path $ProjectRoot "SOURCE_OFFER.txt") (Join-Path $Dist "ScorePlayer\SOURCE_OFFER.txt")

# ZIP final portable package.
$Zip = Join-Path $Dist "ScorePlayer-Windows-x64-portable-v0.8.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path (Join-Path $Dist "ScorePlayer\*") -DestinationPath $Zip -CompressionLevel Optimal

Write-Host ""
Write-Host "Built: $Zip" -ForegroundColor Green
Write-Host "End users only need to unzip and double-click ScorePlayer.exe." -ForegroundColor Green
