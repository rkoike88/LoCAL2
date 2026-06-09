"""MonitorApp — read-only Qt observer panels for the hybrid web+desktop mode.

Spawns the four pure-observer windows (GeneratorWindow, CriticWindow,
MemoryWindow, and per-tool ToolWindows) alongside the web UI. No publisher
is passed to the windows — settings-save is disabled by design so this side
of the app is strictly view-only.

A single ZmqPublisher is created only to broadcast TOOL_SCHEMA_REQUEST at
startup so tools re-announce their schemas and ToolWindows can be spawned.
After that one write the publisher is idle.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    AGENT_TRANSITION,
    CRITIQUE,
    GENERATOR_STATUS,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
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
    # Subscribe to all tool activity via ZMQ prefix match.
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
    """Manages the observer window set for hybrid web+desktop mode.

    Creates GeneratorWindow, CriticWindow, MemoryWindow, and ToolWindows.
    All windows receive ``publisher=None`` — they are read-only views.
    """

    def __init__(
        self,
        memory_service,
        conversation_service,
    ) -> None:
        super().__init__()

        # Publisher used only for the one-time TOOL_SCHEMA_REQUEST broadcast.
        self._pub = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
        self._tool_windows: dict[str, ToolWindow] = {}

        self._generator_window = GeneratorWindow()
        self._generator_window.show()

        # publisher=None → settings-save button disabled; view-only.
        self._critic_window = CriticWindow(publisher=None)
        self._critic_window.show()

        # session_id_getter=None → MemoryWindow shows all engrams unfiltered.
        self._memory_window = MemoryWindow(
            memory_service=memory_service,
            conversation_service=conversation_service,
            session_id_getter=lambda: None,
        )
        self._memory_window.show()

        # Initial tile — tool windows arrive later and re-tile individually.
        self._tile_windows()

        self._worker = _BusWorker(_OBSERVE)
        self._worker.envelope_received.connect(self._on_envelope)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

        # Ask tools to re-announce schemas so ToolWindows can be spawned.
        QTimer.singleShot(600, self._request_schemas)
        self._browser_url: str | None = None

    def _request_schemas(self) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="schema_request",
            subject=TOOL_SCHEMA_REQUEST,
            sender_id="monitor",
            payload={},
        ))

    def _on_envelope(self, envelope: MessageEnvelope) -> None:
        subject = envelope.subject
        payload = envelope.payload

        if subject == GENERATOR_STATUS:
            self._generator_window.update_status(payload)

        elif subject == AGENT_TRANSITION:
            self._generator_window.append_transition(payload)
            self._critic_window.append_transition(payload)
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

    def _on_tool_schema(self, envelope: MessageEnvelope) -> None:
        name = (
            (envelope.payload.get("schema") or {})
            .get("function", {})
            .get("name", "")
        )
        if name and name not in self._tool_windows:
            win = ToolWindow(tool_name=name, publisher=None)
            win.show()
            self._tool_windows[name] = win
            self._tile_windows()

    # Fixed slot order for each tool column.
    _COL5 = ["web_search", "web_fetch", "search_memory"]
    _COL6 = ["search_library", "search_papers", "get_location", "get_datetime"]

    def _tile_windows(self) -> None:
        """Position all windows according to the 1/3 + 1/3 + 1/6 + 1/6 layout.

        The left 1/3 is reserved for the browser — Qt panels occupy the right
        2/3. Title bar height is measured from a live window and subtracted
        from each slot so windows don't overlap. Tool windows occupy fixed
        slots so positions are stable as they arrive one by one.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        sg = screen.availableGeometry()
        W, H = sg.width(), sg.height()
        x0, y0 = sg.x(), sg.y()

        # Measure actual title bar height from a shown window.
        fg = self._critic_window.frameGeometry()
        g  = self._critic_window.geometry()
        tb = fg.height() - g.height()
        if tb <= 0:
            tb = 28  # macOS default fallback

        unit = W // 6          # 1/6 of screen width
        mid_x = x0 + 2 * unit  # middle 1/3 starts after the browser 1/3
        top_h = int(H * 0.58)  # row boundary for critic+generator / memory split
        bot_h = H - top_h

        # Critic — top-left of middle 1/3.
        self._critic_window.setGeometry(mid_x, y0 + tb, unit, top_h - tb)

        # Generator — top-right of middle 1/3.
        self._generator_window.setGeometry(mid_x + unit, y0 + tb, unit, top_h - tb)

        # Memory — full width of middle 1/3, below critic+generator.
        self._memory_window.setGeometry(mid_x, y0 + top_h + tb, 2 * unit, bot_h - tb)

        # Col 5: web_search / web_fetch / search_memory — 3 equal slots.
        col5_x = x0 + 4 * unit
        slot_h5 = H // len(self._COL5)
        for i, name in enumerate(self._COL5):
            win = self._tool_windows.get(name)
            if win:
                win.setGeometry(col5_x, y0 + i * slot_h5 + tb, unit, slot_h5 - tb)

        # Col 6: search_library / search_papers / get_location / get_datetime — 4 slots.
        col6_x = x0 + 5 * unit
        slot_h6 = H // len(self._COL6)
        for i, name in enumerate(self._COL6):
            win = self._tool_windows.get(name)
            if win:
                win.setGeometry(col6_x, y0 + i * slot_h6 + tb, unit, slot_h6 - tb)

    def open_browser(self, url: str) -> None:
        """Open the browser positioned to the left 1/3 of the screen.

        Called via QTimer so screen geometry is available and Qt panels are
        already tiled. Uses literal pixel values in the AppleScript so there
        are no cross-tell-block variable scoping issues.
        """
        import subprocess
        import sys
        import webbrowser

        screen = QApplication.primaryScreen()
        if screen is not None and sys.platform == "darwin":
            sg = screen.availableGeometry()
            third = sg.x() + sg.width() // 3
            bottom = sg.y() + sg.height()
            script = (
                f'tell application "Safari"\n'
                f'    activate\n'
                f'    open location "{url}"\n'
                f'    delay 0.8\n'
                f'    set bounds of front window to {{0, 0, {third}, {bottom}}}\n'
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
    """Entry point called from run_local.py in a thread.

    Creates a QApplication, instantiates MonitorApp, and runs the Qt event
    loop. Blocks until the last window is closed.
    """
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    monitor = MonitorApp(
        memory_service=memory_service,
        conversation_service=conversation_service,
    )
    app.exec()
    monitor.close()
