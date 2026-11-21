"""Starting the web page: `python -m scheduler web`. Listens on 127.0.0.1 only, so no other computer can reach it."""
import socket
import sys
import threading
import webbrowser
from typing import TextIO

import uvicorn

from scheduler.web.app import create_app

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PORT_TRIES = 20


class PortInUse(Exception):
    def __init__(self, port: int):
        super().__init__(f"port {port} is in use")
        self.port = port


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def free_port(start: int) -> int:
    """The first free port from `start` on: a second instance takes the next one."""
    for port in range(start, start + PORT_TRIES):
        if port_free(port):
            return port
    raise PortInUse(start)


def serve(port: int | None = None, open_browser: bool = True, out: TextIO = sys.stdout) -> None:
    if port is not None:
        if not port_free(port):
            raise PortInUse(port)
        chosen = port
    else:
        chosen = free_port(DEFAULT_PORT)
        if chosen != DEFAULT_PORT:
            print(f"Port {DEFAULT_PORT} is in use (another instance?); using {chosen}.", file=out, flush=True)
    url = f"http://{HOST}:{chosen}/"
    print(f"Clashless is running at {url}  (Ctrl+C to stop)", file=out, flush=True)
    if open_browser:
        timer = threading.Timer(1.0, webbrowser.open, args=(url,))  # once the server is listening
        timer.daemon = True
        timer.start()
    uvicorn.run(create_app(), host=HOST, port=chosen, log_level="warning")
