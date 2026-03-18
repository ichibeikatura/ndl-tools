"""Google Cloud Vision API OCR"""

import base64
import json
import os
import urllib.request


ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"


def run(image_path: str) -> str:
    """Google Cloud Vision TEXT_DETECTION でテキスト認識し、fullTextAnnotation.text を返す"""
    api_key = os.environ.get("GOOGLE_CLOUD_VISION_API_KEY", "")
    if not api_key:
        raise RuntimeError("環境変数 GOOGLE_CLOUD_VISION_API_KEY が設定されていません")

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "requests": [
            {
                "image": {"content": image_b64},
                "features": [{"type": "TEXT_DETECTION"}],
                "imageContext": {"languageHints": ["ja", "en"]},
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")

    url = f"{ENDPOINT}?key={api_key}"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.request.URLError:
            if attempt == 2:
                raise
            import time
            time.sleep(2 ** attempt)

    responses = data.get("responses", [])
    if not responses:
        return ""

    full_text = responses[0].get("fullTextAnnotation", {}).get("text", "")
    return full_text.strip()
