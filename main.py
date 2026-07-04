"""
EATokeer Backend — entry point. Starts the FastAPI server + CustomTkinter GUI.

Mirrors UbiTokeer/main.py. The server mints EA GameTokens by swapping the
requested account's saved EA session into the EA App (TcNo-style) and scraping
the bearer token from EADesktop.exe memory, rotating accounts on daily-cap.
"""

import json
import logging
import sys
import threading
from pathlib import Path

import uvicorn

from core.account_manager import configure as configure_account_manager
from core.job_queue import JobQueue
from gui.app import EATokeerApp
from server import api as server_api

CONFIG_PATH = Path(__file__).parent / "config.json"
logger = logging.getLogger("eatokeer")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception as e:
            logger.error(f"Failed to load config.json: {e}")
    return {"port": 8092, "daily_limit": 5, "swap_timeout": 60, "api_key": "",
            "ea_desktop_exe": ""}


def setup_logging(gui_handler: logging.Handler) -> None:
    root = logging.getLogger("eatokeer")
    root.setLevel(logging.DEBUG)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S"))
    root.addHandler(stdout_handler)

    file_handler = logging.FileHandler(str(Path(__file__).parent / "eatokeer.log"), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    root.addHandler(file_handler)

    gui_handler.setLevel(logging.DEBUG)
    root.addHandler(gui_handler)

    for noisy in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)
    logging.getLogger("uvicorn.error").propagate = False


class ServerManager:
    def __init__(self, config: dict):
        self._config = config
        self._server_thread: threading.Thread | None = None
        self._uvicorn_server: uvicorn.Server | None = None

    def start(self) -> None:
        if self._server_thread and self._server_thread.is_alive():
            logger.warning("Server is already running")
            return
        port = self._config.get("port", 8092)
        uv_config = uvicorn.Config(app=server_api.app, host="0.0.0.0", port=port, log_level="warning")
        self._uvicorn_server = uvicorn.Server(uv_config)
        self._server_thread = threading.Thread(target=self._uvicorn_server.run, daemon=True)
        self._server_thread.start()
        logger.info(f"API server started on 0.0.0.0:{port}")

    def stop(self) -> None:
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
            logger.info("API server stopped")

    def update_config(self, config: dict) -> None:
        self._config = config


def main() -> None:
    config = load_config()
    configure_account_manager(config.get("ea_desktop_exe", ""))

    app = EATokeerApp(
        config=config,
        on_save_config=lambda cfg: (job_queue.update_config(cfg), server_mgr.update_config(cfg)),
        on_toggle_server=lambda running: (server_mgr.start() if running else server_mgr.stop()),
    )

    setup_logging(app.get_log_handler())
    logger.info("EATokeer starting up...")

    job_queue = JobQueue(
        config=config,
        on_update=lambda: app.after(0, lambda: app.update_queue_state(job_queue.get_state())),
    )
    app.set_quota_tracker(job_queue._quota)

    server_mgr = ServerManager(config)
    server_api.set_queue(job_queue)
    server_api.set_api_key(config.get("api_key", ""))

    server_mgr.start()
    app.set_server_running(True)
    logger.info("EATokeer ready")

    app.mainloop()

    server_mgr.stop()
    job_queue.shutdown()
    logger.info("EATokeer shut down")


if __name__ == "__main__":
    main()
