"""
SQLite-backed caching layer and Cloudflare cookie vault.
- Caches search queries (TTL: 12h) to avoid repeated API/network calls.
- Caches full webpage Markdown (TTL: 48h) for instant (0ms) re-access.
- Persists Cloudflare cf_clearance cookies & User-Agents per domain (TTL: 4h).
"""
import os
import sqlite3
import hashlib
import json
import time
import uuid
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List

from chunker import extract_headings, extract_section, find_query_passages

VAULT_DIR = os.path.expanduser("~/.cache/web_search_tool")
os.makedirs(VAULT_DIR, exist_ok=True)
VAULT_DB_PATH = os.path.join(VAULT_DIR, "vault.sqlite")

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(VAULT_DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn

def init_vault():
    with get_connection() as conn:
        # Search queries cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                query_hash TEXT PRIMARY KEY,
                query_text TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                ttl_seconds REAL NOT NULL
            );
        """)
        # Page content cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS page_cache (
                url_hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                content_markdown TEXT NOT NULL,
                created_at REAL NOT NULL,
                ttl_seconds REAL NOT NULL
            );
        """)
        # Cloudflare / anti-bot cookie vault
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cookie_vault (
                domain TEXT PRIMARY KEY,
                cookies_json TEXT NOT NULL,
                user_agent TEXT NOT NULL,
                created_at REAL NOT NULL,
                ttl_seconds REAL NOT NULL
            );
        """)
        # Staged documents for out-of-band context reduction & pointer protocol
        conn.execute("""
            CREATE TABLE IF NOT EXISTS staged_documents (
                doc_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                content_markdown TEXT NOT NULL,
                toc_json TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                token_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_staged_expires ON staged_documents(expires_at);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_staged_session ON staged_documents(session_id);")
        conn.execute("DELETE FROM staged_documents WHERE expires_at < ?", (time.time(),))
        conn.commit()

init_vault()

def _normalize_query(query: str) -> str:
    """Normalize query by stripping whitespace, collapsing internal whitespace, and lowercasing."""
    if not query:
        return ""
    return " ".join(query.strip().lower().split())

def _hash(val: str) -> str:
    return hashlib.sha256(_normalize_query(val).encode("utf-8")).hexdigest()

def extract_domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()

# --- Search Cache ---
def get_cached_search(query: str, max_age_hours: float = 12.0) -> Optional[List[Dict[str, Any]]]:
    norm_q = _normalize_query(query)
    q_hash = _hash(norm_q)
    now = time.time()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT result_json, created_at, ttl_seconds FROM search_cache WHERE query_hash = ?", (q_hash,))
            row = cur.fetchone()
            if row:
                res_json, created_at, ttl = row
                if (now - created_at) < min(ttl, max_age_hours * 3600):
                    return json.loads(res_json)
    except Exception:
        pass
    return None

def set_cached_search(query: str, results: List[Dict[str, Any]], ttl_hours: float = 12.0):
    norm_q = _normalize_query(query)
    q_hash = _hash(norm_q)
    now = time.time()
    res_json = json.dumps(results)
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO search_cache (query_hash, query_text, result_json, created_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
            """, (q_hash, norm_q, res_json, now, ttl_hours * 3600))
            conn.commit()
    except Exception:
        pass

# --- Page Cache ---
def get_cached_page(url: str, max_age_hours: float = 48.0) -> Optional[str]:
    u_hash = _hash(url)
    now = time.time()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT content_markdown, created_at, ttl_seconds FROM page_cache WHERE url_hash = ?", (u_hash,))
            row = cur.fetchone()
            if row:
                content, created_at, ttl = row
                if (now - created_at) < min(ttl, max_age_hours * 3600):
                    # Invalidate stale raw PDF binary entries saved before PDF parser was added
                    if content and (content.startswith("%PDF-") or content.startswith("%PDF")):
                        conn.execute("DELETE FROM page_cache WHERE url_hash = ?", (u_hash,))
                        conn.commit()
                        return None
                    return content
    except Exception:
        pass
    return None

def set_cached_page(url: str, content: str, ttl_hours: float = 48.0):
    if not content or len(content.strip()) < 50 or content.startswith("%PDF-") or content.startswith("%PDF"):
        return
    u_hash = _hash(url)
    now = time.time()
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO page_cache (url_hash, url, content_markdown, created_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
            """, (u_hash, url, content, now, ttl_hours * 3600))
            conn.commit()
    except Exception:
        pass

# --- Cookie Vault ---
def get_domain_cookies(url_or_domain: str) -> Optional[Dict[str, Any]]:
    domain = extract_domain(url_or_domain) if "://" in url_or_domain else url_or_domain.lower()
    now = time.time()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT cookies_json, user_agent, created_at, ttl_seconds FROM cookie_vault WHERE domain = ?", (domain,))
            row = cur.fetchone()
            if row:
                cookies_json, ua, created_at, ttl = row
                if (now - created_at) < ttl:
                    return {
                        "cookies": json.loads(cookies_json),
                        "user_agent": ua
                    }
    except Exception:
        pass
    return None

def save_domain_cookies(url_or_domain: str, cookies: Dict[str, str], user_agent: str, ttl_hours: float = 4.0):
    domain = extract_domain(url_or_domain) if "://" in url_or_domain else url_or_domain.lower()
    now = time.time()
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO cookie_vault (domain, cookies_json, user_agent, created_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
            """, (domain, json.dumps(cookies), user_agent, now, ttl_hours * 3600))
            conn.commit()
    except Exception:
        pass

# --- Out-of-Band Staging & Pointer Protocol ---

def stage_document(
    url: str,
    title: str,
    content: str,
    session_id: str = "default",
    ttl_days: float = 7.0
) -> Dict[str, Any]:
    """
    Stage a full document out-of-band in the vault.
    Generates a unique collision-free doc_id and stores TOC metadata.
    """
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    headings = extract_headings(content)
    char_count = len(content)
    token_count = int(char_count / 4.2)
    now = time.time()
    expires_at = now + (ttl_days * 86400.0)
    toc_json = json.dumps(headings)

    with get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO staged_documents (
                doc_id, session_id, url, title, content_markdown, toc_json,
                char_count, token_count, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            doc_id, session_id, url, title, content, toc_json,
            char_count, token_count, now, expires_at
        ))
        conn.commit()

    return {
        "doc_id": doc_id,
        "session_id": session_id,
        "url": url,
        "title": title,
        "char_count": char_count,
        "token_count": token_count,
        "headings": headings,
        "content_markdown": content,
        "content": content,
        "created_at": now,
        "expires_at": expires_at,
    }

def get_staged_document(doc_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch a staged document from the vault by doc_id.
    Returns None if missing or expired.
    """
    now = time.time()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT doc_id, session_id, url, title, content_markdown, toc_json,
                       char_count, token_count, created_at, expires_at
                FROM staged_documents WHERE doc_id = ?
            """, (doc_id,))
            row = cur.fetchone()
            if row:
                (d_id, session_id, url, title, content, toc_json,
                 char_count, token_count, created_at, expires_at) = row
                if now > expires_at:
                    return None
                headings = json.loads(toc_json) if toc_json else []
                return {
                    "doc_id": d_id,
                    "session_id": session_id,
                    "url": url,
                    "title": title,
                    "content_markdown": content,
                    "content": content,
                    "toc_json": toc_json,
                    "headings": headings,
                    "char_count": char_count,
                    "token_count": token_count,
                    "created_at": created_at,
                    "expires_at": expires_at,
                }
    except Exception:
        pass
    return None

def clean_expired_staged_docs() -> int:
    """Purge expired staged documents from the vault."""
    now = time.time()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM staged_documents WHERE expires_at < ?", (now,))
            conn.commit()
            return cur.rowcount
    except Exception:
        return 0

def extract_staged_slice(
    doc_id: str,
    query: str = "",
    section: str = "",
    max_chars: int = 1500
) -> str:
    """
    Extract a targeted slice (by section heading/index or query keywords) from a staged document.
    Enforces max_chars ceiling with clean truncation indicator.
    """
    doc = get_staged_document(doc_id)
    if not doc:
        return f"Error: Document '{doc_id}' was not found or has expired (7-day TTL). Please re-fetch the document from its original URL."

    content = doc.get("content_markdown") or doc.get("content", "")
    headings = doc.get("headings", [])

    def apply_truncation(text: str, limit: int) -> str:
        if len(text) > limit:
            indicator = f"\n\n... [Content truncated at {limit} chars. Use `inspect_vault_doc` with a specific section or query for more.]"
            return text[:max(0, limit - len(indicator))] + indicator
        return text

    # Case 1: Specific section requested
    if section and section.strip():
        sec_clean = section.strip()
        sec_text = extract_section(content, sec_clean)
        if not sec_text.startswith(f"Section '{sec_clean}' not found"):
            # Section found. If query is also provided and section is long, focus on query inside section
            if query and query.strip() and len(sec_text) > max_chars:
                q_passages = find_query_passages(sec_text, query.strip(), max_chars=max_chars)
                if q_passages:
                    return apply_truncation(q_passages, max_chars)
            return apply_truncation(sec_text, max_chars)
        else:
            # Section not found. If query provided, fallback to query search across full document
            if not (query and query.strip()):
                return sec_text

    # Case 2: Query requested (or fallback from section not found)
    if query and query.strip():
        q_passages = find_query_passages(content, query.strip(), max_chars=max_chars)
        if q_passages:
            return apply_truncation(q_passages, max_chars)
        sections_summary = ", ".join(f"`{h['title']}`" for h in headings[:8]) if headings else "None"
        return f"No passages found matching query '{query}' in document '{doc_id}'. Available sections: {sections_summary}."

    # Case 3: Neither section nor query provided: return beginning of document
    return apply_truncation(content, max_chars)

def inspect_staged_doc(
    doc_id: str,
    query: str = "",
    section: str = "",
    max_chars: int = 1500
) -> str:
    """Convenience alias for extract_staged_slice."""
    return extract_staged_slice(doc_id=doc_id, query=query, section=section, max_chars=max_chars)

