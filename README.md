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

出力先のパスも環境変数で変更できる（いずれも省略可。既定値は下記のとおり）:

| 環境変数 | 用途 | 既定値 |
|---|---|---|
| `TRIPLE_OCR_LOG` | `triple_ocr.py` の結果ログ | `~/My Drive/memo/triple-ocr.txt` |
| `NDL_LOG_FILE` | `ndl.py` / `preview.py` の閲覧ログ | `~/My Drive/memo/readkindai.txt` |
| `NDL_SCREENSHOT_DIR` | `ndl.py --full` のスクショ保存先 | `~/Documents/ebook/kindaimemo` |

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
│   └── preview.py        # Preview.app 閲覧ログ記録
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
python3 bin/triple_ocr.py [-h] [--image PATH] [--dir [PATH]] [--pages SPEC]
                          [--pid PID] [--no-biblio] [--no-clipboard] [--no-log] [--ocr-only]
                          [--model NAME] [--engine ENGINE]

--image PATH     スクショ済みの画像ファイルを指定（省略時はインタラクティブ撮影）
--dir [PATH]     ディレクトリ内の画像(.png/.jpg/.jpeg)を一括OCRし honmon.txt に出力（省略時は .）
--pages SPEC     一括モードで扱うコマ番号（例: 10-25, 3,5,10-12）。出力は honmon_p10-25.txt のような
                 範囲付きの名前になる
--pid PID        書誌情報のPIDを明示指定（一括モードで既定は書棚の metadata かディレクトリ名の先頭数字）
--no-biblio      書誌情報の取得をスキップ
--no-clipboard   クリップボードへのコピーをスキップ
--no-log         ログファイルへの保存をスキップ
--ocr-only       OCR結果のみ表示（agy統合をスキップ）
--model NAME     統合校正に使う agy のモデル（`agy models` の ID か表示名）
--engine ENGINE  単一エンジン（google / vision / ndlocr）のみ実行し、統合校正を
                 スキップして生のOCR結果を出力する
```

### 統合校正のモデル（--model）

agy に渡すモデルは既定で `Gemini 3.8 Flash (Low)`。`--model` か環境変数
`TRIPLE_OCR_AGY_MODEL` で変えられる（`--model` が優先）。名前は `agy models` に出る
ID（`gemini-3.8-flash-high`）と表示名（`Gemini 3.8 Flash (High)`）のどちらでもよく、
大文字小文字は区別しない。

```bash
python3 bin/triple_ocr.py --dir 1460379_0001 --model gemini-3.8-flash-high
TRIPLE_OCR_AGY_MODEL="Gemini 3.1 Pro (High)" python3 bin/triple_ocr.py --dir 1460379_0001
```

- 既定以外を指定したときは、起動時に `agy models`（2秒ほど）で名前を確かめ、一覧に無ければ
  使えるモデルを表示して終了コード2で終わる。名前を誤ったまま進むと agy が毎ページ失敗し、
  全ページが Gemini API（従量課金）にフォールバックしてしまうため
- `agy models` 自体が失敗したときは警告を出し、指定された名前をそのまま使う
- Gemini API へのフォールバックは `gemini-2.5-flash` 固定

### agy の利用上限に達したとき

agy が利用上限（`RESOURCE_EXHAUSTED` / `Individual quota reached`）で失敗したときは、
Gemini API（従量課金）には**フォールバックせず**止める。上限以外の失敗（タイムアウト・
認証エラー等）は従来どおり Gemini API にフォールバックする。

- 一括モード（`--dir`）: その時点で校正を止め、校正済みのページだけで `honmon.txt` を書いて
  終了コード1で終わる（校正済みが0ページなら書かない）。上限に達したページは `_pages/` に
  残らないので、解除後に同じコマンドを再実行すればそこから続く
- 単一画像モード: OCR結果をそのまま出力し、通知で上限に達したことを知らせる
- 停止メッセージに解除までの時間（agy の `Resets in …`）を出す。Gemini 系のモデルは上限を
  共有しており、Claude 系は別枠だった（2026-09-29 実測）。すぐ続けたいときは `--model` で
  別枠のモデルを指定する
- 一括モードの `_pages/integrated/` はモデルを区別しない。モデルを変えて作り直すときは
  該当ページの `.txt`（全体なら `_pages/integrated/`）を消してから再実行する

### 単一エンジンモード（--engine）

三系統を回さず1つのエンジンだけで素早くOCRしたいとき用。統合校正を行わないため
数秒で終わる代わりに、誤認識の相互補正は効かない。

```bash
python3 bin/triple_ocr.py --engine google --no-biblio --no-log
```

### 一括モード（ディレクトリまとめOCR）

```bash
python3 bin/triple_ocr.py --dir 1460379_0001
cd ~/Documents/ebook/kindai/2026/"出版者 書名" && triple_ocr.py --dir   # 書棚レイアウト（下記）
```

ディレクトリ内の画像（`.png`/`.jpg`/`.jpeg`、ファイル名昇順）を1枚ずつOCR→agy統合校正し、結果を1つの `honmon.txt` にまとめて対象ディレクトリへ書き出す。単一画像モードとの違い:

- **書誌情報**: Safari ではなく、ディレクトリ直下の `metadata`（書棚レイアウト）か、ディレクトリ名の先頭数字を PID とみなして取得する（例: `1460379_0001` → PID `1460379`）。`--pid` で明示指定も可能。取得した書誌情報は本文冒頭に `【書誌情報】` として付与される。
- **段落結合**: agy に対し、OCRの折り返し改行を段落単位へ結合し段落境界の改行だけ残すよう指示する。
- **旧字体→新字体変換**: 校正後に `ndl_tools/kyujitai.py`（Emacs kreplace の変換表を移植した464組の対応表＋NFC正規化）で決定論的に新字体へ変換する。歴史的仮名遣いはそのまま維持される。変換表には人名・地名の異体字（髙→高、嶋→島 等）も含む。固有名詞の字体を保ちたい場合は「追加分5-c」の行を削除する。
- 各ページは空行で区切って連結される。クリップボードコピー・追記ログは行わない。
- **途中再開**: 各ページの結果を `<対象ディレクトリ>/_pages/<モード>/<画像名>.txt` に保存する（モードは `integrated` / `ocr-only` / `engine-<名前>`）。中断後に同じコマンドを再実行すると、保存済みのページは処理せず、残りのページだけ処理する。特定のページをやり直すときは、そのページの `.txt` を削除して再実行する。agy 校正に失敗して OCR の結果をそのまま使ったページは保存しないため、再実行すると校正し直す。`honmon.txt` は毎回すべてのページから組み立て直す。

- **書棚レイアウト**: 1冊を1ディレクトリにまとめ、画像を `original/` に置き、`metadata`（`https://dl.ndl.go.jp/pid/{pid}` を1行）と `biblio.txt` を並べた構成にも対応する。本のディレクトリ（`original/` の親）を1冊の単位にし、本のディレクトリと `original/` のどちらを渡しても、`original/` の画像を読み、`honmon.txt` と `_pages/` は本のディレクトリ直下に置く。PID は `metadata` から取る。
- **ページ指定（`--pages`）**: `--pages 10-25`（`3,5,10-12` のようにも書ける）で、ファイル名（`0010.jpg` 等）の番号が範囲に入る画像だけを処理する。番号でない名前の画像は対象外。出力は `honmon_p10-25.txt` のように範囲付きの名前にして、範囲を変えて実行しても前の結果を上書きしない（`_pages/` はページ単位なので範囲をまたいで再利用される）。
- **PATH の省略**: `--dir` だけならカレントディレクトリが対象。本のディレクトリに `cd` して `triple_ocr.py --dir` と打てばよい。

### 柱（ランニングヘッダ）の除去

```bash
python3 bin/clean_hashira.py 908672_0001/honmon.txt          # → honmon.clean.txt
python3 bin/clean_hashira.py honmon.txt --report removed.txt # 除去行の一覧も出力
python3 bin/clean_hashira.py honmon.txt --join-without-marker # マーカー無し資料
```

一括OCRした `honmon.txt` には、版面の柱（ノンブル脇の書名・章題）がページごとに混入する。`bin/clean_hashira.py` はそれを行単位で除去し、分断された段落を結合する。

- 除去対象: 書名の柱 / 章題の柱（`第一 〜` 形式。目次と各章冒頭の初出は残す）/ ノンブルのみの行 / 重複した「目次」見出し
- 崩れたOCR（`墓と懺悔`、`第五 生時代` 等）も類似度照合で拾い、`書名 一一八` のようにノンブルが合体した行にも対応する
- **文字は一切書き換えない**（行の削除と結合のみ）。終了前に「除去行を差し引いた原文と出力が完全一致する」ことを自己検証する
- 資料側の前提: 目次終端に「目次終」、奥附の先頭に「奥附」のマーカー行があること（`--toc-end` / `--colophon` で文字列か行番号を指定可能）。目次終マーカーが無い資料では誤削除を避けるため何もしない
- 前付け（漢詩・序）と奥附以降（広告）は行が単位のため、段落結合の対象外

#### マーカーが無い資料での段落結合（`--join-without-marker`）

目次終・奥附のマーカーが無い資料でも、`--join-without-marker` を付けると**柱・ノンブルの除去は行わないまま、ページ跨ぎで分断された段落の結合だけ**を実行する。

`triple_ocr.py --dir` はページを空行で連結するため、ページ境界の切れ目は必ず「空行を挟んだ長い行どうし」になる。この経路は範囲を絞れないぶん、次の条件を**すべて**満たす箇所だけを結合する。

- 直前に空行がある（＝ページ境界か段落境界）
- 前後の行がともに `--min-join-len` 字以上（既定30。見出し・柱・短い引用行を除外する）
- 前の行が `。』」！？）】…―` 等で終わらない（＝文が閉じていない）
- 後の行が `「『（【` や数字で始まらない（＝引用・箇条書きの開始ではない）
- 前後とも章・節見出しではない

自己検証（除去行を差し引いた原文と出力の文字列一致）は従来経路と共通で効く。誤結合が出る資料では `--min-join-len` を上げる（40〜60程度）。マーカーが見つかった資料ではこのフラグは無視され、従来の範囲限定の結合が使われる。

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

結果は `~/My Drive/memo/triple-ocr.txt` に追記される（`TRIPLE_OCR_LOG` で変更可）。

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
（`NDL_LOG_FILE` で変更可）に追記する。`--full` はスクリーンショット
（`~/Documents/ebook/kindaimemo/`、`NDL_SCREENSHOT_DIR` で変更可）も撮る。

```bash
python3 bin/ndl.py --page   # ページ番号のみ追記
python3 bin/ndl.py --full   # 書誌情報＋URL＋タイムスタンプ＋ページ番号＋スクショ
```

書誌情報の取得は `ndl_tools/biblio.py` を使う（`triple_ocr.py` と同じ実装）。

### preview.py — Preview.app 閲覧ログ記録

ダウンロード済み資料を Preview.app で読んでいるときの閲覧状況を、`ndl.py` と同じ
ログファイル（`NDL_LOG_FILE`）に記録する。書誌情報は資料フォルダ内の
`biblio.txt` / `metadata` から読むため、`ndl_tools/biblio.py` には依存しない。

```bash
python3 bin/preview.py --page
python3 bin/preview.py --full
```

## ~/.bin からの利用

`~/.bin` のような PATH の通ったディレクトリに symlink を張ると、どこからでも起動できる。

```bash
ln -sf ~/Documents/github/ndl-tools/bin/ndl.py     ~/.bin/ndl.py
ln -sf ~/Documents/github/ndl-tools/bin/preview.py ~/.bin/preview.py
```

Karabiner の `Cmd+Shift+P` / `Cmd+Shift+M` は `~/.bin/ndl.py`・`~/.bin/preview.py`
を最前面アプリ（Safari / Preview）で振り分けて呼ぶ。symlink 経由なのでこのままでよい。

## 関連リポジトリ

- [ndl-helper](https://github.com/ichibeikatura/ndl-helper) — NDLデジコレの UserScript とローカルHTTPサーバー（`ndl_server.py`）
- [ndl-search](https://github.com/ichibeikatura/ndl-search) — Emacs から NDL を検索する
- [ndl-note-tag](https://github.com/ichibeikatura/ndl-note-tag) — 書誌メモのタグ付け
- [kreplace](https://github.com/ichibeikatura/kreplace) — Emacs の旧字新字変換（`ndl_tools/kyujitai.py` の変換表の出典）
- [proofreader.el](https://github.com/ichibeikatura/proofreader.el) — agy による校正（本ツールの後段で使う）

## ライセンス

MIT License（[LICENSE](LICENSE)）。

`ndl_tools/kyujitai.py` の旧字新字変換表は [kreplace](https://github.com/ichibeikatura/kreplace) から移植したもの。
