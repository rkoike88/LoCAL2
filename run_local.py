"""LoCAL2 unified entry point.

Usage:
  python run_local.py                      # web UI (default), opens browser
  python run_local.py --headless           # web UI, no browser pop
  python run_local.py --desktop            # legacy PySide6 UI
  python run_local.py --model gemma4:27b   # model override
  python run_local.py --web-port 9000      # custom port (default 8000)

Starts in order: ZMQ proxy → tools → GeneratorAgent → MemoryAgent → Critic
  → Reward → web server (unless --desktop) → browser (unless --headless).
"""
from __future__ import annotations

import argparse
import resource
import signal
import sys
import time
import threading
from pathlib import Path

# Raise the open-file soft limit — each ZMQ participant uses 2 sockets and
# macOS defaults to 256, which is too low once we have 7+ participants.
_, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, _hard), _hard))

# Ensure src/ is on the path when run directly.
sys.path.insert(0, str(Path(__file__).parent / "src"))


def _start_proxy() -> None:
    from local.runtime.proxy import run_proxy
    run_proxy()


def _start_generator(model: str, temperature: float | None = None, conversation_service=None) -> None:
    from local.agents.generator_agent import GeneratorAgent
    agent = GeneratorAgent(model=model or None, temperature=temperature, conversation_service=conversation_service)
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


def _open_browser(url: str, panels: bool = False) -> None:
    """Open the browser, positioned to the left 1/3 of the screen when --panels is active."""
    if panels and sys.platform == "darwin":
        import subprocess
        # AppleScript opens Safari and sizes it to the left 1/3 of the screen.
        # Screen bounds come from Finder so no Python screen-size dependency.
        script = f"""
tell application "Finder"
    set {{x1, y1, x2, y2}} to bounds of window of desktop
    set third to (x2 - x1) / 3
end tell
tell application "Safari"
    activate
    open location "{url}"
    delay 0.8
    set bounds of front window to {{x1, y1, third, y2}}
end tell
"""
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=8)
            return
        except Exception:
            pass
    import webbrowser
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LoCAL2")
    parser.add_argument("--desktop", action="store_true", help="Legacy PySide6 UI instead of web")
    parser.add_argument("--panels", action="store_true", help="Open Qt observer panels alongside the web UI")
    parser.add_argument("--headless", action="store_true", help="Web server only, no browser")
    parser.add_argument("--web-port", type=int, default=8000, metavar="PORT")
    parser.add_argument("--model", default="", metavar="MODEL", help="Ollama model override")
    args = parser.parse_args()

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
        target=_start_generator, args=(args.model,), kwargs={"conversation_service": shared_conv},
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
        # Wake Python every 200ms so the SIGINT handler can fire while Qt owns the loop.
        _sigint_timer = QTimer()
        _sigint_timer.start(200)
        _sigint_timer.timeout.connect(lambda: None)
        window.show()
        window.raise_()
        window.activateWindow()
        sys.exit(app_qt.exec())
    else:
        # Web UI (default) — inject conversation service, start server, open browser.
        from local.api.gateway import configure
        configure(conversation_service=shared_conv)

        web_thread = threading.Thread(
            target=_start_web, args=(args.web_port,), daemon=True, name="web"
        )
        web_thread.start()
        url = f"http://localhost:{args.web_port}"
        print(f"[local] Web UI  {url}")

        if not args.headless:
            # Brief pause so uvicorn is ready before the browser hits it.
            time.sleep(1.0)
            _open_browser(url, panels=args.panels)

        if args.panels:
            # Qt observer panels run in the main thread (Qt event loop requirement).
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
            )
            app_qt.exec()
            _monitor.close()
        else:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[local] Shutting down.")


if __name__ == "__main__":
    main()
