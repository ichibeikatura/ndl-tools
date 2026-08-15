# ndl-tools

国立国会図書館デジタルコレクション（NDLデジコレ）を扱う macOS 用 CLI ツール群。
もとは `~/Desktop/triple-ocr` と `~/.bin` に分かれていたものを、書誌情報取得の
重複実装を解消するために1つのリポジトリへ統合した。

| コマンド | 役割 |
|---|---|
| `bin/triple_ocr.py` | 資料画像を三系統OCR＋agy統合校正してテキスト化する（本リポジトリの主機能） |
| `bin/clean_hashira.py` | OCRテキストから柱（ランニングヘッダ）・ノンブルを除去する |
| `bin/ndl.py` | Safari で開いている資料の書誌情報・ページ番号を閲覧ログに記録する |
| `bin/preview.py` | Preview.app で開いている資料の閲覧状況をログに記録する |
| `bin/ns` | NDLデジコレをキーワード・年代・資料種別で検索してブラウザで開く |

書誌情報の取得（Safari連携・JapanLinkCenter API・NDL SRU API）は `ndl_tools/biblio.py`
に集約してあり、`triple_ocr.py` と `ndl.py` の両方がこれを使う。

## triple_ocr.py の概要

```
screencapture -i（範囲選択）
        │
        ├── macOS Vision framework
        ├── Google Cloud Vision API
        └── ndlocr-lite
        │
        ▼
Safari から NDLデジコレの書誌情報を自動取得
        │
        ▼
3つのOCR結果 + 書誌情報 → agy（Antigravity CLI）で統合校正
                            失敗時は Gemini API にフォールバック
        │
        ▼
校正済みテキストをクリップボードにコピー + 標準出力
```

## 必要環境

- macOS（Apple Silicon 推奨）
- Python 3.10+
- [ndlocr-lite](https://github.com/ndl-lab/ndlocr_cli)（`uv tool install` 等でインストール済みであること）
- [agy（Antigravity CLI）](https://antigravity.dev)（主経路の統合校正に使用）
  - Gemini AI Pro 等のサブスクリプションが必要
  - 初回起動時にブラウザ OAuth 認証が必要（`agy` を単体で起動すると自動で案内される）

### Pythonパッケージ

```bash
pip install pyobjc-framework-Vision requests google-generativeai "urllib3<2"
```

### 環境変数

```bash
export GOOGLE_CLOUD_VISION_API_KEY="your_api_key"   # Google Cloud Vision API
export GEMINI_API_KEY="your_api_key"                # Gemini API（agy 失敗時のフォールバック用）
```

`~/.secrets` に書いて `~/.zshenv` から `source ~/.secrets` するのが推奨。

## Karabiner-Elements 設定

グローバル起動する場合、`shell_command` に環境変数と PATH を明示する：

```json
// Cmd-Shift-L: 三系統OCR＋agy統合校正
{
  "shell_command": "source /Users/yourname/.secrets && PATH=/Users/yourname/.local/bin:$PATH /Users/yourname/.pyenv/versions/3.10.9/bin/python3 /Users/yourname/Documents/github/ndl-tools/bin/triple_ocr.py > /tmp/triple-ocr.log 2>&1"
}

// Cmd-Shift-O: Google Cloud Vision 単体で素早くOCR
{
  "shell_command": "source /Users/yourname/.secrets && /Users/yourname/.pyenv/versions/3.10.9/bin/python3 /Users/yourname/Documents/github/ndl-tools/bin/triple_ocr.py --engine google --no-biblio --no-log > /tmp/ocr-google.log 2>&1"
}
```

- Karabiner は非ログインシェルで実行されるため `.zshrc` は読まれない
- `ndlocr-lite` と `agy` が `~/.local/bin` にある場合は PATH に追加が必要
  （`--engine google` は両方とも使わないため PATH の追加は不要）
- API キーは `~/.secrets` から読む。`shell_command` に直接書かないこと

## ファイル構成

```
ndl-tools/
├── bin/                  # 実行スクリプト（~/.bin から symlink する）
│   ├── triple_ocr.py     # 三系統OCR統合校正（メイン）
│   ├── clean_hashira.py  # 柱（ランニングヘッダ）・ノンブルの除去
│   ├── ndl.py            # NDLデジコレ閲覧ログ記録
│   ├── preview.py        # Preview.app 閲覧ログ記録
│   └── ns                # NDLデジコレ検索
└── ndl_tools/            # 共有ライブラリ
    ├── biblio.py         # 書誌情報取得（Safari連携 / JLC / NDL SRU）
    ├── integrator.py     # agy 統合校正（Gemini API フォールバック付き）
    ├── kyujitai.py       # 旧字体→新字体 変換（kreplace 変換表を移植）
    ├── ocr_vision.py     # macOS Vision OCR
    ├── ocr_google.py     # Google Cloud Vision OCR
    └── ocr_ndlocr.py     # ndlocr-lite OCR
```

`bin/` 配下のスクリプトは冒頭でリポジトリルートを `sys.path` に加えてから
`ndl_tools` を import する。`Path(__file__).resolve()` で symlink を辿るため、
`~/.bin` に symlink を置いてどこから起動しても動く。

## 使い方

### 基本（範囲選択してOCR）

```bash
python3 bin/triple_ocr.py
```

実行するとスクリーンキャプチャの範囲選択モードになる。範囲を選択すると、三系統のOCRと書誌情報取得が並列で走り、agy が統合校正した結果が標準出力とクリップボードに出力される。

### オプション

```
python3 bin/triple_ocr.py [-h] [--image PATH] [--dir PATH] [--pid PID] [--no-biblio]
                          [--no-clipboard] [--no-log] [--ocr-only] [--engine ENGINE]

--image PATH     スクショ済みの画像ファイルを指定（省略時はインタラクティブ撮影）
--dir PATH       ディレクトリ内の画像(.png/.jpg/.jpeg)を一括OCRし honmon.txt に出力
--pid PID        書誌情報のPIDを明示指定（一括モードで既定はディレクトリ名の先頭数字）
--no-biblio      書誌情報の取得をスキップ
--no-clipboard   クリップボードへのコピーをスキップ
--no-log         ログファイルへの保存をスキップ
--ocr-only       OCR結果のみ表示（agy統合をスキップ）
--engine ENGINE  単一エンジン（google / vision / ndlocr）のみ実行し、統合校正を
                 スキップして生のOCR結果を出力する
```

### 単一エンジンモード（--engine）

三系統を回さず1つのエンジンだけで素早くOCRしたいとき用。統合校正を行わないため
数秒で終わる代わりに、誤認識の相互補正は効かない。

```bash
python3 bin/triple_ocr.py --engine google --no-biblio --no-log
```

### 一括モード（ディレクトリまとめOCR）

```bash
python3 bin/triple_ocr.py --dir 1460379_0001
```

ディレクトリ内の画像（`.png`/`.jpg`/`.jpeg`、ファイル名昇順）を1枚ずつOCR→agy統合校正し、結果を1つの `honmon.txt` にまとめて対象ディレクトリへ書き出す。単一画像モードとの違い:

- **書誌情報**: Safari ではなくディレクトリ名の先頭数字を PID とみなして取得する（例: `1460379_0001` → PID `1460379`）。`--pid` で明示指定も可能。取得した書誌情報は本文冒頭に `【書誌情報】` として付与される。
- **段落結合**: agy に対し、OCRの折り返し改行を段落単位へ結合し段落境界の改行だけ残すよう指示する。
- **旧字体→新字体変換**: 校正後に `ndl_tools/kyujitai.py`（Emacs kreplace の変換表を移植した464組の対応表＋NFC正規化）で決定論的に新字体へ変換する。歴史的仮名遣いはそのまま維持される。変換表には人名・地名の異体字（髙→高、嶋→島 等）も含む。固有名詞の字体を保ちたい場合は「追加分5-c」の行を削除する。
- 各ページは空行で区切って連結される。クリップボードコピー・追記ログは行わない。

### 柱（ランニングヘッダ）の除去

```bash
python3 bin/clean_hashira.py 908672_0001/honmon.txt          # → honmon.clean.txt
python3 bin/clean_hashira.py honmon.txt --report removed.txt # 除去行の一覧も出力
```

一括OCRした `honmon.txt` には、版面の柱（ノンブル脇の書名・章題）がページごとに混入する。`bin/clean_hashira.py` はそれを行単位で除去し、分断された段落を結合する。

- 除去対象: 書名の柱 / 章題の柱（`第一 〜` 形式。目次と各章冒頭の初出は残す）/ ノンブルのみの行 / 重複した「目次」見出し
- 崩れたOCR（`墓と懺悔`、`第五 生時代` 等）も類似度照合で拾い、`書名 一一八` のようにノンブルが合体した行にも対応する
- **文字は一切書き換えない**（行の削除と結合のみ）。終了前に「除去行を差し引いた原文と出力が完全一致する」ことを自己検証する
- 資料側の前提: 目次終端に「目次終」、奥附の先頭に「奥附」のマーカー行があること（`--toc-end` / `--colophon` で文字列か行番号を指定可能）。目次終マーカーが無い資料では誤削除を避けるため何もしない
- 前付け（漢詩・序）と奥附以降（広告）は行が単位のため、段落結合の対象外

### 使用例

```bash
# 既存の画像ファイルを指定して処理
python3 bin/triple_ocr.py --image ~/Desktop/scan.png

# OCR結果を比較したいだけのとき（agyを使わない）
python3 bin/triple_ocr.py --ocr-only

# パイプで後続処理に渡す（進捗はstderrなので混入しない）
python3 bin/triple_ocr.py | pbpaste
```

## ログ

結果は `~/My Drive/memo/triple-ocr.txt` に追記される（`TRIPLE_OCR_LOG` 環境変数で変更可）。

```
{書誌情報}
{URL}
{タイムスタンプ}:
p{ページ番号}
{OCRテキスト}

```

## 書誌情報の自動取得

Safariで NDLデジタルコレクション（`dl.ndl.go.jp` または `lab.ndl.go.jp`）を開いている状態で実行すると、URL から PID を抽出し、JapanLinkCenter API（フォールバック: NDL SRU API）で書誌情報を取得する。取得した書誌情報は agy へのプロンプトに含まれ、校正精度の向上に使われる。

Safariが NDLデジコレを開いていない場合は警告のみ出して続行する。

## エラーハンドリング

| ケース | 動作 |
|---|---|
| 個別OCRエンジンの失敗 | 警告を表示して残りのエンジンで続行 |
| 全OCRエンジンの失敗 | エラー終了 |
| agy の失敗 | Gemini API にフォールバック（stderr に警告） |
| agy・Gemini API 両方の失敗 | 生OCR結果をそのまま表示して終了 |
| 書誌情報取得の失敗 | 警告のみ、続行 |
| スクリーンキャプチャのキャンセル（Esc） | 正常終了 |

## その他のツール

### ndl.py — NDLデジコレ閲覧ログ記録

Safari で開いている資料の書誌情報・URL・ページ番号を `~/My Drive/memo/readkindai.txt`
に追記する。`--full` はスクリーンショット（`~/Documents/ebook/kindaimemo/`）も撮る。

```bash
python3 bin/ndl.py --page   # ページ番号のみ追記
python3 bin/ndl.py --full   # 書誌情報＋URL＋タイムスタンプ＋ページ番号＋スクショ
```

書誌情報の取得は `ndl_tools/biblio.py` を使う（`triple_ocr.py` と同じ実装）。

### preview.py — Preview.app 閲覧ログ記録

ダウンロード済み資料を Preview.app で読んでいるときの閲覧状況を記録する。
書誌情報は資料フォルダ内の `biblio.txt` / `metadata` から読むため、
`ndl_tools/biblio.py` には依存しない。

```bash
python3 bin/preview.py --page
python3 bin/preview.py --full
```

### ns — NDLデジコレ検索

```bash
ns 八月二十一日              # 全コレクション検索
ns 八月二十一日 1940         # 1940〜1950年に絞り込み
ns 八月二十一日 1940 雑誌    # 1940〜1950年の雑誌のみ
```

## ~/.bin からの利用

`~/.bin` は PATH に入っているため、symlink を張るとどこからでも起動できる。
2026-08-15 に設定済み。

```bash
ln -sf ~/Documents/github/ndl-tools/bin/ndl.py     ~/.bin/ndl.py
ln -sf ~/Documents/github/ndl-tools/bin/preview.py ~/.bin/preview.py
ln -sf ~/Documents/github/ndl-tools/bin/ns         ~/.bin/ns
```

Karabiner の `Cmd+Shift+P` / `Cmd+Shift+M` は `~/.bin/ndl.py`・`~/.bin/preview.py`
を最前面アプリ（Safari / Preview）で振り分けて呼ぶ。symlink 経由なのでこのままでよい。

## 関連リポジトリ

- [ndl-helper](https://github.com/ichibeikatura/ndl-helper) — NDLデジコレの UserScript とローカルHTTPサーバー（`ndl_server.py`）
- [ndl-search](https://github.com/ichibeikatura/ndl-search) — Emacs から NDL を検索する
- [ndl-note-tag](https://github.com/ichibeikatura/ndl-note-tag) — 書誌メモのタグ付け
- [kreplace](https://github.com/ichibeikatura/kreplace) — Emacs の旧字新字変換（`ndl_tools/kyujitai.py` の変換表の出典）
- [proofreader.el](https://github.com/ichibeikatura/proofreader.el) — agy による校正（本ツールの後段で使う）
