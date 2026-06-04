"""Shared file text extraction — used by AttachmentBar and DocumentService.

Supports PDF (via pypdf), and plain text formats.
Images are handled separately by AttachmentBar (base64) and are not extractable as text.
"""
from __future__ import annotations

import base64
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TEXT_EXTS  = {".txt", ".md", ".py", ".js", ".ts", ".yaml", ".json", ".csv"}
PDF_EXT    = ".pdf"


def extract_pdf_text(path: str) -> str:
    """Extract all text from a PDF file, one page per line-group."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_pdf_pages(path: str) -> list[tuple[int, str]]:
    """Return [(page_number, text), ...] for each page of a PDF (1-indexed)."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]


def extract_text(path: str) -> str:
    """Extract plain text from a PDF or text file. Raises ValueError for unsupported types."""
    ext = Path(path).suffix.lower()
    if ext == PDF_EXT:
        return extract_pdf_text(path)
    elif ext in TEXT_EXTS:
        return Path(path).read_text(errors="replace")
    else:
        raise ValueError(f"Unsupported file type for text extraction: {ext}")


def process_for_attachment(path: str) -> dict:
    """Return {type, name, data} dict for AttachmentBar payloads.

    Images → base64 string in data.
    PDFs / text → plain text string in data.
    Unsupported → {type: error}.
    """
    ext = Path(path).suffix.lower()
    name = Path(path).name
    try:
        if ext in IMAGE_EXTS:
            data = base64.b64encode(Path(path).read_bytes()).decode()
            return {"type": "image", "name": name, "data": data}
        elif ext == PDF_EXT:
            return {"type": "text", "name": name, "data": extract_pdf_text(path)}
        elif ext in TEXT_EXTS:
            return {"type": "text", "name": name, "data": Path(path).read_text(errors="replace")}
        else:
            return {"type": "error", "name": name}
    except Exception as exc:
        return {"type": "error", "name": name, "error": str(exc)}
