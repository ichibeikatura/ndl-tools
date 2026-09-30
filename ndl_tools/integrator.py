"""agy（Antigravity CLI）による三系統OCR統合校正（Gemini API フォールバック付き）"""

import os
import re
import subprocess
import sys


MODEL_AGY = "Gemini 3.8 Flash (Low)"  # agy 主経路の既定。--model / TRIPLE_OCR_AGY_MODEL で変更可
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


class AgyQuotaError(RuntimeError):
    """agy の利用上限に達した。Gemini API（従量課金）にはフォールバックせず止める。"""

    def __init__(self, model: str, reset: str = ""):
        self.model = model
        self.reset = reset
        when = f"、解除まで {reset}" if reset else ""
        super().__init__(f"agy の利用上限に達しました（{model}{when}）")


# 上限到達時の agy の出力（実測）:
#   error: Individual quota reached. ... Resets in 2m59s.
#   AGY_ERROR: {..."status":"RESOURCE_EXHAUSTED","error_code":429,...}
_QUOTA_PATTERN = re.compile(r"RESOURCE_EXHAUSTED|quota reached", re.IGNORECASE)
_RESET_PATTERN = re.compile(r"Resets in ([0-9hms]+)")


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


def _agy_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = ":".join([
        os.path.expanduser("~/.local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        env.get("PATH", ""),
    ])
    return env


def list_agy_models() -> list[tuple[str, str]]:
    """`agy models` の一覧を (ID, 表示名) のリストで返す。"""
    result = subprocess.run(
        [_find_agy(), "models"],
        capture_output=True,
        text=True,
        env=_agy_env(),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(f"agy models エラー (code={result.returncode}): {result.stderr.strip()}")
    # 1行1モデルで「ID<TAB>表示名」。"Fetching available models..." 等の行は読み飛ばす
    return [
        tuple(line.split("\t", 1))
        for line in result.stdout.splitlines()
        if "\t" in line
    ]


def resolve_agy_model(name: str) -> str:
    """モデル名（ID か表示名。大文字小文字は区別しない）を agy に渡す表示名に解決する。

    名前を誤ったまま進むと agy が毎ページ失敗し、Gemini API（従量課金）への
    フォールバックが全ページで起きるため、一覧に無い名前は ValueError にする。
    一覧そのものを取れないときは、名前をそのまま使う。
    """
    try:
        models = list_agy_models()
    except Exception as e:
        print(f"[警告] agy のモデル一覧を取得できませんでした（{e}）。'{name}' をそのまま使います", file=sys.stderr)
        return name
    key = name.strip().casefold()
    for model_id, display in models:
        if key in (model_id.casefold(), display.casefold()):
            return display
    available = "\n".join(f"  {model_id}\t{display}" for model_id, display in models)
    raise ValueError(f"agy に '{name}' というモデルはありません。使えるモデル:\n{available}")


def _integrate_via_agy(prompt: str, model: str) -> str:
    agy_cmd = _find_agy()
    env = _agy_env()
    # agy は -p（print）モードでもプロンプトを引数で受け取りつつ、stdout を
    # 出力する前に stdin の EOF を待つ。stdin を継承したまま（TTY やパイプが
    # 開いたまま）だと EOF が来ず agy がハングするため、明示的に閉じる。
    result = subprocess.run(
        [agy_cmd, "--model", model, "-p", prompt],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0 and _QUOTA_PATTERN.search(result.stderr):
        reset = _RESET_PATTERN.search(result.stderr)
        raise AgyQuotaError(model, reset.group(1) if reset else "")
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
    model: str = MODEL_AGY,
) -> str:
    """3つのOCR結果をagy CLIで統合校正する。失敗時はGemini APIにフォールバック

    ただし agy の利用上限に達したときは、課金が膨らまないようフォールバックせず
    AgyQuotaError を送出する。

    join_lines=True で一括モード用に、行内改行を段落単位へ結合する指示を加える。
    model は agy に渡すモデルの表示名（resolve_agy_model() で解決したもの）。
    """
    prompt = _build_prompt(vision_text, google_text, ndlocr_text, biblio, join_lines)

    try:
        return _integrate_via_agy(prompt, model)
    except AgyQuotaError:
        raise
    except Exception as e:
        print(f"[警告] agy 失敗 ({e.__class__.__name__}: {e})、Gemini API にフォールバックします", file=sys.stderr)
        return _integrate_via_api(prompt)
