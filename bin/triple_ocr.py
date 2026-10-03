#!/usr/bin/env python3
"""triple_ocr.py — 三系統OCR統合校正ツール"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
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
from ndl_tools import pdf as pdf_mod

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

# 書棚レイアウト（1冊1ディレクトリ）: {本のディレクトリ}/{original/, biblio.txt, metadata}
# metadata には https://dl.ndl.go.jp/pid/{pid} が1行だけ書かれている。
# 一部のコマだけ置いた本は metadata が無く、biblio.txt だけあることがある。
SHELF_IMAGE_SUBDIR = "original"
SHELF_METADATA = "metadata"
SHELF_BIBLIO = "biblio.txt"


def _list_images(dir_path: Path) -> list[Path]:
    return sorted(
        p for p in dir_path.iterdir()
        if p.is_file() and p.suffix.lower() in BATCH_EXTENSIONS
    )


def _list_pdfs(dir_path: Path) -> list[Path]:
    return sorted(p for p in dir_path.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def _parse_pages(spec: str) -> set[int]:
    """"10-25" や "3,5,10-12" をコマ番号（1始まり）の集合にする。"""
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"ページ指定を解釈できません: {part!r}（例: 10-25, 3,5,10-12）")
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
        if first < 1 or last < first:
            raise ValueError(f"ページ範囲が不正です: {part!r}")
        pages.update(range(first, last + 1))
    return pages


def _format_pages(pages: set[int]) -> str:
    """コマ番号の集合を "3,5,10-12" の形に戻す（honmon のファイル名に使う）。"""
    runs: list[list[int]] = []
    for n in sorted(pages):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def _select_pages(images: list[Path], pages: set[int] | None) -> list[Path]:
    """--pages 指定時は、ファイル名（0010.jpg 等）の番号が範囲に入る画像だけを残す。

    書棚ではファイル名の番号がコマ番号になっている。番号でない名前の画像は対象外。
    """
    if not pages:
        return images
    return [p for p in images if p.stem.isdigit() and int(p.stem) in pages]


def _honmon_name(args) -> str:
    return f"honmon_p{_format_pages(args.pages)}.txt" if args.pages else "honmon.txt"


def _pid_from_dirname(dir_path: Path) -> str:
    """ディレクトリ名の先頭の数字列を PID とみなす（例: 1460379_0001 → 1460379）。"""
    m = re.match(r"(\d+)", dir_path.name)
    return m.group(1) if m else ""


def _pid_from_metadata(book_dir: Path) -> str:
    """書棚ディレクトリの metadata から PID を読む。無ければ空文字。"""
    try:
        url = (book_dir / SHELF_METADATA).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return biblio_mod.extract_pid_and_page(url)[0]


def _resolve_batch_dirs(dir_path: Path) -> tuple[Path, Path]:
    """(画像の置き場所, 出力先) を返す。

    書棚レイアウトでは、本のディレクトリ（original/ の親。biblio.txt・metadata と同じ階層）を
    1冊の単位にする。本のディレクトリと original/ のどちらを渡されても、original/ の画像を読み、
    honmon.txt と _pages/ は本のディレクトリ直下に置く。
    それ以外（画像が直下にあるディレクトリ）は、そのディレクトリ自体を使う。
    """
    if dir_path.name == SHELF_IMAGE_SUBDIR and any(
            (dir_path.parent / name).is_file() for name in (SHELF_METADATA, SHELF_BIBLIO)):
        return dir_path, dir_path.parent
    image_dir = dir_path / SHELF_IMAGE_SUBDIR
    if image_dir.is_dir() and not _list_images(dir_path):
        return image_dir, dir_path
    return dir_path, dir_path


# 一括モードのページ単位の中間ファイル置き場（対象ディレクトリ直下）。
# モードごとにサブフォルダを分け、統合校正の結果と --ocr-only 等の結果が混ざらないようにする。
BATCH_PAGES_DIR = "_pages"


def _batch_mode(args) -> str:
    """中間ファイルのサブフォルダ名に使うモード名。"""
    if args.engine:
        return f"engine-{args.engine}"
    if args.ocr_only:
        return "ocr-only"
    return "integrated"


def _write_page_cache(path: Path, text: str):
    """中断で書きかけのファイルが残らないよう、一時ファイル経由で置き換える。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _fetch_batch_biblio(pid: str, args) -> str:
    """一括モード用の書誌情報を取得する。失敗しても空文字で続行する。"""
    if args.no_biblio:
        return ""
    if not pid:
        eprint("[書誌] PID を特定できませんでした（--pid で指定できます）。")
        return ""
    eprint(f"[書誌] PID={pid} で取得中…")
    try:
        biblio_text = biblio_mod.get_biblio_text(pid)
        eprint(f"[書誌] 取得完了: {biblio_text}" if biblio_text else "[書誌] 取得できませんでした。")
        return biblio_text
    except Exception as e:
        eprint(f"[書誌] エラー: {e}")
        return ""


def _page_cache_dir(out_dir: Path, args) -> Path:
    cache_dir = out_dir / BATCH_PAGES_DIR / _batch_mode(args)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _ocr_page(image: Path, label: str, cache_dir: Path, biblio_text: str, args) -> str | None:
    """1ページをOCR→agy統合→旧字新字変換する。全OCRが失敗したら None。

    処理済みページは中間ファイルから読み込む（特定ページをやり直すときは該当の .txt を消す）。
    """
    from ndl_tools import kyujitai

    cache_path = cache_dir / f"{image.stem}.txt"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    eprint(f"\n[{label}] {image.name}")
    ocr_results = run_ocr(str(image), only=args.engine or "")
    successful = [name for name, text in ocr_results.items() if text]
    if not successful:
        eprint(f"[{label}] 全OCR失敗 — このページをスキップします。")
        return None
    eprint(f"[{label}] OCR成功: {', '.join(successful)}")

    # 校正に失敗したページは中間ファイルを残さず、再実行時に校正し直す
    cacheable = True
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
                model=args.model,
            )
            eprint(f"[{label}] 校正完了")
        except integrator.AgyQuotaError:
            raise
        except Exception as e:
            eprint(f"[{label}] 校正失敗: {e} — OCR結果をそのまま採用します")
            page_text = _raw_ocr_text(ocr_results, labeled=False)
            cacheable = False

    page_text = kyujitai.to_shinjitai(page_text.strip())
    if cacheable:
        _write_page_cache(cache_path, page_text)
    return page_text


def _quota_message(e: "integrator.AgyQuotaError") -> str:
    return (f"\n[停止] {e}。Gemini API にはフォールバックせず、ここで校正を止めます。\n"
            "       解除後に同じコマンドを再実行すると、校正済みのページを飛ばして続きから処理します。"
            "\n       Gemini 系のモデルは上限を共有しているので、すぐ続けるなら --model で Claude 系などを指定します。")


def _write_honmon(out_dir: Path, biblio_text: str, pages: list[str], name: str = "honmon.txt") -> int:
    """honmon.txt を組み立てる（書誌情報を本文冒頭に、ページは空行で区切る）。

    --pages 指定時は範囲付きの名前（honmon_p10-25.txt 等）にして、範囲ごとの結果を上書きしない。
    """
    from ndl_tools import kyujitai

    if not pages:
        eprint("[エラー] 全ページの処理に失敗しました。")
        return 1

    blocks: list[str] = []
    if biblio_text:
        blocks.append("【書誌情報】\n" + kyujitai.to_shinjitai(biblio_text))
    blocks.extend(pages)
    content = "\n\n".join(blocks) + "\n"

    out_path = out_dir / name
    out_path.write_text(content, encoding="utf-8")
    eprint(f"\n[完了] {len(pages)} ページを書き出しました: {out_path}")
    return 0


def run_batch(dir_path: Path, args) -> int:
    """ディレクトリ内の画像と PDF を一括でテキスト化する。

    画像はまとめて honmon.txt に、PDF は1ファイルずつ <PDF名>.txt に（PDF と同じ場所へ）書き出す。
    agy の利用上限に達したら、残りのファイルも処理せずに止める。
    """
    pdfs = _list_pdfs(dir_path)
    image_dir, _ = _resolve_batch_dirs(dir_path)
    # PDF だけのディレクトリでは画像の一括処理を飛ばす（画像が無いというエラーを出さない）
    run_images = not pdfs or bool(_list_images(image_dir))

    status = 0
    try:
        if run_images:
            status = max(status, _run_images(dir_path, args))
        for pdf_path in pdfs:
            status = max(status, _run_pdf(pdf_path, args))
    except integrator.AgyQuotaError:
        return 1
    return status


def _run_images(dir_path: Path, args) -> int:
    """ディレクトリ内の画像を一括でOCR→agy統合→旧字新字変換し、honmon.txt に書き出す。"""
    image_dir, out_dir = _resolve_batch_dirs(dir_path)
    if out_dir != image_dir and (image_dir / BATCH_PAGES_DIR).is_dir():
        eprint(f"[一括] {image_dir / BATCH_PAGES_DIR} は使いません（途中結果は {out_dir / BATCH_PAGES_DIR} に置きます）。")
    images = _list_images(image_dir)
    if not images:
        eprint(f"[エラー] {image_dir} に対象画像（.png/.jpg/.jpeg）が見つかりません。")
        return 1
    images = _select_pages(images, args.pages)
    if not images:
        eprint(f"[エラー] {image_dir} に --pages {_format_pages(args.pages)} の画像が見つかりません。")
        return 1
    eprint(f"[一括] {len(images)} 枚を処理します: {image_dir}")

    # 書誌情報（PID は --pid → 書棚の metadata → ディレクトリ名先頭の数字 の順に決める）
    pid = args.pid or _pid_from_metadata(out_dir) or _pid_from_dirname(out_dir)
    biblio_text = _fetch_batch_biblio(pid, args)

    cache_dir = _page_cache_dir(out_dir, args)
    cached = sum(1 for image in images if (cache_dir / f"{image.stem}.txt").exists())
    if cached:
        eprint(f"[一括] 処理済み {cached} 枚をスキップします（{cache_dir}）")

    pages: list[str] = []
    try:
        for i, image in enumerate(images, 1):
            page_text = _ocr_page(image, f"{i}/{len(images)}", cache_dir, biblio_text, args)
            if page_text is not None:
                pages.append(page_text)
    except integrator.AgyQuotaError as e:
        eprint(_quota_message(e))
        if pages:
            eprint(f"[停止] {len(images)} 枚中 {len(pages)} ページまでで {_honmon_name(args)} を書き出します。")
            _write_honmon(out_dir, biblio_text, pages, _honmon_name(args))
        raise

    return _write_honmon(out_dir, biblio_text, pages, _honmon_name(args))


def _pdf_output_name(pdf_path: Path, args) -> str:
    """PDF と同じ名前で拡張子を .txt にする（--pages 指定時は 名前_p10-25.txt）。"""
    suffix = f"_p{_format_pages(args.pages)}" if args.pages else ""
    return f"{pdf_path.stem}{suffix}.txt"


def _pdf_text_page(pdf_path: Path, page: int, label: str, cache_dir: Path, biblio_text: str, args) -> str:
    """テキスト層のあるページを抜き出し、agy で読む順序だけ整える（文字は変えさせない）。"""
    from ndl_tools import kyujitai

    cache_path = cache_dir / f"{page:04d}.txt"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    eprint(f"\n[{label}] p{page}（テキスト層）")
    text = pdf_mod.extract_text(pdf_path, page)

    # 並べ替えに失敗したページは中間ファイルを残さず、再実行時にやり直す
    cacheable = True
    if not (args.ocr_only or args.engine):
        try:
            text = integrator.reorder(text, biblio=biblio_text, join_lines=True, model=args.model)
            eprint(f"[{label}] 並べ替え完了")
        except integrator.AgyQuotaError:
            raise
        except Exception as e:
            eprint(f"[{label}] 並べ替え失敗: {e} — 抽出テキストをそのまま採用します")
            cacheable = False

    text = kyujitai.to_shinjitai(text.strip())
    if cacheable:
        _write_page_cache(cache_path, text)
    return text


def _run_pdf(pdf_path: Path, args) -> int:
    """PDF を1ページずつテキスト化して <PDF名>.txt に書き出す。

    スキャンページ（全面画像あり）は既存の OCR テキスト層を使わず、画像にして三系統OCR→agy統合。
    テキスト層のページは抜き出して agy で読む順序を整える。
    途中結果は _pages/<モード>/<PDF名>/<ページ番号>.txt に保存し、再実行時は続きから。
    書誌情報は NDL の資料ではないので既定で取得しない（--pid を明示したときだけ取得する）。
    """
    try:
        kinds = pdf_mod.classify_pages(pdf_path)
    except Exception as e:
        eprint(f"[エラー] {pdf_path.name} を読めません: {e}")
        return 1
    if args.pages:
        kinds = [(n, kind) for n, kind in kinds if n in args.pages]
        if not kinds:
            eprint(f"[エラー] {pdf_path.name} に --pages {_format_pages(args.pages)} のページがありません。")
            return 1
    n_scan = sum(1 for _, kind in kinds if kind == "scan")
    eprint(f"\n[PDF] {pdf_path.name}: {len(kinds)} ページ（スキャン {n_scan} / テキスト層 {len(kinds) - n_scan}）")

    biblio_text = _fetch_batch_biblio(args.pid, args) if args.pid else ""

    cache_dir = _page_cache_dir(pdf_path.parent, args) / pdf_path.stem
    cache_dir.mkdir(exist_ok=True)
    cached = sum(1 for n, _ in kinds if (cache_dir / f"{n:04d}.txt").exists())
    if cached:
        eprint(f"[PDF] 処理済み {cached} ページをスキップします（{cache_dir}）")

    name = _pdf_output_name(pdf_path, args)
    pages: list[str] = []
    with tempfile.TemporaryDirectory(prefix="triple_ocr_pdf_") as tmp:
        try:
            for i, (n, kind) in enumerate(kinds, 1):
                label = f"{pdf_path.stem} {i}/{len(kinds)}"
                if kind == "text":
                    page_text = _pdf_text_page(pdf_path, n, label, cache_dir, biblio_text, args)
                elif (cache_dir / f"{n:04d}.txt").exists():
                    page_text = (cache_dir / f"{n:04d}.txt").read_text(encoding="utf-8")
                else:
                    # 画像名（0001.png）の stem が _ocr_page の中間ファイル名になる
                    image = pdf_mod.render_page(pdf_path, n, Path(tmp))
                    page_text = _ocr_page(image, label, cache_dir, biblio_text, args)
                    image.unlink(missing_ok=True)
                if page_text is not None:
                    pages.append(page_text)
        except integrator.AgyQuotaError as e:
            eprint(_quota_message(e))
            if pages:
                eprint(f"[停止] {len(kinds)} ページ中 {len(pages)} ページまでで {name} を書き出します。")
                _write_honmon(pdf_path.parent, biblio_text, pages, name)
            raise

    return _write_honmon(pdf_path.parent, biblio_text, pages, name)


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
    parser.add_argument(
        "--dir", metavar="PATH", nargs="?", const=".",
        help="ディレクトリ内の画像(.png/.jpg/.jpeg)を一括OCRし honmon.txt に出力（旧字→新字変換・段落結合あり。"
             "ページごとに _pages/ へ保存し、再実行時は続きから）。PATH 省略時はカレントディレクトリ。"
             "画像が original/ にある書棚レイアウトでは、本のディレクトリと original/ のどちらを渡しても本のディレクトリ直下に出力。"
             "PDF は1ファイルずつ <PDF名>.txt に出力（スキャンページはOCR、テキスト層のあるページは抜き出して読む順序を整える。"
             "書誌情報は --pid 指定時のみ）",
    )
    parser.add_argument(
        "--pages", metavar="SPEC",
        help="一括モード（--dir）で扱うコマ番号（1始まり。例: 10-25, 3,5,10-12）。ファイル名（0010.jpg 等）の"
             "番号で絞り込み、出力は honmon_p10-25.txt のような範囲付きの名前になる。PDF ではページ番号で絞り込む",
    )
    parser.add_argument("--pid", metavar="PID", help="書誌情報のPIDを明示指定（一括モードで既定は書棚の metadata かディレクトリ名の先頭数字）")
    parser.add_argument("--no-biblio", action="store_true", help="書誌情報の取得をスキップ")
    parser.add_argument("--no-clipboard", action="store_true", help="クリップボードへのコピーをスキップ")
    parser.add_argument("--no-log", action="store_true", help="ログファイルへの保存をスキップ")
    parser.add_argument("--ocr-only", action="store_true", help="OCR結果のみ表示（agy統合をスキップ）")
    parser.add_argument(
        "--model", metavar="NAME",
        help="統合校正に使う agy のモデル（`agy models` の ID か表示名。"
             f"既定: 環境変数 TRIPLE_OCR_AGY_MODEL、未設定なら {integrator.MODEL_AGY}）",
    )
    parser.add_argument(
        "--engine",
        choices=sorted(ALL_ENGINES),
        help="単一エンジンのみ実行し、統合校正をスキップして生のOCR結果を出力する",
    )
    args = parser.parse_args()

    # agy のモデルを決める。既定以外が指定されたときだけ `agy models`（約2秒）で検証する
    # （単一画像モードは Karabiner から起動されるので、既定のままなら待たせない）
    requested = args.model or os.environ.get("TRIPLE_OCR_AGY_MODEL", "")
    args.model = integrator.MODEL_AGY
    if requested and not (args.ocr_only or args.engine):
        try:
            args.model = integrator.resolve_agy_model(requested)
        except ValueError as e:
            eprint(f"[エラー] {e}")
            notify("triple-ocr", f"agy のモデル名が不正です: {requested}")
            return 2

    # 一括モード（--dir）は GUI を使わず同期実行して終了する。
    if args.pages is not None:
        if not args.dir:
            parser.error("--pages は --dir と一緒に指定してください")
        try:
            args.pages = _parse_pages(args.pages)
        except ValueError as e:
            parser.error(str(e))
    if args.dir and not (args.ocr_only or args.engine):
        eprint(f"[校正] agy のモデル: {args.model}")
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

    quota_note = ""
    # 6. 単一エンジン（--engine）は生テキスト、--ocr-only はエンジン名つきで出力する
    if args.engine:
        result_text = _raw_ocr_text(ocr_results, labeled=False)
    elif args.ocr_only:
        result_text = _raw_ocr_text(ocr_results, labeled=True)
    else:
        # 7. agy統合
        eprint(f"[校正] agy（{args.model}）で統合中…")
        if _countdown:
            _countdown.tick()  # 1: agy統合校正中
        try:
            result_text = integrator.integrate(
                vision_text=ocr_results.get("vision", ""),
                google_text=ocr_results.get("google", ""),
                ndlocr_text=ocr_results.get("ndlocr", ""),
                biblio=biblio_text,
                model=args.model,
            )
            eprint("[校正] 完了")
        except integrator.AgyQuotaError as e:
            eprint(f"[校正] {e} — Gemini API は使わず、OCR結果をそのまま表示します")
            result_text = _raw_ocr_text(ocr_results, labeled=True)
            quota_note = str(e)
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

    if quota_note:
        notify("triple-ocr", f"{quota_note} — OCR結果をそのまま出力しました")
    else:
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
