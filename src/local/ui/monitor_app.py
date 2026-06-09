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

        self._worker = _BusWorker(_OBSERVE)
        self._worker.envelope_received.connect(self._on_envelope)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

        # Ask tools to re-announce schemas so ToolWindows can be spawned.
        QTimer.singleShot(600, self._request_schemas)

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
