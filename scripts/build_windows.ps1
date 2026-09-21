from pathlib import Path
import shutil, zipfile, re

src = Path("/mnt/data/score-player-desktop-v0.8")
dst = Path("/mnt/data/score-player-desktop-v0.8.1")
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)

script = dst / "scripts" / "build_windows.ps1"
s = script.read_text(encoding="utf-8")

old = r'''Write-Host "Running packaged EXE offline self-test..." -ForegroundColor Yellow
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
'''

new = r'''Write-Host "Running packaged EXE offline self-test..." -ForegroundColor Yellow

# ScorePlayer.exe is built with PyInstaller --windowed, so invoking it with "&"
# can return control to PowerShell before the GUI-subsystem process has actually
# finished. Use Start-Process -Wait so the build does not try to read the report
# before ScorePlayer.exe has written it.
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

if ($SelfTestProcess.ExitCode -ne 0) {
    if (Test-Path $PackagedReport) {
        Write-Host "Self-test report:"
        Get-Content $PackagedReport
    }
    throw "Packaged ScorePlayer.exe failed offline OMR self-test with exit code $($SelfTestProcess.ExitCode)."
}

if (!(Test-Path $PackagedReport)) {
    throw "Packaged ScorePlayer.exe exited successfully but did not create packaged-self-test.json. This usually means the self-test arguments were not processed correctly."
}

$PackagedJson = Get-Content $PackagedReport -Raw | ConvertFrom-Json
if (-not $PackagedJson.passed -or -not $PackagedJson.checks.omr_offline_inference) {
    Write-Host "Self-test report:"
    Get-Content $PackagedReport
    throw "Packaged ScorePlayer.exe did not pass offline OMR inference."
}
'''

if old not in s:
    raise RuntimeError("Expected self-test block not found")
s = s.replace(old, new)

# Make version label clearer in build output without changing artifact name.
s = s.replace('== ScorePlayer v0.8 Windows portable build ==',
              '== ScorePlayer v0.8.1 Windows portable build ==')

script.write_text(s, encoding="utf-8")

fix_note = dst / "FIX_0.8.1.md"
fix_note.write_text(
"""# v0.8.1 build fix

GitHub Actions 已经成功完成 PyInstaller 打包。

失败点发生在“打包后的 EXE 离线自检”阶段：`ScorePlayer.exe` 是 `--windowed` GUI 程序，
PowerShell 用 `& $Exe ...` 启动后可能在 EXE 真正结束前就继续执行，因此立即读取
`packaged-self-test.json`，导致“文件不存在”。

v0.8.1 修改为：

- `Start-Process -Wait -PassThru`
- 明确等待真正的 `ScorePlayer.exe` 自检结束
- 检查 EXE 的真实 ExitCode
- 报告不存在时给出明确错误，而不是直接 `Get-Content` 崩溃
- 自检失败时打印已有报告

不需要修改 GitHub Actions workflow；只需替换 `scripts/build_windows.ps1` 后重新运行 workflow。
""", encoding="utf-8")

# Also create a standalone replacement script for easiest GitHub upload/edit.
standalone = Path("/mnt/data/build_windows-fixed-v0.8.1.ps1")
shutil.copy2(script, standalone)

zip_path = Path("/mnt/data/ScorePlayer-Desktop-v0.8.1-build-fix.zip")
if zip_path.exists():
    zip_path.unlink()
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in dst.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            z.write(p, arcname=f"ScorePlayer-Desktop-v0.8.1/{p.relative_to(dst)}")

print("Created:", standalone)
print("Created:", zip_path)
