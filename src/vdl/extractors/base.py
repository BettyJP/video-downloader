"""Abstract base class and common HTTP/HLS helpers for video extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
from urllib.parse import urljoin, urlparse

import httpx

from vdl.config import DownloadConfig
from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.cookies import load_cookies_dict
from vdl.utils.logger import get_logger

logger = get_logger("extractors.base")


class BaseExtractor(ABC):
    """Base class for all video extractors."""

    name: str = "base"
    priority: int = 100

    def __init__(self, config: DownloadConfig | None = None) -> None:
        self.config = config or DownloadConfig()

    @classmethod
    def can_handle(cls, url: str) -> bool:
        """Returns True if this extractor supports the given URL."""
        return bool(url and url.startswith(("http://", "https://")))

    @abstractmethod
    def extract(self, url: str) -> MediaInfo:
        """Extracts video metadata and stream URLs from the target page URL."""
        raise NotImplementedError

    def build_headers(self, url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Builds request headers including User-Agent, Referer, and custom config headers."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        headers: dict[str, str] = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Referer": self.config.referer or f"{origin}/",
        }
        headers.update(self.config.headers)
        if extra:
            headers.update(extra)
        return headers

    def build_cookies(self, url: str) -> dict[str, str]:
        """Loads cookies applicable to the target URL."""
        return load_cookies_dict(self.config.cookies_path, url)

    def fetch_text(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Fetches text content using curl_cffi (TLS impersonation) with httpx fallback."""
        req_headers = self.build_headers(url, headers)
        req_cookies = self.build_cookies(url)
        if cookies:
            req_cookies.update(cookies)

        # Try curl_cffi first for TLS fingerprint impersonation (Cloudflare/WAF bypass)
        if self.config.impersonate:
            try:
                from curl_cffi import requests as curl_requests

                resp = curl_requests.get(
                    url,
                    headers=req_headers,
                    cookies=req_cookies,
                    impersonate=self.config.impersonate,
                    proxies={"http": self.config.proxy, "https": self.config.proxy}
                    if self.config.proxy
                    else None,
                    timeout=self.config.timeout_sec,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.text, resp_headers
            except ImportError:
                logger.debug("curl_cffi not installed; falling back to httpx")
            except Exception as exc:
                logger.debug(f"curl_cffi request failed ({exc}); retrying with httpx")

        with httpx.Client(
            headers=req_headers,
            cookies=req_cookies,
            proxy=self.config.proxy,
            timeout=self.config.timeout_sec,
            follow_redirects=True,
            verify=False,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.text, resp_headers

    def expand_hls_variants(
        self,
        m3u8_url: str,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> list[StreamFormat]:
        """Parses an HLS master playlist into multiple StreamFormat objects if possible."""
        fmt_headers = dict(headers or {})
        if referer:
            fmt_headers.setdefault("Referer", referer)
            parsed_ref = urlparse(referer)
            if parsed_ref.scheme and parsed_ref.netloc:
                fmt_headers.setdefault("Origin", f"{parsed_ref.scheme}://{parsed_ref.netloc}")

        try:
            text, _ = self.fetch_text(m3u8_url, headers=fmt_headers, cookies=cookies)
        except Exception as exc:
            logger.debug(f"Could not pre-fetch m3u8 playlist ({m3u8_url}): {exc}")
            return [
                StreamFormat(
                    url=m3u8_url,
                    stream_type=StreamType.HLS,
                    format_id="hls-master",
                    ext="mp4",
                    quality="auto",
                    headers=fmt_headers,
                    cookies=dict(cookies or {}),
                )
            ]

        if "#EXT-X-STREAM-INF" not in text:
            # Media playlist (single stream)
            key_match = re.search(r'#EXT-X-KEY:METHOD=AES-128,URI="([^"]+)"', text)
            key_url = urljoin(m3u8_url, key_match.group(1)) if key_match else None
            return [
                StreamFormat(
                    url=m3u8_url,
                    stream_type=StreamType.HLS,
                    format_id="hls-0",
                    ext="mp4",
                    quality="source",
                    headers=fmt_headers,
                    cookies=dict(cookies or {}),
                    encryption_key_url=key_url,
                )
            ]

        formats: list[StreamFormat] = []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for idx, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF:"):
                attrs = line[len("#EXT-X-STREAM-INF:") :]
                bw_match = re.search(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", attrs)
                res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", attrs)
                name_match = re.search(r'NAME="([^"]+)"', attrs)
                bandwidth = int(bw_match.group(1)) if bw_match else None
                width = int(res_match.group(1)) if res_match else None
                height = int(res_match.group(2)) if res_match else None

                # Find next non-comment line for the variant URI
                variant_uri = None
                for next_line in lines[idx + 1 :]:
                    if not next_line.startswith("#"):
                        variant_uri = next_line
                        break
                if not variant_uri:
                    continue

                full_url = urljoin(m3u8_url, variant_uri)

                # If RESOLUTION was omitted in #EXT-X-STREAM-INF, infer from NAME or URI path
                if height is None:
                    for candidate in (name_match.group(1) if name_match else "", variant_uri):
                        dim_match = re.search(r"(\d{3,4})x(\d{3,4})", candidate)
                        if dim_match:
                            width = int(dim_match.group(1))
                            height = int(dim_match.group(2))
                            break
                        p_match = re.search(
                            r"(?:^|[^\d])(2160|1440|1080|720|480|360|240)p?(?:$|[^\d])",
                            candidate,
                        )
                        if p_match:
                            height = int(p_match.group(1))
                            break

                quality_label = (
                    f"{height}p"
                    if height
                    else (
                        name_match.group(1)
                        if name_match
                        else (f"{bandwidth // 1000}kbps" if bandwidth else "variant")
                    )
                )
                formats.append(
                    StreamFormat(
                        url=full_url,
                        stream_type=StreamType.HLS,
                        format_id=f"hls-{quality_label}",
                        ext="mp4",
                        quality=quality_label,
                        width=width,
                        height=height,
                        bandwidth=bandwidth,
                        headers=fmt_headers,
                        cookies=dict(cookies or {}),
                    )
                )

        # Sort variants from highest quality to lowest quality
        formats.sort(key=lambda f: f.sort_key, reverse=True)

        # Always keep the master playlist available if no variants were parsed
        if not formats:
            formats.append(
                StreamFormat(
                    url=m3u8_url,
                    stream_type=StreamType.HLS,
                    format_id="hls-master",
                    ext="mp4",
                    quality="auto",
                    headers=fmt_headers,
                    cookies=dict(cookies or {}),
                )
            )
        return formats

