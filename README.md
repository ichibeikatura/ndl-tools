# triple-ocr

国立国会図書館デジタルコレクションの資料画像を三種類のOCRエンジンで処理し、agy（Antigravity CLI）で統合校正するmacOS用CLIツール。

## 概要

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

Cmd-Shift-L でグローバル起動する場合、`shell_command` に環境変数と PATH を明示する：

```json
{
  "shell_command": "source /Users/yourname/.secrets && PATH=/Users/yourname/.local/bin:$PATH /Users/yourname/.pyenv/versions/3.10.9/bin/python3 /Users/yourname/Desktop/triple-ocr/triple_ocr.py > /tmp/triple-ocr.log 2>&1"
}
```

- Karabiner は非ログインシェルで実行されるため `.zshrc` は読まれない
- `ndlocr-lite` と `agy` が `~/.local/bin` にある場合は PATH に追加が必要

## ファイル構成

```
triple-ocr/
├── triple_ocr.py   # メインスクリプト
├── ocr_vision.py   # macOS Vision OCR
├── ocr_google.py   # Google Cloud Vision OCR
├── ocr_ndlocr.py   # ndlocr-lite OCR
├── biblio.py       # 書誌情報取得（NDLデジコレ対応）
└── integrator.py   # agy 統合校正（Gemini API フォールバック付き）
```

## 使い方

### 基本（範囲選択してOCR）

```bash
python3 triple_ocr.py
```

実行するとスクリーンキャプチャの範囲選択モードになる。範囲を選択すると、三系統のOCRと書誌情報取得が並列で走り、agy が統合校正した結果が標準出力とクリップボードに出力される。

### オプション

```
python3 triple_ocr.py [-h] [--image PATH] [--no-biblio] [--no-clipboard] [--no-log] [--ocr-only]

--image PATH     スクショ済みの画像ファイルを指定（省略時はインタラクティブ撮影）
--no-biblio      書誌情報の取得をスキップ
--no-clipboard   クリップボードへのコピーをスキップ
--no-log         ログファイルへの保存をスキップ
--ocr-only       OCR結果のみ表示（agy統合をスキップ）
```

### 使用例

```bash
# 既存の画像ファイルを指定して処理
python3 triple_ocr.py --image ~/Desktop/scan.png

# OCR結果を比較したいだけのとき（agyを使わない）
python3 triple_ocr.py --ocr-only

# パイプで後続処理に渡す（進捗はstderrなので混入しない）
python3 triple_ocr.py | pbpaste
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
