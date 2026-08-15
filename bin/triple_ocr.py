#!/usr/bin/env python3
"""triple_ocr.py — 三系統OCR統合校正ツール"""

import argparse
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ~/.bin の symlink 経由でも起動できるよう、実体の位置からリポジトリルートを解決して
# sys.path に加える（resolve() が symlink を辿る）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# rumps は単一画像モードのメニューバー表示専用。一括モード(--dir)では使わないため、
# GUI を使うコードパスでのみ遅延 import する（rumps 未導入環境でも一括は動く）。

from ndl_tools import biblio as biblio_mod
from ndl_tools import integrator, ocr_google, ocr_ndlocr, ocr_vision

# ログファイルのデフォルトパス。環境変数 TRIPLE_OCR_LOG で上書き可能。
DEFAULT_LOG = Path.home() / "My Drive" / "memo" / "triple-ocr.txt"


class Countdown:
    """メニューバーにカウントダウンを表示して処理進捗を示す。"""

    def __init__(self, start: int = 10):
        import rumps

        self._n = start
        self._lock = threading.Lock()
        self._app = rumps.App(str(start), quit_button=None)

    def run_on_main_thread(self):
        """メインスレッドで呼ぶ（ブロッキング）。処理はデーモンスレッドで動かすこと。"""
        self._app.run()

    def tick(self):
        """カウントを1減らしてタイトルを更新する。0以下になったら終了。"""
        import rumps

        with self._lock:
            self._n -= 1
            n = self._n
        self._app.title = str(max(n, 0))
        if n <= 0:
            rumps.quit_application()

    def done(self):
        """処理完了時にメニューバーを即座に消す。"""
        import rumps

        rumps.quit_application()


# 処理中のカウントダウンインスタンス（__main__ 起動時に設定される）
_countdown: Countdown | None = None


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


ALL_ENGINES = {
    "vision": ocr_vision.run,
    "google": ocr_google.run,
    "ndlocr": ocr_ndlocr.run,
}


def run_ocr(image_path: str, only: str = "") -> dict[str, str]:
    """OCRエンジンを並列実行。失敗したエンジンは空文字を返す。

    only にエンジン名を渡すとそのエンジンだけを実行する（--engine）。
    """
    selected = {only: ALL_ENGINES[only]} if only else ALL_ENGINES
    engines = {name: (fn, image_path) for name, fn in selected.items()}
    results = {}
    with ThreadPoolExecutor(max_workers=len(engines)) as executor:
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
            if _countdown:
                _countdown.tick()

    return results


def capture_front_context() -> tuple[str, str]:
    """最前面アプリを判定し、記録元の種別と参照を返す（撮影前に同期実行する）。

    戻り値: (kind, ref)
    - ("safari",  URL)        — Safari が最前面
    - ("preview", ファイル名) — Preview が最前面
    - ("none",    "")         — それ以外・取得失敗
    """
    try:
        app_id = biblio_mod.get_frontmost_app()
        if app_id == "com.apple.Preview":
            try:
                path = biblio_mod.get_preview_file_path()
            except Exception as e:
                eprint(f"[書誌] Previewのファイルパス取得エラー: {e}")
                return "none", ""
            if path:
                filename = Path(path).name
                eprint(f"[書誌] Preview: {filename}")
                return "preview", filename
            eprint("[書誌] Previewのファイルパスを取得できませんでした。")
            return "none", ""
        if app_id and app_id != "com.apple.Safari":
            eprint(f"[書誌] 最前面アプリ（{app_id}）は対象外のためスキップします。")
            return "none", ""
        # Safari（判定に失敗した場合も従来どおり Safari を試す）
        url = biblio_mod.get_safari_url()
        if not url:
            eprint("[書誌] SafariのURLを取得できませんでした。")
            return "none", ""
        return "safari", url
    except Exception as e:
        eprint(f"[書誌] エラー: {e}")
        return "none", ""


def fetch_biblio_for_url(url: str) -> tuple[str, str, str]:
    """Safari の URL から書誌情報テキスト・URL・ページ番号を返す。失敗時は空文字。"""
    try:
        pid, page = biblio_mod.extract_pid_and_page(url)
        if not pid:
            eprint("[書誌] NDLデジコレのURLが見つかりませんでした。")
            return "", url, ""
        eprint(f"[書誌] PID={pid} で取得中…")
        if _countdown:
            _countdown.tick()  # 書誌取得開始
        text = biblio_mod.get_biblio_text(pid)
        if text:
            eprint(f"[書誌] 取得完了: {text}")
        else:
            eprint("[書誌] 書誌情報が取得できませんでした。")
        if _countdown:
            _countdown.tick()  # 書誌取得完了
        return text, url, page
    except Exception as e:
        eprint(f"[書誌] エラー: {e}")
        return "", url, ""


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


def _raw_ocr_text(ocr_results: dict[str, str], labeled: bool = True) -> str:
    """OCR結果をそのまま整形する（agy統合をスキップ／失敗時のフォールバック用）。"""
    parts = []
    for name, label in [("vision", "macOS Vision"), ("google", "Google Cloud Vision"), ("ndlocr", "NDLOCR-Lite")]:
        text = ocr_results.get(name, "")
        if labeled:
            parts.append(f"## {label}\n{text or '（失敗）'}")
        elif text:
            parts.append(text)
    return "\n\n".join(parts)


# 一括モードで扱う画像拡張子（小文字で比較）
BATCH_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _pid_from_dirname(dir_path: Path) -> str:
    """ディレクトリ名の先頭の数字列を PID とみなす（例: 1460379_0001 → 1460379）。"""
    m = re.match(r"(\d+)", dir_path.name)
    return m.group(1) if m else ""


def run_batch(dir_path: Path, args) -> int:
    """ディレクトリ内の画像を一括でOCR→agy統合→旧字新字変換し、honmon.txt に書き出す。"""
    from ndl_tools import kyujitai

    images = sorted(
        p for p in dir_path.iterdir()
        if p.is_file() and p.suffix.lower() in BATCH_EXTENSIONS
    )
    if not images:
        eprint(f"[エラー] {dir_path} に対象画像（.png/.jpg/.jpeg）が見つかりません。")
        return 1
    eprint(f"[一括] {len(images)} 枚を処理します: {dir_path}")

    # 書誌情報（PID はディレクトリ名先頭の数字、--pid で上書き可）
    biblio_text = ""
    if not args.no_biblio:
        pid = args.pid or _pid_from_dirname(dir_path)
        if pid:
            eprint(f"[書誌] PID={pid} で取得中…")
            try:
                biblio_text = biblio_mod.get_biblio_text(pid)
                eprint(f"[書誌] 取得完了: {biblio_text}" if biblio_text else "[書誌] 取得できませんでした。")
            except Exception as e:
                eprint(f"[書誌] エラー: {e}")
        else:
            eprint("[書誌] PID を特定できませんでした（--pid で指定できます）。")

    pages: list[str] = []
    for i, image in enumerate(images, 1):
        eprint(f"\n[{i}/{len(images)}] {image.name}")
        ocr_results = run_ocr(str(image), only=args.engine or "")
        successful = [name for name, text in ocr_results.items() if text]
        if not successful:
            eprint(f"[{i}/{len(images)}] 全OCR失敗 — このページをスキップします。")
            continue
        eprint(f"[{i}/{len(images)}] OCR成功: {', '.join(successful)}")

        if args.ocr_only or args.engine:
            page_text = _raw_ocr_text(ocr_results, labeled=False)
        else:
            try:
                page_text = integrator.integrate(
                    vision_text=ocr_results.get("vision", ""),
                    google_text=ocr_results.get("google", ""),
                    ndlocr_text=ocr_results.get("ndlocr", ""),
                    biblio=biblio_text,
                    join_lines=True,
                )
                eprint(f"[{i}/{len(images)}] 校正完了")
            except Exception as e:
                eprint(f"[{i}/{len(images)}] 校正失敗: {e} — OCR結果をそのまま採用します")
                page_text = _raw_ocr_text(ocr_results, labeled=False)

        pages.append(kyujitai.to_shinjitai(page_text.strip()))

    if not pages:
        eprint("[エラー] 全ページの処理に失敗しました。")
        return 1

    # honmon.txt を組み立てる（書誌情報を本文冒頭に、ページは空行で区切る）
    blocks: list[str] = []
    if biblio_text:
        blocks.append("【書誌情報】\n" + kyujitai.to_shinjitai(biblio_text))
    blocks.extend(pages)
    content = "\n\n".join(blocks) + "\n"

    out_path = dir_path / "honmon.txt"
    out_path.write_text(content, encoding="utf-8")
    eprint(f"\n[完了] {len(pages)} ページを書き出しました: {out_path}")
    return 0


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
    parser.add_argument("--dir", metavar="PATH", help="ディレクトリ内の画像(.png/.jpg/.jpeg)を一括OCRし honmon.txt に出力（旧字→新字変換・段落結合あり）")
    parser.add_argument("--pid", metavar="PID", help="書誌情報のPIDを明示指定（一括モードで既定はディレクトリ名の先頭数字）")
    parser.add_argument("--no-biblio", action="store_true", help="書誌情報の取得をスキップ")
    parser.add_argument("--no-clipboard", action="store_true", help="クリップボードへのコピーをスキップ")
    parser.add_argument("--no-log", action="store_true", help="ログファイルへの保存をスキップ")
    parser.add_argument("--ocr-only", action="store_true", help="OCR結果のみ表示（agy統合をスキップ）")
    parser.add_argument(
        "--engine",
        choices=sorted(ALL_ENGINES),
        help="単一エンジンのみ実行し、統合校正をスキップして生のOCR結果を出力する",
    )
    args = parser.parse_args()

    # 一括モード（--dir）は GUI を使わず同期実行して終了する。
    if args.dir:
        return run_batch(Path(args.dir).expanduser().resolve(), args)

    log_path = Path(os.environ.get("TRIPLE_OCR_LOG", str(DEFAULT_LOG))).expanduser()

    # 1. 最前面アプリの判定と URL/ファイル名の取得（撮影・OCR中にユーザーが
    #    アプリを切り替えても影響しないよう、撮影より先に同期実行する）
    src_kind, src_ref = ("none", "") if args.no_biblio else capture_front_context()

    # 2. 画像の準備
    if args.image:
        image_path = str(Path(args.image).expanduser().resolve())
        eprint(f"[画像] {image_path}")
    else:
        eprint("[撮影] 範囲を選択してください…")
        image_path = str(take_screenshot())
        eprint(f"[撮影] 保存: {image_path}")
        if _countdown:
            _countdown.tick()  # 9: 撮影保存完了

    # 3. 書誌情報のネットワーク取得（Safari時のみ。OCRと並列化するため先にスレッドへ投げる）
    with ThreadPoolExecutor(max_workers=1) as biblio_executor:
        biblio_future = biblio_executor.submit(fetch_biblio_for_url, src_ref) if src_kind == "safari" else None

        # 4. OCR並列実行
        eprint(f"[OCR] {args.engine} を実行中…" if args.engine else "[OCR] 三系統を並列実行中…")
        if _countdown:
            _countdown.tick()  # 8: OCR並列実行開始
        ocr_results = run_ocr(image_path, only=args.engine or "")

        biblio_text, ndl_url, page = "", "", ""
        if src_kind == "preview":
            ndl_url = src_ref  # Preview のファイル名を URL 欄に記録する
        if biblio_future is not None:
            try:
                biblio_text, ndl_url, page = biblio_future.result()
            except Exception as e:
                eprint(f"[書誌] エラー: {e}")

    # 5. OCR結果確認
    successful = [name for name, text in ocr_results.items() if text]
    if not successful:
        eprint("[エラー] 全OCRエンジンが失敗しました。")
        sys.exit(1)
    eprint(f"[OCR] 成功: {', '.join(successful)}")
    if _countdown:
        _countdown.tick()  # 2: OCR成功判定

    # 6. 単一エンジン（--engine）は生テキスト、--ocr-only はエンジン名つきで出力する
    if args.engine:
        result_text = _raw_ocr_text(ocr_results, labeled=False)
    elif args.ocr_only:
        result_text = _raw_ocr_text(ocr_results, labeled=True)
    else:
        # 7. agy統合
        eprint("[校正] agy で統合中…")
        if _countdown:
            _countdown.tick()  # 1: agy統合校正中
        try:
            result_text = integrator.integrate(
                vision_text=ocr_results.get("vision", ""),
                google_text=ocr_results.get("google", ""),
                ndlocr_text=ocr_results.get("ndlocr", ""),
                biblio=biblio_text,
            )
            eprint("[校正] 完了")
        except Exception as e:
            eprint(f"[校正] 失敗: {e} — OCR結果をそのまま表示します")
            result_text = _raw_ocr_text(ocr_results, labeled=True)

    # 8. 出力
    # rumps(Cocoa) の終了経路はインタプリタの正常終了を経ないため、パイプ・
    # リダイレクト時にブロックバッファのまま失われる。明示的にフラッシュする。
    print(result_text, flush=True)

    # 9. ログ保存
    if not args.no_log:
        try:
            save_to_log(result_text, biblio_text, ndl_url, page, log_path)
        except Exception as e:
            eprint(f"[警告] ログ保存に失敗: {e}")

    # 10. クリップボード
    if not args.no_clipboard:
        try:
            copy_to_clipboard(result_text)
            eprint("[完了] クリップボードにコピーしました。")
        except Exception as e:
            eprint(f"[警告] クリップボードへのコピーに失敗: {e}")

    notify("triple-ocr", "校正完了 — クリップボードにコピーしました" if not args.no_clipboard else "校正完了")


if __name__ == "__main__":
    # 一括モード(--dir)と --help は GUI カウントダウンを使わず同期実行する。
    # （GUI スレッドを先に起動すると argparse の出力が表示されないまま終了するため）
    if "--dir" in sys.argv or "-h" in sys.argv or "--help" in sys.argv:
        sys.exit(main() or 0)

    _countdown = Countdown(10)

    def _worker():
        try:
            main()
        finally:
            _countdown.done()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    _countdown.run_on_main_thread()  # Cocoa ランループはメインスレッドで動かす
