# triple-ocr

国立国会図書館デジタルコレクションの資料画像を三種類のOCRエンジンで処理し、Gemini CLIで統合校正するmacOS用CLIツール。

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
3つのOCR結果 + 書誌情報 → Gemini CLI で統合校正
        │
        ▼
校正済みテキストをクリップボードにコピー + 標準出力
```

## 必要環境

- macOS（Apple Silicon 推奨）
- Python 3.10+
- [Gemini CLI](https://github.com/google-gemini/gemini-cli)（`gemini` コマンドがPATHにあること）
- [ndlocr-lite](https://github.com/ndl-lab/ndlocr_cli)（`uv tool install` 等でインストール済みであること）

### Pythonパッケージ

```bash
pip install pyobjc-framework-Vision requests
```

### 環境変数

```bash
export GOOGLE_CLOUD_VISION_API_KEY="your_api_key"
```

## ファイル構成

```
triple-ocr/
├── triple_ocr.py   # メインスクリプト
├── ocr_vision.py   # macOS Vision OCR
├── ocr_google.py   # Google Cloud Vision OCR
├── ocr_ndlocr.py   # ndlocr-lite OCR
├── biblio.py       # 書誌情報取得（NDLデジコレ対応）
└── integrator.py   # Gemini CLI 統合校正
```

## 使い方

### 基本（範囲選択してOCR）

```bash
python3 triple_ocr.py
```

実行するとスクリーンキャプチャの範囲選択モードになる。範囲を選択すると、三系統のOCRと書誌情報取得が並列で走り、Geminiが統合校正した結果が標準出力とクリップボードに出力される。

### オプション

```
python3 triple_ocr.py [-h] [--image PATH] [--no-biblio] [--no-clipboard] [--ocr-only]

--image PATH     スクショ済みの画像ファイルを指定（省略時はインタラクティブ撮影）
--no-biblio      書誌情報の取得をスキップ
--no-clipboard   クリップボードへのコピーをスキップ
--ocr-only       OCR結果のみ表示（Gemini統合をスキップ）
```

### 使用例

```bash
# 既存の画像ファイルを指定して処理
python3 triple_ocr.py --image ~/Desktop/scan.png

# OCR結果を比較したいだけのとき（Geminiを使わない）
python3 triple_ocr.py --ocr-only

# パイプで後続処理に渡す（進捗はstderrなので混入しない）
python3 triple_ocr.py | pbpaste
```

## 書誌情報の自動取得

Safariで NDLデジタルコレクション（`dl.ndl.go.jp` または `lab.ndl.go.jp`）を開いている状態で実行すると、URL から PID を抽出し、JapanLinkCenter API（フォールバック: NDL SRU API）で書誌情報を取得する。取得した書誌情報はGeminiへのプロンプトに含まれ、校正精度の向上に使われる。

Safariが NDLデジコレを開いていない場合は警告のみ出して続行する。

## エラーハンドリング

| ケース | 動作 |
|---|---|
| 個別OCRエンジンの失敗 | 警告を表示して残りのエンジンで続行 |
| 全OCRエンジンの失敗 | エラー終了 |
| Gemini CLIの失敗 | OCR結果をそのまま表示して終了 |
| 書誌情報取得の失敗 | 警告のみ、続行 |
| スクリーンキャプチャのキャンセル（Esc） | 正常終了 |
