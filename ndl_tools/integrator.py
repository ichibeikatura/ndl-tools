"""agy（Antigravity CLI）による三系統OCR統合校正（Gemini API フォールバック付き）"""

import os
import subprocess
import sys


MODEL_AGY = "Gemini 3.1 Pro (High)"  # agy 主経路
# API フォールバックは gemini-2.5-flash（_integrate_via_api 内で指定）

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
{join_instruction}- 復元したテキストのみを出力してください。説明や注釈は不要です\
"""

# 一括モード用の追加指示。OCRの行内改行（レイアウト由来の折り返し）を段落単位に
# まとめ、段落境界の改行だけを残す。
JOIN_INSTRUCTION = (
    "- OCR結果の改行はレイアウト上の折り返しを含みます。文が続く折り返しの改行は"
    "除去して段落単位につなげ、段落の境界（改行して新しい段落が始まる箇所）だけ"
    "改行を残してください\n"
)


def _build_prompt(
    vision_text: str,
    google_text: str,
    ndlocr_text: str,
    biblio: str,
    join_lines: bool = False,
) -> str:
    return PROMPT_TEMPLATE.format(
        biblio=biblio or "（取得できませんでした）",
        vision=vision_text or "（失敗）",
        google=google_text or "（失敗）",
        ndlocr=ndlocr_text or "（失敗）",
        join_instruction=JOIN_INSTRUCTION if join_lines else "",
    )


def _find_agy() -> str:
    """GUIアプリ起動時はPATHが制限されるため、既知の場所も含めて探す"""
    search_dirs = os.environ.get("PATH", "").split(":") + [
        os.path.expanduser("~/.local/bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    for d in search_dirs:
        candidate = os.path.join(d, "agy")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("agy コマンドが見つかりません（PATH を確認してください）")


def _integrate_via_agy(prompt: str) -> str:
    agy_cmd = _find_agy()
    env = os.environ.copy()
    env["PATH"] = ":".join([
        os.path.expanduser("~/.local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        env.get("PATH", ""),
    ])
    # agy は -p（print）モードでもプロンプトを引数で受け取りつつ、stdout を
    # 出力する前に stdin の EOF を待つ。stdin を継承したまま（TTY やパイプが
    # 開いたまま）だと EOF が来ず agy がハングするため、明示的に閉じる。
    result = subprocess.run(
        [agy_cmd, "--model", MODEL_AGY, "-p", prompt],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"agy エラー (code={result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def _integrate_via_api(prompt: str) -> str:
    import google.generativeai as genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数 GEMINI_API_KEY が設定されていません")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)
    return response.text.strip()


def integrate(
    vision_text: str,
    google_text: str,
    ndlocr_text: str,
    biblio: str = "",
    join_lines: bool = False,
) -> str:
    """3つのOCR結果をagy CLIで統合校正する。失敗時はGemini APIにフォールバック

    join_lines=True で一括モード用に、行内改行を段落単位へ結合する指示を加える。
    """
    prompt = _build_prompt(vision_text, google_text, ndlocr_text, biblio, join_lines)

    try:
        return _integrate_via_agy(prompt)
    except Exception as e:
        print(f"[警告] agy 失敗 ({e.__class__.__name__}: {e})、Gemini API にフォールバックします", file=sys.stderr)
        return _integrate_via_api(prompt)
