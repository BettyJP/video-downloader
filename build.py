"""PyInstaller build script for packaging `vdl` with Python runtime and external binaries."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from vdl.utils.paths import find_ffmpeg  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build standalone vdl executable package with bundled Python runtime."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--onedir",
        action="store_true",
        default=True,
        help="Build a one-folder bundle containing vdl.exe, Python runtime, and bin/ (default).",
    )
    mode_group.add_argument(
        "--onefile",
        action="store_true",
        help="Build a single self-extracting vdl.exe executable.",
    )
    parser.add_argument(
        "--no-ffmpeg",
        action="store_true",
        help="Do not bundle ffmpeg.exe even if found on the system.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean PyInstaller build/ and dist/ directories before building.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_dir = PROJECT_ROOT / "build"
    dist_dir = PROJECT_ROOT / "dist"
    bin_dir = PROJECT_ROOT / "bin"
    bin_dir.mkdir(exist_ok=True)

    if args.clean:
        for d in (build_dir, dist_dir):
            if d.exists():
                print(f"[build] Removing {d} ...")
                shutil.rmtree(d)

    entry_script = SRC_DIR / "vdl" / "__main__.py"
    sep = ";" if os.name == "nt" else ":"

    pyinstaller_cmd: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        "vdl",
        "--paths",
        str(SRC_DIR),
        "--hidden-import",
        "vdl",
        "--hidden-import",
        "bs4",
        "--hidden-import",
        "lxml",
        "--hidden-import",
        "httpx",
        "--hidden-import",
        "curl_cffi",
        "--hidden-import",
        "yt_dlp",
        "--hidden-import",
        "playwright.sync_api",
    ]

    if args.onefile:
        pyinstaller_cmd.append("--onefile")
    else:
        pyinstaller_cmd.append("--onedir")

    # Locate and bundle ffmpeg if available
    if not args.no_ffmpeg:
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path and ffmpeg_path.is_file():
            print(f"[build] Bundling ffmpeg from: {ffmpeg_path}")
            pyinstaller_cmd.extend(["--add-binary", f"{ffmpeg_path}{sep}bin"])
        else:
            print("[build] Warning: ffmpeg not found; building without bundled ffmpeg.")

    pyinstaller_cmd.append(str(entry_script))

    print(f"[build] Executing: {' '.join(pyinstaller_cmd)}")
    result = subprocess.run(pyinstaller_cmd, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        print("[build] PyInstaller build failed.", file=sys.stderr)
        return result.returncode

    if args.onefile:
        exe_out = dist_dir / ("vdl.exe" if os.name == "nt" else "vdl")
    else:
        exe_out = dist_dir / "vdl" / ("vdl.exe" if os.name == "nt" else "vdl")
        # Also copy ffmpeg into dist/vdl/bin/ directly for easy access in --onedir mode
        if not args.no_ffmpeg:
            ffmpeg_path = find_ffmpeg()
            if ffmpeg_path and ffmpeg_path.is_file():
                target_bin = dist_dir / "vdl" / "bin"
                target_bin.mkdir(parents=True, exist_ok=True)
                target_ffmpeg = target_bin / ffmpeg_path.name
                if not target_ffmpeg.exists():
                    shutil.copy2(ffmpeg_path, target_ffmpeg)

    print(f"[build] Build succeeded! Output executable: {exe_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

