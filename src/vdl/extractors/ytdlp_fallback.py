"""Fallback extractor powered by yt-dlp."""

from __future__ import annotations

from vdl.extractors.base import BaseExtractor
from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.logger import get_logger

logger = get_logger("extractors.ytdlp")


class YtDlpExtractor(BaseExtractor):
    """Fallback extractor delegating URL extraction to yt-dlp."""

    name = "ytdlp_fallback"
    priority = 90

    def extract(self, url: str) -> MediaInfo:
        """Extracts video info using yt_dlp.YoutubeDL."""
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("yt-dlp is not installed.") from exc

        logger.info(f"[blue][yt-dlp Fallback][/blue] Extracting metadata for: {url}")

        ydl_opts: dict[str, object] = {
            "quiet": not self.config.verbose,
            "no_warnings": not self.config.verbose,
            "noplaylist": True,
            "http_headers": self.build_headers(url),
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
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError(f"yt-dlp returned empty info for {url}")

            if "entries" in info and info["entries"]:
                info = list(info["entries"])[0]

            title = str(info.get("title") or "video")
            video_id = str(info.get("id") or "video")
            duration = info.get("duration")
            thumbnail = info.get("thumbnail")

            formats: list[StreamFormat] = []
            raw_formats = info.get("formats") or [info]
            for f in raw_formats:
                stream_url = f.get("url")
                if not stream_url:
                    continue
                vcodec = f.get("vcodec")
                if vcodec == "none":
                    continue  # Skip audio-only streams when selecting video

                protocol = str(f.get("protocol") or "").lower()
                ext = str(f.get("ext") or "mp4")
                if "m3u8" in protocol or ext == "m3u8":
                    s_type = StreamType.HLS
                    ext = "mp4"
                elif "dash" in protocol or ext == "mpd":
                    s_type = StreamType.DASH
                    ext = "mp4"
                else:
                    s_type = StreamType.HTTP

                height = f.get("height")
                width = f.get("width")
                tbr = f.get("tbr")
                bandwidth = int(tbr * 1000) if isinstance(tbr, (int, float)) else None
                fmt_headers = dict(f.get("http_headers") or {})

                formats.append(
                    StreamFormat(
                        url=str(stream_url),
                        stream_type=s_type,
                        format_id=str(f.get("format_id") or "ytdlp"),
                        ext=ext,
                        quality=str(f.get("format_note") or (f"{height}p" if height else "default")),
                        width=int(width) if width else None,
                        height=int(height) if height else None,
                        bandwidth=bandwidth,
                        headers=fmt_headers,
                        cookies=self.build_cookies(url),
                    )
                )

            # Also add a delegated YTDLP format so yt-dlp can handle complex stream merging if needed
            formats.append(
                StreamFormat(
                    url=url,
                    stream_type=StreamType.YTDLP,
                    format_id="ytdlp-native",
                    ext="mp4",
                    quality="best",
                    height=max((f.height or 0 for f in formats), default=None),
                    headers=self.build_headers(url),
                    cookies=self.build_cookies(url),
                )
            )

            return MediaInfo(
                url=url,
                title=title,
                video_id=video_id,
                extractor=str(info.get("extractor_key") or self.name),
                formats=formats,
                headers=self.build_headers(url),
                cookies=self.build_cookies(url),
                referer=url,
                user_agent=self.config.user_agent,
                duration=float(duration) if isinstance(duration, (int, float)) else None,
                thumbnail=str(thumbnail) if thumbnail else None,
                raw_metadata=dict(info),
            )

