#!/usr/bin/env python3
"""OCRテキストから柱（ランニングヘッダ）・ノンブル・重複「目次」見出しを除去する。

国会図書館デジコレ資料のOCRテキスト（honmon.txt）では、版面の柱
（ノンブル脇の書名・章題）がページごとにOCRされて本文に混入する。
このスクリプトはそれを行単位で機械的に除去し、除去によって（および
柱がOCRされなかったページ境界で）分断された段落を結合する。

処理は行の削除と結合のみで、文字は1文字も書き換えない（旧字体・
歴史的仮名遣いは完全保持）。終了前に「除去行を差し引いた原文と
出力の文字列が完全一致する」ことを自己検証し、不一致なら異常終了する。

前提とする資料構造:
  - 目次の終端に「目次終」等のマーカー行がある（--toc-end で変更可）
  - 巻末の奥附・広告の先頭に「奥附」等のマーカー行がある（--colophon で変更可。
    無ければ末尾まで本文として扱う）
  - 章見出しが「第一 〜」形式で、目次と本文の双方に現れる

目次終マーカーが見つからない資料では前付け・本文の境界が判定できず
誤削除の危険があるため、重複した「目次」見出しの除去以外は何もしない。

usage: clean_hashira.py [-o OUT] [--title T] [--max-page N]
                        [--toc-end M] [--colophon M] [--no-join]
                        [--report FILE] input.txt
"""

import argparse
import difflib
import re
import sys
import unicodedata
from pathlib import Path

KAN = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
       "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
KAN_CHARS = set(KAN) | {"十"}
SENT_END = "。』」！？!?"
CHAPTER_RE = re.compile(r"第[一二三四五六七八九十]+.{2,14}")


def norm(s: str) -> str:
    return re.sub(r"[\s　]+", "", unicodedata.normalize("NFKC", s.strip()))


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def kan_to_int(s: str):
    """位取り漢数字（一〇六 形式）を整数に。変換できなければ None。"""
    if not s or not all(c in KAN for c in s):
        return None
    return int("".join(str(KAN[c]) for c in s))


def is_heading(s: str) -> bool:
    """章見出し（第一 〜）または節見出し（一〇九 〜）か。"""
    n = norm(s)
    if re.match(r"^第[一二三四五六七八九十]", n):
        return True
    return bool(re.match(r"^[一二三四五六七八九〇十百]+[ 　]", s.strip()))


def locate(lines, spec, alt=None):
    """マーカー指定（文字列 or 行番号）を1始まりの行番号に解決する。"""
    if spec and spec.isdigit():
        return int(spec)
    for i, l in enumerate(lines, 1):
        if spec in l:
            return i
    if alt:
        for i, l in enumerate(lines, 1):
            if alt in l:
                return i
    return None


def detect_title(lines):
    """先頭の【書誌情報】ブロックから書名を取る。"""
    if lines and "【書誌情報】" in lines[0] and len(lines) > 1:
        head = lines[1].split()
        if head:
            return head[0]
    return None


def detect_max_page(path: Path):
    """入力と同じディレクトリのページ画像（jpg）枚数をノンブル上限とする。"""
    n = len(list(path.parent.glob("*.jpg")))
    return n if n else None


def main():
    ap = argparse.ArgumentParser(
        description="OCRテキストから柱・ノンブル・重複目次見出しを除去する")
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path,
                    help="出力先（省略時: <input>.clean.txt）")
    ap.add_argument("--title", help="書名（省略時: 【書誌情報】行から自動取得）")
    ap.add_argument("--max-page", type=int,
                    help="ノンブルの最大値（省略時: 同ディレクトリのjpg枚数）")
    ap.add_argument("--toc-end", default="目次終", metavar="M",
                    help="目次終端のマーカー文字列または行番号（既定: 目次終）")
    ap.add_argument("--colophon", default="奥附", metavar="M",
                    help="奥附・巻末広告の開始マーカーまたは行番号（既定: 奥附）")
    ap.add_argument("--no-join", action="store_true",
                    help="分断された段落の結合を行わない")
    ap.add_argument("--report", type=Path, help="除去した行の一覧の出力先")
    args = ap.parse_args()

    lines = [l.rstrip("\n") for l in args.input.open(encoding="utf-8")]
    out_path = args.output or args.input.with_suffix(".clean.txt")

    toc_end = locate(lines, args.toc_end)
    colophon = locate(lines, args.colophon, alt="奥付") or len(lines) + 1
    title = args.title or detect_title(lines)
    max_page = args.max_page or detect_max_page(args.input) or 500

    if toc_end is None:
        print(f"警告: 目次終マーカー '{args.toc_end}' が見つかりません。"
              "前付け・本文の境界を判定できないため、柱・ノンブルの除去と"
              "段落結合は行いません（--toc-end で行番号を指定できます）",
              file=sys.stderr)
    if title is None:
        print("警告: 書名を特定できないため、書名の柱は除去しません"
              "（--title で指定できます）", file=sys.stderr)

    # 目次から正典の章題を採取（章題柱の照合先。本文側の崩れたOCRと類似照合する）
    canon = []
    if toc_end:
        for l in lines[:toc_end]:
            s = norm(l)
            if CHAPTER_RE.fullmatch(s) and s not in canon:
                canon.append(s)

    removed = []   # (行番号, 種別, 内容)
    kept = []      # (行番号, 内容)
    seen_chapter = set()
    seen_toc_header = False

    for idx, raw in enumerate(lines, 1):
        s = norm(raw)
        body = toc_end is not None and idx > toc_end

        if not s:
            kept.append((idx, raw))
            continue

        # 1. 「目次」見出し（初出のみ残す）
        if s == "目次":
            if seen_toc_header:
                removed.append((idx, "目次見出し", raw))
                continue
            seen_toc_header = True
            kept.append((idx, raw))
            continue

        if body:
            # 2. 書名の柱。「墓参と懺悔 一一八」のようにノンブルが合体した行も拾う
            if title and len(s) <= len(title) + 5:
                m = re.fullmatch(
                    r"(.+?)([一二三四五六七八九〇十]{0,4})", s)
                if m:
                    head = m.group(1)
                    if (abs(len(head) - len(title)) <= 1
                            and ratio(head, title) >= 0.8):
                        removed.append((idx, "書名の柱", raw))
                        continue

            # 3. ノンブルのみの行
            if all(c in KAN_CHARS for c in s):
                v = kan_to_int(s)
                if v is not None and 1 <= v <= max_page:
                    removed.append((idx, "ノンブル", raw))
                    continue

            # 4. 章題の柱（各章の初出＝本物の見出しだけ残す）
            if canon and s.startswith("第") and len(s) <= 16:
                # 末尾に合体したノンブルを剥がしてから照合
                core = re.sub(r"[一二三四五六七八九〇]{1,3}$", "", s) or s
                best = max(canon,
                           key=lambda c: max(ratio(s, c), ratio(core, c)))
                r = max(ratio(s, best), ratio(core, best))
                if r >= 0.7:
                    if best in seen_chapter:
                        removed.append((idx, "章題の柱", raw))
                        continue
                    seen_chapter.add(best)

        kept.append((idx, raw))

    # --- 段落結合 -----------------------------------------------------------
    # 対象は目次終〜奥附の本文のみ。前付け（漢詩・序）と巻末（奥附・広告）は
    # 短い行そのものが単位なので結合しない。本文の段落は必ず 。』」等で
    # 終わるため、そうでない行はページ跨ぎの途中切れと判断できる。
    out = []          # [(行番号, 本文, 直前に空行があったか)]
    joins = 0
    pending_blank = False
    join_enabled = not args.no_join and toc_end is not None
    for orig, text in kept:
        if not text.strip():
            pending_blank = True
            continue
        if (join_enabled and out and toc_end < orig < colophon
                and out[-1][0] > toc_end
                and out[-1][1].rstrip()[-1] not in SENT_END
                and not is_heading(text) and not is_heading(out[-1][1])):
            joins += 1
            out[-1] = (out[-1][0], out[-1][1].rstrip() + text.lstrip(),
                       out[-1][2])
        else:
            out.append((orig, text, pending_blank))
        pending_blank = False

    final = []
    for orig, text, blank_before in out:
        if blank_before and final:
            final.append("")
        final.append(text)

    # --- 自己検証: 削除行以外は1文字も変わっていないこと --------------------
    removed_at = {i for i, _, _ in removed}
    expect = "".join(l.strip() for i, l in enumerate(lines, 1)
                     if i not in removed_at)
    got = "".join(l.strip() for l in final)
    if expect != got:
        print("エラー: 出力が原文と一致しません（バグ）。出力を中止します",
              file=sys.stderr)
        sys.exit(1)

    out_path.write_text("\n".join(final).rstrip("\n") + "\n", encoding="utf-8")

    # --- レポート -----------------------------------------------------------
    import collections
    cnt = collections.Counter(k for _, k, _ in removed)
    print(f"除去 {len(removed)} 行 "
          f"({', '.join(f'{k}:{n}' for k, n in cnt.most_common()) or 'なし'}) / "
          f"結合 {joins} 箇所 / {len(lines)} → {len(final)} 行", file=sys.stderr)
    print(f"出力: {out_path}", file=sys.stderr)
    if args.report:
        with args.report.open("w", encoding="utf-8") as f:
            for i, k, t in removed:
                f.write(f"{i}\t{k}\t{t}\n")
        print(f"除去行レポート: {args.report}", file=sys.stderr)


if __name__ == "__main__":
    main()
