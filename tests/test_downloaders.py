"""Unit and integration tests for HTTP/HLS downloaders and CLI."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import httpx

from vdl.cli import build_parser, parse_custom_headers
from vdl.config import DownloadConfig
from vdl.downloader import DownloadManager
from vdl.downloader.hls import HlsDownloader
from vdl.downloader.http import HttpDownloader
from vdl.models import MediaInfo, StreamFormat, StreamType

MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:100
#EXT-X-MAP:URI="init.mp4"
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example.com/key.bin",IV=0x00000000000000000000000000000001
#EXTINF:10.0,
seg1.ts
#EXTINF:10.0,
https://cdn.example.com/hls/seg2.ts
#EXT-X-ENDLIST
"""


def test_parse_media_playlist_and_aes128() -> None:
    init_url, segments = HlsDownloader._parse_media_playlist(
        "https://cdn.example.com/hls/index.m3u8", MEDIA_PLAYLIST
    )
    assert init_url == "https://cdn.example.com/hls/init.mp4"
    assert len(segments) == 2
    assert segments[0].url == "https://cdn.example.com/hls/seg1.ts"
    assert segments[0].key_url == "https://keys.example.com/key.bin"
    assert segments[0].iv == bytes.fromhex("00000000000000000000000000000001")
    assert segments[1].url == "https://cdn.example.com/hls/seg2.ts"

    # Verify AES-128 PKCS7 encryption & decryption round-trip
    key = b"0123456789abcdef"
    iv = b"\x00" * 15 + b"\x01"
    plaintext = b"Hello Video Segment Data!"
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    decrypted = HlsDownloader._decrypt_aes128(ciphertext, key, iv)
    assert decrypted == plaintext


def test_http_downloader_mock_transport(tmp_path: Path, monkeypatch) -> None:
    payload = b"FAKE_MP4_VIDEO_CONTENT_" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(payload))},
            content=payload,
        )

    orig_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return orig_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", patched_client)

    url = "https://cdn.example.com/test.mp4"
    fmt = StreamFormat(url=url, stream_type=StreamType.HTTP, ext="mp4")
    info = MediaInfo(url=url, title="LocalTest", formats=[fmt])
    dest = tmp_path / "out.mp4"

    dl = HttpDownloader(DownloadConfig(concurrency=1))
    saved_path = dl.download(info, fmt, dest)
    assert saved_path.is_file()
    assert saved_path.read_bytes() == payload


def test_cli_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "https://example.com/watch/12345",
            "-o",
            "./downloads/%(title)s.%(ext)s",
            "--cookies",
            "cookies.txt",
            "--use-browser",
            "--write-thumbnail",
            "--skip-download",
            "-H",
            "X-Test: 123",
        ]
    )
    assert args.urls == ["https://example.com/watch/12345"]
    assert args.use_browser is True
    assert args.write_thumbnail is True
    assert args.skip_download is True
    assert str(args.cookies) == "cookies.txt"
    assert parse_custom_headers(args.header) == {"X-Test": "123"}


def test_download_thumbnail(tmp_path: Path, monkeypatch) -> None:
    fake_jpg = b"\xff\xd8\xff\xe0FAKE_JPEG_DATA"
    manager = DownloadManager(
        DownloadConfig(
            output_template=str(tmp_path / "%(title)s.%(ext)s"),
            impersonate="",
            write_thumbnail=True,
            skip_download=True,
        )
    )
    monkeypatch.setattr(
        manager,
        "_fetch_binary",
        lambda url, headers, cookies: (fake_jpg, "image/jpeg"),
    )
    info = MediaInfo(
        url="https://example.com/watch/999",
        title="Thumbnail Test Video",
        thumbnail="https://cdn.example.com/covers/999.jpg",
    )
    saved_thumb = manager.download_thumbnail(info)
    assert saved_thumb == tmp_path / "Thumbnail Test Video.jpg"
    assert saved_thumb.read_bytes() == fake_jpg


