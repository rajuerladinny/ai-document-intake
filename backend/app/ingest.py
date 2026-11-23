"""Turn an uploaded file into something an extractor can read."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import PurePath

MAX_BYTES = 10 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".text", ".csv"}
# Formats the OpenAI and Anthropic vision APIs both accept.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


class UnsupportedFile(ValueError):
    """The file type cannot be read, or the file is empty or too large."""


@dataclass
class UploadedDocument:
    file_name: str
    #: Text layer, when the file has one (.txt, text-based PDF).
    text: str | None
    #: Raw bytes for files that must be read visually (photos, scanned PDFs).
    binary: bytes | None = None
    #: MIME type of the original file, used when it is shown to a reviewer.
    media_type: str | None = None

    @property
    def needs_vision(self) -> bool:
        return self.text is None


def read_upload(file_name: str, data: bytes) -> UploadedDocument:
    if not data:
        raise UnsupportedFile("The file is empty.")
    if len(data) > MAX_BYTES:
        raise UnsupportedFile("The file is larger than 10 MB.")
    suffix = PurePath(file_name).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return UploadedDocument(file_name, data.decode("utf-8", errors="replace"), media_type="text/plain")
    if suffix == ".pdf":
        text = _pdf_text(data)
        if text:
            return UploadedDocument(file_name, text, media_type="application/pdf")
        # No text layer: probably a scan. An AI extractor can read it visually.
        return UploadedDocument(file_name, None, data, "application/pdf")
    if suffix in IMAGE_TYPES:
        return UploadedDocument(file_name, None, data, IMAGE_TYPES[suffix])
    if suffix in {".heic", ".tif", ".tiff"}:
        raise UnsupportedFile("Please convert HEIC or TIFF photos to JPG or PNG first.")
    raise UnsupportedFile(f"Unsupported file type: {suffix or 'none'}.")


def to_text(file_name: str, data: bytes) -> str:
    """Text of a file that has a text layer. Raises UnsupportedFile for photos and scans."""
    doc = read_upload(file_name, data)
    if doc.text is None:
        raise UnsupportedFile("Photos and scans need the AI extractor (EXTRACTOR=llm). Upload a text-based PDF or .txt.")
    return doc.text


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - pypdf is in requirements.txt
        raise UnsupportedFile("PDF support needs the pypdf package.") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:  # pypdf raises several error types for broken files
        raise UnsupportedFile("This PDF could not be read. It may be damaged or password-protected.") from exc
