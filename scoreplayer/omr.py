from __future__ import annotations
import contextlib
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

from scoreplayer.preprocess import preprocess_page


ProgressCallback = Callable[[str], None]


def _runtime_root() -> Path:
    # PyInstaller one-dir: sys._MEIPASS points to _internal.
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return base


def configure_offline_model_home() -> Path:
    """
    Force all likely model/cache locations into the portable application folder.
    The Windows build script pre-warms HOMR with the same HOME variables and then
    packs runtime_home into the executable's _internal directory.
    """
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
    configure_offline_model_home()

    from homr.main import main as homr_main

    before = {p.resolve() for p in work_dir.glob("*.musicxml")}
    old_cwd = Path.cwd()
    old_argv = sys.argv[:]

    try:
        os.chdir(work_dir)
        sys.argv = ["homr", *[str(p) for p in images]]
        # HOMR is a CLI entry point; it may print progress and may raise SystemExit.
        try:
            homr_main()
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise RuntimeError(f"HOMR 退出码：{exc.code}") from exc
    finally:
        os.chdir(old_cwd)
        sys.argv = old_argv

    candidates = [p for p in work_dir.glob("*.musicxml") if p.resolve() not in before]
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

        for idx, src in enumerate(image_paths, start=1):
            dst = work / f"page_{idx:03d}.png"
            progress(f"预处理第 {idx}/{len(image_paths)} 页…")
            info = preprocess_page(src, dst)
            preprocess_report.append({
                "source": info.source,
                "crop_box": list(info.crop_box),
                "skew_degrees": info.skew_degrees,
                "staff_spacing_px": info.staff_spacing_px,
                "scale": info.scale,
            })
            prepared.append(dst)

        progress("AI 正在识别五线谱、音高、和弦与时值…")
        result = _call_homr_cli(prepared, work)
        shutil.copy2(result, output_musicxml)

    progress("识别完成。")
    return output_musicxml, preprocess_report
