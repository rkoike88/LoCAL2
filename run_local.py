"""LoCAL2 unified entry point.

Usage:
  python run_local.py                      # UI only
  python run_local.py --api                # UI + REST API
  python run_local.py --headless --api     # API only (no UI)
  python run_local.py --model gemma4:27b   # model override

Starts in order: ZMQ proxy → GeneratorAgent → API (if --api) → UI (if not --headless).
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


def _start_generator(model: str) -> None:
    from local.agents.generator_agent import GeneratorAgent
    agent = GeneratorAgent(model=model or None)
    agent.run()


def _start_web_search() -> None:
    from local.tools.web_search_tool import WebSearchTool
    WebSearchTool().run()


def _start_web_fetch() -> None:
    from local.tools.web_fetch_tool import WebFetchTool
    WebFetchTool().run()


def _start_search_memory(memory_service) -> None:
    from local.tools.search_memory_tool import SearchMemoryTool
    SearchMemoryTool(memory_service=memory_service).run()


def _start_memory_agent(memory_service) -> None:
    from local.agents.memory_agent import MemoryAgent
    MemoryAgent(memory_service=memory_service).run()


def _start_api(port: int) -> None:
    import uvicorn
    from local.api.gateway import app
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LoCAL2")
    parser.add_argument("--api", action="store_true", help="Start REST API server")
    parser.add_argument("--api-port", type=int, default=8000, metavar="PORT")
    parser.add_argument("--headless", action="store_true", help="No UI (use with --api)")
    parser.add_argument("--model", default="", metavar="MODEL", help="Ollama model override")
    args = parser.parse_args()

    # -- Proxy ---------------------------------------------------------------
    proxy_thread = threading.Thread(target=_start_proxy, daemon=True, name="proxy")
    proxy_thread.start()
    time.sleep(0.3)   # let XSUB/XPUB sockets bind before agents connect

    # -- Tools (BEFORE generator) --------------------------------------------
    # MemoryService created once in main thread — ChromaDB init is not thread-safe
    # under concurrent construction on the same path.
    from local.services.memory_service import MemoryService
    shared_memory = MemoryService()

    threading.Thread(target=_start_web_search, daemon=True, name="web_search").start()
    threading.Thread(target=_start_web_fetch, daemon=True, name="web_fetch").start()
    threading.Thread(target=_start_search_memory, args=(shared_memory,), daemon=True, name="search_memory").start()
    time.sleep(0.5)   # let all tools connect and subscribe to schema.request

    # -- Generator (AFTER tools) ---------------------------------------------
    # Tools must be subscribed before generator publishes schema.request at startup.
    gen_thread = threading.Thread(
        target=_start_generator, args=(args.model,), daemon=True, name="generator"
    )
    gen_thread.start()
    time.sleep(0.2)   # let generator connect before memory_agent starts

    # -- Memory Agent --------------------------------------------------------
    threading.Thread(target=_start_memory_agent, args=(shared_memory,), daemon=True, name="memory_agent").start()

    # -- API -----------------------------------------------------------------
    if args.api:
        api_thread = threading.Thread(
            target=_start_api, args=(args.api_port,), daemon=True, name="api"
        )
        api_thread.start()
        print(f"[local] API  http://0.0.0.0:{args.api_port}")

    # -- UI ------------------------------------------------------------------
    if not args.headless:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from local.transport.bus_config import PROXY_FRONTEND_ADDR
        from local.transport.zmq_pubsub import ZmqPublisher
        from local.ui.main_window import MainWindow

        app_qt = QApplication(sys.argv)
        publisher = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
        window = MainWindow(publisher=publisher, model=args.model)
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
        print("[local] Running headless. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[local] Shutting down.")


if __name__ == "__main__":
    main()
