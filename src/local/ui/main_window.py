"""LoCAL2 main window — conversation log + per-tool panels.

Layout:
  52px icon strip  |  QStackedWidget
                   |    page 0: conversation log + query input
                   |    pages 1-6: tool panels (activity + settings)
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime

try:
    from PySide6.QtCore import QBuffer, QByteArray, QObject, QThread, QTimer, Qt, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QGraphicsOpacityEffect,
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
    AGENT_TRANSITION,
    ANSWER_DIALOG,
    CRITIQUE,
    GENERATION_THINKING,
    PAIRWISE_RESULT,
    QUERY_RECEIVED,
    RESPONSE_GENERATION,
    TOOL_ACTIVITY_GET_DATETIME,
    TOOL_ACTIVITY_GET_LOCATION,
    TOOL_ACTIVITY_SEARCH_MEMORY,
    TOOL_ACTIVITY_SEARCH_DOCUMENTS,
    TOOL_ACTIVITY_SEARCH_PAPERS,
    TOOL_ACTIVITY_WEB_FETCH,
    TOOL_ACTIVITY_WEB_SEARCH,
    TOOL_REQUEST_WEB_FETCH,
    TOOL_REQUEST_WEB_SEARCH,
    TOOL_RESULT_WEB_FETCH,
    TOOL_RESULT_WEB_SEARCH,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
    USER_FEEDBACK,
)
from local.session.local_session import OBSERVE
from local.transport.bus_config import PROXY_BACKEND_ADDR
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber
from local.ui.attachment_bar import AttachmentBar
from local.ui.critic_window import CriticWindow
from local.ui.documents_window import DocumentsWindow
from local.ui.memory_window import MemoryWindow
from local.ui.tool_window import ToolWindow

_TOOL_ACTIVITY_SUBJECTS = [
    TOOL_ACTIVITY_SEARCH_MEMORY,
    TOOL_ACTIVITY_WEB_SEARCH,
    TOOL_ACTIVITY_WEB_FETCH,
    TOOL_ACTIVITY_GET_DATETIME,
    TOOL_ACTIVITY_GET_LOCATION,
    TOOL_ACTIVITY_SEARCH_PAPERS,
    TOOL_ACTIVITY_SEARCH_DOCUMENTS,
]

# col, row within the 5×2 panel grid (right 5/7 of screen)
_TOOL_PANEL_SLOTS: dict[str, tuple[int, int]] = {
    "search_memory":  (0, 0),
    "search_library": (1, 0),
    "web_search":     (2, 0),
    "search_papers":  (3, 0),
    "get_datetime":   (4, 0),
    "web_fetch":      (2, 1),
    "get_location":   (4, 1),
}


# ---------------------------------------------------------------------------
# Streaming response widget
# ---------------------------------------------------------------------------

class StreamingResponseWidget(QWidget):
    """Response card that fills in live: thinking streams first, answer finalizes it."""

    feedback = Signal(str, str)  # (query_id, sentiment)

    def __init__(self, ts: str) -> None:
        super().__init__()
        self.setObjectName("responseItem")
        self._query_id: str = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        self._header = QLabel(f"[{ts}] RESPONSE  ⟳")
        self._header.setObjectName("logHeader")
        layout.addWidget(self._header)

        self._attach_label = QLabel()
        self._attach_label.setObjectName("attachSummary")
        self._attach_label.setVisible(False)
        layout.addWidget(self._attach_label)

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

        # Bottom row: score label (left) + thumbs (right)
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)

        self._score_label = QLabel()
        self._score_label.setObjectName("scoreLabel")
        self._score_label.setVisible(False)
        bottom_row.addWidget(self._score_label)
        bottom_row.addStretch()

        self._thumb_up = QPushButton("👍")
        self._thumb_up.setFlat(True)
        self._thumb_up.setCursor(Qt.PointingHandCursor)
        self._thumb_up.setFixedSize(28, 28)
        self._thumb_up.setVisible(False)
        self._thumb_up.setToolTip("Good response")
        self._thumb_up_opacity = QGraphicsOpacityEffect()
        self._thumb_up_opacity.setOpacity(0.3)
        self._thumb_up.setGraphicsEffect(self._thumb_up_opacity)

        self._thumb_down = QPushButton("👎")
        self._thumb_down.setFlat(True)
        self._thumb_down.setCursor(Qt.PointingHandCursor)
        self._thumb_down.setFixedSize(28, 28)
        self._thumb_down.setVisible(False)
        self._thumb_down.setToolTip("Poor response")
        self._thumb_down_opacity = QGraphicsOpacityEffect()
        self._thumb_down_opacity.setOpacity(0.3)
        self._thumb_down.setGraphicsEffect(self._thumb_down_opacity)

        bottom_row.addWidget(self._thumb_up)
        bottom_row.addWidget(self._thumb_down)
        layout.addLayout(bottom_row)

        self._thinking_visible = True
        self._toggle_btn.clicked.connect(self._toggle)
        self._thumb_up.clicked.connect(lambda: self._emit_feedback("positive"))
        self._thumb_down.clicked.connect(lambda: self._emit_feedback("negative"))

    def set_attachments(self, names: list[str]) -> None:
        if names:
            self._attach_label.setText("[attached: " + ", ".join(names) + "]")
            self._attach_label.setVisible(True)

    def set_score(self, score: int | None, feedback: str) -> None:
        if score is None:
            return
        colors = {5: "#22c55e", 4: "#22c55e", 3: "#f59e0b", 2: "#ef4444", 1: "#ef4444"}
        color = colors.get(score, "#666666")
        self._score_label.setText(f"● {score}/5")
        self._score_label.setStyleSheet(f"color: {color}; font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;")
        self._score_label.setToolTip(feedback or "")
        self._score_label.setVisible(True)

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

    def finalize(self, ts: str, answer: str, tool_calls: list, query_id: str = "") -> None:
        self._query_id = query_id
        tool_ind = f"  ⚙ {len(tool_calls)} tool call(s)" if tool_calls else ""
        self._header.setText(f"[{ts}] RESPONSE{tool_ind}")
        if self._toggle_btn.isVisible():
            self._thinking_visible = False
            self._thinking_box.setVisible(False)
            self._toggle_btn.setText("◈ thinking  ▶")
        self._answer_label.setText(answer or "(empty)")
        self._answer_label.setVisible(True)
        if query_id:
            self._thumb_up.setVisible(True)
            self._thumb_down.setVisible(True)

    def _emit_feedback(self, sentiment: str) -> None:
        if not self._query_id:
            return
        self._thumb_up_opacity.setOpacity(1.0 if sentiment == "positive" else 0.15)
        self._thumb_down_opacity.setOpacity(1.0 if sentiment == "negative" else 0.15)
        self._thumb_up.setEnabled(False)
        self._thumb_down.setEnabled(False)
        self.feedback.emit(self._query_id, sentiment)

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
    critique = Signal(dict)
    pairwise = Signal(dict)
    agent_transition = Signal(dict)
    tool_schema = Signal(str)        # emits tool name on tool.schema arrival
    tool_activity = Signal(object)   # passes MessageEnvelope through

    def log_envelope(self, envelope: MessageEnvelope) -> None:
        now = datetime.now()
        ts = now.strftime("%H:%M:%S") + f".{now.microsecond // 100000}"
        raw = envelope.payload or {}
        subject = envelope.subject

        if subject in _TOOL_ACTIVITY_SUBJECTS:
            self.tool_activity.emit(envelope)
            return

        if subject == TOOL_SCHEMA:
            name = (raw.get("schema") or {}).get("function", {}).get("name", "")
            if name:
                self.tool_schema.emit(name)
            return

        if subject == PAIRWISE_RESULT:
            self.pairwise.emit(raw)
            return

        if subject == AGENT_TRANSITION:
            self.agent_transition.emit(raw)
            return

        if subject == GENERATION_THINKING:
            if raw.get("respondent_id", "A") == "B":
                return
            self.thinking_chunk.emit({
                "ts": ts,
                "chunk": raw.get("chunk") or "",
                "query_id": raw.get("query_id") or "",
            })
            return

        if subject == RESPONSE_GENERATION:
            if raw.get("respondent_id", "A") == "B":
                return  # RespondentB answers are for pairwise comparison only
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
            if raw.get("respondent_id", "A") == "B":
                return
            text = f"[{ts}] DIALOG  (conversation recorded)"

        elif subject == CRITIQUE:
            self.critique.emit({
                "score": raw.get("score"),
                "feedback": raw.get("feedback", ""),
                "query_id": raw.get("query_id", ""),
                "query": raw.get("query", ""),
                "respondent_id": raw.get("respondent_id", "A"),
            })
            return

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
# Paste-aware query input — intercepts Cmd/Ctrl+V when clipboard has an image
# ---------------------------------------------------------------------------

class _PasteAwareLineEdit(QLineEdit):
    image_pasted = Signal(object)  # QImage

    def paste(self) -> None:
        img = QApplication.clipboard().image()
        if not img.isNull():
            self.image_pasted.emit(img)
            return
        super().paste()


# ---------------------------------------------------------------------------
# Drag-drop input container
# ---------------------------------------------------------------------------

class _InputContainer(QWidget):
    """Input area container that accepts file drops and forwards them."""
    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self, publisher: ZmqPublisher, model: str = "", memory_service=None, document_service=None, conversation_service=None) -> None:
        super().__init__()
        self._publisher = publisher
        self._session_id = str(uuid.uuid4())
        self._pending: dict[str, StreamingResponseWidget] = {}
        self._response_widgets: dict[str, StreamingResponseWidget] = {}
        self._pending_attachments: dict[str, list[str]] = {}
        title = "LoCAL2"
        if model:
            title += f"  [{model}]"
        self.setWindowTitle(title)
        # ── Tool windows — spawned reactively on tool.schema ──────────
        self._tool_windows: dict[str, ToolWindow] = {}  # keyed by tool name

        # ── Agent windows — spawned at startup ────────────────────────
        self._critic_window = CriticWindow(publisher=publisher)
        self._critic_window.show()
        self._memory_window = MemoryWindow(
            memory_service=memory_service,
            conversation_service=conversation_service,
            session_id_getter=lambda: self._session_id,
        )
        self._memory_window.show()
        self._documents_window = DocumentsWindow(document_service=document_service, publisher=publisher)
        self._documents_window.show()

        self._tile_windows()

        # ── Stack: page 0 = conversation only ─────────────────────────
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_conversation_page())

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

        self._attachment_bar = AttachmentBar()
        self._attachment_bar.setVisible(False)

        self._query_input = _PasteAwareLineEdit()
        self._query_input.setObjectName("queryInput")
        self._query_input.setPlaceholderText("Type a query and press Enter…")
        self._query_input.returnPressed.connect(self._send_query)
        self._query_input.image_pasted.connect(self._on_clipboard_image)

        clip_btn = QPushButton("+")
        clip_btn.setObjectName("clipBtn")
        clip_btn.setFixedSize(34, 34)
        clip_btn.setToolTip("Attach file…")
        clip_btn.setCursor(Qt.PointingHandCursor)
        clip_btn.clicked.connect(self._open_file_picker)

        send_btn = QPushButton("Send")
        send_btn.setObjectName("sendBtn")
        send_btn.setFixedWidth(72)
        send_btn.clicked.connect(self._send_query)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)
        input_row.addWidget(clip_btn)
        input_row.addWidget(self._query_input)
        input_row.addWidget(send_btn)

        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(12, 0, 12, 12)
        container_layout.setSpacing(0)
        container_layout.addWidget(self._attachment_bar)
        container_layout.addSpacing(8)
        container_layout.addLayout(input_row)

        input_container = _InputContainer()
        input_container.setObjectName("inputContainer")
        input_container.setLayout(container_layout)
        input_container.files_dropped.connect(self._attachment_bar.add_files)

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

        lib_btn = QPushButton("📚")
        lib_btn.setObjectName("sidebarBtn")
        lib_btn.setToolTip("Library")
        lib_btn.setFixedSize(36, 36)
        lib_btn.clicked.connect(lambda: self._documents_window.show() or self._documents_window.raise_())
        strip_layout.addWidget(lib_btn, alignment=Qt.AlignHCenter)

        strip_layout.addStretch()
        return icon_strip

    # ------------------------------------------------------------------
    # Bus monitor
    # ------------------------------------------------------------------

    def _start_bus_monitor(self) -> None:
        all_subjects = (
            list(OBSERVE) + _TOOL_ACTIVITY_SUBJECTS
            + [TOOL_SCHEMA, PAIRWISE_RESULT, AGENT_TRANSITION]
        )
        self._bus_logger = BusLogger()
        self._bus_logger.message.connect(self.append_log)
        self._bus_logger.thinking_chunk.connect(self._on_thinking_chunk)
        self._bus_logger.response.connect(self._on_response)
        self._bus_logger.critique.connect(self._on_critique)
        self._bus_logger.tool_activity.connect(self._on_tool_activity)
        self._bus_logger.tool_schema.connect(self._on_tool_schema)
        self._bus_logger.pairwise.connect(self._critic_window.append_pairwise)
        self._bus_logger.agent_transition.connect(self._on_agent_transition)

        self._monitor_worker = BusMonitorWorker(PROXY_BACKEND_ADDR, subscriptions=all_subjects)
        self._monitor_worker.envelope_received.connect(self._bus_logger.log_envelope)

        self._monitor_thread = QThread(self)
        self._monitor_worker.moveToThread(self._monitor_thread)
        self._monitor_thread.started.connect(self._monitor_worker.run)
        self._monitor_thread.start()

        # Request tool schemas after subscriber connects — tools re-announce
        # so the UI can spawn windows for any tool already running.
        QTimer.singleShot(600, self._request_tool_schemas)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_tool_activity(self, envelope: MessageEnvelope) -> None:
        tool_name = envelope.subject.split(".")[-1]
        win = self._tool_windows.get(tool_name)
        if win:
            win.append_activity(envelope)

    def _request_tool_schemas(self) -> None:
        self._publisher.publish(MessageEnvelope.create(
            message_type="schema_request",
            subject=TOOL_SCHEMA_REQUEST,
            sender_id="ui",
            payload={},
        ))

    def _on_tool_schema(self, tool_name: str) -> None:
        if tool_name not in self._tool_windows:
            win = ToolWindow(tool_name=tool_name, publisher=self._publisher)
            win.show()
            self._tool_windows[tool_name] = win
            self._tile_windows()

    def _tile_windows(self) -> None:
        """Tile all windows across the full screen.

        MainWindow occupies the left 2/7.  The remaining 5/7 is a 5-column × 2-row
        grid of tool and agent panels, grouped by function:
          col 0 — memory    (search_memory / MemoryWindow)
          col 1 — library   (search_library / DocumentsWindow)
          col 2 — web       (web_search / web_fetch)
          col 3 — research  (search_papers / CriticWindow)
          col 4 — utility   (get_datetime / get_location)

        setGeometry() positions the CONTENT area (title bar is outside/above it).
        To keep each row's outer frame within its H/2 budget:
          content_y  = row_start + tb_h
          content_h  = H/2 - tb_h
        so the frame runs from row_start to row_start + H/2.
        """
        screen = QApplication.primaryScreen().availableGeometry()
        W, H = screen.width(), screen.height()
        x0, y0 = screen.x(), screen.y()

        main_w  = W * 2 // 7
        panel_w = W // 7

        # Measure actual title bar height from a shown window
        fg   = self._critic_window.frameGeometry()
        g    = self._critic_window.geometry()
        tb_h = fg.height() - g.height()
        if tb_h <= 0:
            tb_h = 28  # macOS default fallback

        panel_h = H // 2 - tb_h

        def _place(win, col: int, row: int) -> None:
            x = x0 + main_w + col * panel_w
            y = y0 + row * (H // 2) + tb_h
            win.setGeometry(x, y, panel_w, panel_h)

        # Main window: left 2/7, full height
        self.setGeometry(x0, y0 + tb_h, main_w, H - tb_h)

        # Static agent panels
        _place(self._memory_window,    col=0, row=1)
        _place(self._documents_window, col=1, row=1)
        _place(self._critic_window,    col=3, row=1)

        # Tool panels: position each one that has been spawned so far
        for name, win in self._tool_windows.items():
            slot = _TOOL_PANEL_SLOTS.get(name)
            if slot:
                _place(win, col=slot[0], row=slot[1])

    def _on_agent_transition(self, data: dict) -> None:
        agent = data.get("agent", "")
        if agent == "critic":
            self._critic_window.append_transition(data)
        elif agent == "memory_agent":
            self._memory_window.append_transition(data)

    def _send_query(self) -> None:
        query = self._query_input.text().strip()
        if not query:
            return
        query_id = str(uuid.uuid4())
        attachments = self._attachment_bar.attachments()
        if attachments:
            self._pending_attachments[query_id] = [a["name"] for a in attachments]
        envelope = MessageEnvelope.create(
            message_type="query",
            subject=QUERY_RECEIVED,
            sender_id="ui",
            payload={
                "query": query,
                "session_id": self._session_id,
                "query_id": query_id,
                "attachments": attachments,
            },
            correlation_id=query_id,
            metadata={"session_id": self._session_id},
        )
        self._publisher.publish(envelope)
        self._query_input.clear()
        self._attachment_bar.clear()

    def _on_clipboard_image(self, image) -> None:
        buf = QByteArray()
        buffer = QBuffer(buf)
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        b64 = base64.b64encode(bytes(buf)).decode()
        self._attachment_bar.add_attachment({"type": "image", "name": "clipboard.png", "data": b64})

    def _open_file_picker(self) -> None:
        exts = (
            "All supported files (*.jpg *.jpeg *.png *.gif *.webp "
            "*.pdf *.txt *.md *.py *.js *.ts *.yaml *.json *.csv);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp);;"
            "Documents (*.pdf *.txt *.md);;"
            "Code (*.py *.js *.ts *.yaml *.json *.csv)"
        )
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files", "", exts)
        if paths:
            self._attachment_bar.add_files(paths)

    def _new_session(self) -> None:
        self._session_id = str(uuid.uuid4())
        self.append_log(f"── new conversation  session={self._session_id[:8]}… ──")

    def _clear_log(self) -> None:
        self._pending.clear()
        self._response_widgets.clear()
        while self._log_layout.count() > 1:
            item = self._log_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_thinking_chunk(self, data: dict) -> None:
        query_id = data["query_id"]
        if query_id not in self._pending:
            widget = StreamingResponseWidget(data["ts"])
            names = self._pending_attachments.pop(query_id, [])
            widget.set_attachments(names)
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
            names = self._pending_attachments.pop(query_id, [])
            widget.set_attachments(names)
            self._insert_log_widget(widget)
        widget.finalize(data["ts"], data["answer"], data["tool_calls"], query_id=query_id)
        widget.feedback.connect(self._on_user_feedback)
        if query_id:
            self._response_widgets[query_id] = widget
        self._scroll_to_bottom()

    def _on_critique(self, data: dict) -> None:
        query_id = data.get("query_id", "")
        widget = self._response_widgets.get(query_id)
        if widget:
            widget.set_score(data.get("score"), data.get("feedback", ""))
        self._critic_window.append_critique(data)

    def _on_user_feedback(self, query_id: str, sentiment: str) -> None:
        envelope = MessageEnvelope.create(
            message_type="user_feedback",
            subject=USER_FEEDBACK,
            sender_id="ui",
            payload={
                "query_id": query_id,
                "session_id": self._session_id,
                "sentiment": sentiment,
            },
            correlation_id=query_id,
            metadata={"session_id": self._session_id},
        )
        self._publisher.publish(envelope)

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
            QLabel#attachSummary {
                color: #555555;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 11px;
                padding: 0px;
            }
            QPushButton#clipBtn {
                background: #1a1a1a; color: #777; border: 1px solid #333;
                border-radius: 5px; font-size: 20px; padding: 0;
            }
            QPushButton#clipBtn:hover { background: #252525; color: #aaa; }
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
