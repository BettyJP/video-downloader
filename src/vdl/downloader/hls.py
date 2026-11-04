"""HLS (m3u8) and DASH (mpd) stream downloader with AES-128 decryption and FFmpeg remuxing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urljoin

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import httpx
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)

from vdl.config import DownloadConfig
from vdl.models import MediaInfo, StreamFormat, StreamType
from vdl.utils.logger import console, get_logger

logger = get_logger("downloader.hls")


@dataclass
class HlsSegment:
    """Represents a single media segment in an HLS playlist."""

    index: int
    url: str
    key_url: str | None = None
    iv: bytes | None = None


class HlsDownloader:
    """Downloads HLS (.m3u8) and DASH (.mpd) streams and merges them into an MP4 file."""

    def __init__(self, config: DownloadConfig | None = None) -> None:
        self.config = config or DownloadConfig()

    def download(
        self,
        media_info: MediaInfo,
        stream_format: StreamFormat,
        output_path: Path,
    ) -> Path:
        """Downloads an HLS or DASH stream to `output_path`."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        headers = media_info.merged_headers(stream_format)
        cookies = media_info.merged_cookies(stream_format)

        if stream_format.stream_type == StreamType.DASH or ".mpd" in stream_format.url.lower():
            return self._download_via_ffmpeg(stream_format.url, headers, cookies, output_path)

        try:
            return self._download_hls_native(stream_format.url, headers, cookies, output_path)
        except Exception as exc:
            logger.warning(
                f"Native HLS downloader encountered an issue ({exc}); falling back to direct FFmpeg."
            )
            return self._download_via_ffmpeg(stream_format.url, headers, cookies, output_path)

    def _download_hls_native(
        self,
        m3u8_url: str,
        headers: dict[str, str],
        cookies: dict[str, str],
        output_path: Path,
    ) -> Path:
        logger.info(f"[cyan][HlsDownloader][/cyan] Fetching HLS playlist: {m3u8_url}")

        with httpx.Client(
            headers=headers,
            cookies=cookies,
            proxy=self.config.proxy,
            timeout=httpx.Timeout(self.config.timeout_sec, read=60.0),
            follow_redirects=True,
            verify=False,
        ) as client:
            playlist_url, playlist_text = self._resolve_media_playlist(client, m3u8_url)
            init_map_url, segments = self._parse_media_playlist(playlist_url, playlist_text)

            if not segments:
                raise ValueError(f"No segments found in HLS playlist: {playlist_url}")

            logger.info(
                f"[cyan][HlsDownloader][/cyan] Downloading {len(segments)} segment(s) "
                f"(concurrency={self.config.concurrency})..."
            )

            key_cache: dict[str, bytes] = {}
            key_lock = threading.Lock()

            def get_aes_key(key_uri: str) -> bytes:
                with key_lock:
                    if key_uri not in key_cache:
                        resp = client.get(key_uri)
                        resp.raise_for_status()
                        key_cache[key_uri] = resp.content
                    return key_cache[key_uri]

            with tempfile.TemporaryDirectory(prefix="vdl_hls_") as tmp_dir_str:
                tmp_dir = Path(tmp_dir_str)
                merged_ts_path = tmp_dir / "merged_stream.ts"

                init_bytes = b""
                if init_map_url:
                    resp = client.get(init_map_url)
                    resp.raise_for_status()
                    init_bytes = resp.content

                segment_files: list[Path] = [
                    tmp_dir / f"seg_{seg.index:05d}.ts" for seg in segments
                ]

                with Progress(
                    TextColumn("[bold cyan]{task.fields[filename]}", justify="right"),
                    BarColumn(bar_width=None),
                    "[progress.percentage]{task.percentage:>3.1f}%",
                    "•",
                    MofNCompleteColumn(),
                    "•",
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    task_id = progress.add_task(
                        "hls", filename=output_path.name, total=len(segments)
                    )

                    def fetch_segment(seg: HlsSegment, dest: Path) -> None:
                        for attempt in range(4):
                            try:
                                resp = client.get(seg.url)
                                resp.raise_for_status()
                                data = resp.content

                                if seg.key_url:
                                    key_bytes = get_aes_key(seg.key_url)
                                    iv = seg.iv or seg.index.to_bytes(16, byteorder="big")
                                    data = self._decrypt_aes128(data, key_bytes, iv)

                                dest.write_bytes(data)
                                progress.update(task_id, advance=1)
                                return
                            except Exception as exc:
                                if attempt == 3:
                                    raise
                                logger.debug(f"Retrying segment #{seg.index} ({exc})")
                                time.sleep(0.8 * (attempt + 1))

                    with ThreadPoolExecutor(max_workers=self.config.concurrency) as pool:
                        futures = [
                            pool.submit(fetch_segment, seg, segment_files[i])
                            for i, seg in enumerate(segments)
                        ]
                        for fut in as_completed(futures):
                            fut.result()

                with merged_ts_path.open("wb") as out_f:
                    if init_bytes:
                        out_f.write(init_bytes)
                    for seg_file in segment_files:
                        out_f.write(seg_file.read_bytes())

                if self.config.ffmpeg_path and self.config.ffmpeg_path.is_file():
                    self._remux_to_mp4(merged_ts_path, output_path)
                else:
                    logger.warning(
                        "ffmpeg not found; saving concatenated transport stream directly."
                    )
                    output_path.write_bytes(merged_ts_path.read_bytes())

        logger.info(f"[green][HlsDownloader][/green] Saved to: {output_path}")
        return output_path

    def _resolve_media_playlist(
        self, client: httpx.Client, m3u8_url: str
    ) -> tuple[str, str]:
        """Fetches the m3u8 URL and resolves the highest bandwidth variant if it is a master playlist."""
        resp = client.get(m3u8_url)
        resp.raise_for_status()
        text = resp.text

        if "#EXT-X-STREAM-INF" not in text:
            return str(resp.url), text

        best_score = (-1, -1, -1)
        best_uri: str | None = None
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for idx, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF:"):
                bw_match = re.search(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", line)
                res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
                bw = int(bw_match.group(1)) if bw_match else 0
                w = int(res_match.group(1)) if res_match else 0
                h = int(res_match.group(2)) if res_match else 0
                for next_line in lines[idx + 1 :]:
                    if not next_line.startswith("#"):
                        if h == 0:
                            p_match = re.search(
                                r"(?:^|[^\d])(2160|1440|1080|720|480|360|240)p?(?:$|[^\d])",
                                next_line,
                            )
                            if p_match:
                                h = int(p_match.group(1))
                        pixels = (w * h) if (w and h) else (h * h * 16 // 9)
                        score = (pixels, h, bw)
                        if score > best_score:
                            best_score = score
                            best_uri = urljoin(str(resp.url), next_line)
                        break

        if not best_uri:
            raise ValueError(f"Could not resolve variant stream in master playlist: {m3u8_url}")

        sub_resp = client.get(best_uri)
        sub_resp.raise_for_status()
        return str(sub_resp.url), sub_resp.text

    @staticmethod
    def _parse_media_playlist(
        base_url: str, playlist_text: str
    ) -> tuple[str | None, list[HlsSegment]]:
        """Parses a media m3u8 playlist into initialization map URL and HlsSegment items."""
        segments: list[HlsSegment] = []
        current_key_url: str | None = None
        current_iv: bytes | None = None
        init_map_url: str | None = None
        media_sequence = 0

        seq_match = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", playlist_text)
        if seq_match:
            media_sequence = int(seq_match.group(1))

        seg_idx = 0
        for raw_line in playlist_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("#EXT-X-MAP:"):
                uri_match = re.search(r'URI="([^"]+)"', line)
                if uri_match:
                    init_map_url = urljoin(base_url, uri_match.group(1))
                continue

            if line.startswith("#EXT-X-KEY:"):
                method_match = re.search(r"METHOD=([^,\s]+)", line)
                method = method_match.group(1) if method_match else "NONE"
                if method == "NONE":
                    current_key_url = None
                    current_iv = None
                elif method == "AES-128":
                    uri_match = re.search(r'URI="([^"]+)"', line)
                    if uri_match:
                        current_key_url = urljoin(base_url, uri_match.group(1))
                    iv_match = re.search(r"IV=0x([0-9a-fA-F]+)", line)
                    if iv_match:
                        current_iv = bytes.fromhex(iv_match.group(1).zfill(32))
                    else:
                        current_iv = None
                continue

            if line.startswith("#"):
                continue

            seg_url = urljoin(base_url, line)
            seq_num = media_sequence + seg_idx
            iv = current_iv or (seq_num.to_bytes(16, byteorder="big") if current_key_url else None)
            segments.append(
                HlsSegment(
                    index=seg_idx,
                    url=seg_url,
                    key_url=current_key_url,
                    iv=iv,
                )
            )
            seg_idx += 1

        return init_map_url, segments

    @staticmethod
    def _decrypt_aes128(data: bytes, key: bytes, iv: bytes) -> bytes:
        """Decrypts an AES-128-CBC encrypted HLS media segment."""
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(data) + decryptor.finalize()
        if decrypted:
            pad_len = decrypted[-1]
            if 1 <= pad_len <= 16 and decrypted.endswith(bytes([pad_len]) * pad_len):
                decrypted = decrypted[:-pad_len]
        return decrypted

    def _remux_to_mp4(self, input_ts: Path, output_mp4: Path) -> None:
        """Uses FFmpeg stream copy to remux concatenated TS/fMP4 segments into a clean MP4."""
        assert self.config.ffmpeg_path is not None
        cmd = [
            str(self.config.ffmpeg_path),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(input_ts),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_mp4),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.debug(f"FFmpeg remux stderr: {result.stderr}")
            output_mp4.write_bytes(input_ts.read_bytes())

    def _download_via_ffmpeg(
        self,
        stream_url: str,
        headers: dict[str, str],
        cookies: dict[str, str],
        output_path: Path,
    ) -> Path:
        """Downloads an HLS/DASH stream directly using the bundled/system FFmpeg binary."""
        if not self.config.ffmpeg_path or not self.config.ffmpeg_path.is_file():
            raise RuntimeError(
                "FFmpeg executable is required for direct stream downloading. "
                "Place ffmpeg.exe in the `bin/` folder or install FFmpeg in PATH."
            )

        req_headers = dict(headers)
        if cookies and "Cookie" not in req_headers:
            req_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

        header_lines = "".join(f"{k}: {v}\r\n" for k, v in req_headers.items())

        cmd = [
            str(self.config.ffmpeg_path),
            "-y",
            "-loglevel",
            "warning" if self.config.verbose else "error",
            "-headers",
            header_lines,
            "-i",
            stream_url,
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        logger.info(f"[cyan][FFmpeg][/cyan] Downloading stream to: {output_path.name}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg download failed: {result.stderr.strip()}")
        logger.info(f"[green][FFmpeg][/green] Saved to: {output_path}")
        return output_path
