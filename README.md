# vdl - Custom Video Downloader

`vdl` は、特定のWebページURLを入力として、ページ内に埋め込まれた動画（HLS `.m3u8` / DASH `.mpd` / 直リンク `.mp4` 等）をローカルPCに高速ダウンロードするコマンドラインツールです。

`yt-dlp` などの汎用ツールでは標準対応していないサイトや、Cloudflare等のBot対策、難読化されたJavaScript（Dean Edwards Packer等）、動的プレイヤー通信を使用するサイトにも対応しています。また、**Pythonランタイムおよび `ffmpeg` を同梱したスタンドアロンパッケージ**を提供しているため、PythonやFFmpegがインストールされていない **Windows / CentOS / Ubuntu** 環境でも展開するだけですぐに使用できます。

---

## 主な特徴 (Features)

- **Pythonランタイム & FFmpeg同梱**: 実行環境にPythonやFFmpegをインストールすることなく、単体パッケージとして動作します。
- **多段階の動画抽出エンジン**:
  1. **Custom Site Extractor**: HTML解析、OpenGraph/JSON-LD解析、`eval(function(p,a,c,k,e,d)...)` 難読化JavaScriptの自動復号、サイト固有ルールによる高速抽出
  2. **WAF / Cloudflare 回避**: `curl_cffi` による Chrome TLS フィンガープリント偽装通信
  3. **Browser Network Sniffer (`--use-browser`)**: Playwright ヘッドレスブラウザで実際の動画通信（`.m3u8`, `.mpd`, `.mp4`）と認証ヘッダーをインターセプト
  4. **yt-dlp フォールバック**: 一般的な動画共有サイトは `yt-dlp` エンジンへ自動フォールバック
- **最高画質の自動選択**: マスタープレイリスト（`.m3u8`）内の全バリアントを解析し、デフォルトで**最高解像度・最大ビットレート**のストリームを自動選択します。
- **高速マルチスレッドダウンロード & 自動結合**:
  - HLSセグメント（`.ts` / `.m4s`）の並列ダウンロード、AES-128暗号化ストリームの自動復号、および同梱 `ffmpeg` による `.mp4` コンテナへの高速結合（無劣化ストリームコピー）
  - HTTP Byte-Range 対応サーバーからのマルチコネクション分割ダウンロードとレジューム機能

---

## インストール・導入方法 (Installation)

[GitHub Releases](https://github.com/BettyJP/video-downloader/releases) ページから、お使いのOSに対応するアーカイブをダウンロードして展開するだけで利用できます。

### 1. Windows (Windows 10 / 11 / Server x86_64)

Releases から `vdl-windows-x86_64.zip` をダウンロードし、任意のフォルダに展開します。

```powershell
# PowerShell でダウンロード・展開する例
Invoke-WebRequest -Uri "https://github.com/BettyJP/video-downloader/releases/latest/download/vdl-windows-x86_64.zip" -OutFile "vdl-windows-x86_64.zip"
Expand-Archive -Path "vdl-windows-x86_64.zip" -DestinationPath "."

# バージョン確認
.\vdl-windows-x86_64\vdl.exe --version
```

### 2. Ubuntu (Ubuntu 20.04 / 22.04 / 24.04 x86_64)

Releases から `vdl-ubuntu-x86_64.tar.gz` をダウンロードして展開します。

```bash
# ダウンロードと展開
curl -LO "https://github.com/BettyJP/video-downloader/releases/latest/download/vdl-ubuntu-x86_64.tar.gz"
tar -xzf vdl-ubuntu-x86_64.tar.gz

# バージョン確認
./vdl-ubuntu-x86_64/vdl --version

# （任意）どこからでも `vdl` コマンドとして実行できるようにシンボリックリンクを作成
sudo mv vdl-ubuntu-x86_64 /opt/vdl
sudo ln -sf /opt/vdl/vdl /usr/local/bin/vdl
```

### 3. CentOS / RHEL / AlmaLinux / Rocky Linux (8 / 9 x86_64)

CentOS系の `glibc` バージョンに合わせてビルドされた `vdl-centos-x86_64.tar.gz` をダウンロードして展開します。

```bash
# ダウンロードと展開
curl -LO "https://github.com/BettyJP/video-downloader/releases/latest/download/vdl-centos-x86_64.tar.gz"
tar -xzf vdl-centos-x86_64.tar.gz

# バージョン確認
./vdl-centos-x86_64/vdl --version

# （任意）/usr/local/bin に配置する場合
sudo mv vdl-centos-x86_64 /opt/vdl
sudo ln -sf /opt/vdl/vdl /usr/local/bin/vdl
```

---

## 使い方 (Usage)

以下ではコマンド名を `vdl`（Windowsの場合は `.\vdl.exe`）として記載します。

### 基本的なダウンロード（最高画質を自動選択）
URLを指定すると、ページ内の最高画質ストリームを検出し、カレントディレクトリの `./downloads/` 配下に動画タイトル名で保存します。

```bash
vdl "https://example.com/watch/12345"
```

### 利用可能なフォーマット（解像度・画質）の一覧表示
ダウンロードを行わず、抽出されたストリーム一覧を表形式で確認します。

```bash
vdl "https://example.com/watch/12345" -F
```

### 画質（解像度）を指定してダウンロード
デフォルトは `best`（最高画質）ですが、`720p`、`480p`、`worst`（最低画質）、または `-F` で確認した `Format ID` を指定できます。

```bash
# 720p を指定してダウンロード
vdl "https://example.com/watch/12345" -q 720p
```

### 保存先ディレクトリ・ファイル名テンプレートの指定 (`-o`)
`-o` / `--output` オプションで保存先パスやファイル名テンプレートを指定できます。

```bash
# 特定のファイル名で保存
vdl "https://example.com/watch/12345" -o "./movies/my_video.mp4"

# テンプレート変数を使用して保存
vdl "https://example.com/watch/12345" -o "./downloads/[%(extractor)s] %(title)s (%(id)s).%(ext)s"
```

#### 利用可能な出力テンプレート変数
| 変数 | 説明 |
| :--- | :--- |
| `%(title)s` | ページから取得した動画タイトル（ファイル名禁止文字は自動置換） |
| `%(id)s` | 動画IDまたはURLスラッグ |
| `%(extractor)s` | 抽出に使用されたExtractor名（`custom_site`, `browser_sniffer` 等） |
| `%(ext)s` | 出力ファイルの拡張子（`mp4`, `webm` 等） |

### 複数URLの一括ダウンロード
コマンドライン引数に複数のURLを並べるか、1行1URLで記載したテキストファイルを `-i` で指定できます。

```bash
# 複数URLを直接指定
vdl "https://example.com/watch/1" "https://example.com/watch/2"

# URLリストファイル（urls.txt）から一括ダウンロード（# 始まりはコメント行として無視）
vdl -i urls.txt
```

### Cookie・カスタムヘッダー・プロキシの利用
ログインが必要なサイトや、特定の `Referer` / `Header` を要求するサイトでは以下のオプションを使用します。

```bash
# Netscape形式の cookies.txt を読み込んでダウンロード
vdl "https://example.com/watch/12345" --cookies cookies.txt

# カスタムHTTPヘッダーやRefererを付与
vdl "https://example.com/watch/12345" \
  --referer "https://example.com/" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest"

# プロキシ経由でダウンロード
vdl "https://example.com/watch/12345" --proxy "http://127.0.0.1:8080"
```

### ヘッドレスブラウザによる通信スニッフィング (`--use-browser`)
複雑な動的JavaScriptによって動画URLが生成されるサイトでは、`--use-browser` を指定することで Playwright (Chromium) によるネットワーク通信キャプチャを優先実行できます。

```bash
# ヘッドレスブラウザで通信をインターセプトして抽出
vdl "https://example.com/watch/12345" --use-browser

# ブラウザウィンドウを実際に表示して動作確認する場合
vdl "https://example.com/watch/12345" --use-browser --headed -v
```
> [!NOTE]
> `--use-browser` モードを使用する際は、環境に Playwright 用の Chromium が必要です（`playwright install chromium`）。通常の静的解析・難読化JS復号で対応できるサイトでは `--use-browser` なしで動作します。

---

## コマンドラインオプション一覧 (CLI Options)

| オプション | 引数 | デフォルト値 | 説明 |
| :--- | :--- | :--- | :--- |
| `urls` | `[URL ...]` | - | ダウンロード対象のWebページURLまたは直リンクURL（複数指定可） |
| `-i`, `--input-file` | `PATH` | `None` | URL一覧を記載したテキストファイルのパス（1行につき1URL） |
| `-o`, `--output` | `TEMPLATE` | `./downloads/%(title)s.%(ext)s` | 出力先ファイルパスまたはテンプレート文字列 |
| `-q`, `--quality` | `QUALITY` | `best` | 画質指定（`best`, `worst`, `1080p`, `720p`, `480p`, または `Format ID`） |
| `-F`, `--list-formats` | なし | `False` | ダウンロードせず、検出されたストリームフォーマット一覧を表示して終了 |
| `--cookies` | `PATH` | `None` | Netscape形式のCookieファイル（`cookies.txt`）のパス |
| `--use-browser` | なし | `False` | Playwright ヘッドレスブラウザによるネットワーク通信キャプチャを優先使用 |
| `--headed` | なし | `False` | `--use-browser` 時にブラウザ画面を表示モード（非ヘッドレス）で起動 |
| `-H`, `--header` | `KEY:VALUE` | `[]` | リクエストに付与するカスタムHTTPヘッダー（複数回指定可能） |
| `--referer` | `URL` | 対象URLのOrigin | リクエストに付与するカスタム `Referer` URL |
| `--user-agent` | `STRING` | Chrome 131 UA | リクエストに付与するカスタム `User-Agent` 文字列 |
| `--proxy` | `URL` | `None` | HTTP / HTTPS / SOCKS プロキシURL |
| `-c`, `--concurrency` | `INT` | `8` | 並列ダウンロード数（HLSセグメント数 / HTTP Range分割チャンク数） |
| `--ffmpeg-location` | `PATH` | 自動検出 (`bin/ffmpeg`) | 使用する `ffmpeg` 実行ファイルのパスを明示的に指定 |
| `--no-ytdlp` | なし | `False` | `yt-dlp` へのフォールバックを無効化 |
| `-v`, `--verbose` | なし | `False` | デバッグ用の詳細ログおよびエラートレースバックを表示 |
| `-V`, `--version` | なし | - | バージョン番号を表示して終了 |
| `-h`, `--help` | なし | - | ヘルプメッセージを表示して終了 |

---

## 開発・ソースからのビルド手順 (Development & Building)

### 1. 開発環境のセットアップ

Python 3.11 以上（または `uv`）が必要です。

```bash
# uv を使用する場合（推奨）
uv sync --extra dev

# pip を使用する場合
python -m venv .venv
# Windows: .\.venv\Scripts\activate / Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2. テストの実行

```bash
pytest -v
```

### 3. 新しい対象サイトのカスタムルール追加方法

[src/vdl/extractors/site_custom.py](src/vdl/extractors/site_custom.py) の `@register_domain_rule` デコレータを使うことで、特定ドメイン向けの抽出ロジックを数行で追加できます。

```python
from vdl.extractors.site_custom import register_domain_rule, CustomSiteExtractor
from vdl.models import StreamFormat

@register_domain_rule(r"example-video-site\.com")
def _extract_example_site(url: str, page_html: str, soup, extractor: CustomSiteExtractor) -> list[StreamFormat]:
    # ページHTMLやAPIから m3u8 / mp4 のURLを抽出して返す
    m3u8_url = "https://cdn.example-video-site.com/stream/master.m3u8"
    return extractor.expand_hls_variants(m3u8_url, referer=url)
```

### 4. Pythonランタイム同梱パッケージのビルド

#### ローカル環境でのビルド (`build.py`)

```bash
# 現在のOS向けにランタイム＋ffmpeg同梱パッケージをビルドし、dist/releases/ にアーカイブ出力
python build.py --clean --archive

# Windows上で Docker を用いて Windows / CentOS / Ubuntu の3パッケージを一括ビルドする場合
pwsh ./scripts/build_all.ps1
```

#### GitHub Actions による自動ビルド & Releases 公開

本リポジトリには [.github/workflows/release.yml](.github/workflows/release.yml) が設定されています。`v*` タグをプッシュすると、Windows / Ubuntu / CentOS の3環境向けパッケージが自動ビルドされ、GitHub Releases に公開されます。

```bash
git tag v0.1.0
git push origin v0.1.0
```

---

## ドキュメント (Documentation)

- [システムアーキテクチャ・設計書 (`docs/architecture.md`)](docs/architecture.md)

