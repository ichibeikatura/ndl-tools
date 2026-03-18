#!/usr/bin/env python3
"""triple_ocr.py — 三系統OCR統合校正ツール"""

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import biblio as biblio_mod
import integrator
import ocr_google
import ocr_ndlocr
import ocr_vision

# ログファイルのデフォルトパス。環境変数 TRIPLE_OCR_LOG で上書き可能。
DEFAULT_LOG = Path.home() / "My Drive" / "memo" / "triple-ocr.txt"


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def take_screenshot() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(f"/tmp/triple_ocr_{timestamp}.png")
    result = subprocess.run(["screencapture", "-i", str(path)])
    if result.returncode != 0 or not path.exists():
        eprint("スクリーンショットがキャンセルされました。")
        sys.exit(0)
    return path


def run_ocr(image_path: str) -> dict[str, str]:
    """3エンジンを並列実行。失敗したエンジンは空文字を返す。"""
    engines = {
        "vision": (ocr_vision.run, image_path),
        "google": (ocr_google.run, image_path),
        "ndlocr": (ocr_ndlocr.run, image_path),
    }
    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(fn, arg): name
            for name, (fn, arg) in engines.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                eprint(f"[OCR] {name}: 完了")
            except Exception as e:
                eprint(f"[OCR] {name}: 失敗 — {e}")
                results[name] = ""

    return results


def get_biblio_and_page() -> tuple[str, str, str]:
    """書誌情報テキスト・URL・ページ番号を返す。失敗時は空文字。"""
    try:
        url = biblio_mod.get_safari_url()
        if not url:
            eprint("[書誌] SafariのURLを取得できませんでした。")
            return "", "", ""
        pid, page = biblio_mod.extract_pid_and_page(url)
        if not pid:
            eprint("[書誌] NDLデジコレのURLが見つかりませんでした。")
            return "", url, ""
        eprint(f"[書誌] PID={pid} で取得中…")
        text = biblio_mod.get_biblio_text(pid)
        if text:
            eprint(f"[書誌] 取得完了: {text}")
        else:
            eprint("[書誌] 書誌情報が取得できませんでした。")
        return text, url, page
    except Exception as e:
        eprint(f"[書誌] エラー: {e}")
        return "", "", ""


def save_to_log(result_text: str, biblio: str, url: str, page: str, log_path: Path):
    """結果をログファイルに追記する。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    if biblio:
        lines.append(biblio)
    if url:
        lines.append(url)
    lines.append(f"{timestamp}:")
    if page:
        lines.append(f"p{page}")
    lines.append(result_text)
    lines.append("")  # エントリ末尾の空行

    entry = "\n".join(lines) + "\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)
    eprint(f"[ログ] 保存: {log_path}")


def copy_to_clipboard(text: str):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def notify(title: str, message: str):
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


def main():
    parser = argparse.ArgumentParser(
        description="三系統OCR統合校正ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--image", metavar="PATH", help="スクショ済みの画像ファイルを指定（省略時はインタラクティブ撮影）")
    parser.add_argument("--no-biblio", action="store_true", help="書誌情報の取得をスキップ")
    parser.add_argument("--no-clipboard", action="store_true", help="クリップボードへのコピーをスキップ")
    parser.add_argument("--no-log", action="store_true", help="ログファイルへの保存をスキップ")
    parser.add_argument("--ocr-only", action="store_true", help="OCR結果のみ表示（Gemini統合をスキップ）")
    args = parser.parse_args()

    log_path = Path(os.environ.get("TRIPLE_OCR_LOG", str(DEFAULT_LOG))).expanduser()

    # 1. 画像の準備
    if args.image:
        image_path = str(Path(args.image).expanduser().resolve())
        eprint(f"[画像] {image_path}")
    else:
        eprint("[撮影] 範囲を選択してください…")
        image_path = str(take_screenshot())
        eprint(f"[撮影] 保存: {image_path}")

    # 2. 書誌情報取得（並列化のため先にスレッドへ投げる）
    with ThreadPoolExecutor(max_workers=1) as biblio_executor:
        biblio_future = None if args.no_biblio else biblio_executor.submit(get_biblio_and_page)

        # 3. OCR並列実行
        eprint("[OCR] 三系統を並列実行中…")
        ocr_results = run_ocr(image_path)

        biblio_text, ndl_url, page = "", "", ""
        if biblio_future is not None:
            try:
                biblio_text, ndl_url, page = biblio_future.result()
            except Exception as e:
                eprint(f"[書誌] エラー: {e}")

    # 4. OCR結果確認
    successful = [name for name, text in ocr_results.items() if text]
    if not successful:
        eprint("[エラー] 全OCRエンジンが失敗しました。")
        sys.exit(1)
    eprint(f"[OCR] 成功: {', '.join(successful)}")

    # 5. --ocr-only モード
    if args.ocr_only:
        output_parts = []
        for name, label in [("vision", "macOS Vision"), ("google", "Google Cloud Vision"), ("ndlocr", "NDLOCR-Lite")]:
            text = ocr_results.get(name, "")
            output_parts.append(f"## {label}\n{text or '（失敗）'}")
        result_text = "\n\n".join(output_parts)
    else:
        # 6. Gemini統合
        eprint("[Gemini] 統合校正中…")
        try:
            result_text = integrator.integrate(
                vision_text=ocr_results.get("vision", ""),
                google_text=ocr_results.get("google", ""),
                ndlocr_text=ocr_results.get("ndlocr", ""),
                biblio=biblio_text,
            )
            eprint("[Gemini] 完了")
        except Exception as e:
            eprint(f"[Gemini] 失敗: {e} — OCR結果をそのまま表示します")
            output_parts = []
            for name, label in [("vision", "macOS Vision"), ("google", "Google Cloud Vision"), ("ndlocr", "NDLOCR-Lite")]:
                text = ocr_results.get(name, "")
                if text:
                    output_parts.append(f"## {label}\n{text}")
            result_text = "\n\n".join(output_parts)

    # 7. 出力
    print(result_text)

    # 8. ログ保存
    if not args.no_log:
        try:
            save_to_log(result_text, biblio_text, ndl_url, page, log_path)
        except Exception as e:
            eprint(f"[警告] ログ保存に失敗: {e}")

    # 9. クリップボード
    if not args.no_clipboard:
        try:
            copy_to_clipboard(result_text)
            eprint("[完了] クリップボードにコピーしました。")
        except Exception as e:
            eprint(f"[警告] クリップボードへのコピーに失敗: {e}")

    notify("triple-ocr", "校正完了 — クリップボードにコピーしました" if not args.no_clipboard else "校正完了")


if __name__ == "__main__":
    main()
