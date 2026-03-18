"""書誌情報取得モジュール（ndl.py から移植）"""

import html
import json
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

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
    return "".join(KANJI_DIGITS[int(d)] for d in str(n))


def convert_year(year: int) -> str:
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


# === Safari連携 ===
def get_safari_url() -> str:
    result = subprocess.run(
        ["osascript", "-e", 'tell app "Safari" to get the URL of the current tab of window 1'],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def normalize_url(url: str) -> str:
    return url.replace("https://lab.ndl.go.jp/dl/book/", "https://dl.ndl.go.jp/pid/")


def extract_pid_and_page(url: str) -> tuple[str, str]:
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
    url = f"https://japanlinkcenter.org/data/10.11501/{pid}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def format_biblio_info(data: dict) -> str:
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
    query = f'anywhere="R100000039-I{pid}"'
    params = urllib.parse.urlencode({
        "operation": "searchRetrieve",
        "recordSchema": "dcndl",
        "maximumRecords": "1",
        "query": query,
    })
    url = f"https://ndlsearch.ndl.go.jp/api/sru?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return _parse_sru_xml(response.read())
    except Exception:
        return {}


def _parse_sru_xml(xml_bytes: bytes) -> dict:
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
