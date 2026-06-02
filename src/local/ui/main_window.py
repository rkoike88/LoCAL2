"""LoCAL2 main window — conversation log + per-tool panels.

Layout:
  52px icon strip  |  QStackedWidget
                   |    page 0: conversation log + query input
                   |    pages 1-6: tool panels (activity + settings)
"""
from __future__ import annotations

import uuid
from datetime import datetime

try:
    from PySide6.QtCore import QObject, QThread, Qt, Signal
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required. Install pyside6 first.") from exc

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    ANSWER_DIALOG,
    CRITIQUE,
    GENERATION_THINKING,
    QUERY_RECEIVED,
    RESPONSE_GENERATION,
    TOOL_ACTIVITY_GET_TOPIC,
    TOOL_ACTIVITY_SAVE_TOPIC,
    TOOL_ACTIVITY_SEARCH_MEMORY,
    TOOL_ACTIVITY_WEB_FETCH,
    TOOL_ACTIVITY_WEB_SEARCH,
    TOOL_REQUEST_WEB_FETCH,
    TOOL_REQUEST_WEB_SEARCH,
    TOOL_RESULT_WEB_FETCH,
    TOOL_RESULT_WEB_SEARCH,
)
from local.session.local_session import OBSERVE
from local.transport.bus_config import PROXY_BACKEND_ADDR, PROXY_FRONTEND_ADDR
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber
from local.ui.tool_panel import ToolPanel

# Activity subjects that tool panels need to receive
_TOOL_ACTIVITY_SUBJECTS = [
    TOOL_ACTIVITY_SAVE_TOPIC,
    TOOL_ACTIVITY_GET_TOPIC,
    TOOL_ACTIVITY_SEARCH_MEMORY,
    TOOL_ACTIVITY_WEB_SEARCH,
    TOOL_ACTIVITY_WEB_FETCH,
]

# (label, tooltip, config_name, activity_subject)
_TOOL_DEFS = [
    ("Sv", "save_topic",              "save_topic",              TOOL_ACTIVITY_SAVE_TOPIC),
    ("Rc", "recall_topic",            "get_topic",               TOOL_ACTIVITY_GET_TOPIC),
    ("Sm", "search_memory",           "search_memory",           TOOL_ACTIVITY_SEARCH_MEMORY),
    ("Ws", "web_search",              "web_search",              TOOL_ACTIVITY_WEB_SEARCH),
    ("Wf", "web_fetch",               "web_fetch",               TOOL_ACTIVITY_WEB_FETCH),
]


# ---------------------------------------------------------------------------
# Streaming response widget
# ---------------------------------------------------------------------------

class StreamingResponseWidget(QWidget):
    """Response card that fills in live: thinking streams first, answer finalizes it."""

    def __init__(self, ts: str) -> None:
        super().__init__()
        self.setObjectName("responseItem")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        self._header = QLabel(f"[{ts}] RESPONSE  ⟳")
        self._header.setObjectName("logHeader")
        layout.addWidget(self._header)

        self._toggle_btn = QPushButton("◈ thinking  ▼")
        self._toggle_btn.setObjectName("thinkingToggle")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._toggle_btn.setVisible(False)
        layout.addWidget(self._toggle_btn)

        self._thinking_box = QTextEdit()
        self._thinking_box.setObjectName("thinkingBox")
        self._thinking_box.setReadOnly(True)
        self._thinking_box.setMaximumHeight(280)
        self._thinking_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._thinking_box.setVisible(False)
        layout.addWidget(self._thinking_box)

        self._answer_label = QLabel()
        self._answer_label.setObjectName("logAnswer")
        self._answer_label.setWordWrap(True)
        self._answer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._answer_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._answer_label.setVisible(False)
        layout.addWidget(self._answer_label)

        self._thinking_visible = True
        self._toggle_btn.clicked.connect(self._toggle)

    def append_thinking_chunk(self, chunk: str) -> None:
        if not self._toggle_btn.isVisible():
            self._toggle_btn.setVisible(True)
            self._thinking_box.setVisible(True)
        cursor = self._thinking_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(chunk)
        self._thinking_box.setTextCursor(cursor)
        self._thinking_box.verticalScrollBar().setValue(
            self._thinking_box.verticalScrollBar().maximum()
        )

    def finalize(self, ts: str, answer: str, tool_calls: list) -> None:
        tool_ind = f"  ⚙ {len(tool_calls)} tool call(s)" if tool_calls else ""
        self._header.setText(f"[{ts}] RESPONSE{tool_ind}")
        if self._toggle_btn.isVisible():
            self._thinking_visible = False
            self._thinking_box.setVisible(False)
            self._toggle_btn.setText("◈ thinking  ▶")
        self._answer_label.setText(answer or "(empty)")
        self._answer_label.setVisible(True)

    def _toggle(self) -> None:
        self._thinking_visible = not self._thinking_visible
        self._thinking_box.setVisible(self._thinking_visible)
        self._toggle_btn.setText(
            "◈ thinking  ▼" if self._thinking_visible else "◈ thinking  ▶"
        )


# ---------------------------------------------------------------------------
# Background bus monitor
# ---------------------------------------------------------------------------

class BusMonitorWorker(QObject):
    envelope_received = Signal(object)

    def __init__(self, address: str, subscriptions: list[str]) -> None:
        super().__init__()
        self._address = address
        self._subscriptions = subscriptions
        self._running = True

    def run(self) -> None:
        sub = ZmqSubscriber(self._address, subscriptions=self._subscriptions, bind=False)
        try:
            while self._running:
                msg = sub.receive_with_timeout(200)
                if msg is not None:
                    self.envelope_received.emit(msg)
        finally:
            sub.close()

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Bus event formatter
# ---------------------------------------------------------------------------

class BusLogger(QObject):
    message = Signal(str)
    thinking_chunk = Signal(dict)
    response = Signal(dict)
    tool_activity = Signal(object)   # passes MessageEnvelope through

    def log_envelope(self, envelope: MessageEnvelope) -> None:
        now = datetime.now()
        ts = now.strftime("%H:%M:%S") + f".{now.microsecond // 100000}"
        raw = envelope.payload or {}
        subject = envelope.subject

        if subject in _TOOL_ACTIVITY_SUBJECTS:
            self.tool_activity.emit(envelope)
            return

        if subject == GENERATION_THINKING:
            self.thinking_chunk.emit({
                "ts": ts,
                "chunk": raw.get("chunk") or "",
                "query_id": raw.get("query_id") or "",
            })
            return

        if subject == RESPONSE_GENERATION:
            self.response.emit({
                "ts": ts,
                "answer": (raw.get("answer") or "").strip(),
                "thinking": (raw.get("thinking") or "").strip(),
                "tool_calls": raw.get("tool_calls") or [],
                "query_id": raw.get("query_id") or "",
            })
            return

        if subject == QUERY_RECEIVED:
            query = (raw.get("query") or "")[:100].replace("\n", " ")
            text = f"[{ts}] QUERY\n  {query}"

        elif subject == ANSWER_DIALOG:
            text = f"[{ts}] DIALOG  (conversation recorded)"

        elif subject == CRITIQUE:
            score = raw.get("score", "")
            verdict = raw.get("verdict", "")
            text = f"[{ts}] CRITIQUE  score={score}  verdict={verdict}"

        elif subject in (TOOL_REQUEST_WEB_SEARCH, TOOL_REQUEST_WEB_FETCH):
            tool_name = subject.split(".")[-1]
            args = raw.get("args") or {}
            text = f"[{ts}] TOOL REQUEST  {tool_name}\n  args: {args}"

        elif subject in (TOOL_RESULT_WEB_SEARCH, TOOL_RESULT_WEB_FETCH):
            tool_name = subject.split(".")[-1]
            snippet = str(raw.get("result") or "")[:80].replace("\n", " ")
            text = f"[{ts}] TOOL RESULT  {tool_name}\n  {snippet}"

        else:
            text = f"[{ts}] {subject.upper()}\n  sender={envelope.sender_id}"

        self.message.emit(text)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self, publisher: ZmqPublisher, model: str = "") -> None:
        super().__init__()
        self._publisher = publisher
        self._session_id = str(uuid.uuid4())
        self._pending: dict[str, StreamingResponseWidget] = {}
        title = "LoCAL2"
        if model:
            title += f"  [{model}]"
        self.setWindowTitle(title)
        self.resize(960, 720)

        # ── Tool panels (one per tool, keyed by activity subject) ─────
        self._tool_panels: dict[str, ToolPanel] = {}

        # ── Stack: page 0 = conversation, pages 1-N = tool panels ─────
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_conversation_page())
        for label, tooltip, config_name, activity_subject in _TOOL_DEFS:
            panel = ToolPanel(
                tool_name=tooltip,
                config_name=config_name,
                publisher=publisher,
            )
            self._tool_panels[activity_subject] = panel
            self._stack.addWidget(panel)

        # ── Icon strip ────────────────────────────────────────────────
        icon_strip = self._build_sidebar()

        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(icon_strip)
        root.addWidget(self._stack, 1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        self._apply_styles()
        self._start_bus_monitor()

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _build_conversation_page(self) -> QWidget:
        self._log_widget = QWidget()
        self._log_widget.setObjectName("logWidget")
        self._log_layout = QVBoxLayout(self._log_widget)
        self._log_layout.setContentsMargins(0, 8, 0, 8)
        self._log_layout.setSpacing(0)
        self._log_layout.addStretch()

        self._log_scroll = QScrollArea()
        self._log_scroll.setObjectName("logScroll")
        self._log_scroll.setWidget(self._log_widget)
        self._log_scroll.setWidgetResizable(True)
        self._log_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._query_input = QLineEdit()
        self._query_input.setObjectName("queryInput")
        self._query_input.setPlaceholderText("Type a query and press Enter…")
        self._query_input.returnPressed.connect(self._send_query)

        send_btn = QPushButton("Send")
        send_btn.setObjectName("sendBtn")
        send_btn.setFixedWidth(72)
        send_btn.clicked.connect(self._send_query)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)
        input_row.addWidget(self._query_input)
        input_row.addWidget(send_btn)

        input_container = QWidget()
        input_container.setObjectName("inputContainer")
        input_container.setLayout(input_row)
        input_container.setContentsMargins(12, 8, 12, 12)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._log_scroll, 1)
        layout.addWidget(input_container)
        return page

    def _build_sidebar(self) -> QWidget:
        clear_btn = QPushButton("+")
        clear_btn.setObjectName("sidebarBtn")
        clear_btn.setToolTip("Clear log")
        clear_btn.setFixedSize(36, 36)
        clear_btn.clicked.connect(self._clear_log)

        new_session_btn = QPushButton("⟳")
        new_session_btn.setObjectName("sidebarBtn")
        new_session_btn.setToolTip("New conversation")
        new_session_btn.setFixedSize(36, 36)
        new_session_btn.clicked.connect(self._new_session)

        # Nav button to return to conversation
        chat_btn = QPushButton("◉")
        chat_btn.setObjectName("sidebarNavBtn")
        chat_btn.setToolTip("Conversation")
        chat_btn.setFixedSize(36, 36)
        chat_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))

        icon_strip = QWidget()
        icon_strip.setObjectName("sidebar")
        icon_strip.setFixedWidth(52)
        strip_layout = QVBoxLayout(icon_strip)
        strip_layout.setContentsMargins(8, 12, 8, 12)
        strip_layout.setSpacing(8)
        strip_layout.addWidget(clear_btn, alignment=Qt.AlignHCenter)
        strip_layout.addWidget(new_session_btn, alignment=Qt.AlignHCenter)

        # Separator
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setObjectName("sidebarSep")
        strip_layout.addSpacing(4)
        strip_layout.addWidget(sep)
        strip_layout.addSpacing(4)

        strip_layout.addWidget(chat_btn, alignment=Qt.AlignHCenter)

        # Tool nav buttons (stack pages start at index 1)
        for stack_idx, (label, tooltip, _, _) in enumerate(_TOOL_DEFS, start=1):
            btn = QPushButton(label)
            btn.setObjectName("sidebarNavBtn")
            btn.setToolTip(tooltip)
            btn.setFixedSize(36, 36)
            btn.clicked.connect(lambda checked=False, idx=stack_idx: self._stack.setCurrentIndex(idx))
            strip_layout.addWidget(btn, alignment=Qt.AlignHCenter)

        strip_layout.addStretch()
        return icon_strip

    # ------------------------------------------------------------------
    # Bus monitor
    # ------------------------------------------------------------------

    def _start_bus_monitor(self) -> None:
        all_subjects = list(OBSERVE) + _TOOL_ACTIVITY_SUBJECTS
        self._bus_logger = BusLogger()
        self._bus_logger.message.connect(self.append_log)
        self._bus_logger.thinking_chunk.connect(self._on_thinking_chunk)
        self._bus_logger.response.connect(self._on_response)
        self._bus_logger.tool_activity.connect(self._on_tool_activity)

        self._monitor_worker = BusMonitorWorker(PROXY_BACKEND_ADDR, subscriptions=all_subjects)
        self._monitor_worker.envelope_received.connect(self._bus_logger.log_envelope)

        self._monitor_thread = QThread(self)
        self._monitor_worker.moveToThread(self._monitor_thread)
        self._monitor_thread.started.connect(self._monitor_worker.run)
        self._monitor_thread.start()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_tool_activity(self, envelope: MessageEnvelope) -> None:
        panel = self._tool_panels.get(envelope.subject)
        if panel:
            panel.append_activity(envelope)

    def _send_query(self) -> None:
        query = self._query_input.text().strip()
        if not query:
            return
        query_id = str(uuid.uuid4())
        envelope = MessageEnvelope.create(
            message_type="query",
            subject=QUERY_RECEIVED,
            sender_id="ui",
            payload={
                "query": query,
                "session_id": self._session_id,
                "query_id": query_id,
            },
            correlation_id=query_id,
            metadata={"session_id": self._session_id},
        )
        self._publisher.publish(envelope)
        self._query_input.clear()

    def _new_session(self) -> None:
        self._session_id = str(uuid.uuid4())
        self.append_log(f"── new conversation  session={self._session_id[:8]}… ──")

    def _clear_log(self) -> None:
        self._pending.clear()
        while self._log_layout.count() > 1:
            item = self._log_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_thinking_chunk(self, data: dict) -> None:
        query_id = data["query_id"]
        if query_id not in self._pending:
            widget = StreamingResponseWidget(data["ts"])
            self._insert_log_widget(widget)
            self._pending[query_id] = widget
            self._scroll_to_bottom()
        self._pending[query_id].append_thinking_chunk(data["chunk"])
        self._scroll_to_bottom()

    def _on_response(self, data: dict) -> None:
        query_id = data["query_id"]
        widget = self._pending.pop(query_id, None)
        if widget is None:
            widget = StreamingResponseWidget(data["ts"])
            self._insert_log_widget(widget)
        widget.finalize(data["ts"], data["answer"], data["tool_calls"])
        self._scroll_to_bottom()

    def append_log(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("logItem")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._insert_log_widget(label)
        self._scroll_to_bottom()

    def _insert_log_widget(self, widget: QWidget) -> None:
        self._log_layout.insertWidget(self._log_layout.count() - 1, widget)

    def _scroll_to_bottom(self) -> None:
        self._log_scroll.verticalScrollBar().setValue(
            self._log_scroll.verticalScrollBar().maximum()
        )

    def closeEvent(self, event) -> None:
        self._monitor_worker.stop()
        self._monitor_thread.quit()
        self._monitor_thread.wait(1000)
        self._publisher.close()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0d0d0d;
                color: #ececec;
                font-size: 14px;
            }
            QWidget#sidebar {
                background: #171717;
                border-right: 1px solid #222222;
            }
            QWidget#sidebarSep {
                background: #2a2a2a;
            }
            QPushButton#sidebarBtn {
                background: transparent;
                color: #888888;
                border: none;
                border-radius: 8px;
                font-size: 18px;
                padding: 0px;
            }
            QPushButton#sidebarBtn:hover {
                background: #2a2a2a;
                color: #ececec;
            }
            QPushButton#sidebarNavBtn {
                background: transparent;
                color: #666666;
                border: none;
                border-radius: 6px;
                font-size: 11px;
                font-family: "Menlo", "Monaco", "Courier New";
                padding: 0px;
            }
            QPushButton#sidebarNavBtn:hover {
                background: #2a2a2a;
                color: #ececec;
            }
            QScrollArea#logScroll {
                background: #0d0d0d;
                border: none;
            }
            QWidget#logWidget {
                background: #0d0d0d;
            }
            QScrollArea#activityScroll {
                background: #0d0d0d;
                border: none;
            }
            QWidget#activityWidget {
                background: #0d0d0d;
            }
            QLabel#logItem {
                background: transparent;
                color: #666666;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 12px;
                padding: 2px 24px;
            }
            QWidget#responseItem {
                background: #111111;
                border-top: 1px solid #1e1e1e;
                border-bottom: 1px solid #1e1e1e;
            }
            QLabel#logHeader {
                color: #555555;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 12px;
            }
            QLabel#logAnswer {
                color: #ececec;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 13px;
                padding: 4px 0px;
            }
            QPushButton#thinkingToggle {
                background: transparent;
                color: #555555;
                border: none;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 12px;
                padding: 2px 0px;
                text-align: left;
            }
            QPushButton#thinkingToggle:hover {
                color: #888888;
            }
            QTextEdit#thinkingBox {
                background: #0a0a0a;
                color: #555555;
                border: 1px solid #1e1e1e;
                border-radius: 4px;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 12px;
                padding: 8px;
            }
            QWidget#inputContainer {
                background: #111111;
                border-top: 1px solid #222222;
            }
            QLineEdit#queryInput {
                background: #1a1a1a;
                color: #ececec;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 14px;
            }
            QLineEdit#queryInput:focus {
                border: 1px solid #555555;
            }
            QPushButton#sendBtn {
                background: #2a2a2a;
                color: #ececec;
                border: 1px solid #383838;
                border-radius: 6px;
                padding: 8px 0px;
                font-size: 13px;
            }
            QPushButton#sendBtn:hover {
                background: #383838;
            }
            QTabWidget#toolTabs::pane {
                background: #0d0d0d;
                border: none;
                border-top: 1px solid #222222;
            }
            QTabBar::tab {
                background: #171717;
                color: #666666;
                border: none;
                padding: 6px 16px;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background: #0d0d0d;
                color: #ececec;
                border-bottom: 2px solid #555555;
            }
            QTabBar::tab:hover {
                color: #aaaaaa;
            }
            QPlainTextEdit#yamlEditor {
                background: #0a0a0a;
                color: #cccccc;
                border: 1px solid #222222;
                border-radius: 4px;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 12px;
                padding: 8px;
            }
            QWidget#settingsBtnRow {
                background: #111111;
                border-top: 1px solid #1e1e1e;
            }
            QPushButton#saveSettingsBtn {
                background: #2a2a2a;
                color: #ececec;
                border: 1px solid #383838;
                border-radius: 5px;
                padding: 5px 0px;
                font-size: 13px;
            }
            QPushButton#saveSettingsBtn:hover {
                background: #383838;
            }
            QLabel#settingsStatus {
                color: #666666;
                font-size: 12px;
                font-family: "Menlo", "Monaco", "Courier New";
            }
            QScrollBar:vertical {
                background: #0d0d0d;
                width: 6px;
            }
            QScrollBar::handle:vertical {
                background: #333333;
                border-radius: 3px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
