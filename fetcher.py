"""
High-performance, stealth webpage fetcher with SQLite Caching and Cloudflare Cookie Vault.
Tier 1: curl_cffi with Chrome/Safari TLS & HTTP/2 impersonation + vaulted cf_clearance cookies (~150ms).
Tier 2: Camoufox C++ stealth browser to solve challenges & harvest cf_clearance for Tier 1.
"""
import re
import sys
import logging
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse
from curl_cffi import requests
import trafilatura
from vault import (
    get_cached_page,
    set_cached_page,
    get_domain_cookies,
    save_domain_cookies,
    extract_domain
)

import threading

logger = logging.getLogger("stealth_fetcher")
_BROWSER_LOCK = threading.Lock()

CLOUDFLARE_INDICATORS = [
    "Just a moment...",
    "cf-chl-widget-",
    "challenges.cloudflare.com",
    "Enable JavaScript and cookies to continue",
    "Attention Required! | Cloudflare",
    "Checking your browser before accessing",
    "Ray ID:",
]

def is_blocked_or_challenged(html: str, status_code: int) -> bool:
    """Detect if response is an anti-bot challenge or block."""
    if status_code in (403, 429, 503):
        return True
    if not html:
        return True
    return any(ind in html for ind in CLOUDFLARE_INDICATORS)

def fetch_fast_tier(url: str, timeout: int = 12) -> Tuple[bool, str, str]:
    """
    Tier 1: Ultra-fast TLS impersonation using curl_cffi.
    Injects cached Cloudflare cookies (cf_clearance) if previously harvested.
    """
    domain = extract_domain(url)
    vault_entry = get_domain_cookies(domain)
    
    session = requests.Session(impersonate="chrome120")
    
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }
    
    # Inject vaulted clearance cookies if available
    if vault_entry:
        cookies = vault_entry.get("cookies", {})
        ua = vault_entry.get("user_agent")
        if ua:
            headers["User-Agent"] = ua
        session.cookies.update(cookies)

    try:
        resp = session.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        if resp.status_code == 200 and not is_blocked_or_challenged(resp.text, resp.status_code):
            md = trafilatura.extract(
                resp.text,
                output_format="markdown",
                include_links=True,
                include_images=False,
                favor_recall=True
            )
            if md and len(md.strip()) > 100:
                set_cached_page(url, md, ttl_hours=48.0)
                return True, md, resp.text
        return False, f"Tier 1 challenge/block (HTTP {resp.status_code})", resp.text
    except Exception as e:
        return False, f"Tier 1 error: {str(e)}", ""

def fetch_stealth_tier(url: str, timeout: int = 25) -> Tuple[bool, str]:
    """
    Tier 2: C++ stealth headless browser using Camoufox.
    Solves Cloudflare Turnstile and saves cf_clearance to the cookie vault.
    """
    domain = extract_domain(url)
    try:
        from camoufox.sync_api import Camoufox
        with _BROWSER_LOCK:
            with Camoufox(headless=True) as browser:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                page.wait_for_timeout(2500)
                
                # Harvest cookies from browser session
                try:
                    cookies = page.context.cookies()
                    cookie_dict = {c["name"]: c["value"] for c in cookies}
                    ua = page.evaluate("navigator.userAgent")
                    if "cf_clearance" in cookie_dict:
                        save_domain_cookies(domain, cookie_dict, ua, ttl_hours=4.0)
                except Exception:
                    pass

                html = page.content()
                md = trafilatura.extract(
                    html,
                    output_format="markdown",
                    include_links=True,
                    include_images=False,
                    favor_recall=True
                )
                if md and len(md.strip()) > 100:
                    set_cached_page(url, md, ttl_hours=48.0)
                    return True, md
                return False, "Camoufox loaded page but extracted content was empty."
    except Exception as e:
        return False, f"Tier 2 Camoufox error: {str(e)}"

def fetch_page(url: str, force_stealth: bool = False, use_cache: bool = True) -> str:
    """
    Fetches a webpage using SQLite cache -> Tier 1 (fast TLS) -> Tier 2 (stealth browser).
    """
    if use_cache and not force_stealth:
        cached = get_cached_page(url)
        if cached:
            return cached

    if not force_stealth:
        success, md, _ = fetch_fast_tier(url)
        if success:
            return md

    success, md = fetch_stealth_tier(url)
    if success:
        return md

    return f"Failed to fetch content from {url}. Reason: {md}"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: fetcher.py <url>")
        sys.exit(1)
    print(fetch_page(sys.argv[1]))
