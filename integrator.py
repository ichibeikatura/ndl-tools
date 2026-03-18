"""Gemini CLI による三系統OCR統合校正"""

import subprocess


PROMPT_TEMPLATE = """\
以下は、同一の近代日本語資料画像に対して三種類のOCRエンジンを実行した結果です。
三つの結果を比較し、最も正確なテキストを復元してください。

## 書誌情報
{biblio}

## OCR結果1: macOS Vision
{vision}

## OCR結果2: Google Cloud Vision
{google}

## OCR結果3: NDLOCR-Lite（国立国会図書館開発・近代資料特化）
{ndlocr}

## 指示
- 三つのOCR結果を比較し、文脈と書誌情報を考慮して最も正確なテキストを復元してください
- 原文の表記を尊重し、旧字体・歴史的仮名遣いはそのまま維持してください
- 新字体への正規化、現代仮名遣いへの変換は行わないでください
- ルビ（振り仮名）がOCR結果に混入している場合は除去してください
- 明らかな誤認識のみ修正し、判断できない箇所はOCR結果のうち最も信頼度の高いものを採用してください
- 復元したテキストのみを出力してください。説明や注釈は不要です\
"""


def integrate(
    vision_text: str,
    google_text: str,
    ndlocr_text: str,
    biblio: str = "",
) -> str:
    """3つのOCR結果をGemini CLIで統合校正する"""
    prompt = PROMPT_TEMPLATE.format(
        biblio=biblio or "（取得できませんでした）",
        vision=vision_text or "（失敗）",
        google=google_text or "（失敗）",
        ndlocr=ndlocr_text or "（失敗）",
    )

    result = subprocess.run(
        ["gemini", "-m", "gemini-2.5-pro", "-o", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gemini failed: {result.stderr}")

    return result.stdout.strip()
