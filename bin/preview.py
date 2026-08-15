#!/usr/bin/env python3
"""
Preview.app閲覧ログ記録スクリプト

Usage:
    preview.py --page     # ページ番号のみ追記
    preview.py --full     # 書誌情報＋URL＋タイムスタンプ＋ページ番号

ディレクトリ構造:
    書籍フォルダ/
    ├── biblio.txt        # 書誌情報（1行目を使用）
    ├── metadata          # URL
    └── original/
        ├── 001.jpg
        └── ...
"""

import argparse
import subprocess
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

# === 設定 ===
LOG_FILE = Path.home() / "My Drive" / "memo" / "readkindai.txt"

# === AppleScript（インライン実行） ===
APPLESCRIPT_GET_DOCUMENT = '''
tell application "System Events"
    tell process "Preview"
        set thefile to value of attribute "AXDocument" of window 1
    end tell
end tell
return thefile
'''

def run_applescript(script: str) -> str:
    """AppleScriptを実行して結果を返す"""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()

# === Preview.app連携 ===
def get_preview_file_path() -> Path:
    """Preview.appで開いているファイルのパスを取得"""
    raw_url = run_applescript(APPLESCRIPT_GET_DOCUMENT)
    
    # file:// 形式を除去してURLデコード
    if raw_url.startswith("file://"):
        raw_url = raw_url[7:]
    
    decoded_path = unquote(raw_url)
    return Path(decoded_path)

def extract_page_number(file_path: Path) -> str:
    """ファイル名からページ番号を抽出: 001.jpg → 1"""
    stem = file_path.stem  # 拡張子なしのファイル名
    # 数字部分を抽出
    match = re.search(r"(\d+)", stem)
    if match:
        return str(int(match.group(1)))  # 先頭ゼロを除去
    return "1"

def get_book_dir(file_path: Path) -> Path:
    """画像ファイルから書籍フォルダを取得: .../書籍/original/001.jpg → .../書籍/"""
    # original/ の親ディレクトリが書籍フォルダ
    return file_path.parent.parent

def read_file_first_line(filepath: Path) -> str:
    """ファイルの1行目を読み取る"""
    if not filepath.exists():
        return ""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.readline().strip()
    except Exception:
        return ""

def focus_preview():
    """Preview.appにフォーカスを戻す"""
    subprocess.run(["open", "-a", "Preview"], capture_output=True)

def show_notification(title: str, message: str):
    """macOS通知を表示"""
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)

# === ログ書き込み ===
def append_to_log(content: str):
    """ログファイルに追記"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(content)

# === メイン処理 ===
def cmd_page():
    """ページ番号のみ追記"""
    file_path = get_preview_file_path()
    page = extract_page_number(file_path)
    append_to_log(f"p{page.zfill(2)} ")
    focus_preview()

def cmd_full():
    """書誌情報＋URL＋タイムスタンプ＋ページ番号"""
    file_path = get_preview_file_path()
    book_dir = get_book_dir(file_path)
    
    biblio = read_file_first_line(book_dir / "biblio.txt")
    url = read_file_first_line(book_dir / "metadata")
    page = extract_page_number(file_path)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S:")
    
    # ndl.pyと同じログ形式
    log_content = f"\n{biblio}\n{url}\n{timestamp}\np{page.zfill(2)} "
    append_to_log(log_content)
    
    show_notification("KindaiMemo", "Accomplish")

def main():
    parser = argparse.ArgumentParser(description="Preview.app閲覧ログ記録")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--page", action="store_true", help="ページ番号のみ追記")
    group.add_argument("--full", action="store_true", help="書誌情報＋URL＋タイムスタンプ＋ページ番号")
    
    args = parser.parse_args()
    
    if args.page:
        cmd_page()
    elif args.full:
        cmd_full()

if __name__ == "__main__":
    main()
