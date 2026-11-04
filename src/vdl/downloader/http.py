"""HTTP/HTTPS progressive and multi-connection chunk downloader."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
import time

import httpx
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from vdl.config import DownloadConfig
from vdl.models import MediaInfo, StreamFormat
from vdl.utils.logger import console, get_logger

logger = get_logger("downloader.http")


class HttpDownloader:
    """Downloads direct HTTP/HTTPS video streams (.mp4, .webm, etc.) with chunked concurrency."""

    def __init__(self, config: DownloadConfig | None = None) -> None:
        self.config = config or DownloadConfig()

    def download(
        self,
        media_info: MediaInfo,
        stream_format: StreamFormat,
        output_path: Path,
    ) -> Path:
        """Downloads `stream_format` to `output_path`."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        headers = media_info.merged_headers(stream_format)
        cookies = media_info.merged_cookies(stream_format)
        url = stream_format.url

        logger.info(f"[cyan][HttpDownloader][/cyan] Downloading: {output_path.name}")

        with httpx.Client(
            headers=headers,
            cookies=cookies,
            proxy=self.config.proxy,
            timeout=httpx.Timeout(self.config.timeout_sec, read=120.0),
            follow_redirects=True,
            verify=False,
        ) as client:
            total_size, accepts_ranges = self._probe_stream(client, url)

            if (
                accepts_ranges
                and total_size
                and total_size > self.config.chunk_size * 2
                and self.config.concurrency > 1
            ):
                logger.debug(
                    f"Server supports byte ranges (size={total_size} bytes). "
                    f"Using {self.config.concurrency} concurrent connections."
                )
                self._download_parallel_ranges(client, url, output_path, total_size)
            else:
                self._download_single_stream(client, url, output_path, total_size)

        logger.info(f"[green][HttpDownloader][/green] Saved to: {output_path}")
        return output_path

    def _probe_stream(
        self, client: httpx.Client, url: str
    ) -> tuple[int | None, bool]:
        """Probes target URL for Content-Length and byte-range support."""
        try:
            resp = client.head(url)
            if resp.status_code < 400:
                cl = resp.headers.get("content-length")
                total = int(cl) if cl and cl.isdigit() else None
                accepts = "bytes" in (resp.headers.get("accept-ranges") or "").lower()
                if total:
                    return total, accepts
        except Exception:
            pass

        try:
            resp = client.get(url, headers={"Range": "bytes=0-0"})
            if resp.status_code == 206:
                content_range = resp.headers.get("content-range") or ""
                if "/" in content_range:
                    total_str = content_range.split("/")[-1].strip()
                    if total_str.isdigit():
                        return int(total_str), True
        except Exception:
            pass

        return None, False

    def _download_single_stream(
        self,
        client: httpx.Client,
        url: str,
        output_path: Path,
        total_size: int | None,
    ) -> None:
        part_path = output_path.with_suffix(output_path.suffix + ".part")
        downloaded = part_path.stat().st_size if part_path.exists() else 0
        req_headers: dict[str, str] = {}
        mode = "wb"

        if downloaded > 0 and total_size and downloaded < total_size:
            req_headers["Range"] = f"bytes={downloaded}-"
            mode = "ab"
        else:
            downloaded = 0

        with Progress(
            TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            with client.stream("GET", url, headers=req_headers) as resp:
                resp.raise_for_status()
                if resp.status_code == 200 and mode == "ab":
                    mode = "wb"
                    downloaded = 0

                if total_size is None:
                    cl = resp.headers.get("content-length")
                    if cl and cl.isdigit():
                        total_size = int(cl) + downloaded

                task_id = progress.add_task(
                    "download",
                    filename=output_path.name,
                    total=total_size,
                    completed=downloaded,
                )

                with part_path.open(mode) as f:
                    for chunk in resp.iter_bytes(chunk_size=self.config.chunk_size):
                        f.write(chunk)
                        progress.update(task_id, advance=len(chunk))

        part_path.replace(output_path)

    def _download_parallel_ranges(
        self,
        client: httpx.Client,
        url: str,
        output_path: Path,
        total_size: int,
    ) -> None:
        part_path = output_path.with_suffix(output_path.suffix + ".part")
        with part_path.open("wb") as f:
            f.truncate(total_size)

        segment_size = max(
            self.config.chunk_size * 2, total_size // (self.config.concurrency * 4)
        )
        ranges: list[tuple[int, int]] = []
        offset = 0
        while offset < total_size:
            end = min(offset + segment_size - 1, total_size - 1)
            ranges.append((offset, end))
            offset = end + 1

        file_lock = threading.Lock()

        with Progress(
            TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task(
                "download", filename=output_path.name, total=total_size
            )

            def fetch_range(start: int, end: int) -> None:
                for attempt in range(3):
                    try:
                        with client.stream(
                            "GET",
                            url,
                            headers={"Range": f"bytes={start}-{end}"},
                        ) as resp:
                            resp.raise_for_status()
                            pos = start
                            for chunk in resp.iter_bytes(chunk_size=262144):
                                with file_lock:
                                    with part_path.open("r+b") as f:
                                        f.seek(pos)
                                        f.write(chunk)
                                pos += len(chunk)
                                progress.update(task_id, advance=len(chunk))
                        return
                    except Exception as exc:
                        if attempt == 2:
                            raise
                        logger.debug(f"Retrying range {start}-{end} after error: {exc}")
                        time.sleep(1.0 * (attempt + 1))

            with ThreadPoolExecutor(max_workers=self.config.concurrency) as pool:
                futures = [pool.submit(fetch_range, s, e) for s, e in ranges]
                for fut in as_completed(futures):
                    fut.result()

        part_path.replace(output_path)
