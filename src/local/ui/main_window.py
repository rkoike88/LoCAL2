"""LoCAL2 main window — bus log + query input.

Layout:
  52px icon strip  |  log view (fills remaining space)
  ─────────────────────────────────────────────────────
                   |  query input row (bottom)

BusMonitorWorker runs in a background QThread and emits all bus envelopes.
BusLogger formats them and pushes strings to the log view via Qt signals.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

try:
    from PySide6.QtCore import QObject, QThread, Qt, Signal
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QSizePolicy,
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
    QUERY_RECEIVED,
    RESPONSE_GENERATION,
    TOOL_REQUEST_WEB_FETCH,
    TOOL_REQUEST_WEB_SEARCH,
    TOOL_RESULT_WEB_FETCH,
    TOOL_RESULT_WEB_SEARCH,
)
from local.session.local_session import OBSERVE
from local.transport.bus_config import PROXY_BACKEND_ADDR, PROXY_FRONTEND_ADDR
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber


# ---------------------------------------------------------------------------
# Background bus monitor
# ---------------------------------------------------------------------------

class BusMonitorWorker(QObject):
    """Subscribes to the bus in a background thread; emits one signal per envelope."""

    envelope_received = Signal(object)  # MessageEnvelope

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
    """Thread-safe formatter: receives envelopes, emits styled log strings."""

    message = Signal(str)

    def log_envelope(self, envelope: MessageEnvelope) -> None:
        now = datetime.now()
        ts = now.strftime("%H:%M:%S") + f".{now.microsecond // 100000}"
        raw = envelope.payload or {}
        subject = envelope.subject

        if subject == QUERY_RECEIVED:
            query = (raw.get("query") or "")[:100].replace("\n", " ")
            text = f"\n[{ts}] QUERY\n  {query}"

        elif subject == RESPONSE_GENERATION:
            answer = (raw.get("answer") or "")[:80].replace("\n", " ")
            truncated = len(raw.get("answer") or "") > 80
            thinking = raw.get("thinking") or ""
            tool_calls = raw.get("tool_calls") or []
            think_ind = "◈ thinking" if thinking else ""
            tool_ind = f"⚙ {len(tool_calls)} tool call(s)" if tool_calls else ""
            flags = "  ".join(x for x in [think_ind, tool_ind] if x)
            text = f"\n[{ts}] RESPONSE\n  Answer:  {answer}{'…' if truncated else ''}"
            if flags:
                text += f"\n  Flags:   {flags}"

        elif subject == ANSWER_DIALOG:
            text = f"\n[{ts}] DIALOG  (conversation recorded)"

        elif subject == CRITIQUE:
            score = raw.get("score", "")
            verdict = raw.get("verdict", "")
            text = f"\n[{ts}] CRITIQUE  score={score}  verdict={verdict}"

        elif subject in (TOOL_REQUEST_WEB_SEARCH, TOOL_REQUEST_WEB_FETCH):
            tool_name = subject.split(".")[-1]   # "web_search" or "web_fetch"
            args = raw.get("args") or {}
            text = f"\n[{ts}] TOOL REQUEST  {tool_name}\n  args: {args}"

        elif subject in (TOOL_RESULT_WEB_SEARCH, TOOL_RESULT_WEB_FETCH):
            tool_name = subject.split(".")[-1]
            snippet = str(raw.get("result") or "")[:80].replace("\n", " ")
            text = f"\n[{ts}] TOOL RESULT  {tool_name}\n  {snippet}"

        else:
            text = f"\n[{ts}] {subject.upper()}\n  sender={envelope.sender_id}"

        self.message.emit(text)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """LoCAL2 participant window: bus log + query submission."""

    def __init__(self, publisher: ZmqPublisher, model: str = "") -> None:
        super().__init__()
        self._publisher = publisher
        self._session_id = str(uuid.uuid4())
        title = "LoCAL2 Generator"
        if model:
            title += f"  [{model}]"
        self.setWindowTitle(title)
        self.resize(900, 720)

        # ── Icon strip ────────────────────────────────────────────────
        clear_btn = QPushButton("+")
        clear_btn.setObjectName("sidebarBtn")
        clear_btn.setToolTip("Clear log")
        clear_btn.setFixedSize(36, 36)
        clear_btn.clicked.connect(lambda: self._log_view.clear())

        new_session_btn = QPushButton("⟳")
        new_session_btn.setObjectName("sidebarBtn")
        new_session_btn.setToolTip("New conversation")
        new_session_btn.setFixedSize(36, 36)
        new_session_btn.clicked.connect(self._new_session)

        icon_strip = QWidget()
        icon_strip.setObjectName("sidebar")
        icon_strip.setFixedWidth(52)
        strip_layout = QVBoxLayout(icon_strip)
        strip_layout.setContentsMargins(8, 12, 8, 12)
        strip_layout.setSpacing(8)
        strip_layout.addWidget(clear_btn, alignment=Qt.AlignHCenter)
        strip_layout.addWidget(new_session_btn, alignment=Qt.AlignHCenter)
        strip_layout.addStretch()

        # ── Log view ──────────────────────────────────────────────────
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setObjectName("conversationLog")
        self._log_view.setPlaceholderText("Bus events will appear here…")

        # ── Query input row ───────────────────────────────────────────
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

        # ── Main content area (log + input) ───────────────────────────
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._log_view, 1)

        input_container = QWidget()
        input_container.setObjectName("inputContainer")
        input_container.setLayout(input_row)
        input_container.setContentsMargins(12, 8, 12, 12)
        content_layout.addWidget(input_container)

        # ── Root layout ───────────────────────────────────────────────
        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(icon_strip)

        content_widget = QWidget()
        content_widget.setLayout(content_layout)
        root.addWidget(content_widget, 1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        self._apply_styles()
        self._start_bus_monitor()

    def _start_bus_monitor(self) -> None:
        self._bus_logger = BusLogger()
        self._bus_logger.message.connect(self.append_log)

        self._monitor_worker = BusMonitorWorker(PROXY_BACKEND_ADDR, subscriptions=OBSERVE)
        self._monitor_worker.envelope_received.connect(self._bus_logger.log_envelope)

        self._monitor_thread = QThread(self)
        self._monitor_worker.moveToThread(self._monitor_thread)
        self._monitor_thread.started.connect(self._monitor_worker.run)
        self._monitor_thread.start()

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
        self.append_log(f"\n── new conversation  session={self._session_id[:8]}… ──")

    def append_log(self, text: str) -> None:
        self._log_view.append(text)
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def closeEvent(self, event) -> None:
        self._monitor_worker.stop()
        self._monitor_thread.quit()
        self._monitor_thread.wait(1000)
        self._publisher.close()
        super().closeEvent(event)

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
            QTextEdit#conversationLog {
                background: #0d0d0d;
                color: #ececec;
                border: none;
                padding: 16px 24px;
                font-family: monospace;
                font-size: 13px;
                selection-background-color: #2a2a2a;
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
