"""NDLデジタルコレクションの書誌情報取得モジュール。

bin/ndl.py と bin/triple_ocr.py の共通基盤。もとは ndl.py 内に直接あったものを
triple-ocr が複製し、両者が分岐していたため統合した（User-Agent・SRUリトライは
ndl.py 側、各種 timeout と Preview/frontmost 連携は triple-ocr 側の実装を採る）。
"""

import html
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# NDL / JapanLinkCenter は既定の urllib User-Agent に応答しないことがあるため明示する。
_UA = "Mozilla/5.0"

# === 元号データ ===
ERAS = [
    (1868, "明治"),
    (1912, "大正"),
    (1926, "昭和"),
    (1989, "平成"),
    (2019, "令和"),
]

KANJI_DIGITS = "〇一二三四五六七八九"


def to_kanji_number(n: int) -> str:
    """アラビア数字を漢数字に変換（桁表記なし、単純置換）"""
    return "".join(KANJI_DIGITS[int(d)] for d in str(n))


def convert_year(year: int) -> str:
    """西暦を和暦表記に変換: 明治三四(一九〇一)年 1901"""
    era_name = None
    era_year = None
    for i, (start, name) in enumerate(ERAS):
        if i + 1 < len(ERAS):
            if start <= year < ERAS[i + 1][0]:
                era_name = name
                era_year = year - start + 1
                break
        else:
            if year >= start:
                era_name = name
                era_year = year - start + 1
                break
    if era_name is None:
        return str(year)
    era_year_kanji = to_kanji_number(era_year)
    year_kanji = to_kanji_number(year)
    return f"{era_name}{era_year_kanji}({year_kanji})年 {year}"


# === フロントアプリ判定 ===
def get_frontmost_app() -> str:
    """最前面アプリの bundle identifier を返す。失敗時は空文字。"""
    result = subprocess.run(
        [
            "osascript",
            "-e", 'tell application "System Events" to set p to first application process whose frontmost is true',
            "-e", "return bundle identifier of p",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


# === Preview連携 ===
# AXDocument 属性経由でファイルパスを取得する（Preview のスクリプト辞書に
# document クラスの応答が無くても動く。~/.bin/preview.py と同方式）。
_PREVIEW_DOC_SCRIPT = """
tell application "System Events"
    tell process "Preview"
        set thefile to value of attribute "AXDocument" of window 1
    end tell
end tell
return thefile
"""


def get_preview_file_path() -> str:
    """Preview.app の最前面ウィンドウで開いているファイルのフルパスを返す。失敗時は空文字。

    アクセシビリティ許可がない等の実行エラー時は RuntimeError を送出する。
    """
    result = subprocess.run(
        ["osascript", "-e", _PREVIEW_DOC_SCRIPT],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    raw = result.stdout.strip()
    if not raw or raw == "missing value":
        return ""
    if raw.startswith("file://"):
        raw = raw[7:]
    return urllib.parse.unquote(raw)


# === Safari連携 ===
def get_safari_url() -> str:
    """Safariの現在のタブのURLを取得"""
    result = subprocess.run(
        ["osascript", "-e", 'tell app "Safari" to get the URL of the current tab of window 1'],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def normalize_url(url: str) -> str:
    """lab.ndl.go.jp形式をdl.ndl.go.jp形式に正規化"""
    return url.replace("https://lab.ndl.go.jp/dl/book/", "https://dl.ndl.go.jp/pid/")


def extract_pid_and_page(url: str) -> tuple[str, str]:
    """URLからPIDとページ番号を抽出"""
    normalized = normalize_url(url)
    match = re.search(r"/pid/(\d+)(?:/\d+)?(?:/(\d+))?", normalized)
    if match:
        pid = match.group(1)
        page = match.group(2) or "1"
        return pid, page
    return "", ""


# === 書誌情報取得 ===
_SRW = "http://www.loc.gov/zing/srw/"
_NS_NDL = {
    "rdf":     "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcndl":   "http://ndl.go.jp/dcndl/terms/",
    "foaf":    "http://xmlns.com/foaf/0.1/",
}


def _t(prefix: str, local: str) -> str:
    return f"{{{_NS_NDL[prefix]}}}{local}"


def _format_author_literal(literal: str) -> str:
    """'永田, 清, 1903-1957' → '永田 清 (1903-1957)' に変換"""
    parts = [p.strip() for p in literal.split(",")]
    name_parts = []
    year_part = ""
    for p in parts:
        if re.match(r"^\d{4}(-\d{4})?-?$|^-\d{4}$", p):
            year_part = p
        else:
            name_parts.append(p)
    name = " ".join(p for p in name_parts if p)
    return f"{name} ({year_part})" if year_part else name


def fetch_biblio_info(pid: str) -> dict:
    """JapanLinkCenter APIから書誌情報を取得"""
    url = f"https://japanlinkcenter.org/data/10.11501/{pid}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": _UA}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def format_biblio_info(data: dict) -> str:
    """JLC書誌情報を整形"""
    if not data:
        return ""
    title = data.get("title", "")
    author_raw = data.get("author", [])
    if isinstance(author_raw, list):
        authors = []
        for a in author_raw:
            if isinstance(a, dict):
                if "literal" in a:
                    name = _format_author_literal(a["literal"])
                else:
                    family = a.get("family", "")
                    given = a.get("given", "")
                    name = f"{family} {given}".strip() if given else family
                if name:
                    authors.append(name)
            else:
                authors.append(str(a))
        author = " ".join(authors)
    else:
        author = str(author_raw)
    publisher = data.get("publisher", "")
    issued_raw = data.get("issued", {})
    year = None
    if isinstance(issued_raw, dict):
        date_parts = issued_raw.get("date-parts", [[]])
        if date_parts and date_parts[0]:
            year = date_parts[0][0]
    year_str = convert_year(year) if year else ""
    parts = [title, author, publisher, year_str]
    return " ".join(p for p in parts if p)


def fetch_ndl_sru(pid: str) -> dict:
    """NDL SRU APIから書誌情報を取得（JLCのフォールバック）。失敗時1回リトライ。"""
    query = f'anywhere="R100000039-I{pid}"'
    params = urllib.parse.urlencode({
        "operation": "searchRetrieve",
        "recordSchema": "dcndl",
        "maximumRecords": "1",
        "query": query,
    })
    url = f"https://ndlsearch.ndl.go.jp/api/sru?{params}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/xml", "User-Agent": _UA}
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return _parse_sru_xml(response.read())
        except Exception:
            if attempt == 0:
                time.sleep(2)
    return {}


def _parse_sru_xml(xml_bytes: bytes) -> dict:
    """SRU XMLレスポンスを解析してDC-NDL書誌情報を返す"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {}
    num_el = root.find(f"{{{_SRW}}}numberOfRecords")
    if num_el is None or (num_el.text or "0").strip() == "0":
        return {}
    record_data_el = root.find(f".//{{{_SRW}}}recordData")
    if record_data_el is None:
        return {}
    raw = html.unescape((record_data_el.text or "").strip())
    if not raw:
        return {}
    try:
        rdf_root = ET.fromstring(raw)
    except ET.ParseError:
        return {}
    bib = rdf_root.find(f".//{_t('dcndl', 'BibResource')}")
    if bib is None:
        return {}
    title = ""
    t_el = bib.find(_t("dcterms", "title"))
    if t_el is not None:
        title = (t_el.text or "").strip()
    if not title:
        t_el = bib.find(f".//{_t('dc', 'title')}/{_t('rdf', 'Description')}/{_t('rdf', 'value')}")
        if t_el is not None:
            title = (t_el.text or "").strip()
    creators = []
    for agent in bib.findall(f"{_t('dcterms', 'creator')}/{_t('foaf', 'Agent')}/{_t('foaf', 'name')}"):
        n = (agent.text or "").strip()
        if n:
            creators.append(n)
    if not creators:
        for c in bib.findall(_t("dc", "creator")):
            n = (c.text or "").strip()
            if n:
                creators.append(n)
    publishers = []
    for agent in bib.findall(f"{_t('dcterms', 'publisher')}/{_t('foaf', 'Agent')}/{_t('foaf', 'name')}"):
        n = (agent.text or "").strip()
        if n:
            publishers.append(n)
    issued = ""
    issued_el = bib.find(_t("dcterms", "issued"))
    if issued_el is not None:
        issued = (issued_el.text or "").strip()
    return {"title": title, "creators": creators, "publishers": publishers, "issued": issued}


def format_ndl_sru_info(data: dict) -> str:
    """NDL SRU書誌情報を整形"""
    if not data or not data.get("title"):
        return ""
    title = data["title"]
    author = " ".join(data.get("creators", []))
    publisher = " ".join(data.get("publishers", []))
    year_str = ""
    m = re.match(r"(\d{4})", data.get("issued", ""))
    if m:
        year_str = convert_year(int(m.group(1)))
    parts = [title, author, publisher, year_str]
    return " ".join(p for p in parts if p)


def get_biblio_text(pid: str) -> str:
    """PIDから書誌情報テキストを取得。JLC → NDL SRU フォールバック。"""
    jlc_data = fetch_biblio_info(pid)
    biblio = format_biblio_info(jlc_data)
    jlc_author = jlc_data.get("author", [])
    has_jlc_author = bool(jlc_author) if isinstance(jlc_author, list) else bool(jlc_author)
    if not biblio or not has_jlc_author:
        sru_data = fetch_ndl_sru(pid)
        sru_biblio = format_ndl_sru_info(sru_data)
        if sru_biblio:
            biblio = sru_biblio
    return biblio
