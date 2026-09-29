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
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List

VAULT_DIR = os.path.expanduser("~/.cache/web_search_tool")
os.makedirs(VAULT_DIR, exist_ok=True)
VAULT_DB_PATH = os.path.join(VAULT_DIR, "vault.sqlite")

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(VAULT_DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
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
        conn.commit()

init_vault()

def _hash(val: str) -> str:
    return hashlib.sha256(val.strip().lower().encode("utf-8")).hexdigest()

def extract_domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()

# --- Search Cache ---
def get_cached_search(query: str, max_age_hours: float = 12.0) -> Optional[List[Dict[str, Any]]]:
    q_hash = _hash(query)
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
    q_hash = _hash(query)
    now = time.time()
    res_json = json.dumps(results)
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO search_cache (query_hash, query_text, result_json, created_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
            """, (q_hash, query, res_json, now, ttl_hours * 3600))
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
