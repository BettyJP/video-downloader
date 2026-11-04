"""Cookie loading and conversion utilities (Netscape cookies.txt & Playwright format)."""

from __future__ import annotations

from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vdl.utils.logger import get_logger

logger = get_logger("cookies")


def load_cookies_dict(
    cookies_path: str | Path | None,
    target_url: str | None = None,
) -> dict[str, str]:
    """Loads cookies from a Netscape-format cookies.txt file into a key-value dict."""
    if not cookies_path:
        return {}

    path = Path(cookies_path)
    if not path.is_file():
        logger.warning(f"Cookie file not found: {path}")
        return {}

    target_domain = ""
    if target_url:
        parsed = urlparse(target_url)
        target_domain = (parsed.hostname or "").lower()

    cookies: dict[str, str] = {}
    try:
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        for cookie in jar:
            cookie_domain = (cookie.domain or "").lstrip(".").lower()
            if target_domain and cookie_domain and not (
                target_domain == cookie_domain or target_domain.endswith(f".{cookie_domain}")
            ):
                continue
            cookies[cookie.name] = cookie.value or ""
        return cookies
    except Exception:
        # Fallback manual parser for lenient Netscape cookie files
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") and not stripped.startswith("#HttpOnly_"):
                continue
            if stripped.startswith("#HttpOnly_"):
                stripped = stripped[len("#HttpOnly_"):]
            parts = stripped.split("\t")
            if len(parts) >= 7:
                domain, _, _, _, _, name, value = parts[:7]
                cookie_domain = domain.lstrip(".").lower()
                if target_domain and cookie_domain and not (
                    target_domain == cookie_domain or target_domain.endswith(f".{cookie_domain}")
                ):
                    continue
                cookies[name] = value
        return cookies


def load_playwright_cookies(
    cookies_path: str | Path | None,
    target_url: str | None = None,
) -> list[dict[str, Any]]:
    """Loads cookies from a Netscape cookies.txt file into Playwright context cookie objects."""
    if not cookies_path:
        return []

    path = Path(cookies_path)
    if not path.is_file():
        return []

    pw_cookies: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        http_only = False
        if stripped.startswith("#HttpOnly_"):
            http_only = True
            stripped = stripped[len("#HttpOnly_"):]
        elif not stripped or stripped.startswith("#"):
            continue

        parts = stripped.split("\t")
        if len(parts) >= 7:
            domain, _, c_path, secure_str, expires_str, name, value = parts[:7]
            cookie_obj: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": c_path or "/",
                "secure": secure_str.upper() == "TRUE",
                "httpOnly": http_only,
            }
            if expires_str.isdigit() and int(expires_str) > 0:
                cookie_obj["expires"] = int(expires_str)
            pw_cookies.append(cookie_obj)

    if not pw_cookies and target_url:
        # Fallback using load_cookies_dict
        simple = load_cookies_dict(path, target_url)
        for k, v in simple.items():
            pw_cookies.append({"name": k, "value": v, "url": target_url})

    return pw_cookies

