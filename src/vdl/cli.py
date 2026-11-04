"""Command-line interface for Custom Video Downloader (vdl)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from rich.table import Table

from vdl import __version__
from vdl.config import DEFAULT_USER_AGENT, DownloadConfig
from vdl.downloader import DownloadManager
from vdl.extractors import ExtractorManager
from vdl.models import MediaInfo
from vdl.utils.logger import console, setup_logger


def build_parser() -> argparse.ArgumentParser:
    """Creates the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="vdl",
        description="Custom Video Downloader - Download embedded & dynamic streams from any URL.",
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="Target video page URL(s) to download.",
    )
    parser.add_argument(
        "-i",
        "--input-file",
        type=Path,
        help="Text file containing URLs to download (one URL per line).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="./downloads/%(title)s.%(ext)s",
        help="Output file path or template (default: './downloads/%%(title)s.%%(ext)s').",
    )
    parser.add_argument(
        "-q",
        "--quality",
        default="best",
        help="Preferred stream quality or format ID (e.g., best, 1080p, 720p, worst).",
    )
    parser.add_argument(
        "--cookies",
        type=Path,
        help="Path to Netscape format cookies.txt file.",
    )
    parser.add_argument(
        "--use-browser",
        action="store_true",
        help="Prioritize headless browser network sniffing (Playwright) for stream detection.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Playwright browser in visible (non-headless) mode.",
    )
    parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        metavar="KEY:VALUE",
        help="Custom HTTP header to attach (can be specified multiple times).",
    )
    parser.add_argument(
        "--referer",
        help="Custom Referer URL.",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="Custom User-Agent header.",
    )
    parser.add_argument(
        "--proxy",
        help="HTTP/HTTPS/SOCKS proxy URL.",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=8,
        help="Number of concurrent segment/chunk connections (default: 8).",
    )
    parser.add_argument(
        "--ffmpeg-location",
        type=Path,
        help="Custom path to ffmpeg executable.",
    )
    parser.add_argument(
        "--no-ytdlp",
        action="store_true",
        help="Disable fallback to yt-dlp.",
    )
    parser.add_argument(
        "--write-thumbnail",
        action="store_true",
        help="Write thumbnail image to disk.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Do not download the video (useful with --write-thumbnail).",
    )
    parser.add_argument(
        "-F",
        "--list-formats",
        action="store_true",
        help="List all detected video stream formats without downloading.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"vdl {__version__}",
    )
    return parser


def parse_custom_headers(raw_headers: list[str]) -> dict[str, str]:
    """Parses 'Key: Value' CLI header arguments into a dictionary."""
    headers: dict[str, str] = {}
    for item in raw_headers:
        if ":" in item:
            k, v = item.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


def collect_target_urls(urls: list[str], input_file: Path | None) -> list[str]:
    """Combines positional URLs and URLs read from `--input-file`."""
    collected: list[str] = [u.strip() for u in urls if u.strip()]
    if input_file:
        if not input_file.is_file():
            raise FileNotFoundError(f"Input file not found: {input_file}")
        for line in input_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                collected.append(stripped)
    return collected


def print_formats_table(media_info: MediaInfo) -> None:
    """Renders detected stream formats in a formatted Rich table."""
    table = Table(title=f"Available Formats: {media_info.title} [{media_info.extractor}]")
    table.add_column("Format ID", style="cyan", no_wrap=True)
    table.add_column("Type", style="magenta")
    table.add_column("Ext", style="green")
    table.add_column("Resolution / Quality", style="yellow")
    table.add_column("Bandwidth", justify="right")
    table.add_column("Stream URL", overflow="fold")

    for fmt in sorted(media_info.formats, key=lambda f: f.sort_key, reverse=True):
        bw_str = f"{fmt.bandwidth // 1000} kbps" if fmt.bandwidth else "-"
        res_str = (
            f"{fmt.width}x{fmt.height} ({fmt.quality})"
            if fmt.width and fmt.height
            else fmt.resolution_label
        )
        table.add_row(
            fmt.format_id,
            fmt.stream_type.value,
            fmt.ext,
            res_str,
            bw_str,
            fmt.url[:100] + ("..." if len(fmt.url) > 100 else ""),
        )
    console.print(table)


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point for the `vdl` CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logger = setup_logger(verbose=args.verbose)

    try:
        target_urls = collect_target_urls(args.urls, args.input_file)
    except Exception as exc:
        logger.error(f"[red]{exc}[/red]")
        return 1

    if not target_urls:
        parser.print_help()
        return 1

    config = DownloadConfig(
        output_template=args.output,
        cookies_path=args.cookies,
        use_browser=args.use_browser,
        browser_headless=not args.headed,
        preferred_quality=args.quality,
        user_agent=args.user_agent,
        referer=args.referer,
        headers=parse_custom_headers(args.header),
        proxy=args.proxy,
        concurrency=max(1, args.concurrency),
        ffmpeg_path=args.ffmpeg_location,
        no_ytdlp_fallback=args.no_ytdlp,
        write_thumbnail=args.write_thumbnail,
        skip_download=args.skip_download,
        info_only=args.list_formats,
        verbose=args.verbose,
    )

    extractor_manager = ExtractorManager(config)
    download_manager = DownloadManager(config)

    failures = 0
    for url in target_urls:
        try:
            media_info = extractor_manager.extract(url)
            if config.info_only:
                print_formats_table(media_info)
            if config.write_thumbnail:
                download_manager.download_thumbnail(media_info)
            if not config.info_only and not config.skip_download:
                download_manager.download(media_info)
        except KeyboardInterrupt:
            logger.warning("Download cancelled by user.")
            return 130
        except Exception as exc:
            failures += 1
            logger.error(f"[bold red]Failed to process URL ({url}):[/bold red] {exc}")
            if config.verbose:
                logger.exception("Detailed traceback:")

    return 1 if failures > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

