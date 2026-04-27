"""Zip-bomb defenses for OOXML container formats (DOCX, XLSX).

DOCX and XLSX are zip archives. python-docx / openpyxl will happily
decompress whatever the central directory advertises, which lets a tiny
file balloon into gigabytes during extraction. We pre-flight every input
through this guard so the extractor never touches a payload we can spot
as malicious before unzip.
"""

from __future__ import annotations

import io
import zipfile

# Sized for "real, large business documents are fine" — a 200 MB
# uncompressed corpus is generous; legitimate DOCX/XLSX rarely exceed
# tens of MB even with embedded media.
_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024

# Real OOXML files compress well, but ratios above ~30× are unusual.
# Cap at 100× so we block obvious bombs without false-flagging the
# occasional CSV-like sheet of repetitive text.
_MAX_COMPRESSION_RATIO = 100


def check_ooxml_zip_safe(data: bytes, *, label: str) -> None:
    """Raise ``ValueError`` if ``data`` looks like a zip bomb.

    Validates the central directory's declared sizes before any extractor
    starts decompressing entries.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            entries = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{label}: not a valid zip archive") from exc

    total_uncompressed = sum(zi.file_size for zi in entries)
    total_compressed = sum(zi.compress_size for zi in entries)

    if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"{label}: archive declares {total_uncompressed} bytes uncompressed "
            f"(cap is {_MAX_UNCOMPRESSED_BYTES})",
        )

    if total_compressed and total_uncompressed / total_compressed > _MAX_COMPRESSION_RATIO:
        ratio = total_uncompressed / total_compressed
        raise ValueError(
            f"{label}: compression ratio {ratio:.0f}x exceeds {_MAX_COMPRESSION_RATIO}x — "
            "refusing as zip bomb",
        )
