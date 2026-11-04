"""Download manager routing streams to HTTP, HLS/DASH, or yt-dlp engines."""

from __future__ import annotations

from pathlib import Path

from vdl.config import DownloadConfig
from vdl.downloader.hls import HlsDownloader
from vdl.downloader.http import HttpDownloader
from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.logger import get_logger
from vdl.utils.paths import format_output_path

logger = get_logger("downloader")


class DownloadManager:
    """Coordinates format selection and dispatches to the appropriate download engine."""

    def __init__(self, config: DownloadConfig | None = None) -> None:
        self.config = config or DownloadConfig()
        self.http_downloader = HttpDownloader(self.config)
        self.hls_downloader = HlsDownloader(self.config)

    def download(
        self,
        media_info: MediaInfo,
        preferred_quality: str | None = None,
        output_template: str | None = None,
    ) -> Path:
        """Selects the best matching format from `media_info` and downloads it."""
        quality = preferred_quality or self.config.preferred_quality
        selected_fmt = media_info.select_format(quality)
        template = output_template or self.config.output_template
        output_path = format_output_path(template, media_info, selected_fmt.ext)

        logger.info(
            f"Selected format: [bold]{selected_fmt.format_id}[/bold] "
            f"({selected_fmt.stream_type.value}, quality={selected_fmt.resolution_label})"
        )

        if selected_fmt.stream_type == StreamType.HTTP:
            return self.http_downloader.download(media_info, selected_fmt, output_path)

        if selected_fmt.stream_type in (StreamType.HLS, StreamType.DASH):
            return self.hls_downloader.download(media_info, selected_fmt, output_path)

        if selected_fmt.stream_type == StreamType.YTDLP:
            return self._download_via_ytdlp(media_info, selected_fmt, output_path)

        raise ValueError(f"Unsupported stream type: {selected_fmt.stream_type}")

    def _download_via_ytdlp(
        self,
        media_info: MediaInfo,
        stream_format: StreamFormat,
        output_path: Path,
    ) -> Path:
        import yt_dlp

        output_path.parent.mkdir(parents=True, exist_ok=True)
        ydl_opts: dict[str, object] = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": str(output_path),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "http_headers": media_info.merged_headers(stream_format),
            "quiet": not self.config.verbose,
        }
        if self.config.impersonate:
            try:
                from yt_dlp.networking.impersonate import ImpersonateTarget

                ydl_opts["impersonate"] = ImpersonateTarget(client=self.config.impersonate)
            except Exception:
                pass
        if self.config.cookies_path and self.config.cookies_path.is_file():
            ydl_opts["cookiefile"] = str(self.config.cookies_path)
        if self.config.proxy:
            ydl_opts["proxy"] = self.config.proxy
        if self.config.ffmpeg_path:
            ydl_opts["ffmpeg_location"] = str(self.config.ffmpeg_path)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([media_info.url])

        return output_path


__all__ = ["DownloadManager", "HlsDownloader", "HttpDownloader"]

