"""Path resolution helpers supporting both development and PyInstaller frozen environments."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vdl.models import MediaInfo

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_frozen() -> bool:
    """Returns True when running inside a PyInstaller/Nuitka frozen bundle."""
    return bool(getattr(sys, "frozen", False))


def get_base_dir() -> Path:
    """Returns the application base directory (handles PyInstaller _MEIPASS and onedir)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    # Project root: src/vdl/utils/paths.py -> parents[3]
    return Path(__file__).resolve().parents[3]


def get_executable_dir() -> Path:
    """Returns the directory containing the running executable or project root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return get_base_dir()


def get_bin_dir() -> Path:
    """Returns the bundled `bin/` directory path."""
    # Check next to the executable first (for --onedir or external bin/), then _MEIPASS
    exe_bin = get_executable_dir() / "bin"
    if exe_bin.exists():
        return exe_bin
    return get_base_dir() / "bin"


def find_ffmpeg(custom_path: str | Path | None = None) -> Path | None:
    """Locates the ffmpeg executable, prioritizing bundled bin/ffmpeg(.exe)."""
    if custom_path:
        custom = Path(custom_path)
        if custom.is_file():
            return custom.resolve()

    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

    # 1. Check bundled bin/ directory (both _MEIPASS/bin and executable_dir/bin)
    candidates = [
        get_bin_dir() / exe_name,
        get_base_dir() / "bin" / exe_name,
        get_executable_dir() / exe_name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    # 2. Check system PATH
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return Path(which_ffmpeg).resolve()

    # 3. Check WinGet default installation path on Windows
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            if winget_pkgs.is_dir():
                for match in winget_pkgs.glob("*FFmpeg*/*/bin/ffmpeg.exe"):
                    if match.is_file():
                        return match.resolve()

    return None


def configure_playwright_browsers_path() -> None:
    """Configures PLAYWRIGHT_BROWSERS_PATH if bundled browsers exist in bin/browsers."""
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        return
    for candidate in (get_bin_dir() / "browsers", get_executable_dir() / "browsers"):
        if candidate.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate.resolve())
            break


def sanitize_filename(name: str, max_length: int = 180) -> str:
    """Sanitizes a string to be safe for use as a file name on Windows/POSIX."""
    if not name:
        return "video"
    cleaned = INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "video"
    return cleaned[:max_length].rstrip(" .")


def format_output_path(
    output_template: str,
    media_info: MediaInfo,
    ext: str | None = None,
) -> Path:
    """Formats an output file path supporting yt-dlp style templates like %(title)s.%(ext)s."""
    final_ext = (ext or "mp4").lstrip(".")
    safe_title = sanitize_filename(media_info.title)
    safe_id = sanitize_filename(media_info.video_id or "video")
    safe_extractor = sanitize_filename(media_info.extractor or "generic")

    template_vars = {
        "title": safe_title,
        "id": safe_id,
        "extractor": safe_extractor,
        "ext": final_ext,
    }

    # If the user passed an existing directory path without template placeholders
    raw_path = Path(output_template)
    if output_template.endswith(("/", "\\")) or (raw_path.exists() and raw_path.is_dir()):
        return raw_path / f"{safe_title}.{final_ext}"

    if "%(" in output_template:
        try:
            rendered = output_template % template_vars
        except (KeyError, ValueError):
            rendered = output_template
            for key, val in template_vars.items():
                rendered = rendered.replace(f"%({key})s", val)
        path = Path(rendered)
    else:
        path = raw_path
        if not path.suffix:
            path = path.with_suffix(f".{final_ext}")

    return path

