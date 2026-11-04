"""Data models for media information and stream formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StreamType(str, Enum):
    """Supported video stream delivery protocols."""

    HTTP = "http"      # Direct progressive download (.mp4, .webm, etc.)
    HLS = "hls"        # HTTP Live Streaming (.m3u8)
    DASH = "dash"      # Dynamic Adaptive Streaming over HTTP (.mpd)
    YTDLP = "ytdlp"    # Delegated to yt-dlp engine


@dataclass
class StreamFormat:
    """Represents a single downloadable media stream variant."""

    url: str
    stream_type: StreamType = StreamType.HTTP
    format_id: str = "default"
    ext: str = "mp4"
    quality: str = "unknown"
    width: int | None = None
    height: int | None = None
    bandwidth: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    encryption_key_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def resolution_label(self) -> str:
        """Human-readable resolution or quality label."""
        if self.height:
            return f"{self.height}p"
        if self.quality and self.quality != "unknown":
            return self.quality
        return self.format_id

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        """Sort key ranking highest resolution and highest bitrate first."""
        h = self.height or 0
        w = self.width or 0
        pixels = (w * h) if (w and h) else (h * h * 16 // 9)
        bw = self.bandwidth or 0
        type_priority = {
            StreamType.HTTP: 3,
            StreamType.HLS: 3,
            StreamType.DASH: 2,
            StreamType.YTDLP: 1,
        }.get(self.stream_type, 0)
        return (pixels, h, bw, type_priority)


@dataclass
class MediaInfo:
    """Aggregated metadata and stream formats extracted from a target URL."""

    url: str
    title: str
    video_id: str = "video"
    extractor: str = "generic"
    formats: list[StreamFormat] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    referer: str | None = None
    user_agent: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def select_format(self, preferred_quality: str = "best") -> StreamFormat:
        """Selects the best matching stream format (defaults to highest quality 'best')."""
        if not self.formats:
            raise ValueError(f"No downloadable stream formats found for URL: {self.url}")

        sorted_formats = sorted(self.formats, key=lambda f: f.sort_key, reverse=True)

        if not preferred_quality or preferred_quality.lower() in ("best", "highest", "max"):
            return sorted_formats[0]

        if preferred_quality.lower() in ("worst", "lowest", "min"):
            return sorted_formats[-1]

        target = preferred_quality.lower().rstrip("p")
        for fmt in sorted_formats:
            if fmt.format_id.lower() == preferred_quality.lower():
                return fmt
            if fmt.height and str(fmt.height) == target:
                return fmt
            if fmt.quality.lower().rstrip("p") == target:
                return fmt

        # If numeric height requested, pick closest available height <= target
        if target.isdigit():
            target_h = int(target)
            candidates = [f for f in sorted_formats if f.height and f.height <= target_h]
            if candidates:
                return candidates[0]

        return sorted_formats[0]

    def merged_headers(self, fmt: StreamFormat | None = None) -> dict[str, str]:
        """Returns combined HTTP headers for the media and specific format."""
        result: dict[str, str] = {}
        if self.user_agent:
            result["User-Agent"] = self.user_agent
        if self.referer:
            result["Referer"] = self.referer
        result.update(self.headers)
        if fmt and fmt.headers:
            result.update(fmt.headers)
        return result

    def merged_cookies(self, fmt: StreamFormat | None = None) -> dict[str, str]:
        """Returns combined cookies for the media and specific format."""
        result: dict[str, str] = dict(self.cookies)
        if fmt and fmt.cookies:
            result.update(fmt.cookies)
        return result
