"""Custom site extractor using HTML/DOM parsing, embedded JSON analysis, and TLS impersonation."""

from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from vdl.extractors.base import BaseExtractor
from vdl.extractors.js_unpacker import unpack_all_from_html
from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.logger import get_logger

logger = get_logger("extractors.custom")

# Regex patterns to detect video streams in inline scripts, JSON configs, and HTML attributes
STREAM_URL_REGEX = re.compile(
    r"""(?P<url>https?:\\?/\\?/[^\s"'<>\\]+?\.(?:m3u8|mpd|mp4|webm|m4v)(?:\?[^\s"'<>\\]*)?)""",
    re.IGNORECASE,
)

PLAYER_CONFIG_URL_REGEX = re.compile(
    r"""(?:file|src|source|video_?url|stream_?url|hls_?url|mp4_?url|contentUrl|manifestUrl)\s*["']?\s*[:=]\s*["'](?P<url>https?:\\?/\\?/[^"']+)["']""",
    re.IGNORECASE,
)

# Registry for domain-specific custom extraction hooks
CustomRuleHook = Callable[[str, str, BeautifulSoup, "CustomSiteExtractor"], list[StreamFormat]]
DOMAIN_RULES: list[tuple[re.Pattern[str], CustomRuleHook]] = []


def register_domain_rule(domain_regex: str) -> Callable[[CustomRuleHook], CustomRuleHook]:
    """Decorator to register a custom extraction function for specific domain patterns."""
    pattern = re.compile(domain_regex, re.IGNORECASE)

    def decorator(func: CustomRuleHook) -> CustomRuleHook:
        DOMAIN_RULES.append((pattern, func))
        return func

    return decorator


class CustomSiteExtractor(BaseExtractor):
    """Extractor for custom sites via HTML parsing, JSON-LD, script regex, and iframe traversal."""

    name = "custom_site"
    priority = 20

    def extract(self, url: str) -> MediaInfo:
        """Extracts video streams from HTML, embedded scripts, and iframes."""
        logger.info(f"[cyan][CustomExtractor][/cyan] Analyzing page: {url}")
        cookies = self.build_cookies(url)
        headers = self.build_headers(url)

        page_html, _ = self.fetch_text(url, headers=headers, cookies=cookies)
        soup = BeautifulSoup(page_html, "lxml")

        title = self._extract_title(soup, url)
        video_id = self._derive_video_id(url)
        thumbnail = self._extract_thumbnail(soup, url)

        formats: list[StreamFormat] = []

        # 1. Execute any registered domain-specific rules first
        hostname = urlparse(url).hostname or ""
        for pattern, hook in DOMAIN_RULES:
            if pattern.search(hostname) or pattern.search(url):
                logger.debug(f"Running domain rule '{hook.__name__}' for {hostname}")
                formats.extend(hook(url, page_html, soup, self))

        # 2. Extract from HTML tags (<video>, <source>, <meta>, JSON-LD) and inline scripts if needed
        if not formats:
            formats.extend(
                self._extract_formats_from_document(
                    page_url=url,
                    page_html=page_html,
                    soup=soup,
                    referer=url,
                    cookies=cookies,
                )
            )

        # 3. If no streams found on main page, inspect embedded <iframe> players (1 level deep)
        if not formats:
            for iframe_url in self._find_candidate_iframes(soup, url):
                logger.info(f"[cyan][CustomExtractor][/cyan] Inspecting embedded iframe: {iframe_url}")
                try:
                    iframe_headers = self.build_headers(iframe_url, extra={"Referer": url})
                    iframe_html, _ = self.fetch_text(
                        iframe_url, headers=iframe_headers, cookies=cookies
                    )
                    iframe_soup = BeautifulSoup(iframe_html, "lxml")
                    if title == video_id:
                        iframe_title = self._extract_title(iframe_soup, iframe_url)
                        if iframe_title:
                            title = iframe_title
                    formats.extend(
                        self._extract_formats_from_document(
                            page_url=iframe_url,
                            page_html=iframe_html,
                            soup=iframe_soup,
                            referer=iframe_url,
                            cookies=cookies,
                        )
                    )
                except Exception as exc:
                    logger.debug(f"Failed to inspect iframe {iframe_url}: {exc}")

        deduped_formats = self._deduplicate_formats(formats)
        deduped_formats.sort(key=lambda f: f.sort_key, reverse=True)
        if not deduped_formats:
            raise ValueError(f"CustomSiteExtractor found no video streams on {url}")

        logger.info(
            f"[green][CustomExtractor][/green] Found {len(deduped_formats)} stream format(s) for '{title}'"
        )
        return MediaInfo(
            url=url,
            title=title,
            video_id=video_id,
            extractor=self.name,
            formats=deduped_formats,
            headers=headers,
            cookies=cookies,
            referer=url,
            user_agent=self.config.user_agent,
            thumbnail=thumbnail,
        )

    def _extract_formats_from_document(
        self,
        page_url: str,
        page_html: str,
        soup: BeautifulSoup,
        referer: str,
        cookies: dict[str, str],
    ) -> list[StreamFormat]:
        """Extracts candidate video streams from a parsed HTML document."""
        discovered_urls: list[tuple[str, str | None]] = []

        # A. <video> and <source> tags
        for tag in soup.find_all(["video", "source"]):
            for attr in ("src", "data-src", "data-video", "data-url", "data-mp4", "data-hls"):
                val = tag.get(attr)
                if val and isinstance(val, str):
                    label = tag.get("label") or tag.get("res") or tag.get("size")
                    discovered_urls.append((urljoin(page_url, val.strip()), str(label) if label else None))

        # B. OpenGraph / Twitter meta video tags
        for meta in soup.find_all("meta"):
            prop = (meta.get("property") or meta.get("name") or "").lower()
            if prop in (
                "og:video",
                "og:video:url",
                "og:video:secure_url",
                "twitter:player:stream",
            ):
                content = meta.get("content")
                if content and isinstance(content, str):
                    discovered_urls.append((urljoin(page_url, content.strip()), "meta"))

        # C. JSON-LD (VideoObject)
        for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
            raw_json = script.string or script.get_text()
            if not raw_json:
                continue
            try:
                data = json.loads(raw_json)
                for video_url in self._find_urls_in_json(data):
                    discovered_urls.append((urljoin(page_url, video_url), "json-ld"))
            except Exception:
                pass

        # D. Next.js / Nuxt / Hydration JSON scripts
        for script in soup.find_all("script", id=re.compile(r"__NEXT_DATA__|__NUXT__", re.I)):
            raw_json = script.string or script.get_text()
            if not raw_json:
                continue
            try:
                data = json.loads(raw_json)
                for video_url in self._find_urls_in_json(data):
                    discovered_urls.append((urljoin(page_url, video_url), "hydration-json"))
            except Exception:
                pass

        # E. Unpack any Dean Edwards `eval(function(p,a,c,k,e,d)...)` packed JS blocks
        unpacked_js_blocks = unpack_all_from_html(page_html)
        if unpacked_js_blocks:
            logger.debug(f"Unpacked {len(unpacked_js_blocks)} obfuscated JS block(s)")

        # F. Regex scan across full HTML, inline JavaScript, and unpacked JS blocks for stream URLs
        searchable_texts = [html.unescape(page_html).replace(r"\/", "/")]
        for block in unpacked_js_blocks:
            searchable_texts.append(html.unescape(block).replace(r"\/", "/"))

        for text_blob in searchable_texts:
            for match in STREAM_URL_REGEX.finditer(text_blob):
                raw_url = match.group("url")
                discovered_urls.append((raw_url, None))

            for match in PLAYER_CONFIG_URL_REGEX.finditer(text_blob):
                raw_url = match.group("url").replace(r"\/", "/")
                if self._looks_like_media_url(raw_url):
                    discovered_urls.append((raw_url, "player-config"))

        # Convert discovered URLs into StreamFormat objects
        formats: list[StreamFormat] = []
        seen: set[str] = set()
        req_headers = self.build_headers(page_url, extra={"Referer": referer})

        for stream_url, quality_hint in discovered_urls:
            cleaned_url = stream_url.strip().rstrip("\\'\"")
            if not cleaned_url.startswith(("http://", "https://")):
                continue
            if cleaned_url in seen or self._is_ignored_asset(cleaned_url):
                continue
            seen.add(cleaned_url)

            stream_type, ext = self._classify_stream_url(cleaned_url)
            height = self._infer_height(cleaned_url, quality_hint)
            quality = quality_hint or (f"{height}p" if height else "source")

            if stream_type == StreamType.HLS:
                formats.extend(
                    self.expand_hls_variants(
                        cleaned_url,
                        referer=referer,
                        headers=req_headers,
                        cookies=cookies,
                    )
                )
            else:
                formats.append(
                    StreamFormat(
                        url=cleaned_url,
                        stream_type=stream_type,
                        format_id=f"{stream_type.value}-{len(formats)}",
                        ext=ext,
                        quality=quality,
                        height=height,
                        headers=req_headers,
                        cookies=dict(cookies),
                    )
                )

        return formats

    @staticmethod
    def _extract_title(soup: BeautifulSoup, fallback_url: str) -> str:
        """Extracts video title from OpenGraph, Twitter meta, H1, or <title>."""
        for prop in ("og:title", "twitter:title"):
            meta = soup.find("meta", attrs={"property": prop}) or soup.find(
                "meta", attrs={"name": prop}
            )
            if meta and meta.get("content"):
                return str(meta["content"]).strip()

        if soup.title and soup.title.string:
            return soup.title.string.strip()

        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)

        path_part = urlparse(fallback_url).path.rstrip("/").split("/")[-1]
        return path_part or "video"

    @staticmethod
    def _extract_thumbnail(soup: BeautifulSoup, base_url: str) -> str | None:
        for prop in ("og:image", "twitter:image"):
            meta = soup.find("meta", attrs={"property": prop}) or soup.find(
                "meta", attrs={"name": prop}
            )
            if meta and meta.get("content"):
                return urljoin(base_url, str(meta["content"]).strip())
        video = soup.find("video")
        if video and video.get("poster"):
            return urljoin(base_url, str(video["poster"]).strip())
        return None

    @staticmethod
    def _derive_video_id(url: str) -> str:
        parsed = urlparse(url)
        slug = parsed.path.rstrip("/").split("/")[-1]
        if slug and len(slug) <= 48:
            return re.sub(r"[^a-zA-Z0-9_-]", "_", slug)
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]

    @classmethod
    def _find_urls_in_json(cls, node: object) -> list[str]:
        results: list[str] = []
        if isinstance(node, dict):
            for k, v in node.items():
                key_lower = str(k).lower()
                if isinstance(v, str) and (
                    key_lower in ("contenturl", "embedurl", "videourl", "streamurl", "hlsurl", "file", "src")
                    or cls._looks_like_media_url(v)
                ):
                    if v.startswith(("http://", "https://")) and cls._looks_like_media_url(v):
                        results.append(v)
                else:
                    results.extend(cls._find_urls_in_json(v))
        elif isinstance(node, list):
            for item in node:
                results.extend(cls._find_urls_in_json(item))
        return results

    @staticmethod
    def _find_candidate_iframes(soup: BeautifulSoup, base_url: str) -> list[str]:
        iframes: list[str] = []
        ignore_keywords = (
            "googletagmanager",
            "doubleclick",
            "facebook.com/plugins",
            "ads",
            "recaptcha",
            "snaptrckr",
            "myavlive",
            "mayzaent",
            "exoclick",
            "juicyads",
            "tsyndicate",
            "stripchat",
            "chaturbate",
            "trafficjunky",
            "magsrv",
            "popads",
        )
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or iframe.get("data-src")
            if not src or not isinstance(src, str):
                continue
            full_url = urljoin(base_url, src.strip())
            if not full_url.startswith(("http://", "https://")):
                continue
            if any(kw in full_url.lower() for kw in ignore_keywords):
                continue
            iframes.append(full_url)
        return iframes[:5]

    @staticmethod
    def _looks_like_media_url(url: str) -> bool:
        lower = url.lower()
        return any(
            ext in lower
            for ext in (".m3u8", ".mpd", ".mp4", ".webm", ".m4v", "manifest", "playlist.m3u8", "/hls/")
        )

    @staticmethod
    def _is_ignored_asset(url: str) -> bool:
        lower = url.lower()
        ignored = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".ico", "preview.mp4")
        path = urlparse(lower).path
        return path.endswith(ignored)

    @staticmethod
    def _classify_stream_url(url: str) -> tuple[StreamType, str]:
        path = urlparse(url.lower()).path
        if ".m3u8" in url.lower() or path.endswith(".m3u8"):
            return StreamType.HLS, "mp4"
        if ".mpd" in url.lower() or path.endswith(".mpd"):
            return StreamType.DASH, "mp4"
        if path.endswith(".webm"):
            return StreamType.HTTP, "webm"
        return StreamType.HTTP, "mp4"

    @staticmethod
    def _infer_height(url: str, hint: str | None = None) -> int | None:
        for text in (hint or "", url):
            match = re.search(r"(?:^|[^\d])(2160|1440|1080|720|480|360|240)p?(?:$|[^\d])", text)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _deduplicate_formats(formats: list[StreamFormat]) -> list[StreamFormat]:
        seen: set[str] = set()
        unique: list[StreamFormat] = []
        for fmt in formats:
            if fmt.url not in seen:
                seen.add(fmt.url)
                unique.append(fmt)
        return unique


@register_domain_rule(r"missav\.")
def _extract_missav(
    url: str,
    page_html: str,
    soup: BeautifulSoup,
    extractor: CustomSiteExtractor,
) -> list[StreamFormat]:
    """Domain-specific extractor rule for missav.* sites (packed JS & UUID reconstruction)."""
    formats: list[StreamFormat] = []
    cookies = extractor.build_cookies(url)
    headers = extractor.build_headers(url, extra={"Referer": url})
    seen_m3u8: set[str] = set()

    # 1. Inspect unpacked eval(function(p,a,c,k,e,d)...) blocks for m3u8 URLs
    for unpacked in unpack_all_from_html(page_html):
        normalized = unpacked.replace(r"\/", "/")
        for match in re.finditer(r"https?://[^\s\"'<>\\]+?\.m3u8(?:\?[^\s\"'<>\\]*)?", normalized):
            m3u8_url = match.group(0)
            if m3u8_url not in seen_m3u8:
                seen_m3u8.add(m3u8_url)
                formats.extend(
                    extractor.expand_hls_variants(
                        m3u8_url, referer=url, headers=headers, cookies=cookies
                    )
                )

    # 2. Fallback: extract UUID from nineyu.com/<uuid>/seek or reversed keyword list
    if not seen_m3u8:
        uuid_match = re.search(
            r"nineyu\.com/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
            page_html,
            re.IGNORECASE,
        )
        if uuid_match:
            uuid_str = uuid_match.group(1)
            m3u8_url = f"https://surrit.com/{uuid_str}/playlist.m3u8"
            seen_m3u8.add(m3u8_url)
            formats.extend(
                extractor.expand_hls_variants(
                    m3u8_url, referer=url, headers=headers, cookies=cookies
                )
            )
        else:
            # Check pipe-separated 5-segment UUID next to 'surrit'
            pipe_uuid = re.search(
                r"m3u8\|([0-9a-f]{12})\|([0-9a-f]{4})\|([0-9a-f]{4})\|([0-9a-f]{4})\|([0-9a-f]{8})",
                page_html,
                re.IGNORECASE,
            )
            if pipe_uuid:
                p5, p4, p3, p2, p1 = pipe_uuid.groups()
                m3u8_url = f"https://surrit.com/{p1}-{p2}-{p3}-{p4}-{p5}/playlist.m3u8"
                formats.extend(
                    extractor.expand_hls_variants(
                        m3u8_url, referer=url, headers=headers, cookies=cookies
                    )
                )

    return formats


