#!/usr/bin/env python3
"""
Shared persistent worker daemon for the web search tool.

Problem it solves: every MCP client app (Claude Code, Claude Desktop,
Antigravity, a local LLM's MCP client, ...) launches its OWN mcp_server.py
process over stdio. If mcp_server.py does the heavy lifting itself, every
running client app pays its own full copy of GLiNER + the cross-encoder +
the Gemma SLM in RAM/VRAM, and per-process semaphores give zero protection
against two separate client apps running model inference at the same
moment (semaphores only coordinate threads within one process).

This daemon is the single process that actually holds the models and the
shared browser. mcp_server.py becomes a thin client (see daemon_client.py)
that forwards each tool call to it over a Unix domain socket, auto-spawning
this process on first use. It exits itself after IDLE_TIMEOUT_SECONDS of
no requests, so it doesn't sit there holding RAM/VRAM when nothing is using
the tool.
"""
import os
import sys
import json
import time
import fcntl
import signal
import socket
import socketserver
import threading
import logging
import warnings

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore")
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("gliner").setLevel(logging.ERROR)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

RUNTIME_DIR = os.path.expanduser("~/.cache/web_search_tool")
os.makedirs(RUNTIME_DIR, exist_ok=True)
SOCKET_PATH = os.path.join(RUNTIME_DIR, "daemon.sock")
LOCK_PATH = os.path.join(RUNTIME_DIR, "daemon.lock")
LOG_PATH = os.path.join(RUNTIME_DIR, "daemon.log")
IDLE_TIMEOUT_SECONDS = 30 * 60  # auto-exit after 30 minutes with no requests

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("ws_daemon")

from searcher import search_web
from fetcher import fetch_page
from chunker import get_table_of_contents, extract_section
from parallel_pipeline import (
    fast_intelligent_search,
    format_fast_digest,
    deep_reasoning_search,
    format_deep_digest,
)

# Concurrency throttling: lives HERE now (not in each client process) since
# this is the one place that actually touches the models/hardware.
_ML_SEMAPHORE = threading.Semaphore(1)
_FETCH_SEMAPHORE = threading.Semaphore(2)
_SEARCH_SEMAPHORE = threading.Semaphore(3)

_last_activity = time.time()
_activity_lock = threading.Lock()

def _touch():
    global _last_activity
    with _activity_lock:
        _last_activity = time.time()

# --- Command handlers (ported from the old mcp_server.py tool bodies) ---

def _handle_search(kwargs):
    with _SEARCH_SEMAPHORE:
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 5)
        perspective = kwargs.get("perspective", "tech")
        results = search_web(query, max_results=max_results, perspective=perspective)
        if not results:
            return "No results found."
        out = []
        for idx, r in enumerate(results, 1):
            p_tag = f" [{r.get('perspective', 'tech').upper()}]" if perspective == "dual" else ""
            out.append(f"{idx}. [{r.get('title')}]({r.get('href')}){p_tag}")
            out.append(f"   {r.get('body')}\n")
        return "\n".join(out)

def _handle_fetch(kwargs):
    with _FETCH_SEMAPHORE:
        url = kwargs.get("url", "")
        use_browser = kwargs.get("force_browser", False) or kwargs.get("force_stealth", False)
        skip_tls_verify = kwargs.get("skip_tls_verify", True)
        content = fetch_page(url, force_stealth=use_browser, skip_tls_verify=skip_tls_verify)
        if len(content) > 25000:
            content = content[:24600] + (
                "\n\n> [!NOTE]\n"
                f"> *Webpage content capped at 25,000 characters (truncated). Use `get_toc('{url}')` and `read_section` to read specific sections.*"
            )
        return content

def _handle_get_toc(kwargs):
    with _FETCH_SEMAPHORE:
        content = fetch_page(kwargs.get("url", ""))
        return get_table_of_contents(content)

def _handle_read_section(kwargs):
    with _FETCH_SEMAPHORE:
        content = fetch_page(kwargs.get("url", ""))
        return extract_section(content, kwargs.get("section_name_or_index", ""))

def _handle_fast_neural_search(kwargs):
    with _ML_SEMAPHORE:
        data = fast_intelligent_search(
            kwargs.get("query", ""),
            max_results=kwargs.get("num_pages", 4),
            perspective=kwargs.get("perspective", "tech"),
            instruction=kwargs.get("instruction") or None
        )
        return format_fast_digest(data)

def _handle_batch_search(kwargs):
    queries = kwargs.get("queries") or []
    if not queries:
        return "No queries provided."
    capped_queries = queries[:3]
    max_results_per_query = kwargs.get("max_results_per_query", 3)
    perspective = kwargs.get("perspective", "tech")
    outputs = []
    for idx, q in enumerate(capped_queries, 1):
        with _ML_SEMAPHORE:
            data = fast_intelligent_search(q, max_results=max_results_per_query, perspective=perspective)
            digest = format_fast_digest(data)
            outputs.append(f"## Batch Query [{idx}/{len(capped_queries)}]: `{q}`\n\n{digest}")
    res_str = "\n\n========================================\n\n".join(outputs)
    if len(res_str) > 25000:
        res_str = res_str[:24600] + "\n\n> [!NOTE]\n> *Batch output capped at 25,000 characters (truncated).*"
    return res_str

def _handle_deep_reasoning_search(kwargs):
    with _ML_SEMAPHORE:
        data = deep_reasoning_search(
            kwargs.get("query", ""),
            perspective=kwargs.get("perspective", "tech"),
            reasoning_intent=kwargs.get("reasoning_intent") or None,
            max_results=kwargs.get("num_pages", 3)
        )
        return format_deep_digest(data)

HANDLERS = {
    "search": _handle_search,
    "fetch": _handle_fetch,
    "get_toc": _handle_get_toc,
    "read_section": _handle_read_section,
    "fast_neural_search": _handle_fast_neural_search,
    "batch_search": _handle_batch_search,
    "deep_reasoning_search": _handle_deep_reasoning_search,
}

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        _touch()
        try:
            line = self.rfile.readline()
            if not line:
                return
            req = json.loads(line.decode("utf-8"))
            cmd = req.get("cmd")
            kwargs = req.get("kwargs", {}) or {}
            handler = HANDLERS.get(cmd)
            if handler is None:
                resp = {"ok": False, "error": f"Unknown command: {cmd}"}
            else:
                try:
                    result = handler(kwargs)
                    resp = {"ok": True, "result": result}
                except Exception as e:
                    logger.exception(f"Error handling command '{cmd}'")
                    resp = {"ok": False, "error": str(e)}
        except Exception as e:
            resp = {"ok": False, "error": f"Malformed request: {e}"}
        _touch()
        try:
            self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))
        except Exception:
            pass

class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

def _idle_watchdog():
    while True:
        time.sleep(30)
        with _activity_lock:
            idle_for = time.time() - _last_activity
        if idle_for > IDLE_TIMEOUT_SECONDS:
            logger.info(f"Idle for {idle_for:.0f}s, shutting down.")
            # Signal ourselves rather than os._exit(), so the SIGTERM handler
            # in main() runs and atexit cleanup (closing the shared browser)
            # actually happens instead of being skipped.
            os.kill(os.getpid(), signal.SIGTERM)
            return

def main():
    # Single-instance guard: hold an exclusive lock for the life of this
    # process. If another process already holds it, a daemon is already
    # starting or running, so just exit.
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.info("Another process already owns the daemon lock; exiting.")
        return

    # We now hold the only lock, so any socket file left behind is stale
    # (e.g. from a daemon that was killed without cleaning up).
    try:
        os.remove(SOCKET_PATH)
    except FileNotFoundError:
        pass

    server = _ThreadingUnixServer(SOCKET_PATH, _Handler)
    logger.info(f"Daemon listening on {SOCKET_PATH} (pid={os.getpid()})")

    # A plain `kill` sends SIGTERM, whose default disposition terminates the
    # process immediately WITHOUT running atexit hooks -- which is exactly
    # what closes the shared Camoufox browser (see fetcher._close_shared_browser).
    # Without this handler, killing the daemon can orphan a running browser
    # process. Raising SystemExit here instead triggers a normal interpreter
    # shutdown, so atexit cleanup actually runs.
    def _handle_term(signum, frame):
        logger.info(f"Received signal {signum}, shutting down.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    watchdog = threading.Thread(target=_idle_watchdog, daemon=True)
    watchdog.start()

    try:
        server.serve_forever()
    finally:
        try:
            os.remove(SOCKET_PATH)
        except Exception:
            pass

if __name__ == "__main__":
    main()
