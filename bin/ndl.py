#!/usr/bin/env python3
"""
NDLデジタルコレクション閲覧ログ記録スクリプト

Usage:
    ndl.py --page     # ページ番号のみ追記
    ndl.py --full     # 書誌情報＋URL＋タイムスタンプ＋ページ番号＋スクショ

書誌情報の取得（Safari連携・JLC・NDL SRU）は ndl_tools.biblio に集約してある。
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ~/.bin の symlink 経由でも起動できるよう、実体の位置からリポジトリルートを解決して
# sys.path に加える（resolve() が symlink を辿る）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ndl_tools import biblio as biblio_mod
from ndl_tools.biblio import extract_pid_and_page, get_safari_url

# === 設定 ===
LOG_FILE = Path.home() / "My Drive" / "memo" / "readkindai.txt"
SCREENSHOT_DIR = Path.home() / "Documents" / "ebook" / "kindaimemo"

# === スクリーンショット ===
def take_screenshot(filename: str):
    """スクリーンショットを撮影"""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = SCREENSHOT_DIR / f"{filename}.png"
    subprocess.run(["screencapture", "-rx", "-t", "png", str(filepath)])

# === ログ書き込み ===
def append_to_log(content: str):
    """ログファイルに追記"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(content)

# === メイン処理 ===
def cmd_page():
    """ページ番号のみ追記"""
    url = get_safari_url()
    _, page = extract_pid_and_page(url)
    append_to_log(f"p{page.zfill(2)} \n")

def cmd_full():
    """書誌情報＋URL＋タイムスタンプ＋ページ番号＋スクショ"""
    url = get_safari_url()
    pid, page = extract_pid_and_page(url)
    
    # 書誌情報取得: JLC → NDL SRU フォールバック
    biblio = biblio_mod.get_biblio_text(pid)

    if not biblio:
        print(f"警告: 書誌情報を取得できませんでした (pid: {pid})", file=sys.stderr)

    # タイムスタンプ
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S:")
    
    # ログ追記
    log_content = f"\n{biblio}\n{url}\n{timestamp}\np{page.zfill(2)} "
    append_to_log(log_content)
    
    # スクリーンショット
    date_str = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    screenshot_name = f"{biblio}_{date_str}"
    # ファイル名に使えない文字を除去
    screenshot_name = re.sub(r'[/:*?"<>|]', "", screenshot_name)
    take_screenshot(screenshot_name)

def main():
    parser = argparse.ArgumentParser(description="NDLデジタルコレクション閲覧ログ記録")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--page", action="store_true", help="ページ番号のみ追記")
    group.add_argument("--full", action="store_true", help="書誌情報＋スクショ")
    
    args = parser.parse_args()
    
    if args.page:
        cmd_page()
    elif args.full:
        cmd_full()

if __name__ == "__main__":
    main()
