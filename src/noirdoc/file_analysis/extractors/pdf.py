"""PDF text extraction using pypdfium2, with optional OCR fallback."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


def extract_pdf(data: bytes, *, max_pages: int = 50) -> str:
    """Extract text from a PDF byte-string, page by page.

    Returns concatenated text with double-newlines between pages.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    pages: list[str] = []
    try:
        for i, page in enumerate(pdf):
            if i >= max_pages:
                break
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range())
            textpage.close()
            page.close()
    finally:
        pdf.close()
    return "\n\n".join(pages)


def extract_pdf_with_ocr_fallback(
    data: bytes,
    *,
    max_pages: int = 50,
    min_chars_per_page: int = 100,
    ocr_lang: str = "deu+eng",
    render_scale: int = 2,
) -> str:
    """Extract PDF text; for pages whose text layer is sparse, OCR the rendered page.

    Handles DocuSign-style PDFs where the envelope-ID stamp is the only real
    text layer and the body is raster/XFA/permission-restricted. Pages whose
    text layer already has enough content use the fast text path.
    """
    import pypdfium2 as pdfium

    from noirdoc.file_analysis.extractors.ocr import ocr_image

    pdf = pdfium.PdfDocument(data)
    pages: list[str] = []
    ocr_triggered = 0
    try:
        for i, page in enumerate(pdf):
            if i >= max_pages:
                break
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            textpage.close()

            if len(text.strip()) < min_chars_per_page:
                pil = page.render(scale=render_scale).to_pil()
                text = ocr_image(pil, lang=ocr_lang)
                ocr_triggered += 1

            pages.append(text)
            page.close()
    finally:
        pdf.close()

    if ocr_triggered:
        logger.info(
            "pdf.ocr_fallback_used",
            pages_total=len(pages),
            pages_ocr_triggered=ocr_triggered,
            threshold=min_chars_per_page,
        )

    return "\n\n".join(pages)
