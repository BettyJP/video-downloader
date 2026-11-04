"""Unit tests for models, path formatting, and cookie loading."""

from __future__ import annotations

from pathlib import Path

from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.cookies import load_cookies_dict, load_playwright_cookies
from vdl.utils.paths import format_output_path, sanitize_filename


def test_sanitize_filename() -> None:
    assert sanitize_filename('My: "Video" <Test>?*|\\/') == "My_ _Video_ _Test______"
    assert sanitize_filename("   ") == "video"


def test_format_output_path_template() -> None:
    info = MediaInfo(
        url="https://example.com/watch/99",
        title='Sample: "Lecture 1"',
        video_id="99",
        extractor="custom_site",
    )
    out = format_output_path("./downloads/%(title)s_%(id)s.%(ext)s", info, "mp4")
    assert out == Path("./downloads/Sample_ _Lecture 1__99.mp4")


def test_media_info_select_format() -> None:
    f360 = StreamFormat(
        url="https://cdn.example.com/360.mp4",
        stream_type=StreamType.HTTP,
        format_id="http-360p",
        height=360,
        bandwidth=500_000,
    )
    f720 = StreamFormat(
        url="https://cdn.example.com/720.m3u8",
        stream_type=StreamType.HLS,
        format_id="hls-720p",
        height=720,
        bandwidth=2_000_000,
    )
    f1080 = StreamFormat(
        url="https://cdn.example.com/1080.m3u8",
        stream_type=StreamType.HLS,
        format_id="hls-1080p",
        height=1080,
        bandwidth=4_500_000,
    )
    info = MediaInfo(
        url="https://example.com/v/1",
        title="Demo",
        formats=[f360, f1080, f720],
    )

    assert info.select_format("best").height == 1080
    assert info.select_format("worst").height == 360
    assert info.select_format("720p").height == 720
    assert info.select_format("720").height == 720
    assert info.select_format("hls-720p").format_id == "hls-720p"


def test_load_cookies(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".example.com\tTRUE\t/\tTRUE\t2147483647\tsession_id\tabc123xyz\n"
        "#HttpOnly_.example.com\tTRUE\t/\tTRUE\t2147483647\tauth_token\tsecret999\n"
        ".other.org\tTRUE\t/\tFALSE\t2147483647\tother_cookie\tval\n",
        encoding="utf-8",
    )

    cookies = load_cookies_dict(cookie_file, "https://sub.example.com/video")
    assert cookies == {"session_id": "abc123xyz", "auth_token": "secret999"}

    pw_cookies = load_playwright_cookies(cookie_file)
    assert len(pw_cookies) == 3
    auth_cookie = next(c for c in pw_cookies if c["name"] == "auth_token")
    assert auth_cookie["httpOnly"] is True
    assert auth_cookie["secure"] is True

