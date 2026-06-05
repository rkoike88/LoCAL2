"""DocumentsWindow — floating window for managing the RAG document library.

Lets the user:
  - Set the library topic (updates documents.yaml, triggers schema re-announce)
  - Add files via file picker (PDF, txt, md — ingestion runs in a QThread)
  - See ingested sources with chunk counts
  - Delete a source from the library
"""
from __future__ import annotations

import logging
from pathlib import Path

try:
    from PySide6.QtCore import QObject, QThread, Qt, Signal
    from PySide6.QtWidgets import (
        QFileDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc

from local.config_loader import ConfigManager, get_config

logger = logging.getLogger(__name__)

_CONFIG_NAME = "documents"
_ACCEPTED_EXTS = "Documents (*.pdf *.txt *.md *.py *.yaml *.json *.csv)"


# ---------------------------------------------------------------------------
# Background ingest worker
# ---------------------------------------------------------------------------

class _IngestWorker(QObject):
    status        = Signal(str)            # free-form status string for immediate display
    chunk_done    = Signal(str, int, int)  # filename, current, total
    file_done     = Signal(str, int)       # filename, chunk_count
    error         = Signal(str, str)       # filename, error_message
    finished      = Signal()

    def __init__(self, document_service, paths: list[str]) -> None:
        super().__init__()
        self._docs  = document_service
        self._paths = paths

    def run(self) -> None:
        total_files = len(self._paths)
        for file_num, path in enumerate(self._paths, 1):
            name = Path(path).name
            self.status.emit(f"[{file_num}/{total_files}] Extracting text from {name}…")
            try:
                def on_progress(current: int, total: int, _name=name) -> None:
                    self.chunk_done.emit(_name, current, total)
                n = self._docs.ingest_file(path, on_progress=on_progress)
                self.file_done.emit(name, n)
            except Exception as exc:
                self.error.emit(name, str(exc))
        self.finished.emit()


# ---------------------------------------------------------------------------
# Documents window
# ---------------------------------------------------------------------------

class DocumentsWindow(QWidget):

    # Emitted when a schema re-announce is needed (topic saved)
    schema_changed = Signal()

    def __init__(self, document_service=None, publisher=None) -> None:
        super().__init__()
        self._docs              = document_service
        self._publisher         = publisher
        self._thread: QThread | None = None
        self._worker = None
        self._ingest_total_files = 0
        self._ingest_file_num    = 0

        self.setWindowTitle("library")
        self.resize(560, 480)
        self.setWindowFlags(Qt.Window)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_topic_row())
        layout.addWidget(self._build_table(), 1)
        layout.addWidget(self._build_status_bar())

        self._apply_styles()
        self._refresh()

    # ------------------------------------------------------------------
    # UI builders
    # ------------------------------------------------------------------

    def _build_header(self) -> QWidget:
        title = QLabel("library")
        title.setObjectName("winTitle")

        add_btn = QPushButton("+ Files")
        add_btn.setObjectName("libAddBtn")
        add_btn.setToolTip("Add individual files")
        add_btn.clicked.connect(self._open_file_picker)

        folder_btn = QPushButton("+ Folder")
        folder_btn.setObjectName("libAddBtn")
        folder_btn.setToolTip("Add all supported files from a folder")
        folder_btn.clicked.connect(self._open_folder_picker)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("libRefreshBtn")
        refresh_btn.clicked.connect(self._refresh)

        row = QHBoxLayout()
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)
        row.addWidget(title, 1)
        row.addWidget(add_btn)
        row.addWidget(folder_btn)
        row.addWidget(refresh_btn)

        header = QWidget()
        header.setObjectName("winHeader")
        header.setLayout(row)
        return header

    def _build_topic_row(self) -> QWidget:
        label = QLabel("Topic:")
        label.setObjectName("libTopicLabel")
        label.setFixedWidth(46)

        cfg = get_config(_CONFIG_NAME) or {}
        self._topic_input = QLineEdit(cfg.get("topic", ""))
        self._topic_input.setObjectName("libTopicInput")
        self._topic_input.setPlaceholderText(
            "e.g. MBA textbooks covering strategy, finance, marketing, and operations"
        )

        save_btn = QPushButton("Save")
        save_btn.setObjectName("libSaveBtn")
        save_btn.clicked.connect(self._save_topic)

        row = QHBoxLayout()
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)
        row.addWidget(label)
        row.addWidget(self._topic_input, 1)
        row.addWidget(save_btn)

        bar = QWidget()
        bar.setObjectName("libTopicBar")
        bar.setLayout(row)
        return bar

    def _build_table(self) -> QWidget:
        self._table = QTableWidget(0, 3)
        self._table.setObjectName("libTable")
        self._table.setHorizontalHeaderLabels(["Source", "Chunks", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        return self._table

    def _build_status_bar(self) -> QWidget:
        self._status = QLabel("")
        self._status.setObjectName("libStatus")

        self._progress = QProgressBar()
        self._progress.setObjectName("libProgress")
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)

        bar = QWidget()
        bar.setObjectName("libStatusBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._progress)

        inner = QWidget()
        inner_layout = QHBoxLayout(inner)
        inner_layout.setContentsMargins(10, 4, 10, 4)
        inner_layout.addWidget(self._status)
        layout.addWidget(inner)
        return bar

    # ------------------------------------------------------------------
    # Topic
    # ------------------------------------------------------------------

    def _save_topic(self) -> None:
        topic = self._topic_input.text().strip()
        cfg = get_config(_CONFIG_NAME) or {}
        cfg["topic"] = topic
        ConfigManager.save(_CONFIG_NAME, cfg)
        self._set_status(f"Topic saved. Gemma will use this to decide when to search the library.")
        # Trigger schema re-announce so Gemma sees the updated description immediately
        self._trigger_schema_reannounce()

    def _trigger_schema_reannounce(self) -> None:
        from local.protocol.subjects import TOOL_SCHEMA_REQUEST
        from local.protocol.envelope import MessageEnvelope
        if self._publisher:
            self._publisher.publish(MessageEnvelope.create(
                message_type="config_reload",
                subject=TOOL_SCHEMA_REQUEST,
                sender_id="documents_window",
                payload={},
            ))

    # ------------------------------------------------------------------
    # File ingestion
    # ------------------------------------------------------------------

    def _open_file_picker(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add to library", "", _ACCEPTED_EXTS
        )
        if paths:
            self._ingest(paths)

    def _open_folder_picker(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder to ingest")
        if not folder:
            return
        supported = {".pdf", ".txt", ".md", ".py", ".yaml", ".json", ".csv"}
        paths = [
            str(p) for p in Path(folder).rglob("*")
            if p.is_file() and p.suffix.lower() in supported
        ]
        if not paths:
            self._set_status(f"No supported files found in {Path(folder).name}/")
            return
        self._set_status(f"Found {len(paths)} file(s) in {Path(folder).name}/ — starting ingestion…")
        self._ingest(paths)

    def _ingest(self, paths: list[str]) -> None:
        if self._thread and self._thread.isRunning():
            self._set_status("Ingestion already running — please wait.")
            return
        if not self._docs:
            return

        self._ingest_total_files = len(paths)
        self._ingest_file_num = 0
        self._set_status(f"Starting ingestion of {len(paths)} file(s)…")
        self._progress.setValue(0)
        self._progress.setVisible(True)

        worker = _IngestWorker(self._docs, paths)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.status.connect(self._set_status)
        worker.chunk_done.connect(self._on_chunk_done)
        worker.file_done.connect(self._on_file_done)
        worker.error.connect(self._on_ingest_error)
        worker.finished.connect(self._on_ingest_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._thread = thread
        self._worker = worker  # keep alive until thread runs; cleared in _on_ingest_finished
        thread.start()

    def _on_chunk_done(self, name: str, current: int, total: int) -> None:
        pct = int(current / total * 100) if total else 0
        self._progress.setValue(pct)
        self._set_status(
            f"[{self._ingest_file_num + 1}/{self._ingest_total_files}] "
            f"Embedding {name} — {current} / {total}"
        )

    def _on_file_done(self, name: str, chunks: int) -> None:
        self._ingest_file_num += 1
        self._set_status(
            f"Done: {name} ({chunks} chunks) — "
            f"{self._ingest_file_num} / {self._ingest_total_files} files"
        )
        self._progress.setValue(0)
        self._refresh()

    def _on_ingest_error(self, name: str, error: str) -> None:
        self._ingest_file_num += 1
        self._set_status(f"Error — {name}: {error}")

    def _on_ingest_finished(self) -> None:
        self._worker = None
        self._progress.setVisible(False)
        self._refresh()
        total = self._docs.count() if self._docs else 0
        self._set_status(f"All done. Library: {total} total chunks.")

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self._table.setRowCount(0)
        if not self._docs:
            return
        sources = self._docs.list_sources()
        for name in sources:
            # Count chunks for this source
            try:
                result = self._docs._collection.get(
                    where={"source_file": name}, include=["metadatas"]
                )
                count = len(result.get("ids") or [])
            except Exception:
                count = 0

            r = self._table.rowCount()
            self._table.insertRow(r)

            name_item = QTableWidgetItem(name)
            name_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            self._table.setItem(r, 0, name_item)

            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignCenter)
            self._table.setItem(r, 1, count_item)

            del_btn = QPushButton()
            del_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            del_btn.setObjectName("libDelBtn")
            del_btn.setToolTip(f"Delete {name}")
            del_btn.clicked.connect(lambda checked=False, n=name: self._delete_source(n))
            self._table.setCellWidget(r, 2, del_btn)

    def _delete_source(self, name: str) -> None:
        if not self._docs:
            return
        reply = QMessageBox.question(
            self,
            "Remove from library",
            f"Remove '{name}' from the library?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        n = self._docs.delete_source(name)
        self._set_status(f"Deleted {name} ({n} chunks removed)")
        self._refresh()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        self._status.setText(msg)

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            DocumentsWindow, QWidget { background: #111111; }
            QWidget#winHeader { background: #1a1a1a; border-bottom: 1px solid #2a2a2a; }
            QLabel#winTitle {
                color: #d4d4d4; font-family: 'Menlo','Monaco','Courier New';
                font-size: 13px; font-weight: bold;
            }
            QWidget#libTopicBar { background: #161616; border-bottom: 1px solid #2a2a2a; }
            QLabel#libTopicLabel {
                color: #888; font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
            }
            QLineEdit#libTopicInput {
                background: #1a1a1a; color: #d4d4d4; border: 1px solid #333;
                border-radius: 4px; padding: 4px 8px;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
            }
            QPushButton#libAddBtn, QPushButton#libRefreshBtn, QPushButton#libSaveBtn {
                background: #1a2a3a; color: #9dbde8; border: 1px solid #2a4a6a;
                border-radius: 4px; padding: 3px 10px; font-size: 12px;
            }
            QPushButton#libAddBtn:hover, QPushButton#libRefreshBtn:hover,
            QPushButton#libSaveBtn:hover { background: #1e3448; }
            QPushButton#libSaveBtn { background: #1a2a1a; color: #7ec8a4; border-color: #3a5a3a; }
            QPushButton#libSaveBtn:hover { background: #1e3a1e; }
            QTableWidget#libTable {
                background: #111111; color: #d4d4d4; gridline-color: #1e1e1e;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
                border: none;
            }
            QTableWidget#libTable::item { padding: 4px 8px; }
            QHeaderView::section {
                background: #1a1a1a; color: #888; border: none;
                border-bottom: 1px solid #2a2a2a; padding: 4px 8px; font-size: 11px;
            }
            QPushButton#libDelBtn {
                background: #2a1a1a; color: #e06c75; border: 1px solid #5a3a3a;
                border-radius: 3px; padding: 2px 8px; font-size: 11px; margin: 2px;
            }
            QPushButton#libDelBtn:hover { background: #3a1a1a; }
            QWidget#libStatusBar { background: #0e0e0e; border-top: 1px solid #1e1e1e; }
            QLabel#libStatus {
                color: #666; font-family: 'Menlo','Monaco','Courier New'; font-size: 11px;
            }
            QProgressBar#libProgress {
                background: #1a1a1a; border: none;
            }
            QProgressBar#libProgress::chunk {
                background: #2a6a4a;
            }
        """)
