"""Image text extraction via OCR (pytesseract + Pillow)."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

_MAX_DIM = 4096


def ocr_image(img: PILImage, *, lang: str = "deu+eng") -> str:
    """Run Tesseract OCR on an already-loaded PIL image.

    Large images are resized before processing to cap memory usage.
    """
    import pytesseract

    if max(img.size) > _MAX_DIM:
        ratio = _MAX_DIM / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)))

    return pytesseract.image_to_string(img, lang=lang)


def extract_ocr(data: bytes, *, lang: str = "deu+eng") -> str:
    """Run Tesseract OCR on an image byte-string."""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    return ocr_image(img, lang=lang)
