"""Image text extraction via OCR (pytesseract + Pillow)."""

from __future__ import annotations

import io
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

_MAX_DIM = 4096
# Pillow emits DecompressionBombWarning above MAX_IMAGE_PIXELS and
# raises DecompressionBombError above 2x. We treat both as fatal so a
# malicious input cannot trigger huge memory allocations.
_MAX_IMAGE_PIXELS = 50_000_000


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
    """Run Tesseract OCR on an image byte-string.

    Raises ``ValueError`` if the image exceeds the decompression-bomb
    threshold; the caller treats this as an extraction failure.
    """
    from PIL import Image

    previous_max = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(f"image refused: decompression bomb suspected ({exc})") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max

    return ocr_image(img, lang=lang)
