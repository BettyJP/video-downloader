"""Unit tests for CustomSiteExtractor and ExtractorManager."""

from __future__ import annotations

from vdl.config import DownloadConfig
from vdl.extractors import CustomSiteExtractor, ExtractorManager
from vdl.extractors.site_custom import (
    _matches_domain_spec,
    hash_domain_token,
    obfuscate_domain_pattern,
)
from vdl.models import StreamType

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Fallback Title</title>
  <meta property="og:title" content="Custom Stream Test Video" />
  <meta property="og:image" content="/thumb.jpg" />
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "VideoObject",
    "name": "Custom Stream Test Video",
    "contentUrl": "https://cdn.example.com/videos/direct_720p.mp4"
  }
  </script>
</head>
<body>
  <video poster="/poster.png">
    <source src="https://cdn.example.com/videos/stream_1080p.mp4" label="1080p" />
  </video>
  <script>
    const playerConfig = {
      hlsUrl: "https:\\/\\/cdn.example.com\\/hls\\/master.m3u8?token=xyz"
    };
  </script>
</body>
</html>
"""

MASTER_M3U8 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720
720p/prog_index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080
1080p/prog_index.m3u8
"""


def test_custom_site_extractor_parses_html_and_hls(monkeypatch) -> None:
    extractor = CustomSiteExtractor(DownloadConfig(impersonate=""))

    def fake_fetch_text(url: str, headers=None, cookies=None):
        if "master.m3u8" in url:
            return MASTER_M3U8, {"content-type": "application/vnd.apple.mpegurl"}
        return SAMPLE_HTML, {"content-type": "text/html"}

    monkeypatch.setattr(extractor, "fetch_text", fake_fetch_text)

    info = extractor.extract("https://example.com/watch/custom-123")
    assert info.title == "Custom Stream Test Video"
    assert info.thumbnail == "https://example.com/thumb.jpg"

    urls = {f.url for f in info.formats}
    assert "https://cdn.example.com/videos/stream_1080p.mp4" in urls
    assert "https://cdn.example.com/videos/direct_720p.mp4" in urls
    assert "https://cdn.example.com/hls/720p/prog_index.m3u8" in urls
    assert "https://cdn.example.com/hls/1080p/prog_index.m3u8" in urls

    best = info.select_format("best")
    assert best.height == 1080


def test_extractor_manager_direct_mp4_url() -> None:
    manager = ExtractorManager(DownloadConfig(impersonate=""))
    info = manager.extract("https://cdn.example.com/media/sample_video.mp4")
    assert info.extractor == "direct_stream"
    assert len(info.formats) == 1
    assert info.formats[0].stream_type == StreamType.HTTP
    assert info.formats[0].ext == "mp4"


def test_packed_js_and_domain_hash_matching(monkeypatch) -> None:
    packed_html = r"""
    <html>
    <head><meta property="og:title" content="Packed Stream Test Title" /></head>
    <body>
    <script>
    eval(function(p,a,c,k,e,d){e=function(c){return c};if(!''.replace(/^/,String)){while(c--){d[c]=k[c]||c}k=[function(e){return d[e]}];e=function(){return'\\w+'};c=1};while(c--){if(k[c]){p=p.replace(new RegExp('\\b'+e(c)+'\\b','g'),k[c])}}return p}('0=\'1://2.3/4-5-6-7-8/9.a\';',11,11,'source|https|media-cdn|example|12345678|1234|1234|1234|123456789abc|playlist|m3u8'.split('|'),0,{}))
    </script>
    </body>
    </html>
    """
    extractor = CustomSiteExtractor(DownloadConfig(impersonate=""))

    def fake_fetch_text(url: str, headers=None, cookies=None):
        if "playlist.m3u8" in url:
            return MASTER_M3U8, {"content-type": "application/vnd.apple.mpegurl"}
        return packed_html, {"content-type": "text/html"}

    monkeypatch.setattr(extractor, "fetch_text", fake_fetch_text)
    info = extractor.extract("https://video-portal.example/watch/sample-001")
    assert info.title == "Packed Stream Test Title"
    urls = {f.url for f in info.formats}
    assert "https://media-cdn.example/12345678-1234-1234-1234-123456789abc/720p/prog_index.m3u8" in urls
    assert "https://media-cdn.example/12345678-1234-1234-1234-123456789abc/1080p/prog_index.m3u8" in urls

    token_hash = hash_domain_token("video-portal")
    assert _matches_domain_spec(f"sha256:{token_hash}", "https://sub.video-portal.example/v/1")
    assert not _matches_domain_spec(f"sha256:{token_hash}", "https://other-site.example/v/1")

    obf_regex = obfuscate_domain_pattern(r"video-portal.*")
    assert _matches_domain_spec(obf_regex, "https://video-portal123.example/v/1")
    assert not _matches_domain_spec(obf_regex, "https://other-site.example/v/1")


