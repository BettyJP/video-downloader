"""PyInstaller build script for packaging `vdl` with Python runtime and external binaries.

Supports Windows (.zip) and Linux - Ubuntu / CentOS (.tar.gz) standalone bundles.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from vdl.utils.paths import find_ffmpeg  # noqa: E402


def detect_default_target_name() -> str:
    """Generates a default archive base name such as `vdl-windows-x86_64` or `vdl-linux-x86_64`."""
    sys_name = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine or "x86_64"

    if sys_name == "linux":
        # Check /etc/os-release to distinguish ubuntu / centos / rocky / almalinux
        os_release = Path("/etc/os-release")
        if os_release.is_file():
            content = os_release.read_text(encoding="utf-8", errors="ignore").lower()
            if "ubuntu" in content:
                sys_name = "ubuntu"
            elif any(k in content for k in ("centos", "almalinux", "rocky", "rhel")):
                sys_name = "centos"
    return f"vdl-{sys_name}-{arch}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build standalone vdl executable package with bundled Python runtime."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--onedir",
        action="store_true",
        default=True,
        help="Build a one-folder bundle containing vdl, Python runtime, and bin/ (default).",
    )
    mode_group.add_argument(
        "--onefile",
        action="store_true",
        help="Build a single self-extracting vdl executable.",
    )
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Create a distributable release archive (.zip on Windows, .tar.gz on Linux) in dist/releases/.",
    )
    parser.add_argument(
        "--target-name",
        default=None,
        help="Custom archive base name (e.g., vdl-windows-x86_64, vdl-centos-x86_64, vdl-ubuntu-x86_64).",
    )
    parser.add_argument(
        "--no-ffmpeg",
        action="store_true",
        help="Do not bundle ffmpeg even if found on the system.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean PyInstaller build/ and dist/vdl directories before building.",
    )
    return parser.parse_args()


def ensure_executable_permissions(path: Path) -> None:
    """Ensures a binary has +x permissions on POSIX systems."""
    if os.name != "nt" and path.is_file():
        current = path.stat().st_mode
        path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def create_release_archive(
    source_path: Path,
    releases_dir: Path,
    target_name: str,
) -> Path:
    """Packages the built output into a .zip (Windows) or .tar.gz (Linux) archive."""
    releases_dir.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        archive_path = releases_dir / f"{target_name}.zip"
        if archive_path.exists():
            archive_path.unlink()
        print(f"[build] Creating ZIP archive: {archive_path}")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if source_path.is_dir():
                for file_path in source_path.rglob("*"):
                    if file_path.is_file():
                        arcname = Path(target_name) / file_path.relative_to(source_path)
                        zf.write(file_path, arcname)
            else:
                zf.write(source_path, Path(target_name) / source_path.name)
        return archive_path

    archive_path = releases_dir / f"{target_name}.tar.gz"
    if archive_path.exists():
        archive_path.unlink()
    print(f"[build] Creating TAR.GZ archive: {archive_path}")
    with tarfile.open(archive_path, "w:gz") as tf:
        if source_path.is_dir():
            tf.add(source_path, arcname=target_name)
        else:
            tf.add(source_path, arcname=f"{target_name}/{source_path.name}")
    return archive_path


def main() -> int:
    args = parse_args()
    build_dir = PROJECT_ROOT / "build"
    dist_dir = PROJECT_ROOT / "dist"
    bin_dir = PROJECT_ROOT / "bin"
    bin_dir.mkdir(exist_ok=True)

    if args.clean:
        for d in (build_dir, dist_dir / "vdl"):
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
        "--collect-submodules",
        "rich._unicode_data",
        "--collect-all",
        "curl_cffi",
        "--copy-metadata",
        "vdl",
    ]

    if args.onefile:
        pyinstaller_cmd.append("--onefile")
    else:
        pyinstaller_cmd.append("--onedir")

    # Locate and bundle ffmpeg if available
    ffmpeg_path = None if args.no_ffmpeg else find_ffmpeg()
    if ffmpeg_path and ffmpeg_path.is_file():
        print(f"[build] Bundling ffmpeg from: {ffmpeg_path}")
        pyinstaller_cmd.extend(["--add-binary", f"{ffmpeg_path}{sep}bin"])
    elif not args.no_ffmpeg:
        print("[build] Warning: ffmpeg not found; building without bundled ffmpeg.")

    pyinstaller_cmd.append(str(entry_script))

    print(f"[build] Executing: {' '.join(pyinstaller_cmd)}")
    result = subprocess.run(pyinstaller_cmd, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        print("[build] PyInstaller build failed.", file=sys.stderr)
        return result.returncode

    exe_name = "vdl.exe" if os.name == "nt" else "vdl"
    if args.onefile:
        exe_out = dist_dir / exe_name
        ensure_executable_permissions(exe_out)
        bundle_root = exe_out
    else:
        bundle_root = dist_dir / "vdl"
        exe_out = bundle_root / exe_name
        ensure_executable_permissions(exe_out)
        # Also copy ffmpeg into dist/vdl/bin/ directly for easy access in --onedir mode
        if ffmpeg_path and ffmpeg_path.is_file():
            target_bin = bundle_root / "bin"
            target_bin.mkdir(parents=True, exist_ok=True)
            target_ffmpeg = target_bin / ffmpeg_path.name
            if not target_ffmpeg.exists():
                shutil.copy2(ffmpeg_path, target_ffmpeg)
            ensure_executable_permissions(target_ffmpeg)

    print(f"[build] Build succeeded! Output executable: {exe_out}")

    if args.archive:
        target_name = args.target_name or detect_default_target_name()
        releases_dir = dist_dir / "releases"
        archive_path = create_release_archive(bundle_root, releases_dir, target_name)
        print(f"[build] Release archive ready: {archive_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
