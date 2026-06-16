"""LoCAL2 runtime launcher — importable entry point used by the CLI.

All startup logic lives here so that both ``python run_local.py`` (dev) and
``local2`` (installed CLI) share the same code path.
"""
from __future__ import annotations

import argparse
import os
import resource
import signal
import sys
import time
import threading

# Raise the open-file soft limit — each ZMQ participant uses 2 sockets and
# macOS defaults to 256, which is too low once we have 7+ participants.
_, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, _hard), _hard))


def _start_proxy() -> None:
    from local.runtime.proxy import run_proxy
    run_proxy()


def _late_schema_refresh(delay: float = 2.0) -> None:
    """Re-broadcast TOOL_SCHEMA_REQUEST after a delay to catch any late-starting tools."""
    from local.protocol.messages import ToolSchemaRequest
    from local.transport.bus_config import PROXY_FRONTEND_ADDR
    from local.transport.zmq_pubsub import ZmqPublisher
    pub = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
    time.sleep(delay)  # wait after connecting — ZMQ drops messages sent before the connection settles
    pub.publish(ToolSchemaRequest(), sender_id="run-refresh")


def _start_generator(model: str, temperature: float | None = None, conversation_service=None, tool_dispatcher=None) -> None:
    from local.agents.generator_agent import GeneratorAgent
    agent = GeneratorAgent(model=model or None, temperature=temperature, conversation_service=conversation_service, tool_dispatcher=tool_dispatcher)
    agent.run()


def _start_web_search() -> None:
    from local.tools.web_search_tool import WebSearchTool
    WebSearchTool().run()


def _start_web_fetch() -> None:
    from local.tools.web_fetch_tool import WebFetchTool
    WebFetchTool().run()


def _start_datetime() -> None:
    from local.tools.datetime_tool import DateTimeTool
    DateTimeTool().run()


def _start_location() -> None:
    from local.tools.location_tool import LocationTool
    LocationTool().run()


def _start_semantic_scholar() -> None:
    from local.tools.semantic_scholar_tool import SemanticScholarTool
    SemanticScholarTool().run()


def _start_search_library(document_service) -> None:
    from local.tools.search_library_tool import SearchLibraryTool
    SearchLibraryTool(document_service=document_service).run()


def _start_search_memory(memory_service) -> None:
    from local.tools.search_memory_tool import SearchMemoryTool
    SearchMemoryTool(memory_service=memory_service).run()


def _start_memory_agent(memory_service) -> None:
    from local.agents.memory_agent import MemoryAgent
    MemoryAgent(memory_service=memory_service).run()


def _start_critic() -> None:
    from local.agents.critic_agent import CriticAgent
    CriticAgent().run()


def _start_reward(memory_service) -> None:
    from local.services.reward_service import RewardService
    RewardService(memory_service=memory_service).run()


def _start_web(port: int) -> None:
    import uvicorn
    from local.api.gateway import app
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    """Poll localhost:port until it accepts connections or timeout expires."""
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LoCAL2")
    parser.add_argument("--desktop", action="store_true", help="Legacy PySide6 UI instead of web")
    parser.add_argument("--panels", action="store_true", help="Open Qt observer panels alongside the web UI")
    parser.add_argument("--headless", action="store_true", help="Web server only, no browser")
    parser.add_argument("--web-only", action="store_true", help="Web server only — no local agents; use with --ipaddress")
    parser.add_argument("--ipaddress", default="", metavar="IP", help="Remote bus host IP (sets LOCAL2_PROXY_HOST)")
    parser.add_argument("--web-port", type=int, default=8000, metavar="PORT")
    parser.add_argument("--model", default="", metavar="MODEL", help="Ollama model override")
    args = parser.parse_args()

    if args.ipaddress:
        os.environ["LOCAL2_PROXY_HOST"] = args.ipaddress

    if args.web_only:
        # Remote-bus mode: skip proxy and all agents; web server connects to remote bus.
        from local.services.conversation_service import ConversationService
        shared_conv = ConversationService()
        from local.api.gateway import configure
        configure(conversation_service=shared_conv, memory_service=None)
        web_thread = threading.Thread(
            target=_start_web, args=(args.web_port,), daemon=True, name="web"
        )
        web_thread.start()
        url = f"http://localhost:{args.web_port}"
        print(f"[local] Web UI (remote bus: {args.ipaddress or os.environ.get('LOCAL2_PROXY_HOST', '?')})  {url}")
        if not args.headless:
            _wait_for_port(args.web_port)
            import webbrowser
            webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[local] Shutting down.")
        return

    # -- Proxy ---------------------------------------------------------------
    proxy_thread = threading.Thread(target=_start_proxy, daemon=True, name="proxy")
    proxy_thread.start()
    time.sleep(0.3)   # let XSUB/XPUB sockets bind before agents connect

    # -- Tools (BEFORE generator) --------------------------------------------
    # Both ChromaDB services created in main thread — PersistentClient is not
    # thread-safe under concurrent construction on the same path.
    from local.services.memory_service import MemoryService
    from local.services.document_service import DocumentService
    from local.services.conversation_service import ConversationService
    shared_memory = MemoryService()
    shared_documents = DocumentService()
    shared_conv = ConversationService()

    from local.services.model_service import ModelService
    shared_model_service = ModelService(conversation_service=shared_conv)
    threading.Thread(target=shared_model_service.run, daemon=True, name="model_service").start()

    threading.Thread(target=_start_web_search, daemon=True, name="web_search").start()
    threading.Thread(target=_start_web_fetch, daemon=True, name="web_fetch").start()
    threading.Thread(target=_start_search_memory, args=(shared_memory,), daemon=True, name="search_memory").start()
    threading.Thread(target=_start_datetime, daemon=True, name="datetime").start()
    threading.Thread(target=_start_location, daemon=True, name="location").start()
    threading.Thread(target=_start_semantic_scholar, daemon=True, name="semantic_scholar").start()
    threading.Thread(target=_start_search_library, args=(shared_documents,), daemon=True, name="search_library").start()
    time.sleep(0.5)   # let all tools connect and subscribe to schema.request

    # -- Generator (AFTER tools) ---------------------------------------------
    # Tools must be subscribed before generator publishes schema.request at startup.
    gen_thread = threading.Thread(
        target=_start_generator, args=(args.model,),
        kwargs={"conversation_service": shared_conv},
        daemon=True, name="generator_a"
    )
    gen_thread.start()

    time.sleep(0.2)   # let generator connect before memory_agent starts

    # -- Memory Agent --------------------------------------------------------
    threading.Thread(target=_start_memory_agent, args=(shared_memory,), daemon=True, name="memory_agent").start()

    # -- Critic --------------------------------------------------------------
    threading.Thread(target=_start_critic, daemon=True, name="critic").start()

    # -- Reward --------------------------------------------------------------
    threading.Thread(target=_start_reward, args=(shared_memory,), daemon=True, name="reward").start()

    # -- UI ------------------------------------------------------------------
    if args.desktop:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from local.transport.bus_config import PROXY_FRONTEND_ADDR
        from local.transport.zmq_pubsub import ZmqPublisher
        from local.ui.main_window import MainWindow

        app_qt = QApplication(sys.argv)
        publisher = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
        window = MainWindow(publisher=publisher, model=args.model, memory_service=shared_memory, document_service=shared_documents, conversation_service=shared_conv)
        signal.signal(signal.SIGINT, lambda *_: app_qt.quit())
        _sigint_timer = QTimer()
        _sigint_timer.start(200)
        _sigint_timer.timeout.connect(lambda: None)
        window.show()
        window.raise_()
        window.activateWindow()
        sys.exit(app_qt.exec())
    else:
        from local.api.gateway import configure
        configure(conversation_service=shared_conv, memory_service=shared_memory)

        web_thread = threading.Thread(
            target=_start_web, args=(args.web_port,), daemon=True, name="web"
        )
        web_thread.start()
        threading.Thread(target=_late_schema_refresh, daemon=True, name="schema_refresh").start()
        url = f"http://localhost:{args.web_port}"
        print(f"[local] Web UI  {url}")

        if args.panels:
            import signal as _signal
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            from local.ui.monitor_app import MonitorApp

            app_qt = QApplication([])
            _signal.signal(_signal.SIGINT, lambda *_: app_qt.quit())
            _sigint_timer = QTimer()
            _sigint_timer.start(200)
            _sigint_timer.timeout.connect(lambda: None)
            _monitor = MonitorApp(
                memory_service=shared_memory,
                conversation_service=shared_conv,
                document_service=shared_documents,
            )
            if not args.headless:
                _wait_for_port(args.web_port)
                QTimer.singleShot(0, lambda: _monitor.open_browser(url))
            app_qt.exec()
            _monitor.close()
            sys.exit(0)

        if not args.panels and not args.headless:
            import webbrowser
            _wait_for_port(args.web_port)
            webbrowser.open(url)

        # Keep main thread alive so all daemon threads (uvicorn, agents, tools) stay running.
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[local] Shutting down.")


if __name__ == "__main__":
    main()
