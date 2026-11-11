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
import math
from typing import Any

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

from urllib.parse import urlparse

from searcher import search_web
from fetcher import fetch_page
from chunker import (
    get_table_of_contents,
    extract_section,
    extract_headings,
    safe_truncate_markdown,
)
from parallel_pipeline import (
    fast_intelligent_search,
    format_fast_digest,
    deep_reasoning_search,
    format_deep_digest,
    format_manifest_card,
)
from vault import (
    stage_document,
    inspect_staged_doc,
    record_fact,
    get_ledger_markdown,
    clear_ledger,
    clean_expired_staged_docs,
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

def _safe_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return default
        return int(val)
    except (ValueError, TypeError, OverflowError):
        return default

def _safe_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "y", "t")
    return False

# --- Command handlers (ported from the old mcp_server.py tool bodies) ---

def _handle_search(kwargs):
    with _SEARCH_SEMAPHORE:
        query = str(kwargs.get("query") or "")
        max_results = _safe_int(kwargs.get("max_results"), 5)
        perspective = str(kwargs.get("perspective") or "tech")
        results = search_web(query, max_results=max_results, perspective=perspective)
        if not results:
            return "No results found."
        if results and isinstance(results[0], dict) and "error" in results[0]:
            return f"Search Error: {results[0]['error']}"
        out = []
        for idx, r in enumerate(results, 1):
            p_tag = f" [{r.get('perspective', 'tech').upper()}]" if perspective == "dual" else ""
            out.append(f"{idx}. [{r.get('title')}]({r.get('href')}){p_tag}")
            out.append(f"   {r.get('body')}\n")
        return "\n".join(out)

def _handle_fetch(kwargs):
    with _FETCH_SEMAPHORE:
        url = str(kwargs.get("url") or "")
        use_browser = _safe_bool(kwargs.get("force_browser", False)) or _safe_bool(kwargs.get("force_stealth", False))
        skip_tls_verify = _safe_bool(kwargs["skip_tls_verify"]) if "skip_tls_verify" in kwargs else True
        as_pointer = _safe_bool(kwargs.get("as_pointer", False))
        session_id = str(kwargs.get("session_id") or "default")

        if as_pointer:
            # Out-of-band staging: fetch full document uncapped
            content = fetch_page(url, force_stealth=use_browser, skip_tls_verify=skip_tls_verify, max_chars=None)
            if content.startswith("Failed to retrieve"):
                return content

            headings = extract_headings(content)
            title = headings[0]["title"] if headings else (urlparse(url).netloc or url)
            staged_meta = stage_document(
                url=url,
                title=title,
                content=content,
                session_id=session_id,
                headings=headings
            )
            return format_manifest_card(staged_meta)

        content = fetch_page(url, force_stealth=use_browser, skip_tls_verify=skip_tls_verify, max_chars=6000)
        return content

def _handle_get_toc(kwargs):
    with _FETCH_SEMAPHORE:
        content = fetch_page(str(kwargs.get("url") or ""), max_chars=None)
        if content.startswith("Failed to retrieve"):
            return content
        return get_table_of_contents(content)

def _handle_read_section(kwargs):
    with _FETCH_SEMAPHORE:
        content = fetch_page(str(kwargs.get("url") or ""), max_chars=None)
        if content.startswith("Failed to retrieve"):
            return content
        return extract_section(content, str(kwargs.get("section_name_or_index") or ""))

def _handle_fast_neural_search(kwargs):
    with _ML_SEMAPHORE:
        density = str(kwargs.get("density") or "compact")
        num_pages = _safe_int(kwargs.get("num_pages"), 4)
        data = fast_intelligent_search(
            str(kwargs.get("query") or ""),
            max_results=num_pages,
            perspective=str(kwargs.get("perspective") or "tech"),
            instruction=kwargs.get("instruction") or None,
            density=density
        )
        return format_fast_digest(data, density=density)

def _handle_batch_search(kwargs):
    queries = kwargs.get("queries")
    if queries is None:
        return "No queries provided."
    if isinstance(queries, str):
        if "\n" in queries:
            raw_queries = queries.splitlines()
        elif "," in queries:
            raw_queries = queries.split(",")
        else:
            raw_queries = [queries]
    elif isinstance(queries, (list, tuple)):
        raw_queries = list(queries)
    else:
        raw_queries = [str(queries)]

    queries = [str(q).strip() for q in raw_queries if q and str(q).strip()]
    if not queries:
        return "No queries provided."

    capped_queries = queries[:3]
    max_results_per_query = _safe_int(kwargs.get("max_results_per_query"), 3)
    perspective = str(kwargs.get("perspective") or "tech")
    density = str(kwargs.get("density") or "compact")
    outputs = []
    for idx, q in enumerate(capped_queries, 1):
        with _ML_SEMAPHORE:
            data = fast_intelligent_search(q, max_results=max_results_per_query, perspective=perspective, density=density)
            digest = format_fast_digest(data, density=density)
            outputs.append(f"## Batch Query [{idx}/{len(capped_queries)}]: `{q}`\n\n{digest}")
    res_str = "\n\n========================================\n\n".join(outputs)
    if len(res_str) > 25000:
        res_str = safe_truncate_markdown(
            res_str,
            25000,
            suffix="\n\n> [!NOTE]\n> *Batch output capped at 25,000 characters (truncated).*"
        )
    return res_str

def _handle_deep_reasoning_search(kwargs):
    with _ML_SEMAPHORE:
        num_pages = _safe_int(kwargs.get("num_pages"), 3)
        data = deep_reasoning_search(
            str(kwargs.get("query") or ""),
            perspective=str(kwargs.get("perspective") or "tech"),
            reasoning_intent=kwargs.get("reasoning_intent") or None,
            max_results=num_pages
        )
        return format_deep_digest(data)

def _handle_inspect_vault_doc(kwargs):
    doc_id = str(kwargs.get("doc_id") or "")
    query = str(kwargs.get("query") or "")
    section = str(kwargs.get("section") or "")
    max_chars = _safe_int(kwargs.get("max_chars"), 1500)
    return inspect_staged_doc(doc_id=doc_id, query=query, section=section, max_chars=max_chars)

def _handle_record_research_fact(kwargs):
    category = str(kwargs.get("category") or "").strip()
    fact_text = str(kwargs.get("fact_text") or "").strip()
    source_url = str(kwargs.get("source_url") or "").strip()
    source_title = str(kwargs.get("source_title") or "").strip()
    if not category or not fact_text:
        return "Error: category and fact_text are required."
    tier = _safe_int(kwargs.get("tier"), 1)
    pass_num = _safe_int(kwargs.get("pass_num"), 1)
    tags = kwargs.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    fact_id = record_fact(
        category=category,
        tier=tier,
        fact_text=fact_text,
        source_url=source_url,
        source_title=source_title,
        pass_num=pass_num,
        tags=tags
    )
    return f"Recorded fact #{fact_id} in category '{category}' (Tier {tier}, Pass {pass_num})."

def _handle_get_research_ledger(kwargs):
    category = kwargs.get("category")
    if category is not None:
        category = str(category).strip() or None
    tier = kwargs.get("tier")
    if tier is not None:
        try:
            tier = int(tier)
        except (ValueError, TypeError):
            tier = None
    return get_ledger_markdown(category=category, tier=tier)

def _handle_clear_research_ledger(kwargs):
    category = kwargs.get("category")
    if category is not None:
        category = str(category).strip() or None
    confirm = _safe_bool(kwargs.get("confirm", False))
    return clear_ledger(category=category, confirm=confirm)

HANDLERS = {
    "search": _handle_search,
    "fetch": _handle_fetch,
    "get_toc": _handle_get_toc,
    "read_section": _handle_read_section,
    "fast_neural_search": _handle_fast_neural_search,
    "batch_search": _handle_batch_search,
    "deep_reasoning_search": _handle_deep_reasoning_search,
    "inspect_vault_doc": _handle_inspect_vault_doc,
    "record_research_fact": _handle_record_research_fact,
    "get_research_ledger": _handle_get_research_ledger,
    "clear_research_ledger": _handle_clear_research_ledger,
}

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        _touch()
        try:
            line = self.rfile.readline(1048576)
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
            self.wfile.write((json.dumps(resp, default=str) + "\n").encode("utf-8"))
        except Exception:
            pass

class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

def _idle_watchdog():
    last_clean = time.time()
    while True:
        time.sleep(30)
        now = time.time()
        # Periodically clean expired staged docs (every 10 minutes)
        if now - last_clean > 600:
            try:
                purged = clean_expired_staged_docs()
                if purged > 0:
                    logger.info(f"Purged {purged} expired staged documents from vault.")
            except Exception as e:
                logger.warning(f"Error purging expired docs: {e}")
            last_clean = now

        with _activity_lock:
            idle_for = now - _last_activity
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
