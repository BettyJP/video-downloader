"""Headless browser network sniffer extractor using Playwright."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from vdl.extractors.base import BaseExtractor
from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.cookies import load_cookies_dict, load_playwright_cookies
from vdl.utils.logger import get_logger

logger = get_logger("extractors.sniffer")

MEDIA_CONTENT_TYPES = (
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "application/dash+xml",
    "video/mp4",
    "video/webm",
    "video/x-m4v",
)

MEDIA_URL_PATTERN = re.compile(
    r"\.(?:m3u8|mpd|mp4|webm|m4v)(?:\?|$|#)",
    re.IGNORECASE,
)


class BrowserSnifferExtractor(BaseExtractor):
    """Extracts dynamic video streams by intercepting browser network requests via Playwright."""

    name = "browser_sniffer"
    priority = 50

    def extract(self, url: str) -> MediaInfo:
        """Opens the page in Playwright, intercepts media network traffic, and returns MediaInfo."""
        try:
            from playwright.sync_api import Response, sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for BrowserSnifferExtractor. "
                "Install it with `pip install playwright && playwright install chromium`."
            ) from exc

        logger.info(f"[magenta][BrowserSniffer][/magenta] Launching browser to inspect: {url}")

        captured_streams: list[tuple[str, str, dict[str, str]]] = []
        seen_urls: set[str] = set()

        def handle_response(response: Response) -> None:
            try:
                resp_url = response.url
                if not resp_url.startswith(("http://", "https://")) or resp_url in seen_urls:
                    return
                # Ignore individual .ts / .m4s segments when we want the manifest or full mp4
                lower_path = urlparse(resp_url.lower()).path
                if lower_path.endswith((".ts", ".m4s", ".aac", ".vtt", ".srt", ".jpg", ".png", ".gif")):
                    return

                content_type = (response.headers.get("content-type") or "").lower().split(";")[0].strip()
                is_media_ct = any(ct in content_type for ct in MEDIA_CONTENT_TYPES)
                is_media_url = bool(MEDIA_URL_PATTERN.search(resp_url))

                if is_media_ct or is_media_url:
                    # Filter out tiny tracking/preview clips (< 50KB if content-length is known on mp4)
                    if content_type.startswith("video/"):
                        cl = response.headers.get("content-length")
                        if cl and cl.isdigit() and int(cl) < 50_000:
                            return

                    req_headers = {
                        k: v
                        for k, v in response.request.headers.items()
                        if k.lower()
                        in ("user-agent", "referer", "origin", "authorization", "cookie")
                    }
                    seen_urls.add(resp_url)
                    logger.debug(
                        f"[BrowserSniffer] Captured stream: {resp_url} (Content-Type: {content_type})"
                    )
                    captured_streams.append((resp_url, content_type, req_headers))
            except Exception as exc:
                logger.debug(f"Error inspecting response: {exc}")

        pw_cookies = load_playwright_cookies(self.config.cookies_path, url)
        dict_cookies = load_cookies_dict(self.config.cookies_path, url)

        with sync_playwright() as p:
            proxy_cfg = {"server": self.config.proxy} if self.config.proxy else None
            browser = p.chromium.launch(
                headless=self.config.browser_headless,
                proxy=proxy_cfg,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--autoplay-policy=no-user-gesture-required",
                    "--mute-audio",
                ],
            )
            try:
                extra_headers = dict(self.config.headers)
                if self.config.referer:
                    extra_headers.setdefault("Referer", self.config.referer)

                context = browser.new_context(
                    user_agent=self.config.user_agent,
                    extra_http_headers=extra_headers if extra_headers else None,
                    ignore_https_errors=True,
                )
                if pw_cookies:
                    try:
                        context.add_cookies(pw_cookies)
                    except Exception as exc:
                        logger.debug(f"Could not add cookies to Playwright context: {exc}")

                page = context.new_page()
                page.on("response", handle_response)

                page.goto(url, wait_until="domcontentloaded", timeout=self.config.browser_timeout_ms)

                # Wait briefly for initial player scripts to fire
                page.wait_for_timeout(2500)

                # If no stream captured yet, attempt to trigger video playback or click play overlays
                if not captured_streams:
                    self._trigger_video_playback(page)
                    page.wait_for_timeout(3500)

                # Also check DOM <video> / <source> elements rendered via JS
                dom_urls = page.evaluate(
                    """() => {
                        const urls = [];
                        document.querySelectorAll('video, video source').forEach(el => {
                            if (el.src && el.src.startsWith('http')) urls.push(el.src);
                            if (el.currentSrc && el.currentSrc.startsWith('http')) urls.push(el.currentSrc);
                        });
                        return urls;
                    }"""
                )
                for dom_url in dom_urls or []:
                    if dom_url not in seen_urls:
                        seen_urls.add(dom_url)
                        captured_streams.append(
                            (dom_url, "", {"Referer": page.url, "User-Agent": self.config.user_agent})
                        )

                title = page.title() or self._fallback_title(url)

                # Extract browser cookies after page load (includes dynamically issued session/token cookies)
                for c in context.cookies():
                    if c.get("name") and c.get("value"):
                        dict_cookies[c["name"]] = c["value"]
            finally:
                browser.close()

        if not captured_streams:
            raise ValueError(f"BrowserSnifferExtractor did not capture any video streams on {url}")

        formats: list[StreamFormat] = []
        for idx, (stream_url, content_type, req_headers) in enumerate(captured_streams):
            normalized_headers = self._normalize_headers(req_headers, fallback_referer=url)
            stream_type, ext = self._classify_stream(stream_url, content_type)

            if stream_type == StreamType.HLS:
                formats.extend(
                    self.expand_hls_variants(
                        stream_url,
                        referer=normalized_headers.get("Referer", url),
                        headers=normalized_headers,
                        cookies=dict_cookies,
                    )
                )
            else:
                height = self._infer_height(stream_url)
                formats.append(
                    StreamFormat(
                        url=stream_url,
                        stream_type=stream_type,
                        format_id=f"sniffer-{stream_type.value}-{idx}",
                        ext=ext,
                        quality=f"{height}p" if height else "sniffer",
                        height=height,
                        headers=normalized_headers,
                        cookies=dict(dict_cookies),
                    )
                )

        video_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        logger.info(
            f"[green][BrowserSniffer][/green] Captured {len(formats)} stream format(s) for '{title}'"
        )
        return MediaInfo(
            url=url,
            title=title,
            video_id=video_id,
            extractor=self.name,
            formats=formats,
            headers=self.build_headers(url),
            cookies=dict_cookies,
            referer=url,
            user_agent=self.config.user_agent,
        )

    @staticmethod
    def _trigger_video_playback(page: object) -> None:
        """Attempts to click play buttons or start HTML5 video playback via JS."""
        try:
            # Try common play button selectors
            selectors = [
                "button.vjs-big-play-button",
                ".jw-icon-display",
                ".plyr__control--overlaid",
                "[aria-label*='Play' i]",
                "video",
            ]
            for sel in selectors:
                locator = page.locator(sel).first  # type: ignore[attr-defined]
                if locator.count() > 0 and locator.is_visible():
                    locator.click(timeout=1500, force=True)
                    break
        except Exception:
            pass

        try:
            page.evaluate(  # type: ignore[attr-defined]
                """() => {
                    document.querySelectorAll('video').forEach(v => {
                        v.muted = true;
                        v.play().catch(() => {});
                    });
                }"""
            )
        except Exception:
            pass

    def _normalize_headers(
        self, raw_headers: dict[str, str], fallback_referer: str
    ) -> dict[str, str]:
        header_map = {
            "user-agent": "User-Agent",
            "referer": "Referer",
            "origin": "Origin",
            "authorization": "Authorization",
            "cookie": "Cookie",
        }
        result: dict[str, str] = {
            "User-Agent": self.config.user_agent,
            "Referer": fallback_referer,
        }
        for k, v in raw_headers.items():
            canonical = header_map.get(k.lower(), k)
            result[canonical] = v
        result.update(self.config.headers)
        return result

    @staticmethod
    def _classify_stream(url: str, content_type: str) -> tuple[StreamType, str]:
        lower_url = url.lower()
        if (
            ".m3u8" in lower_url
            or "mpegurl" in content_type
        ):
            return StreamType.HLS, "mp4"
        if ".mpd" in lower_url or "dash+xml" in content_type:
            return StreamType.DASH, "mp4"
        if ".webm" in lower_url or "webm" in content_type:
            return StreamType.HTTP, "webm"
        return StreamType.HTTP, "mp4"

    @staticmethod
    def _infer_height(url: str) -> int | None:
        match = re.search(r"(?:^|[^\d])(2160|1440|1080|720|480|360|240)p?(?:$|[^\d])", url)
        return int(match.group(1)) if match else None

    @staticmethod
    def _fallback_title(url: str) -> str:
        slug = urlparse(url).path.rstrip("/").split("/")[-1]
        return slug or "video"

