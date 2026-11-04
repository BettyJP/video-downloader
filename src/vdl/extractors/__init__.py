"""Extractor registry and URL router (`ExtractorManager`)."""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from vdl.config import DownloadConfig
from vdl.extractors.base import BaseExtractor
from vdl.extractors.generic_sniffer import BrowserSnifferExtractor
from vdl.extractors.site_custom import CustomSiteExtractor, register_domain_rule
from vdl.extractors.ytdlp_fallback import YtDlpExtractor
from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.logger import get_logger

logger = get_logger("extractors.router")

EXTRACTOR_REGISTRY: list[type[BaseExtractor]] = []


def register_extractor(cls: type[BaseExtractor]) -> type[BaseExtractor]:
    """Class decorator to register a custom BaseExtractor subclass."""
    if cls not in EXTRACTOR_REGISTRY:
        EXTRACTOR_REGISTRY.append(cls)
        EXTRACTOR_REGISTRY.sort(key=lambda c: c.priority)
    return cls


class ExtractorManager:
    """Routes target URLs through appropriate extractors in priority order."""

    def __init__(self, config: DownloadConfig | None = None) -> None:
        self.config = config or DownloadConfig()

    def extract(self, url: str) -> MediaInfo:
        """Extracts MediaInfo from `url` using direct detection or layered extractors."""
        # 0. Check if the input URL is already a direct media stream (.m3u8, .mpd, .mp4, .webm)
        direct_info = self._try_direct_stream_url(url)
        if direct_info is not None:
            return direct_info

        extractors = self._build_extractor_pipeline(url)
        errors: list[str] = []

        for extractor in extractors:
            if not extractor.can_handle(url):
                continue
            try:
                media_info = extractor.extract(url)
                if media_info and media_info.formats:
                    return media_info
            except Exception as exc:
                logger.debug(f"Extractor '{extractor.name}' failed for {url}: {exc}")
                errors.append(f"{extractor.name}: {exc}")

        error_summary = "; ".join(errors) if errors else "No compatible extractor found"
        raise RuntimeError(f"Failed to extract video streams from {url} ({error_summary})")

    def _build_extractor_pipeline(self, url: str) -> list[BaseExtractor]:
        """Builds the ordered list of extractors to try for `url`."""
        pipeline: list[BaseExtractor] = []

        # Include any user-registered custom BaseExtractor subclasses first
        for ext_cls in EXTRACTOR_REGISTRY:
            if ext_cls.can_handle(url):
                pipeline.append(ext_cls(self.config))

        if self.config.use_browser:
            pipeline.append(BrowserSnifferExtractor(self.config))
            pipeline.append(CustomSiteExtractor(self.config))
        else:
            pipeline.append(CustomSiteExtractor(self.config))
            pipeline.append(BrowserSnifferExtractor(self.config))

        if not self.config.no_ytdlp_fallback:
            pipeline.append(YtDlpExtractor(self.config))

        return pipeline

    def _try_direct_stream_url(self, url: str) -> MediaInfo | None:
        """Returns MediaInfo immediately if the URL itself points to a media manifest or file."""
        parsed = urlparse(url)
        path_lower = parsed.path.lower()
        base_ext = CustomSiteExtractor(self.config)
        headers = base_ext.build_headers(url)
        cookies = base_ext.build_cookies(url)

        if path_lower.endswith(".m3u8"):
            slug = parsed.path.rstrip("/").split("/")[-1].rsplit(".", 1)[0] or "hls_video"
            formats = base_ext.expand_hls_variants(
                url, referer=self.config.referer or url, headers=headers, cookies=cookies
            )
            return MediaInfo(
                url=url,
                title=slug,
                video_id=hashlib.sha1(url.encode("utf-8")).hexdigest()[:10],
                extractor="direct_hls",
                formats=formats,
                headers=headers,
                cookies=cookies,
                referer=self.config.referer or url,
                user_agent=self.config.user_agent,
            )

        if path_lower.endswith((".mp4", ".webm", ".m4v", ".mpd")):
            slug = parsed.path.rstrip("/").split("/")[-1].rsplit(".", 1)[0] or "direct_video"
            ext = "webm" if path_lower.endswith(".webm") else "mp4"
            s_type = StreamType.DASH if path_lower.endswith(".mpd") else StreamType.HTTP
            return MediaInfo(
                url=url,
                title=slug,
                video_id=hashlib.sha1(url.encode("utf-8")).hexdigest()[:10],
                extractor="direct_stream",
                formats=[
                    StreamFormat(
                        url=url,
                        stream_type=s_type,
                        format_id="direct-0",
                        ext=ext,
                        quality="source",
                        headers=headers,
                        cookies=cookies,
                    )
                ],
                headers=headers,
                cookies=cookies,
                referer=self.config.referer or url,
                user_agent=self.config.user_agent,
            )

        return None


__all__ = [
    "BaseExtractor",
    "BrowserSnifferExtractor",
    "CustomSiteExtractor",
    "ExtractorManager",
    "YtDlpExtractor",
    "register_domain_rule",
    "register_extractor",
]

