"""MonitorApp — read-only Qt observer panels for the hybrid web+desktop mode.

Layout (5 equal columns, browser occupies the left 2):

  [  browser (2/5)  ] [ gen+critic (1/5) ] [ web tools (1/5) ] [ lib tools (1/5) ]

  Col 2: GeneratorWindow (top 60%) / CriticWindow (bottom 40%)
  Col 3: web_search / web_fetch / search_memory — 3 equal slots
  Col 4: search_library / search_papers / get_location / get_datetime — 4 equal slots

MemoryWindow is hidden by default; opened on demand via the ⊞ button on
the search_memory ToolWindow.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import ToolSchemaRequest
from local.protocol.subjects import (
    AGENT_TRANSITION,
    CRITIQUE,
    GENERATOR_STATUS,
    TOOL_SCHEMA,
)
from local.transport.bus_config import PROXY_BACKEND_ADDR, PROXY_FRONTEND_ADDR
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber
from local.ui.critic_window import CriticWindow
from local.ui.generator_window import GeneratorWindow
from local.ui.memory_window import MemoryWindow
from local.ui.tool_window import ToolWindow

_TOOL_ACTIVITY_PREFIX = "tool.activity."

_OBSERVE = [
    TOOL_SCHEMA,
    GENERATOR_STATUS,
    AGENT_TRANSITION,
    CRITIQUE,
    _TOOL_ACTIVITY_PREFIX,
]


class _BusWorker(QObject):
    envelope_received: Signal = Signal(object)

    def __init__(self, subscriptions: list[str]) -> None:
        super().__init__()
        self._subscriptions = subscriptions
        self._running = True

    def run(self) -> None:
        sub = ZmqSubscriber(PROXY_BACKEND_ADDR, subscriptions=self._subscriptions, bind=False)
        while self._running:
            msg = sub.receive_with_timeout(200)
            if msg is not None:
                self.envelope_received.emit(msg)
        sub.close()

    def stop(self) -> None:
        self._running = False


class MonitorApp(QObject):
    """Manages the observer window set for hybrid web+desktop mode."""

    # Fixed slot order for tool columns.
    _COL3 = ["web_search", "web_fetch", "search_memory"]
    _COL4 = ["search_library", "search_papers", "get_location", "get_datetime"]

    def __init__(
        self,
        memory_service,
        conversation_service,
        document_service=None,
    ) -> None:
        super().__init__()

        self._pub = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
        self._document_service = document_service
        self._tool_windows: dict[str, ToolWindow] = {}

        # Agent windows — always created, generator+critic auto-shown.
        self._generator_window = GeneratorWindow()
        self._generator_window.show()

        self._critic_window = CriticWindow(publisher=None)
        self._critic_window.show()

        # MemoryWindow is hidden by default; raised on demand via ⊞ on search_memory.
        self._memory_window = MemoryWindow(
            memory_service=memory_service,
            conversation_service=conversation_service,
            session_id_getter=lambda: None,
        )

        # DocumentsWindow is created lazily on first ⊞ click on search_library.
        self._docs_window = None

        self._tile_windows()

        self._worker = _BusWorker(_OBSERVE)
        self._worker.envelope_received.connect(self._on_envelope)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

        QTimer.singleShot(600, self._request_schemas)

    def _request_schemas(self) -> None:
        self._pub.publish(ToolSchemaRequest(), sender_id="monitor")

    def _on_envelope(self, envelope: MessageEnvelope) -> None:
        subject = envelope.subject
        payload = envelope.payload

        if subject == GENERATOR_STATUS:
            self._generator_window.update_status(payload)

        elif subject == AGENT_TRANSITION:
            self._generator_window.append_transition(payload)
            self._critic_window.append_transition(payload)
            if self._memory_window.isVisible():
                self._memory_window.append_transition(payload)

        elif subject == CRITIQUE:
            self._critic_window.append_critique({
                "score": payload.get("score"),
                "feedback": payload.get("feedback", ""),
                "query_id": payload.get("query_id", ""),
                "query": payload.get("query", ""),
            })

        elif subject == TOOL_SCHEMA:
            self._on_tool_schema(envelope)

        elif subject.startswith(_TOOL_ACTIVITY_PREFIX):
            tool_name = subject[len(_TOOL_ACTIVITY_PREFIX):]
            win = self._tool_windows.get(tool_name)
            if win:
                win.append_activity(envelope)

    def _open_docs_window(self) -> None:
        if self._docs_window is None:
            from local.ui.documents_window import DocumentsWindow
            self._docs_window = DocumentsWindow(
                document_service=self._document_service,
                publisher=self._pub,
            )
        self._docs_window.show()
        self._docs_window.raise_()

    def _raise_memory_window(self) -> None:
        self._memory_window.show()
        self._memory_window.raise_()

    def _on_tool_schema(self, envelope: MessageEnvelope) -> None:
        name = (
            (envelope.payload.get("schema") or {})
            .get("function", {})
            .get("name", "")
        )
        if name and name not in self._tool_windows:
            on_lib_click = None
            lib_tooltip = "Open"
            pub = None
            if name == "search_library" and self._document_service:
                on_lib_click = self._open_docs_window
                lib_tooltip = "Manage library"
                pub = self._pub
            elif name == "search_memory":
                on_lib_click = self._raise_memory_window
                lib_tooltip = "Browse memories"
            win = ToolWindow(tool_name=name, publisher=pub, on_lib_click=on_lib_click, lib_tooltip=lib_tooltip)
            win.show()
            self._tool_windows[name] = win
            self._tile_windows()

    def _tile_windows(self) -> None:
        """Position all windows in the 2/5 + 1/5 + 1/5 + 1/5 layout.

        Browser occupies the left 2/5. Qt panels occupy the right 3/5:
          Col 2 (1/5): GeneratorWindow top 60% / CriticWindow bottom 40%
          Col 3 (1/5): web_search / web_fetch / search_memory — 3 equal slots
          Col 4 (1/5): search_library / search_papers / get_location / get_datetime — 4 slots
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        sg = screen.availableGeometry()
        W, H = sg.width(), sg.height()
        x0, y0 = sg.x(), sg.y()

        fg = self._critic_window.frameGeometry()
        g  = self._critic_window.geometry()
        tb = fg.height() - g.height()
        if tb <= 0:
            tb = 28

        unit = W // 5          # 1/5 of screen width
        col2_x = x0 + 2 * unit

        gen_h  = int(H * 0.6)
        crit_h = H - gen_h

        # Col 2: generator top, critic bottom.
        self._generator_window.setGeometry(col2_x, y0 + tb, unit, gen_h - tb)
        self._critic_window.setGeometry(col2_x, y0 + gen_h + tb, unit, crit_h - tb)

        # Col 3: web_search / web_fetch / search_memory — 3 equal slots.
        col3_x = x0 + 3 * unit
        slot_h3 = H // len(self._COL3)
        for i, name in enumerate(self._COL3):
            win = self._tool_windows.get(name)
            if win:
                win.setGeometry(col3_x, y0 + i * slot_h3 + tb, unit, slot_h3 - tb)

        # Col 4: search_library / search_papers / get_location / get_datetime — 4 slots.
        col4_x = x0 + 4 * unit
        slot_h4 = H // len(self._COL4)
        for i, name in enumerate(self._COL4):
            win = self._tool_windows.get(name)
            if win:
                win.setGeometry(col4_x, y0 + i * slot_h4 + tb, unit, slot_h4 - tb)

    def open_browser(self, url: str) -> None:
        """Open the browser positioned to the left 2/5 of the screen."""
        import subprocess
        import sys
        import webbrowser

        screen = QApplication.primaryScreen()
        if screen is not None and sys.platform == "darwin":
            sg = screen.availableGeometry()
            two_fifths = sg.x() + sg.width() * 2 // 5
            bottom = sg.y() + sg.height()
            script = (
                f'tell application "Safari"\n'
                f'    activate\n'
                f'    open location "{url}"\n'
                f'    delay 0.8\n'
                f'    set bounds of front window to {{0, 0, {two_fifths}, {bottom}}}\n'
                f'end tell\n'
            )
            try:
                result = subprocess.run(
                    ["osascript", "-e", script], check=False, timeout=10
                )
                if result.returncode == 0:
                    return
            except Exception:
                pass

        webbrowser.open(url)

    def close(self) -> None:
        self._worker.stop()
        self._thread.quit()
        self._thread.wait()
        self._pub.close()


def run_monitor(memory_service, conversation_service) -> None:
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    monitor = MonitorApp(
        memory_service=memory_service,
        conversation_service=conversation_service,
    )
    app.exec()
    monitor.close()
