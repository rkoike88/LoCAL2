"""MemoryWindow — floating memory inspector for LoCAL2.

Browse mode: list_episodic() — all engrams, newest first.
Search mode: search_episodic() — semantic search, ranked as Gemma sees them.
Embedding call runs in a QThread worker so the UI stays responsive.
"""
from __future__ import annotations

import time

try:
    from PySide6.QtCore import QObject, QThread, Qt, Signal
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QPushButton,
        QSizePolicy,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc


# ---------------------------------------------------------------------------
# Background search worker
# ---------------------------------------------------------------------------

class _SearchWorker(QObject):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, memory_service, query: str) -> None:
        super().__init__()
        self._memory = memory_service
        self._query = query

    def run(self) -> None:
        try:
            results = self._memory.search_episodic(self._query)
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Memory window
# ---------------------------------------------------------------------------

class MemoryWindow(QWidget):
    _COLS = ["Age", "Resp", "Score", "Senti", "Winner", "Query"]

    def __init__(self, memory_service=None) -> None:
        super().__init__()
        self._memory = memory_service
        self._search_thread: QThread | None = None
        self._search_mode = False

        self.setWindowTitle("memory")
        self.resize(680, 500)
        self.setWindowFlags(Qt.Window)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_search_bar())
        layout.addWidget(self._build_splitter(), 1)

        self._apply_styles()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _build_header(self) -> QWidget:
        title = QLabel("memory")
        title.setObjectName("winTitle")

        self._browse_btn = QPushButton("Browse")
        self._browse_btn.setObjectName("memModeBtn")
        self._browse_btn.setCheckable(True)
        self._browse_btn.setChecked(True)
        self._browse_btn.clicked.connect(self._activate_browse)

        self._search_btn = QPushButton("Search")
        self._search_btn.setObjectName("memModeBtn")
        self._search_btn.setCheckable(True)
        self._search_btn.clicked.connect(self._activate_search)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setObjectName("memRefreshBtn")
        self._refresh_btn.clicked.connect(self._refresh)

        row = QHBoxLayout()
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)
        row.addWidget(title, 1)
        row.addWidget(self._browse_btn)
        row.addWidget(self._search_btn)
        row.addWidget(self._refresh_btn)

        header = QWidget()
        header.setObjectName("winHeader")
        header.setLayout(row)
        return header

    # ------------------------------------------------------------------
    # Search bar (hidden in Browse mode)
    # ------------------------------------------------------------------

    def _build_search_bar(self) -> QWidget:
        self._search_input = QLineEdit()
        self._search_input.setObjectName("memSearchInput")
        self._search_input.setPlaceholderText("Enter query for semantic search…")
        self._search_input.returnPressed.connect(self._run_search)

        go_btn = QPushButton("Search")
        go_btn.setObjectName("memGoBtn")
        go_btn.clicked.connect(self._run_search)

        self._status_label = QLabel("")
        self._status_label.setObjectName("memStatus")

        row = QHBoxLayout()
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(6)
        row.addWidget(self._search_input, 1)
        row.addWidget(go_btn)
        row.addWidget(self._status_label)

        self._search_bar = QWidget()
        self._search_bar.setObjectName("memSearchBar")
        self._search_bar.setLayout(row)
        self._search_bar.setVisible(False)
        return self._search_bar

    # ------------------------------------------------------------------
    # Table + detail pane
    # ------------------------------------------------------------------

    def _build_splitter(self) -> QWidget:
        self._table = QTableWidget(0, len(self._COLS))
        self._table.setObjectName("memTable")
        self._table.setHorizontalHeaderLabels(self._COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._on_row_selected)

        self._detail = QTextEdit()
        self._detail.setObjectName("memDetail")
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Click a row to see full content…")
        self._detail.setMaximumHeight(160)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        return splitter

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _activate_browse(self) -> None:
        self._search_mode = False
        self._browse_btn.setChecked(True)
        self._search_btn.setChecked(False)
        self._search_bar.setVisible(False)
        self._refresh()

    def _activate_search(self) -> None:
        self._search_mode = True
        self._search_btn.setChecked(True)
        self._browse_btn.setChecked(False)
        self._search_bar.setVisible(True)
        self._search_input.setFocus()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._search_mode:
            self._run_search()
        else:
            self._load_browse()

    def _load_browse(self) -> None:
        if not self._memory:
            return
        rows = self._memory.list_episodic(n=100)
        self._populate_table(rows, score_col_label="Score")

    def _run_search(self) -> None:
        if not self._memory:
            return
        query = self._search_input.text().strip()
        if not query:
            return
        if self._search_thread and self._search_thread.isRunning():
            return

        self._status_label.setText("Searching…")
        self._refresh_btn.setEnabled(False)

        worker = _SearchWorker(self._memory, query)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_search_done)
        worker.error.connect(self._on_search_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._search_thread = thread
        thread.start()

    def _on_search_done(self, results: list) -> None:
        self._status_label.setText(f"{len(results)} results")
        self._refresh_btn.setEnabled(True)
        self._populate_table(results, score_col_label="Score")

    def _on_search_error(self, msg: str) -> None:
        self._status_label.setText(f"Error: {msg}")
        self._refresh_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self, rows: list, score_col_label: str = "Score") -> None:
        self._table.setRowCount(0)
        self._detail.clear()
        self._table.horizontalHeaderItem(2).setText(score_col_label)

        for row_data in rows:
            meta = row_data.get("metadata") or {}
            content = row_data.get("content") or ""

            age = self._format_age(meta.get("timestamp"))
            resp = meta.get("respondent_id", "A")
            score = meta.get("critic_score")
            score_str = f"{score}/5" if score is not None else "—"
            sentiment = meta.get("user_sentiment")
            senti_str = "👍" if sentiment == 1 else ("👎" if sentiment == -1 else "—")
            winner = meta.get("pairwise_winner")
            winner_str = "✓" if winner is True else ("✗" if winner is False else "—")
            query_text = (meta.get("query") or content[:60]).replace("\n", " ")

            r = self._table.rowCount()
            self._table.insertRow(r)
            for col, val in enumerate([age, resp, score_str, senti_str, winner_str, query_text]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                self._table.setItem(r, col, item)

            # store full content on the first cell for detail view
            self._table.item(r, 0).setData(Qt.UserRole, content)

    def _on_row_selected(self) -> None:
        rows = self._table.selectedItems()
        if not rows:
            return
        content = self._table.item(rows[0].row(), 0).data(Qt.UserRole) or ""
        self._detail.setPlainText(content)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_age(timestamp) -> str:
        if not timestamp:
            return "—"
        secs = int(time.time() - float(timestamp))
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h"
        return f"{secs // 86400}d"

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            MemoryWindow, QWidget { background: #111111; }
            QWidget#winHeader { background: #1a1a1a; border-bottom: 1px solid #2a2a2a; }
            QLabel#winTitle {
                color: #d4d4d4; font-family: 'Menlo','Monaco','Courier New';
                font-size: 13px; font-weight: bold;
            }
            QWidget#memSearchBar { background: #161616; border-bottom: 1px solid #2a2a2a; }
            QLineEdit#memSearchInput {
                background: #1a1a1a; color: #d4d4d4; border: 1px solid #333;
                border-radius: 4px; padding: 4px 8px;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
            }
            QPushButton#memGoBtn, QPushButton#memRefreshBtn {
                background: #1a2a3a; color: #9dbde8; border: 1px solid #2a4a6a;
                border-radius: 4px; padding: 3px 10px; font-size: 12px;
            }
            QPushButton#memGoBtn:hover, QPushButton#memRefreshBtn:hover { background: #1e3448; }
            QPushButton#memModeBtn {
                background: #1a1a1a; color: #888; border: 1px solid #333;
                border-radius: 4px; padding: 3px 10px; font-size: 12px;
            }
            QPushButton#memModeBtn:checked { background: #1a2a1a; color: #7ec8a4; border-color: #3a5a3a; }
            QLabel#memStatus { color: #666; font-size: 11px; min-width: 80px; }
            QTableWidget#memTable {
                background: #111111; color: #d4d4d4; gridline-color: #1e1e1e;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
                border: none;
            }
            QTableWidget#memTable::item:selected { background: #1a2a3a; }
            QHeaderView::section {
                background: #1a1a1a; color: #888; border: none;
                border-bottom: 1px solid #2a2a2a; padding: 4px 8px; font-size: 11px;
            }
            QTextEdit#memDetail {
                background: #141414; color: #aaa; border: none; border-top: 1px solid #2a2a2a;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
                padding: 8px;
            }
        """)
