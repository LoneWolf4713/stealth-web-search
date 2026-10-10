"""
Thin client for talking to daemon.py over a Unix domain socket.
Spawns the daemon on first use if it isn't already running.
"""
import os
import sys
import json
import time
import socket
import subprocess
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.expanduser("~/.cache/web_search_tool")
os.makedirs(RUNTIME_DIR, exist_ok=True)
SOCKET_PATH = os.path.join(RUNTIME_DIR, "daemon.sock")
DAEMON_SCRIPT = os.path.join(SCRIPT_DIR, "daemon.py")
DAEMON_OUT_LOG = os.path.join(RUNTIME_DIR, "daemon.out.log")

# Generous: covers cold model loading + a multi-page fetch/browser escalation
# + deep SLM reasoning on the daemon's first real request.
REQUEST_TIMEOUT_SECONDS = 180

_spawn_lock = threading.Lock()

def _spawn_daemon():
    with _spawn_lock:
        try:
            with open(DAEMON_OUT_LOG, "a") as log_f:
                subprocess.Popen(
                    [sys.executable, DAEMON_SCRIPT],
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    cwd=SCRIPT_DIR,
                )
        except Exception:
            pass

def _try_once(payload: bytes):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(REQUEST_TIMEOUT_SECONDS)
    try:
        sock.connect(SOCKET_PATH)
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)
    finally:
        sock.close()

def daemon_request(cmd: str, **kwargs) -> str:
    """Send one command to the shared daemon, spawning it if needed. Returns
    the final string result the MCP tool should return to its caller."""
    payload = (json.dumps({"cmd": cmd, "kwargs": kwargs}) + "\n").encode("utf-8")

    for attempt in range(2):
        try:
            raw = _try_once(payload)
            if not raw:
                return "The web-search daemon closed the connection without a response."
            resp = json.loads(raw.decode("utf-8"))
            if resp.get("ok"):
                return resp.get("result", "")
            return f"Daemon error: {resp.get('error', 'unknown error')}"
        except (ConnectionRefusedError, FileNotFoundError):
            _spawn_daemon()
            time.sleep(1.5)
            continue
        except socket.timeout:
            return "Timed out waiting for the web-search daemon to respond."
        except Exception as e:
            return f"Daemon communication error: {e}"

    return "Failed to reach the web-search daemon after spawning it."
