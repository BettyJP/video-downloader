"""Download manager routing streams to HTTP, HLS/DASH, or yt-dlp engines, plus thumbnail downloading."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from vdl.config import DownloadConfig
from vdl.downloader.hls import HlsDownloader
from vdl.downloader.http import HttpDownloader
from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.logger import get_logger
from vdl.utils.paths import format_output_path

logger = get_logger("downloader")

IMAGE_CONTENT_TYPE_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
}


class DownloadManager:
    """Coordinates format selection, video downloading, and thumbnail saving."""

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

    def download_thumbnail(
        self,
        media_info: MediaInfo,
        output_template: str | None = None,
    ) -> Path:
        """Downloads and saves the thumbnail image for `media_info`."""
        thumb_url = media_info.thumbnail
        if not thumb_url:
            raise ValueError(f"No thumbnail URL found for: {media_info.url}")

        logger.info(f"[cyan][Thumbnail][/cyan] Downloading thumbnail: {thumb_url}")
        headers = media_info.merged_headers()
        headers["Accept"] = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
        cookies = media_info.merged_cookies()

        img_bytes, content_type = self._fetch_binary(thumb_url, headers=headers, cookies=cookies)
        thumb_ext = self._infer_image_ext(thumb_url, content_type)

        template = output_template or self.config.output_template
        thumb_path = format_output_path(template, media_info, thumb_ext)
        if thumb_path.suffix.lower() in (".mp4", ".mkv", ".webm", ".ts", ".m4v", ".mov"):
            thumb_path = thumb_path.with_suffix(f".{thumb_ext}")

        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(img_bytes)
        logger.info(f"[green][Thumbnail][/green] Saved thumbnail to: {thumb_path}")
        return thumb_path

    def _fetch_binary(
        self,
        url: str,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> tuple[bytes, str]:
        """Fetches binary data using curl_cffi (TLS impersonation) with httpx fallback."""
        if self.config.impersonate:
            try:
                from curl_cffi import requests as curl_requests

                resp = curl_requests.get(
                    url,
                    headers=headers,
                    cookies=cookies,
                    impersonate=self.config.impersonate,
                    proxies={"http": self.config.proxy, "https": self.config.proxy}
                    if self.config.proxy
                    else None,
                    timeout=self.config.timeout_sec,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                ct = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                return resp.content, ct
            except ImportError:
                pass
            except Exception as exc:
                logger.debug(f"curl_cffi thumbnail fetch failed ({exc}); retrying with httpx")

        with httpx.Client(
            headers=headers,
            cookies=cookies,
            proxy=self.config.proxy,
            timeout=self.config.timeout_sec,
            follow_redirects=True,
            verify=False,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            ct = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            return resp.content, ct

    @staticmethod
    def _infer_image_ext(url: str, content_type: str) -> str:
        """Determines image file extension from HTTP Content-Type or URL suffix."""
        if content_type in IMAGE_CONTENT_TYPE_EXT:
            return IMAGE_CONTENT_TYPE_EXT[content_type]

        path_lower = urlparse(url.lower()).path
        for ext in ("webp", "png", "gif", "avif", "jpeg", "jpg"):
            if path_lower.endswith(f".{ext}"):
                return "jpg" if ext == "jpeg" else ext

        return "jpg"

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
