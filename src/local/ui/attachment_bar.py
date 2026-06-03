"""AttachmentBar — chip strip for file attachments in the query input area.

Paperclip button (⌁) opens a file picker. Drag-drop is handled by the parent
container and forwarded via add_files(). Each accepted file becomes a chip.
attachments() returns the current [{type, name, data}] list for the bus payload.
"""
from __future__ import annotations

import base64
from pathlib import Path

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QFileDialog,
        QHBoxLayout,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_TEXT_EXTS  = {".txt", ".md", ".py", ".js", ".ts", ".yaml", ".json", ".csv"}


def _extract_pdf_text(path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _process_file(path: str) -> dict:
    ext = Path(path).suffix.lower()
    name = Path(path).name
    try:
        if ext in _IMAGE_EXTS:
            data = base64.b64encode(Path(path).read_bytes()).decode()
            return {"type": "image", "name": name, "data": data}
        elif ext == ".pdf":
            return {"type": "text", "name": name, "data": _extract_pdf_text(path)}
        elif ext in _TEXT_EXTS:
            return {"type": "text", "name": name, "data": Path(path).read_text(errors="replace")}
        else:
            return {"type": "error", "name": name}
    except Exception as exc:
        return {"type": "error", "name": name, "error": str(exc)}


class AttachmentBar(QWidget):
    """Horizontal chip strip for attached files. Hidden when empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._attachments: list[dict] = []

        # Chip area inside a scroll area so many chips don't break the layout
        self._chip_widget = QWidget()
        self._chip_widget.setObjectName("chipWidget")
        self._chip_layout = QHBoxLayout(self._chip_widget)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(4)
        self._chip_layout.addStretch()

        self._chip_scroll = QScrollArea()
        self._chip_scroll.setObjectName("chipScroll")
        self._chip_scroll.setWidget(self._chip_widget)
        self._chip_scroll.setWidgetResizable(True)
        self._chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._chip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chip_scroll.setFixedHeight(34)
        self._chip_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(0)
        row.addWidget(self._chip_scroll, 1)

        self.setFixedHeight(42)
        self.setObjectName("attachmentBar")
        self._apply_styles()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_files(self, paths: list[str]) -> None:
        for path in paths:
            self.add_attachment(_process_file(path))

    def add_attachment(self, att: dict) -> None:
        self._attachments.append(att)
        self._add_chip(att)
        self.setVisible(True)

    def attachments(self) -> list[dict]:
        return [a for a in self._attachments if a.get("type") != "error"]

    def clear(self) -> None:
        self._attachments.clear()
        while self._chip_layout.count() > 1:  # keep the trailing stretch
            item = self._chip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.setVisible(False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _add_chip(self, att: dict) -> None:
        is_error = att.get("type") == "error"
        icon = "📎" if not is_error else "⚠"
        label = f"{icon} {att['name']}  ✕"
        btn = QPushButton(label)
        btn.setObjectName("chipError" if is_error else "chip")
        btn.setFlat(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn.clicked.connect(lambda checked=False, a=att, b=btn: self._remove_chip(a, b))
        # Insert before the trailing stretch
        self._chip_layout.insertWidget(self._chip_layout.count() - 1, btn)

    def _remove_chip(self, att: dict, btn: QPushButton) -> None:
        self._attachments.remove(att)
        btn.deleteLater()
        if not self._attachments:
            self.setVisible(False)

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QWidget#attachmentBar {
                background: #161616;
                border-bottom: 1px solid #2a2a2a;
            }
            QWidget#chipWidget { background: transparent; }
            QScrollArea#chipScroll {
                background: transparent;
                border: none;
            }
            QPushButton#clipBtn {
                background: #1a1a1a; color: #888; border: 1px solid #333;
                border-radius: 4px; font-size: 14px; padding: 0;
            }
            QPushButton#clipBtn:hover { background: #222; color: #aaa; }
            QPushButton#chip {
                background: #1a2a1a; color: #7ec8a4; border: 1px solid #3a5a3a;
                border-radius: 10px; padding: 2px 10px; font-size: 11px;
                font-family: 'Menlo','Monaco','Courier New';
            }
            QPushButton#chip:hover { background: #1e3a1e; }
            QPushButton#chipError {
                background: #2a1a1a; color: #e06c75; border: 1px solid #5a3a3a;
                border-radius: 10px; padding: 2px 10px; font-size: 11px;
                font-family: 'Menlo','Monaco','Courier New';
            }
        """)
