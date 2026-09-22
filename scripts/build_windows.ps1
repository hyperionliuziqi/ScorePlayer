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

# ---------------------------------------------------------------------------
# v0.8.6 one-file hotfix
# ---------------------------------------------------------------------------
# This build script patches the two Python files that caused the packaged OMR
# self-test failure, so the user only has to replace THIS ONE FILE in GitHub.
Write-Host "Applying ScorePlayer v0.8.6 source hotfix..." -ForegroundColor Yellow

$PatchedOmr = @'
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable

from scoreplayer.preprocess import preprocess_page


ProgressCallback = Callable[[str], None]


def _runtime_root() -> Path:
    # PyInstaller one-dir: sys._MEIPASS points to _internal.
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))


def configure_offline_model_home() -> Path:
    root = _runtime_root()
    home = root / "runtime_home"
    home.mkdir(parents=True, exist_ok=True)

    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    os.environ["HF_HOME"] = str(home / ".cache" / "huggingface")
    os.environ["TORCH_HOME"] = str(home / ".cache" / "torch")
    return home


def homr_available() -> tuple[bool, str]:
    try:
        import homr  # noqa: F401
        import onnxruntime  # noqa: F401
        return True, "HOMR + ONNX Runtime 已加载"
    except Exception as exc:
        return False, f"HOMR 引擎不可用：{exc}"


def _call_homr_cli(images: list[Path], work_dir: Path) -> Path:
    """
    Invoke HOMR exactly like its CLI, but inside the packaged application.

    All input images passed here should already be copied into work_dir so
    HOMR also writes its MusicXML there.
    """
    configure_offline_model_home()

    from homr.main import main as homr_main

    before = {p.resolve() for p in work_dir.glob("*.musicxml")}
    old_cwd = Path.cwd()
    old_argv = sys.argv[:]

    try:
        os.chdir(work_dir)
        sys.argv = ["homr", *[str(p) for p in images]]
        try:
            homr_main()
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise RuntimeError(f"HOMR 退出码：{exc.code}") from exc
    finally:
        os.chdir(old_cwd)
        sys.argv = old_argv

    candidates = [
        p for p in work_dir.glob("*.musicxml")
        if p.resolve() not in before
    ]
    if not candidates:
        candidates = list(work_dir.glob("*.musicxml"))

    if not candidates:
        raise RuntimeError("HOMR 执行结束，但没有生成 MusicXML 文件。")

    return max(candidates, key=lambda p: p.stat().st_mtime)


def recognize_pages(
    image_paths: list[str | Path],
    output_musicxml: str | Path,
    progress: ProgressCallback | None = None,
) -> tuple[Path, list[dict]]:
    """
    Recognize one or more score pages.

    v0.8.5 robustness change:
    1. Try ScorePlayer's normalized/preprocessed pages first.
    2. If HOMR throws on those pages, automatically retry the original pages
       copied byte-for-byte into the temp work directory.

    This is important because HOMR already contains its own staff/geometry
    normalization and some scores are actually easier for HOMR in their
    original form.
    """
    if not image_paths:
        raise ValueError("至少需要一张曲谱图片。")

    ok, msg = homr_available()
    if not ok:
        raise RuntimeError(msg)

    progress = progress or (lambda _msg: None)
    output_musicxml = Path(output_musicxml)
    output_musicxml.parent.mkdir(parents=True, exist_ok=True)

    preprocess_report: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="scoreplayer_omr_") as tmp:
        work = Path(tmp)
        prepared: list[Path] = []
        raw_copies: list[Path] = []

        for idx, src_value in enumerate(image_paths, start=1):
            src = Path(src_value)
            if not src.exists():
                raise FileNotFoundError(f"曲谱图片不存在：{src}")

            # Always make a raw copy inside the work directory. This gives HOMR
            # a stable ASCII-ish path and guarantees its output lands nearby.
            suffix = src.suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                suffix = ".png"

            raw_dst = work / f"raw_page_{idx:03d}{suffix}"
            shutil.copy2(src, raw_dst)
            raw_copies.append(raw_dst)

            prep_dst = work / f"prepared_page_{idx:03d}.png"
            progress(f"预处理第 {idx}/{len(image_paths)} 页…")

            try:
                info = preprocess_page(src, prep_dst)
                preprocess_report.append({
                    "source": info.source,
                    "crop_box": list(info.crop_box),
                    "skew_degrees": info.skew_degrees,
                    "staff_spacing_px": info.staff_spacing_px,
                    "scale": info.scale,
                    "preprocess_ok": True,
                })
                prepared.append(prep_dst)
            except Exception as exc:
                # A preprocessing error must not prevent HOMR from trying the
                # original page.
                preprocess_report.append({
                    "source": str(src),
                    "preprocess_ok": False,
                    "preprocess_error": str(exc),
                })

        prepared_error = None

        if len(prepared) == len(raw_copies):
            try:
                progress("AI 正在识别预处理后的曲谱…")
                result = _call_homr_cli(prepared, work)
                for item in preprocess_report:
                    item["omr_input_mode"] = "preprocessed"
                shutil.copy2(result, output_musicxml)
                progress("识别完成。")
                return output_musicxml, preprocess_report
            except Exception as exc:
                prepared_error = f"{type(exc).__name__}: {exc}"
                progress("预处理版本识别失败，正在自动改用原始图片重试…")

        # Robust fallback: raw page copies.
        try:
            result = _call_homr_cli(raw_copies, work)
            for item in preprocess_report:
                item["omr_input_mode"] = "raw-fallback"
                if prepared_error:
                    item["prepared_omr_error"] = prepared_error
            shutil.copy2(result, output_musicxml)
            progress("识别完成（已使用原始图片回退模式）。")
            return output_musicxml, preprocess_report
        except Exception as raw_exc:
            raw_trace = traceback.format_exc()
            message = (
                "HOMR 对预处理图片和原始图片都识别失败。\n"
                f"预处理版本错误：{prepared_error or '未运行'}\n"
                f"原始图片错误：{type(raw_exc).__name__}: {raw_exc}\n"
                "原始图片 traceback：\n"
                f"{raw_trace}"
            )
            raise RuntimeError(message) from raw_exc
'@
$PatchedSelfTest = @'
from __future__ import annotations

import json
import tempfile
import traceback
from pathlib import Path

import cv2
import numpy as np

from scoreplayer.diagnostics import analyze_score
from scoreplayer.layout import analyze_page
from scoreplayer.midi import export_midi
from scoreplayer.musicxml import parse_musicxml
from scoreplayer.omr import homr_available, recognize_pages
from scoreplayer.synth import render_wav


SMOKE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>4</divisions><staves>2</staves></attributes>
      <direction><sound tempo="120"/></direction>
      <barline location="left"><repeat direction="forward"/></barline>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><staff>1</staff></note>
      <note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><staff>1</staff></note>
      <note><chord/><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><staff>1</staff></note>
    </measure>
    <measure number="2">
      <note><pitch><step>D</step><alter>1</alter><octave>4</octave></pitch><duration>4</duration><voice>1</voice><staff>1</staff></note>
      <barline location="right"><repeat direction="backward" times="2"/></barline>
    </measure>
    <measure number="3">
      <note><pitch><step>F</step><octave>4</octave></pitch><duration>8</duration><voice>1</voice><staff>1</staff></note>
    </measure>
  </part>
</score-partwise>
"""


def run_self_test(
    require_engine: bool = False,
    omr_smoke_image: str | None = None,
    report_path: str | None = None,
) -> int:
    result: dict = {
        "version": "0.8.5",
        "checks": {},
        "details": {},
    }

    with tempfile.TemporaryDirectory(prefix="scoreplayer_selftest_") as td:
        td = Path(td)

        xml = td / "smoke.musicxml"
        xml.write_text(SMOKE_XML, encoding="utf-8")

        score = parse_musicxml(xml)
        result["checks"]["musicxml"] = bool(score.events)
        result["checks"]["repeat_route"] = score.measure_order[:5] == [1, 2, 1, 2, 3]
        result["checks"]["chord"] = len(
            [e for e in score.events if abs(e.beat) < 1e-9]
        ) >= 3
        result["checks"]["diagnostics"] = isinstance(analyze_score(score), list)

        wav = render_wav(
            score,
            td / "smoke.wav",
            bpm_override=120,
            sample_rate=8000,
        )
        mid = export_midi(score, td / "smoke.mid", bpm=120)
        result["checks"]["wav"] = wav.exists() and wav.stat().st_size > 1000
        result["checks"]["midi"] = (
            mid.exists() and mid.read_bytes().startswith(b"MThd")
        )

        # Page-follow geometry smoke test.
        layout_image = np.full((520, 900, 3), 255, dtype=np.uint8)
        for staff_y in (120, 212):
            for line in range(5):
                y = staff_y + line * 8
                cv2.line(layout_image, (80, y), (820, y), (0, 0, 0), 2)
        for x in (80, 330, 575, 820):
            cv2.line(layout_image, (x, 120), (x, 244), (0, 0, 0), 2)

        layout_path = td / "layout-smoke.png"
        cv2.imwrite(str(layout_path), layout_image)
        page_geometry = analyze_page(layout_path)
        result["checks"]["layout_systems"] = len(page_geometry.systems) == 1
        result["checks"]["layout_measures"] = (
            bool(page_geometry.systems)
            and page_geometry.systems[0].detected_measure_count == 3
        )

        engine_ok, engine_msg = homr_available()
        result["checks"]["omr_engine_import"] = engine_ok
        result["details"]["engine"] = engine_msg

        if omr_smoke_image:
            try:
                omr_out = td / "omr_smoke.musicxml"
                _path, omr_report = recognize_pages(
                    [omr_smoke_image],
                    omr_out,
                )
                result["checks"]["omr_offline_inference"] = (
                    omr_out.exists() and omr_out.stat().st_size > 100
                )
                result["details"]["omr_output_size"] = (
                    omr_out.stat().st_size if omr_out.exists() else 0
                )
                result["details"]["omr_preprocess_report"] = omr_report
            except Exception as exc:
                result["checks"]["omr_offline_inference"] = False
                result["details"]["omr_error"] = str(exc)
                # v0.8.5: include the full traceback so future CI failures are
                # diagnosable instead of only showing the last exception line.
                result["details"]["omr_traceback"] = traceback.format_exc()

    core_keys = [
        "musicxml",
        "repeat_route",
        "chord",
        "diagnostics",
        "wav",
        "midi",
        "layout_systems",
        "layout_measures",
    ]
    ok = all(result["checks"].get(k, False) for k in core_keys)

    if require_engine:
        ok = ok and result["checks"].get("omr_engine_import", False)
        if omr_smoke_image:
            ok = ok and result["checks"].get("omr_offline_inference", False)

    result["passed"] = bool(ok)

    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return 0 if ok else 3
'@

[System.IO.File]::WriteAllText(
    (Join-Path $ProjectRoot "scoreplayer\omr.py"),
    $PatchedOmr,
    (New-Object System.Text.UTF8Encoding($false))
)

[System.IO.File]::WriteAllText(
    (Join-Path $ProjectRoot "scoreplayer\selftest.py"),
    $PatchedSelfTest,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host "Source hotfix applied." -ForegroundColor Green


Write-Host "== ScorePlayer v0.8.6 one-file Windows portable build ==" -ForegroundColor Cyan

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
