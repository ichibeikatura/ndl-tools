"""macOS Vision framework OCR"""

import sys


def run(image_path: str) -> str:
    """VNRecognizeTextRequest でテキスト認識し、改行区切りで返す"""
    try:
        import Vision
        import Quartz
    except ImportError:
        raise RuntimeError("pyobjc-framework-Vision が必要です: pip install pyobjc-framework-Vision")

    image_url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None, image_path.encode("utf-8"), len(image_path.encode("utf-8")), False
    )
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(image_url, {})

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["ja", "en"])
    request.setUsesLanguageCorrection_(True)

    error = None
    success = handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError("Vision OCR failed")

    observations = request.results()
    lines = []
    for obs in observations:
        candidate = obs.topCandidates_(1)
        if candidate:
            lines.append(str(candidate[0].string()))

    return "\n".join(lines)
