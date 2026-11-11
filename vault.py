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
import math
from datetime import datetime, timezone
from collections import defaultdict
from urllib.parse import urlparse, urlsplit, urlunsplit
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

from chunker import extract_headings, extract_section, find_query_passages
try:
    from chunker import safe_truncate_markdown
except ImportError:
    # Fallback in case chunker.py is compiled or imported before safe_truncate_markdown is added
    def safe_truncate_markdown(text: str, max_chars: int, suffix: str = "") -> str:
        if not text or len(text) <= max_chars:
            return text
        target_len = max(0, max_chars - len(suffix))
        if target_len <= 0:
            return suffix[:max_chars]
        slice_candidate = text[:target_len]
        last_newline = slice_candidate.rfind("\n")
        if last_newline > int(target_len * 0.8):
            truncated = slice_candidate[:last_newline]
        else:
            truncated = slice_candidate
        # Check for unclosed code fences
        fence_matches = [m for m in truncated.split("\n") if m.strip().startswith("```")]
        if len(fence_matches) % 2 != 0:
            truncated = truncated.rstrip() + "\n```"
        return truncated + suffix

VAULT_DIR = os.path.expanduser("~/.cache/web_search_tool")
os.makedirs(VAULT_DIR, exist_ok=True)
VAULT_DB_PATH = os.path.join(VAULT_DIR, "vault.sqlite")
_last_cleanup: float = 0.0

@contextmanager
def get_connection():
    """Context manager providing thread-safe SQLite connection with guaranteed closure."""
    conn = sqlite3.connect(VAULT_DB_PATH, timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        yield conn
    finally:
        conn.close()

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
        conn.execute("DELETE FROM search_cache WHERE (created_at + ttl_seconds) < ?", (time.time(),))
        conn.execute("DELETE FROM page_cache WHERE (created_at + ttl_seconds) < ?", (time.time(),))
        conn.execute("DELETE FROM cookie_vault WHERE (created_at + ttl_seconds) < ?", (time.time(),))

        # Persistent Research State Ledger
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                tier INTEGER NOT NULL,
                pass_num INTEGER NOT NULL,
                fact_text TEXT NOT NULL,
                source_url TEXT,
                source_title TEXT,
                tags_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_cat_tier ON research_ledger(category, tier);")
        conn.commit()

init_vault()

def _normalize_query(query: Any) -> str:
    """Normalize query by stripping whitespace, collapsing internal whitespace, and lowercasing."""
    if query is None:
        return ""
    return " ".join(str(query).strip().lower().split())

def _hash(val: str) -> str:
    return hashlib.sha256(_normalize_query(val).encode("utf-8")).hexdigest()

def _hash_url(url: str) -> str:
    """
    Generate SHA-256 hash for a URL preserving path and query case sensitivity.
    Lowercases scheme and hostname (netloc), strictly preserving casing of path and query.
    Strips URL fragments so anchor links map to the same cached resource.
    Strips trailing slash on path if root, while keeping query params intact.
    """
    if not url:
        return hashlib.sha256(b"").hexdigest()
    url_str = str(url).strip()
    try:
        parts = urlsplit(url_str)
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        # Normalize trailing slash on path (e.g. /docs/api/ -> /docs/api)
        norm_path = parts.path.rstrip("/") if parts.path != "/" else "/"
        canonical_url = urlunsplit((scheme, netloc, norm_path, parts.query, ""))
    except Exception:
        canonical_url = url_str
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()

def extract_domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(str(url))
    netloc = parsed.netloc if isinstance(parsed.netloc, str) else parsed.netloc.decode("utf-8", "ignore")
    return netloc.lower()

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
        ttl = float(ttl_hours)
        if math.isnan(ttl) or math.isinf(ttl) or ttl <= 0:
            ttl = 12.0
        ttl = min(ttl, 365.0 * 24)
    except (ValueError, TypeError):
        ttl = 12.0
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO search_cache (query_hash, query_text, result_json, created_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
            """, (q_hash, norm_q, res_json, now, ttl * 3600))
            conn.commit()
    except Exception:
        pass

# --- Page Cache ---
def get_cached_page(url: str, max_age_hours: float = 48.0) -> Optional[str]:
    u_hash = _hash_url(url)
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
    u_hash = _hash_url(url)
    now = time.time()
    try:
        ttl = float(ttl_hours)
        if math.isnan(ttl) or math.isinf(ttl) or ttl <= 0:
            ttl = 48.0
        ttl = min(ttl, 365.0 * 24)
    except (ValueError, TypeError):
        ttl = 48.0
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO page_cache (url_hash, url, content_markdown, created_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
            """, (u_hash, url, content, now, ttl * 3600))
            conn.commit()
    except Exception:
        pass

# --- Cookie Vault ---
def get_domain_cookies(url_or_domain: str) -> Optional[Dict[str, Any]]:
    if not url_or_domain:
        return None
    url_or_domain = str(url_or_domain)
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
    if not url_or_domain:
        return
    url_or_domain = str(url_or_domain)
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
    ttl_days: float = 7.0,
    headings: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Stage a full document out-of-band in the vault.
    Generates a unique collision-free doc_id and stores TOC metadata.
    """
    global _last_cleanup
    now = time.time()
    if (now - _last_cleanup) > 3600.0:
        try:
            clean_expired_staged_docs()
        except Exception:
            pass
        _last_cleanup = now

    url = str(url or "")
    title = str(title or "Untitled")
    session_id = str(session_id or "default")
    content = str(content or "")

    try:
        ttl = float(ttl_days)
        if math.isnan(ttl) or math.isinf(ttl) or ttl <= 0:
            ttl = 7.0
        ttl = min(ttl, 365.0)  # Cap max TTL at 365 days
    except (ValueError, TypeError):
        ttl = 7.0
    ttl_days = ttl

    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    if headings is None:
        try:
            headings = extract_headings(content)
        except Exception:
            headings = []
    elif not isinstance(headings, list):
        headings = []

    char_count = len(content)
    token_count = int(char_count / 4.2)
    expires_at = now + (ttl_days * 86400.0)
    toc_json = json.dumps(headings, default=str)

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
    if not doc_id:
        return None
    now = time.time()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT doc_id, session_id, url, title, content_markdown, toc_json,
                       char_count, token_count, created_at, expires_at
                FROM staged_documents WHERE doc_id = ?
            """, (str(doc_id),))
            row = cur.fetchone()
            if row:
                (d_id, session_id, url, title, content, toc_json,
                 char_count, token_count, created_at, expires_at) = row
                if now > expires_at:
                    conn.execute("DELETE FROM staged_documents WHERE doc_id = ?", (str(doc_id),))
                    conn.commit()
                    return None
                try:
                    headings = json.loads(toc_json) if toc_json else []
                except Exception:
                    headings = []
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
    """Purge expired staged documents and expired cache rows from the vault."""
    now = time.time()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM staged_documents WHERE expires_at < ?", (now,))
            staged_purged = cur.rowcount
            cur.execute("DELETE FROM search_cache WHERE (created_at + ttl_seconds) < ?", (now,))
            cur.execute("DELETE FROM page_cache WHERE (created_at + ttl_seconds) < ?", (now,))
            cur.execute("DELETE FROM cookie_vault WHERE (created_at + ttl_seconds) < ?", (now,))
            conn.commit()
            return staged_purged
    except Exception:
        return 0

def clean_expired_cache() -> Dict[str, int]:
    """Purge expired rows from search_cache, page_cache, cookie_vault, and staged_documents."""
    now = time.time()
    purged = {"search_cache": 0, "page_cache": 0, "cookie_vault": 0, "staged_documents": 0}
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM search_cache WHERE (created_at + ttl_seconds) < ?", (now,))
            purged["search_cache"] = cur.rowcount
            cur.execute("DELETE FROM page_cache WHERE (created_at + ttl_seconds) < ?", (now,))
            purged["page_cache"] = cur.rowcount
            cur.execute("DELETE FROM cookie_vault WHERE (created_at + ttl_seconds) < ?", (now,))
            purged["cookie_vault"] = cur.rowcount
            cur.execute("DELETE FROM staged_documents WHERE expires_at < ?", (now,))
            purged["staged_documents"] = cur.rowcount
            conn.commit()
    except Exception:
        pass
    return purged

def extract_staged_slice(
    doc_id: str,
    query: str = "",
    section: str = "",
    max_chars: int = 1500
) -> str:
    """
    Extract a targeted slice (by section heading/index or query keywords) from a staged document.
    Enforces max_chars ceiling with clean syntax-aware markdown truncation indicator.
    """
    try:
        max_chars = int(max_chars)
    except (ValueError, TypeError):
        max_chars = 1500

    doc = get_staged_document(doc_id)
    if not doc:
        return f"Error: Document '{doc_id}' was not found or has expired (7-day TTL). Please re-fetch the document from its original URL."

    content = doc.get("content_markdown") or doc.get("content", "")
    headings = doc.get("headings", [])
    trunc_suffix = f"\n\n... [Content truncated at {max_chars} chars. Use `inspect_vault_doc` with a specific section or query for more.]"

    section_str = str(section or "").strip()
    query_str = str(query or "").strip()

    # Case 1: Specific section requested
    if section_str:
        sec_clean = section_str
        sec_text = extract_section(content, sec_clean)
        if not sec_text.startswith(f"Section '{sec_clean}' not found"):
            # Section found. If query is also provided, focus on query inside section
            if query_str:
                q_passages = find_query_passages(sec_text, query_str, max_chars=max_chars)
                if q_passages:
                    return safe_truncate_markdown(q_passages, max_chars, suffix=trunc_suffix)
                return f"No passages found matching query '{query_str}' in section '{sec_clean}' of document '{doc_id}'."
            return safe_truncate_markdown(sec_text, max_chars, suffix=trunc_suffix)
        else:
            sections_summary = ", ".join(f"`{h.get('title', str(h)) if isinstance(h, dict) else str(h)}`" for h in headings[:8]) if headings else "None"
            return f"Error: Section '{sec_clean}' was not found in document '{doc_id}'. Available sections: {sections_summary}."

    # Case 2: Query requested
    if query_str:
        q_passages = find_query_passages(content, query_str, max_chars=max_chars)
        if q_passages:
            return safe_truncate_markdown(q_passages, max_chars, suffix=trunc_suffix)
        sections_summary = ", ".join(f"`{h.get('title', str(h)) if isinstance(h, dict) else str(h)}`" for h in headings[:8]) if headings else "None"
        return f"No passages found matching query '{query_str}' in document '{doc_id}'. Available sections: {sections_summary}."

    # Case 3: Neither section nor query provided: return beginning of document
    return safe_truncate_markdown(content, max_chars, suffix=trunc_suffix)

def inspect_staged_doc(
    doc_id: str,
    query: str = "",
    section: str = "",
    max_chars: int = 1500
) -> str:
    """Convenience alias for extract_staged_slice."""
    return extract_staged_slice(doc_id=doc_id, query=query, section=section, max_chars=max_chars)


# --- Persistent Research State Ledger ---

def record_fact(
    category: str,
    tier: int,
    fact_text: str,
    source_url: str = "",
    source_title: str = "",
    pass_num: int = 1,
    tags: Optional[List[str]] = None
) -> int:
    """
    Record an established research fact or finding into the persistent SQLite ledger.
    Returns the integer primary key of the inserted row.
    """
    category_clean = str(category or "general").strip()
    fact_clean = str(fact_text or "").strip()
    try:
        tier_int = int(tier)
    except (ValueError, TypeError, OverflowError):
        tier_int = 1
    try:
        pass_int = int(pass_num)
    except (ValueError, TypeError, OverflowError):
        pass_int = 1

    tags_json = json.dumps(list(tags) if isinstance(tags, (list, tuple, set)) else [], default=str)
    now = time.time()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO research_ledger (
                category, tier, pass_num, fact_text, source_url, source_title, tags_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            category_clean,
            tier_int,
            pass_int,
            fact_clean,
            str(source_url or "").strip(),
            str(source_title or "").strip(),
            tags_json,
            now
        ))
        conn.commit()
        return cur.lastrowid


def get_ledger_entries(
    category: Optional[str] = None,
    tier: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Query research ledger entries matching optional category and tier filters.
    Ordered by category, tier, pass_num, created_at.
    """
    category = str(category).strip() if category is not None else None
    query = """
        SELECT id, category, tier, pass_num, fact_text, source_url, source_title, tags_json, created_at
        FROM research_ledger
    """
    params = []
    conditions = []
    if category:
        conditions.append("category = ?")
        params.append(category)
    if tier is not None:
        try:
            tier_val = int(tier)
            conditions.append("tier = ?")
            params.append(tier_val)
        except (ValueError, TypeError, OverflowError):
            pass

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY category ASC, tier ASC, pass_num ASC, created_at ASC"

    entries = []
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        for row in cur.fetchall():
            tags = json.loads(row[7]) if row[7] else []
            entries.append({
                "id": row[0],
                "category": row[1],
                "tier": row[2],
                "pass_num": row[3],
                "fact_text": row[4],
                "source_url": row[5],
                "source_title": row[6],
                "tags": tags,
                "created_at": row[8],
            })
    return entries


def get_ledger_markdown(
    category: Optional[str] = None,
    tier: Optional[int] = None
) -> str:
    """
    Format facts into a concise, token-dense Markdown briefing grouped by # Category and ## Tier X.
    Compact representation: bullet points with source link, date, tags.
    Formatted specifically so an AI agent can inject this as the opening context for a fresh session
    after flushing its conversational history.
    """
    category = str(category).strip() if category is not None else None
    entries = get_ledger_entries(category=category, tier=tier)
    if not entries:
        filter_desc = []
        if category:
            filter_desc.append(f"category='{category}'")
        if tier is not None:
            filter_desc.append(f"tier={tier}")
        filter_str = f" for {', '.join(filter_desc)}" if filter_desc else ""
        return f"No research facts recorded in ledger{filter_str}."

    grouped = defaultdict(lambda: defaultdict(list))
    for e in entries:
        grouped[e["category"]][e["tier"]].append(e)

    lines = ["# Research Ledger Briefing", f"*Established Facts: {len(entries)}*"]

    for cat in sorted(grouped.keys()):
        lines.append(f"\n# {cat}")
        for t in sorted(grouped[cat].keys()):
            lines.append(f"## Tier {t}")
            for item in grouped[cat][t]:
                src_parts = []
                url = item.get("source_url", "")
                title = item.get("source_title", "")
                if url:
                    label = title if title else (urlparse(url).netloc or "Source")
                    src_parts.append(f"[{label}]({url})")

                tags = item.get("tags", [])
                tag_str = f" `[{', '.join(str(t) for t in tags)}]`" if tags else ""

                try:
                    dt_str = datetime.fromtimestamp(float(item["created_at"]), timezone.utc).strftime("%Y-%m-%d")
                except (ValueError, TypeError, OverflowError, OSError):
                    dt_str = "unknown-date"

                src_info = f" ({', '.join(src_parts)} | {dt_str})" if src_parts else f" ({dt_str})"
                pass_info = f" [Pass {item['pass_num']}]" if item.get("pass_num") else ""

                lines.append(f"- {item['fact_text']}{src_info}{tag_str}{pass_info}")

    return "\n".join(lines)


def clear_ledger(
    category: Optional[str] = None,
    confirm: bool = False
) -> str:
    """
    Clear entries from the research ledger.
    Requires confirm=True to execute delete.
    If category is provided, clears only that category; otherwise clears all.
    Returns count of deleted facts.
    """
    if isinstance(confirm, str):
        is_confirmed = confirm.strip().lower() in ("true", "1", "yes")
    else:
        is_confirmed = bool(confirm)

    if not is_confirmed:
        return "Action aborted: confirm=True is required to clear research ledger entries."

    with get_connection() as conn:
        cur = conn.cursor()
        if category and str(category).strip():
            cat = str(category).strip()
            cur.execute("DELETE FROM research_ledger WHERE category = ?", (cat,))
            count = cur.rowcount
            conn.commit()
            return f"Cleared {count} facts from category '{cat}'."
        else:
            cur.execute("DELETE FROM research_ledger")
            count = cur.rowcount
            conn.commit()
            return f"Cleared all {count} facts from the research ledger."


