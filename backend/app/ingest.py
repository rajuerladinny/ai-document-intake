"""Turn an uploaded file into text the extractor can read."""

from __future__ import annotations

import io
from pathlib import PurePath

MAX_BYTES = 10 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".text", ".csv"}


class UnsupportedFile(ValueError):
    """The file type cannot be read yet, or the file is empty or too large."""


def to_text(file_name: str, data: bytes) -> str:
    if not data:
        raise UnsupportedFile("The file is empty.")
    if len(data) > MAX_BYTES:
        raise UnsupportedFile("The file is larger than 10 MB.")
    suffix = PurePath(file_name).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return data.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        return _pdf_text(data)
    if suffix in {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff"}:
        raise UnsupportedFile("Photos and scans need OCR, which arrives in milestone 2. Upload a PDF or text file.")
    raise UnsupportedFile(f"Unsupported file type: {suffix or 'none'}.")


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - pypdf is in requirements.txt
        raise UnsupportedFile("PDF support needs the pypdf package.") from exc
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if not text:
        raise UnsupportedFile("This PDF has no text layer (it is probably a scan). OCR arrives in milestone 2.")
    return text
