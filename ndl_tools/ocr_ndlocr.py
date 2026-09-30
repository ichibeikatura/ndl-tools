"""ndlocr-lite OCR"""

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def run(image_path: str) -> str:
    """ndlocr-lite を呼び出し、XML出力からテキストを抽出して返す"""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["ndlocr-lite", "--sourceimg", image_path, "--output", tmpdir],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ndlocr-lite failed: {result.stderr}")

        xml_files = list(Path(tmpdir).glob("**/*.xml"))
        if not xml_files:
            raise RuntimeError("ndlocr-lite: XML出力ファイルが見つかりません")

        return _parse_xml(xml_files[0])


def _parse_xml(xml_path: Path) -> str:
    """OCRDATASET XML を解析して ORDER 昇順でテキストを結合"""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    lines = []
    for line in root.iter("LINE"):
        text = line.get("STRING", "")
        try:
            order = int(line.get("ORDER", "0"))
        except ValueError:
            order = 0
        lines.append((order, text))

    lines.sort(key=lambda x: x[0])
    return "\n".join(text for _, text in lines if text)
