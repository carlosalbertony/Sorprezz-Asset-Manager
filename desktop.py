from __future__ import annotations

import ctypes
import socket
import threading
import time
from pathlib import Path

import uvicorn
import webview

from app import APP_NAME, APP_VERSION, app, init_db, load_config


HOST = "127.0.0.1"
PREFERRED_PORT = 8765


def find_port(start: int = PREFERRED_PORT, attempts: int = 50) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No se encontró un puerto local disponible para iniciar la aplicación.")


def wait_until_ready(port: int, timeout: float = 12.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            if s.connect_ex((HOST, port)) == 0:
                return
        time.sleep(0.08)
    raise RuntimeError("El servicio local de Sorprezz no pudo iniciar a tiempo.")


class DesktopBridge:
    def __init__(self) -> None:
        self.window = None

    def is_ready(self):
        return self.window is not None

    def choose_folder(self):
        if self.window is None:
            return None
        try:
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if not result:
                return None
            if isinstance(result, (list, tuple)):
                return str(result[0]) if result else None
            return str(result)
        except Exception:
            return None

    def choose_files(self):
        """Selecciona uno o varios archivos locales para copiarlos a la Biblioteca."""
        if self.window is None:
            return []
        try:
            result = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
            if not result:
                return []
            if isinstance(result, (list, tuple)):
                return [str(x) for x in result]
            return [str(result)]
        except Exception:
            return []


def set_windows_app_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Sorprezz.AssetManager.1")
    except Exception:
        pass


def main() -> None:
    set_windows_app_id()
    init_db()
    load_config()

    port = find_port()
    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True, name="SorprezzLocalServer")
    thread.start()
    wait_until_ready(port)

    bridge = DesktopBridge()
    window = webview.create_window(
        f"{APP_NAME} · v{APP_VERSION}",
        f"http://{HOST}:{port}",
        js_api=bridge,
        width=1440,
        height=900,
        min_size=(1050, 700),
        resizable=True,
        text_select=True,
    )
    bridge.window = window

    try:
        webview.start(debug=False)
    finally:
        server.should_exit = True
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
