"""Configuration management for the Custom Video Downloader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vdl.utils.paths import configure_playwright_browsers_path, find_ffmpeg

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class DownloadConfig:
    """Configuration parameters for extraction and downloading."""

    output_template: str = "./downloads/%(title)s.%(ext)s"
    cookies_path: Path | None = None
    use_browser: bool = False
    browser_headless: bool = True
    browser_timeout_ms: int = 25000
    preferred_quality: str = "best"
    user_agent: str = DEFAULT_USER_AGENT
    referer: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    proxy: str | None = None
    concurrency: int = 8
    chunk_size: int = 1024 * 1024  # 1 MiB
    impersonate: str = "chrome"
    ffmpeg_path: Path | None = None
    no_ytdlp_fallback: bool = False
    write_thumbnail: bool = False
    skip_download: bool = False
    info_only: bool = False
    verbose: bool = False
    timeout_sec: float = 30.0

    def __post_init__(self) -> None:
        configure_playwright_browsers_path()
        if self.cookies_path and isinstance(self.cookies_path, str):
            self.cookies_path = Path(self.cookies_path)
        if self.ffmpeg_path and isinstance(self.ffmpeg_path, str):
            self.ffmpeg_path = Path(self.ffmpeg_path)
        if self.ffmpeg_path is None:
            self.ffmpeg_path = find_ffmpeg()

