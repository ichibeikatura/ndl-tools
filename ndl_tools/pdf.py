"""PDF のページ判定・テキスト抽出・画像化（poppler の pdfinfo / pdfimages / pdftotext / pdftoppm を使う）

ページは次の二種類に振り分ける。
- スキャン: ページの大半を覆う画像がある。Acrobat Paper Capture 等の OCR テキスト層が
  付いていても品質が低い（縦書きが崩れる等）ので使わず、画像にして OCR に回す
- テキスト: 全面画像が無く、テキスト層がある（最初からデジタルで作られた PDF）。
  pdftotext で抜き出す。段組で読む順序が乱れることがあるので、並べ替えは呼び出し側で行う
全面画像もテキストも無いページ（白紙・図だけ等）はスキャン扱いにして OCR に任せる。
"""

import re
import subprocess
from pathlib import Path

# 画像がページ面積のこの割合以上を覆っていればスキャンページとみなす
SCAN_COVERAGE = 0.5

# OCR 用に画像化するときの解像度。スキャン PDF の元画像は 280〜400dpi が多い
RENDER_DPI = 300


def _run(cmd: list[str]) -> str:
    # -q でも出る ICC プロファイル等の警告は stderr に捨てる
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} エラー (code={result.returncode}): {result.stderr.strip()}")
    return result.stdout


def page_count(pdf: Path) -> int:
    m = re.search(r"^Pages:\s+(\d+)", _run(["pdfinfo", str(pdf)]), re.MULTILINE)
    if not m:
        raise RuntimeError(f"ページ数を取得できません: {pdf}")
    return int(m.group(1))


def _page_areas(pdf: Path, n_pages: int) -> dict[int, float]:
    """ページ番号 → ページ面積（pt²）。"""
    out = _run(["pdfinfo", "-f", "1", "-l", str(n_pages), str(pdf)])
    return {
        int(m.group(1)): float(m.group(2)) * float(m.group(3))
        for m in re.finditer(r"^Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+) pts", out, re.MULTILINE)
    }


def _image_coverage(pdf: Path, n_pages: int) -> dict[int, float]:
    """ページ番号 → 画像が覆う面積の割合（最大の画像1枚ぶん）。

    pdfimages -list の列: page num type width height color comp bpc enc interp object ID x-ppi y-ppi ...
    幅・高さ（px）を ppi で割って pt に直し、ページ面積と比べる。
    """
    areas = _page_areas(pdf, n_pages)
    coverage: dict[int, float] = {}
    for line in _run(["pdfimages", "-list", str(pdf)]).splitlines()[2:]:
        cols = line.split()
        if len(cols) < 14 or cols[2] != "image":
            continue
        try:
            page, w, h = int(cols[0]), int(cols[3]), int(cols[4])
            xppi, yppi = float(cols[12]), float(cols[13])
        except ValueError:
            continue
        if page not in areas or xppi <= 0 or yppi <= 0:
            continue
        ratio = (w / xppi * 72) * (h / yppi * 72) / areas[page]
        coverage[page] = max(coverage.get(page, 0.0), ratio)
    return coverage


def extract_text(pdf: Path, page: int) -> str:
    """テキスト層をコンテンツストリーム順（-raw）で抜き出す。

    既定の読み順推定は2段組で左右の行を交互に混ぜることがあり、-raw のほうが崩れにくい。
    """
    return _run(["pdftotext", "-q", "-raw", "-f", str(page), "-l", str(page), str(pdf), "-"]).strip()


def classify_pages(pdf: Path) -> list[tuple[int, str]]:
    """全ページを (ページ番号, "scan" | "text") のリストにする。"""
    n_pages = page_count(pdf)
    coverage = _image_coverage(pdf, n_pages)
    kinds = []
    for page in range(1, n_pages + 1):
        if coverage.get(page, 0.0) >= SCAN_COVERAGE or not extract_text(pdf, page):
            kinds.append((page, "scan"))
        else:
            kinds.append((page, "text"))
    return kinds


def render_page(pdf: Path, page: int, out_dir: Path) -> Path:
    """1ページを PNG にして out_dir に置き、そのパスを返す。"""
    prefix = out_dir / f"{page:04d}"
    _run(["pdftoppm", "-q", "-r", str(RENDER_DPI), "-png", "-singlefile",
          "-f", str(page), "-l", str(page), str(pdf), str(prefix)])
    return prefix.with_suffix(".png")
