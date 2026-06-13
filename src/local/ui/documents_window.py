"""DocumentsWindow — floating window for managing the RAG document library.

Two-level navigation:
  Collections view (default) — list all collections with source/chunk counts; CRUD
  Sources view (drill-down)  — sources in a collection; move, delete, ingest
"""
from __future__ import annotations

import logging
from pathlib import Path

try:
    from PySide6.QtCore import QObject, QThread, Qt, Signal
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QStackedWidget,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc

from local.config_loader import ConfigManager, get_config

logger = logging.getLogger(__name__)

_CONFIG_NAME = "documents"
_ACCEPTED_EXTS = "Documents (*.pdf *.txt *.md *.py *.yaml *.json *.csv)"
_PAGE_COLLECTIONS = 0
_PAGE_SOURCES = 1


# ---------------------------------------------------------------------------
# Background ingest worker
# ---------------------------------------------------------------------------

class _IngestWorker(QObject):
    status     = Signal(str)
    chunk_done = Signal(str, int, int)   # filename, current, total
    file_done  = Signal(str, int)        # filename, chunk_count
    error      = Signal(str, str)        # filename, error_message
    finished   = Signal()

    def __init__(self, document_service, paths: list[str], collection: str) -> None:
        super().__init__()
        self._docs       = document_service
        self._paths      = paths
        self._collection = collection

    def run(self) -> None:
        total = len(self._paths)
        for i, path in enumerate(self._paths, 1):
            name = Path(path).name
            self.status.emit(f"[{i}/{total}] Extracting {name}…")
            try:
                def on_progress(cur: int, tot: int, _n=name) -> None:
                    self.chunk_done.emit(_n, cur, tot)
                n = self._docs.ingest_file(path, self._collection, on_progress=on_progress)
                self.file_done.emit(name, n)
            except Exception as exc:
                self.error.emit(name, str(exc))
        self.finished.emit()


# ---------------------------------------------------------------------------
# Add-collection dialog
# ---------------------------------------------------------------------------

class _AddCollectionDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New collection")
        self.setMinimumWidth(400)

        form = QFormLayout()
        self._name = QLineEdit()
        self._name.setPlaceholderText("mba  (internal key, no spaces)")
        self._display = QLineEdit()
        self._display.setPlaceholderText("MBA Textbooks")
        self._desc = QTextEdit()
        self._desc.setPlaceholderText("MBA textbooks covering strategy, finance, marketing, and operations")
        self._desc.setFixedHeight(72)
        form.addRow("Name:", self._name)
        form.addRow("Display name:", self._display)
        form.addRow("Description:", self._desc)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        name = self._name.text().strip()
        if not name or " " in name:
            QMessageBox.warning(self, "Invalid name",
                                "Name must be non-empty and contain no spaces.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self._name.text().strip(),
            "display_name": self._display.text().strip() or self._name.text().strip(),
            "description": self._desc.toPlainText().strip(),
        }


# ---------------------------------------------------------------------------
# DocumentsWindow
# ---------------------------------------------------------------------------

class DocumentsWindow(QWidget):

    def __init__(self, document_service=None, publisher=None) -> None:
        super().__init__()
        self._docs              = document_service
        self._publisher         = publisher
        self._thread: QThread | None = None
        self._worker            = None
        self._current_collection: str | None = None   # name key of drilled-in collection
        self._ingest_total      = 0
        self._ingest_done       = 0

        self.setWindowTitle("library")
        self.resize(600, 500)
        self.setWindowFlags(Qt.Window)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_collections_page())
        self._stack.addWidget(self._build_sources_page())

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._stack)
        root.addWidget(self._build_status_bar())

        self._apply_styles()
        self._show_collections()

    # ------------------------------------------------------------------
    # Page: Collections
    # ------------------------------------------------------------------

    def _build_collections_page(self) -> QWidget:
        # Header
        title = QLabel("library")
        title.setObjectName("winTitle")

        add_col_btn = QPushButton("+ Collection")
        add_col_btn.setObjectName("libAddBtn")
        add_col_btn.clicked.connect(self._add_collection)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("libRefreshBtn")
        refresh_btn.clicked.connect(self._show_collections)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(10, 6, 10, 6)
        header_row.setSpacing(6)
        header_row.addWidget(title, 1)
        header_row.addWidget(add_col_btn)
        header_row.addWidget(refresh_btn)

        header = QWidget()
        header.setObjectName("winHeader")
        header.setLayout(header_row)

        # Table
        self._col_table = QTableWidget(0, 4)
        self._col_table.setObjectName("libTable")
        self._col_table.setHorizontalHeaderLabels(["Collection", "Sources", "Description", ""])
        self._col_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._col_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._col_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._col_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._col_table.verticalHeader().setVisible(False)
        self._col_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._col_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._col_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._col_table.cellDoubleClicked.connect(self._on_collection_double_click)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._col_table, 1)
        return page

    # ------------------------------------------------------------------
    # Page: Sources
    # ------------------------------------------------------------------

    def _build_sources_page(self) -> QWidget:
        # Header
        self._back_btn = QPushButton("← library")
        self._back_btn.setObjectName("libBackBtn")
        self._back_btn.clicked.connect(self._show_collections)

        self._src_title = QLabel("")
        self._src_title.setObjectName("winTitle")

        add_files_btn = QPushButton("+ Files")
        add_files_btn.setObjectName("libAddBtn")
        add_files_btn.clicked.connect(self._open_file_picker)

        add_folder_btn = QPushButton("+ Folder")
        add_folder_btn.setObjectName("libAddBtn")
        add_folder_btn.clicked.connect(self._open_folder_picker)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("libRefreshBtn")
        refresh_btn.clicked.connect(self._show_sources)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(10, 6, 10, 6)
        header_row.setSpacing(6)
        header_row.addWidget(self._back_btn)
        header_row.addWidget(self._src_title, 1)
        header_row.addWidget(add_files_btn)
        header_row.addWidget(add_folder_btn)
        header_row.addWidget(refresh_btn)

        header = QWidget()
        header.setObjectName("winHeader")
        header.setLayout(header_row)

        # Sources table
        self._src_table = QTableWidget(0, 3)
        self._src_table.setObjectName("libTable")
        self._src_table.setHorizontalHeaderLabels(["Source", "Chunks", ""])
        self._src_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._src_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._src_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._src_table.verticalHeader().setVisible(False)
        self._src_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._src_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        # Description bar
        desc_label = QLabel("Description:")
        desc_label.setObjectName("libTopicLabel")
        self._desc_input = QLineEdit()
        self._desc_input.setObjectName("libTopicInput")
        save_desc_btn = QPushButton("Save")
        save_desc_btn.setObjectName("libSaveBtn")
        save_desc_btn.clicked.connect(self._save_description)

        desc_row = QHBoxLayout()
        desc_row.setContentsMargins(10, 6, 10, 6)
        desc_row.setSpacing(6)
        desc_row.addWidget(desc_label)
        desc_row.addWidget(self._desc_input, 1)
        desc_row.addWidget(save_desc_btn)

        desc_bar = QWidget()
        desc_bar.setObjectName("libTopicBar")
        desc_bar.setLayout(desc_row)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._src_table, 1)
        layout.addWidget(desc_bar)
        return page

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
    # Navigation
    # ------------------------------------------------------------------

    def _show_collections(self) -> None:
        self._current_collection = None
        self._stack.setCurrentIndex(_PAGE_COLLECTIONS)
        self._refresh_collections()

    def _show_sources(self) -> None:
        if not self._current_collection:
            return
        self._stack.setCurrentIndex(_PAGE_SOURCES)
        self._refresh_sources()

    def _on_collection_double_click(self, row: int, _col: int) -> None:
        item = self._col_table.item(row, 0)
        if item:
            self._current_collection = item.data(Qt.ItemDataRole.UserRole)
            self._show_sources()

    # ------------------------------------------------------------------
    # Collections page — data
    # ------------------------------------------------------------------

    def _refresh_collections(self) -> None:
        self._col_table.setRowCount(0)
        if not self._docs:
            return
        collections = self._docs.list_collections()
        for col in collections:
            r = self._col_table.rowCount()
            self._col_table.insertRow(r)

            name_item = QTableWidgetItem(col["display_name"])
            name_item.setData(Qt.ItemDataRole.UserRole, col["name"])
            self._col_table.setItem(r, 0, name_item)

            src_item = QTableWidgetItem(str(col["source_count"]))
            src_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._col_table.setItem(r, 1, src_item)

            desc_item = QTableWidgetItem(col["description"])
            self._col_table.setItem(r, 2, desc_item)

            # Action buttons
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(2, 0, 2, 0)
            actions_layout.setSpacing(2)

            rename_btn = QPushButton("✎")
            rename_btn.setObjectName("libActionBtn")
            rename_btn.setToolTip("Rename collection")
            rename_btn.setFixedWidth(26)
            rename_btn.clicked.connect(
                lambda checked=False, n=col["name"]: self._rename_collection(n)
            )
            actions_layout.addWidget(rename_btn)

            del_btn = QPushButton()
            del_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            del_btn.setObjectName("libDelBtn")
            del_btn.setToolTip(f"Delete {col['display_name']}")
            del_btn.setFixedWidth(26)
            del_btn.clicked.connect(
                lambda checked=False, n=col["name"], d=col["display_name"]: self._delete_collection(n, d)
            )
            actions_layout.addWidget(del_btn)

            self._col_table.setCellWidget(r, 3, actions)
            self._col_table.setRowHeight(r, 28)

    def _add_collection(self) -> None:
        dlg = _AddCollectionDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        cfg = get_config(_CONFIG_NAME) or {}
        cols = list(cfg.get("collections") or [])
        # Check for duplicate name
        if any(c.get("name") == vals["name"] for c in cols):
            QMessageBox.warning(self, "Duplicate name",
                                f"A collection named '{vals['name']}' already exists.")
            return
        cols.append(vals)
        cfg["collections"] = cols
        ConfigManager.save(_CONFIG_NAME, cfg)
        self._trigger_schema_reannounce()
        self._refresh_collections()

    def _rename_collection(self, name: str) -> None:
        cfg = get_config(_CONFIG_NAME) or {}
        cols = list(cfg.get("collections") or [])
        col = next((c for c in cols if c.get("name") == name), None)
        if not col:
            return

        new_display, ok = self._input_dialog("Rename collection",
                                             "New display name:",
                                             col.get("display_name", name))
        if not ok or not new_display.strip():
            return
        col["display_name"] = new_display.strip()
        cfg["collections"] = cols
        ConfigManager.save(_CONFIG_NAME, cfg)
        self._trigger_schema_reannounce()
        self._refresh_collections()

    def _delete_collection(self, name: str, display: str) -> None:
        reply = QMessageBox.question(
            self, "Delete collection",
            f"Delete '{display}' and all its documents?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self._docs:
            n = self._docs.delete_collection_chunks(name)
            self._set_status(f"Deleted {n} chunks from '{display}'")

        cfg = get_config(_CONFIG_NAME) or {}
        cols = [c for c in (cfg.get("collections") or []) if c.get("name") != name]
        cfg["collections"] = cols
        ConfigManager.save(_CONFIG_NAME, cfg)
        self._trigger_schema_reannounce()
        self._refresh_collections()

    # ------------------------------------------------------------------
    # Sources page — data
    # ------------------------------------------------------------------

    def _refresh_sources(self) -> None:
        col_name = self._current_collection
        if not col_name:
            return

        # Update breadcrumb title
        cfg = get_config(_CONFIG_NAME) or {}
        display = col_name
        for c in (cfg.get("collections") or []):
            if c.get("name") == col_name:
                display = c.get("display_name", col_name)
                self._desc_input.setText(c.get("description", ""))
                break
        self._src_title.setText(display)

        self._src_table.setRowCount(0)
        if not self._docs:
            return

        sources = self._docs.list_sources_detail(col_name)
        other_collections = [
            c for c in (cfg.get("collections") or [])
            if c.get("name") != col_name
        ]

        for src in sources:
            src_file = src["source_file"]
            chunk_count = src["chunk_count"]
            r = self._src_table.rowCount()
            self._src_table.insertRow(r)

            name_item = QTableWidgetItem(src_file)
            self._src_table.setItem(r, 0, name_item)

            count_item = QTableWidgetItem(str(chunk_count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._src_table.setItem(r, 1, count_item)

            # Actions: move combo + delete
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(2, 0, 2, 0)
            actions_layout.setSpacing(4)

            if other_collections:
                move_combo = QComboBox()
                move_combo.setObjectName("libMoveCombo")
                move_combo.addItem("Move to…")
                for oc in other_collections:
                    move_combo.addItem(oc.get("display_name", oc["name"]), userData=oc["name"])
                move_combo.currentIndexChanged.connect(
                    lambda idx, combo=move_combo, sf=src_file: self._on_move(combo, sf, idx)
                )
                actions_layout.addWidget(move_combo)

            del_btn = QPushButton()
            del_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            del_btn.setObjectName("libDelBtn")
            del_btn.setToolTip(f"Delete {src_file}")
            del_btn.setFixedWidth(26)
            del_btn.clicked.connect(
                lambda checked=False, sf=src_file: self._delete_source(sf)
            )
            actions_layout.addWidget(del_btn)

            self._src_table.setCellWidget(r, 2, actions)
            self._src_table.setRowHeight(r, 28)

    def _on_move(self, combo: QComboBox, source_file: str, idx: int) -> None:
        if idx == 0:
            return  # "Move to…" placeholder
        to_col = combo.itemData(idx)
        if not to_col or not self._current_collection or not self._docs:
            return
        combo.setCurrentIndex(0)
        n = self._docs.move_source(source_file, self._current_collection, to_col)
        self._set_status(f"Moved {source_file} → {to_col} ({n} chunks)")
        self._refresh_sources()
        self._refresh_collections()

    def _delete_source(self, source_file: str) -> None:
        if not self._docs or not self._current_collection:
            return
        reply = QMessageBox.question(
            self, "Remove from library",
            f"Remove '{source_file}' from the library?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        n = self._docs.delete_source(source_file, self._current_collection)
        self._set_status(f"Deleted {source_file} ({n} chunks removed)")
        self._refresh_sources()

    def _save_description(self) -> None:
        if not self._current_collection:
            return
        desc = self._desc_input.text().strip()
        cfg = get_config(_CONFIG_NAME) or {}
        for col in (cfg.get("collections") or []):
            if col.get("name") == self._current_collection:
                col["description"] = desc
                break
        ConfigManager.save(_CONFIG_NAME, cfg)
        self._trigger_schema_reannounce()
        self._set_status("Description saved.")

    # ------------------------------------------------------------------
    # File ingestion
    # ------------------------------------------------------------------

    def _open_file_picker(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add to library", "", _ACCEPTED_EXTS)
        if paths:
            self._ingest(paths)

    def _open_folder_picker(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder to ingest")
        if not folder:
            return
        supported = {".pdf", ".txt", ".md", ".py", ".yaml", ".json", ".csv"}
        paths = [str(p) for p in Path(folder).rglob("*")
                 if p.is_file() and p.suffix.lower() in supported]
        if not paths:
            self._set_status(f"No supported files found in {Path(folder).name}/")
            return
        self._set_status(f"Found {len(paths)} file(s) — starting ingestion…")
        self._ingest(paths)

    def _ingest(self, paths: list[str]) -> None:
        if not self._current_collection:
            return
        if self._thread and self._thread.isRunning():
            self._set_status("Ingestion already running — please wait.")
            return
        if not self._docs:
            return

        self._ingest_total = len(paths)
        self._ingest_done = 0
        self._set_status(f"Starting ingestion of {len(paths)} file(s)…")
        self._progress.setValue(0)
        self._progress.setVisible(True)

        worker = _IngestWorker(self._docs, paths, self._current_collection)
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
        self._worker = worker
        thread.start()

    def _on_chunk_done(self, name: str, current: int, total: int) -> None:
        pct = int(current / total * 100) if total else 0
        self._progress.setValue(pct)
        self._set_status(
            f"[{self._ingest_done + 1}/{self._ingest_total}] Embedding {name} — {current}/{total}"
        )

    def _on_file_done(self, name: str, chunks: int) -> None:
        self._ingest_done += 1
        self._set_status(
            f"Done: {name} ({chunks} chunks) — {self._ingest_done}/{self._ingest_total} files"
        )
        self._progress.setValue(0)
        self._refresh_sources()

    def _on_ingest_error(self, name: str, error: str) -> None:
        self._ingest_done += 1
        self._set_status(f"Error — {name}: {error}")

    def _on_ingest_finished(self) -> None:
        self._worker = None
        self._progress.setVisible(False)
        self._refresh_sources()
        self._set_status("Ingestion complete.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trigger_schema_reannounce(self) -> None:
        from local.protocol.messages import ToolSchemaRequest
        if self._publisher:
            self._publisher.publish(ToolSchemaRequest(), sender_id="documents_window")

    def _set_status(self, msg: str) -> None:
        self._status.setText(msg)

    def _input_dialog(self, title: str, label: str, default: str = "") -> tuple[str, bool]:
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text, ok

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            DocumentsWindow, QWidget { background: #111111; }
            QWidget#winHeader { background: #1a1a1a; border-bottom: 1px solid #2a2a2a; }
            QLabel#winTitle {
                color: #d4d4d4; font-family: 'Menlo','Monaco','Courier New';
                font-size: 13px; font-weight: bold;
            }
            QWidget#libTopicBar { background: #161616; border-top: 1px solid #2a2a2a; }
            QLabel#libTopicLabel {
                color: #888; font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
            }
            QLineEdit#libTopicInput {
                background: #1a1a1a; color: #d4d4d4; border: 1px solid #333;
                border-radius: 4px; padding: 4px 8px;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
            }
            QPushButton#libAddBtn, QPushButton#libRefreshBtn {
                background: #1a2a3a; color: #9dbde8; border: 1px solid #2a4a6a;
                border-radius: 4px; padding: 3px 10px; font-size: 12px;
            }
            QPushButton#libAddBtn:hover, QPushButton#libRefreshBtn:hover { background: #1e3448; }
            QPushButton#libBackBtn {
                background: transparent; color: #666; border: none;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px; padding: 2px 6px;
            }
            QPushButton#libBackBtn:hover { color: #aaa; }
            QPushButton#libSaveBtn {
                background: #1a2a1a; color: #7ec8a4; border: 1px solid #3a5a3a;
                border-radius: 4px; padding: 3px 10px; font-size: 12px;
            }
            QPushButton#libSaveBtn:hover { background: #1e3a1e; }
            QTableWidget#libTable {
                background: #111111; color: #d4d4d4; gridline-color: #1e1e1e;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px; border: none;
            }
            QTableWidget#libTable::item { padding: 4px 8px; }
            QHeaderView::section {
                background: #1a1a1a; color: #888; border: none;
                border-bottom: 1px solid #2a2a2a; padding: 4px 8px; font-size: 11px;
            }
            QPushButton#libDelBtn, QPushButton#libActionBtn {
                background: #2a1a1a; border: 1px solid #5a3a3a;
                border-radius: 3px; padding: 1px 4px; font-size: 11px; margin: 2px;
            }
            QPushButton#libDelBtn { color: #e06c75; }
            QPushButton#libActionBtn { color: #9dbde8; background: #1a1a2a; border-color: #3a3a5a; }
            QPushButton#libDelBtn:hover { background: #3a1a1a; }
            QPushButton#libActionBtn:hover { background: #1e1e3a; }
            QComboBox#libMoveCombo {
                background: #1a1a1a; color: #888; border: 1px solid #333;
                border-radius: 3px; padding: 1px 6px; font-size: 11px;
            }
            QWidget#libStatusBar { background: #0e0e0e; border-top: 1px solid #1e1e1e; }
            QLabel#libStatus {
                color: #666; font-family: 'Menlo','Monaco','Courier New'; font-size: 11px;
            }
            QProgressBar#libProgress { background: #1a1a1a; border: none; }
            QProgressBar#libProgress::chunk { background: #2a6a4a; }
        """)
