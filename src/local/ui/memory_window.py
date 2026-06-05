"""MemoryWindow — floating memory inspector for LoCAL2.

Browse mode:  list_episodic() — all engrams, newest first.
Search mode:  search_episodic() — semantic search, ranked as Gemma sees them.
Context mode: ConversationService.get_history() — live messages Gemma sees this session.

Embedding search runs in a QThread worker so the UI stays responsive.
Transition log strip at bottom shows memory_agent state transitions.
"""
from __future__ import annotations

import time
from datetime import datetime

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
    error    = Signal(str)

    def __init__(self, memory_service, query: str) -> None:
        super().__init__()
        self._memory = memory_service
        self._query  = query

    def run(self) -> None:
        try:
            results = self._memory.search_episodic(self._query)
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Memory window
# ---------------------------------------------------------------------------

_MODE_BROWSE  = "browse"
_MODE_SEARCH  = "search"
_MODE_CONTEXT = "context"

# Role display config: (label, colour)
_ROLE_STYLE: dict[str, tuple[str, str]] = {
    "user":      ("user",      "#9dbde8"),
    "assistant": ("asst",      "#7ec8a4"),
    "tool":      ("tool_res",  "#c8a47e"),
    "tool_call": ("tool_call", "#c8a47e"),
    "system":    ("sys",       "#888888"),
}


class MemoryWindow(QWidget):
    _EPISODIC_COLS = ["Age", "Resp", "Score", "Senti", "Winner", "Query"]
    _CONTEXT_COLS  = ["#", "Role", "Preview"]

    def __init__(self, memory_service=None, conversation_service=None, session_id_getter=None) -> None:
        super().__init__()
        self._memory            = memory_service
        self._conv              = conversation_service
        self._get_session_id    = session_id_getter or (lambda: None)
        self._search_thread: QThread | None = None
        self._search_worker     = None   # strong ref — prevents GC before thread runs
        self._mode              = _MODE_BROWSE
        self._browsed_session_id: str | None = None  # session_id of selected memory row

        self.setWindowTitle("memory")
        self.resize(680, 500)
        self.setWindowFlags(Qt.Window)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_search_bar())
        layout.addWidget(self._build_splitter(), 1)
        layout.addWidget(self._build_transition_log())

        self._apply_styles()
        self._refresh()

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

        self._context_btn = QPushButton("Context")
        self._context_btn.setObjectName("memModeBtn")
        self._context_btn.setCheckable(True)
        self._context_btn.clicked.connect(self._activate_context)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setObjectName("memRefreshBtn")
        self._refresh_btn.clicked.connect(self._refresh)

        row = QHBoxLayout()
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)
        row.addWidget(title, 1)
        row.addWidget(self._browse_btn)
        row.addWidget(self._search_btn)
        row.addWidget(self._context_btn)
        row.addWidget(self._refresh_btn)

        header = QWidget()
        header.setObjectName("winHeader")
        header.setLayout(row)
        return header

    # ------------------------------------------------------------------
    # Search bar (visible in Search mode only)
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
        self._table = QTableWidget(0, len(self._EPISODIC_COLS))
        self._table.setObjectName("memTable")
        self._table.setHorizontalHeaderLabels(self._EPISODIC_COLS)
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

    def _set_mode_buttons(self, mode: str) -> None:
        self._browse_btn.setChecked(mode == _MODE_BROWSE)
        self._search_btn.setChecked(mode == _MODE_SEARCH)
        self._context_btn.setChecked(mode == _MODE_CONTEXT)

    def _activate_browse(self) -> None:
        self._mode = _MODE_BROWSE
        self._browsed_session_id = None
        self._set_mode_buttons(_MODE_BROWSE)
        self._search_bar.setVisible(False)
        self._context_btn.setText("Context")
        self._set_table_columns(self._EPISODIC_COLS)
        self._load_browse()

    def _activate_search(self) -> None:
        self._mode = _MODE_SEARCH
        self._browsed_session_id = None
        self._set_mode_buttons(_MODE_SEARCH)
        self._search_bar.setVisible(True)
        self._context_btn.setText("Context")
        self._set_table_columns(self._EPISODIC_COLS)
        self._search_input.setFocus()

    def _activate_context(self) -> None:
        # Capture browsed_session_id BEFORE switching mode
        session_override = self._browsed_session_id
        print(f"[MemoryWindow] activate_context: session_override={session_override!r}  current_session={self._get_session_id()!r}")
        self._mode = _MODE_CONTEXT
        self._set_mode_buttons(_MODE_CONTEXT)
        self._search_bar.setVisible(False)
        self._set_table_columns(self._CONTEXT_COLS)
        self._context_btn.setText("Context")
        self._load_context(session_id_override=session_override)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._mode == _MODE_CONTEXT:
            self._load_context()

    def _set_table_columns(self, cols: list[str]) -> None:
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        hdr = self._table.horizontalHeader()
        for i in range(len(cols) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(len(cols) - 1, QHeaderView.Stretch)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._mode == _MODE_BROWSE:
            self._load_browse()
        elif self._mode == _MODE_SEARCH:
            self._run_search()
        else:
            self._load_context()

    def _load_browse(self) -> None:
        if not self._memory:
            return
        rows = self._memory.list_episodic(n=100)
        self._populate_episodic(rows)

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
        self._search_worker  = worker  # keep alive until thread runs
        thread.start()

    def _on_search_done(self, results: list) -> None:
        self._search_worker = None
        self._status_label.setText(f"{len(results)} results")
        self._refresh_btn.setEnabled(True)
        self._populate_episodic(results)

    def _on_search_error(self, msg: str) -> None:
        self._search_worker = None
        self._status_label.setText(f"Error: {msg}")
        self._refresh_btn.setEnabled(True)

    def _load_context(self, session_id_override: str | None = None) -> None:
        self._table.setRowCount(0)
        self._detail.clear()
        if not self._conv:
            self._detail.setPlaceholderText("No conversation service. Restart the stack and try again.")
            return
        session_id = session_id_override or self._get_session_id()
        messages = self._conv.get_history(session_id)
        print(f"[MemoryWindow] load_context: session={session_id!r}  messages={len(messages)}  conv_sessions={list(self._conv._sessions.keys())}")
        if not messages:
            if session_id_override:
                self._detail.setPlaceholderText(
                    "Session history not available.\n"
                    "This memory is from a previous run — conversation history is in-memory only."
                )
            else:
                self._detail.setPlaceholderText(
                    "No conversation messages yet.\nSend a message first, then click Refresh."
                )
            return
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            # tool_calls list → synthetic "tool_call" role for display
            if "tool_calls" in msg and msg["tool_calls"]:
                role = "tool_call"
            content = self._message_preview(msg)
            full    = self._message_full(msg)

            label, colour = _ROLE_STYLE.get(role, (role, "#888"))

            r = self._table.rowCount()
            self._table.insertRow(r)

            num_item = QTableWidgetItem(str(i + 1))
            num_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignCenter)
            self._table.setItem(r, 0, num_item)

            role_item = QTableWidgetItem(label)
            role_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignCenter)
            role_item.setForeground(Qt.GlobalColor.white)
            self._table.setItem(r, 1, role_item)

            preview_item = QTableWidgetItem(content)
            preview_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            preview_item.setData(Qt.UserRole, full)
            self._table.setItem(r, 2, preview_item)

            # colour the role cell
            self._table.item(r, 1).setForeground(
                __import__("PySide6.QtGui", fromlist=["QColor"]).QColor(colour)
            )

        n = len(messages)
        count_str = f"{n} message{'s' if n != 1 else ''}"
        self._status_label.setText(count_str)
        self._context_btn.setText(f"Context ({n})")

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def _populate_episodic(self, rows: list) -> None:
        self._table.setRowCount(0)
        self._detail.clear()
        for row_data in rows:
            meta    = row_data.get("metadata") or {}
            content = row_data.get("content") or ""

            age        = self._format_age(meta.get("timestamp"))
            resp       = meta.get("respondent_id", "A")
            score      = meta.get("critic_score")
            score_str  = f"{score}/5" if score is not None else "—"
            sentiment  = meta.get("user_sentiment")
            senti_str  = "👍" if sentiment == 1 else ("👎" if sentiment == -1 else "—")
            winner     = meta.get("pairwise_winner")
            winner_str = "✓" if winner is True else ("✗" if winner is False else "—")
            query_text = (meta.get("query") or content[:60]).replace("\n", " ")

            r = self._table.rowCount()
            self._table.insertRow(r)
            for col, val in enumerate([age, resp, score_str, senti_str, winner_str, query_text]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                self._table.setItem(r, col, item)
            self._table.item(r, 0).setData(Qt.UserRole, content)
            self._table.item(r, 0).setData(Qt.UserRole + 1, meta.get("session_id", ""))

    def _on_row_selected(self) -> None:
        rows = self._table.selectedItems()
        if not rows:
            return
        r = rows[0].row()
        if self._mode in (_MODE_BROWSE, _MODE_SEARCH):
            col0 = self._table.item(r, 0)
            self._browsed_session_id = (col0.data(Qt.UserRole + 1) or None) if col0 else None
            print(f"[MemoryWindow] row selected: row={r}  browsed_session_id={self._browsed_session_id!r}")
        # context mode: full text on preview column (col 2); episodic: col 0
        col = 2 if self._mode == _MODE_CONTEXT else 0
        item = self._table.item(r, col)
        if item:
            self._detail.setPlainText(item.data(Qt.UserRole) or item.text())

    @staticmethod
    def _message_preview(msg: dict) -> str:
        if "tool_calls" in msg and msg["tool_calls"]:
            calls = msg["tool_calls"]
            names = ", ".join(c.get("function", {}).get("name", "?") for c in calls)
            return f"→ {names}"
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content.replace("\n", " ")[:120]

    @staticmethod
    def _message_full(msg: dict) -> str:
        import json
        if "tool_calls" in msg and msg["tool_calls"]:
            return json.dumps(msg["tool_calls"], indent=2)
        content = msg.get("content") or ""
        if isinstance(content, list):
            return json.dumps(content, indent=2)
        return content

    # ------------------------------------------------------------------
    # Transition log
    # ------------------------------------------------------------------

    def _build_transition_log(self) -> QWidget:
        self._transition_log = QTextEdit()
        self._transition_log.setObjectName("memTransitionLog")
        self._transition_log.setReadOnly(True)
        self._transition_log.setFixedHeight(56)
        return self._transition_log

    def append_transition(self, data: dict) -> None:
        ts     = datetime.now().strftime("%H:%M:%S")
        from_s = data.get("from", "?")
        action = data.get("action", "?")
        to_s   = data.get("to", "?")
        line   = f'<span style="color:#444">[{ts}]  {from_s} → {to_s}  ({action})</span>'
        self._transition_log.append(line)

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
            QTextEdit#memTransitionLog {
                background: #0e0e0e; color: #444; border: none; border-top: 1px solid #1e1e1e;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 11px;
                padding: 4px 8px;
            }
        """)
