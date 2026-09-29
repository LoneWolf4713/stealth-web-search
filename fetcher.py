"""
Webpage Fetcher with Multi-Tier Resolution, SSL Fallback, and Anti-Bot/Paywall Classification.
Tier 1: Fast TLS impersonation via curl_cffi with automatic SSL fallback (verify=False) and HTTP fallback.
Tier 2: Headless browser via Camoufox with ignore_https_errors=True to solve complex pages and challenges.
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

WALL_AND_BLOCK_PATTERNS = [
    (re.compile(r"JavaScript is disabled|Please enable JavaScript|You need to enable JavaScript", re.I), "JavaScript required"),
    (re.compile(r"Join for free to read|Sign in to ResearchGate|Log in to access|Sign in or create an account", re.I), "Login wall / Membership required"),
    (re.compile(r"Access Denied|403 Forbidden|Access to this page is restricted", re.I), "Access denied by host"),
    (re.compile(r"Subscribe to view|Purchase this article|Institutional access|Purchase PDF", re.I), "Paywall / Subscription required"),
    (re.compile(r"cf-chl-widget|challenges\.cloudflare\.com|Checking your browser before accessing", re.I), "Anti-bot challenge active"),
    (re.compile(r"%PDF-", re.I), "PDF binary document (direct link)"),
]

def classify_page_issues(html: str, text: str) -> Optional[str]:
    """Classify why a page returned empty, blocked, or unusable text."""
    combined = (html[:4000] + " " + text[:2000]).strip()
    for pattern, description in WALL_AND_BLOCK_PATTERNS:
        if pattern.search(combined):
            return description
    if len(text.strip()) < 80:
        return "Page loaded but main content was empty or protected"
    return None

def is_blocked_or_challenged(html: str, status_code: int) -> bool:
    """Detect if response is an anti-bot challenge or block."""
    if status_code in (403, 429, 503):
        return True
    if not html:
        return True
    return any(ind in html for ind in CLOUDFLARE_INDICATORS)

def fetch_fast_tier(url: str, timeout: int = 12) -> Tuple[bool, str, str, Optional[str]]:
    """
    Tier 1: TLS impersonation using curl_cffi.
    Includes automated SSL certificate fallback and HTTP downgrade fallback.
    Returns: (success: bool, markdown: str, raw_html: str, error_reason: Optional[str])
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
    
    if vault_entry:
        cookies = vault_entry.get("cookies", {})
        ua = vault_entry.get("user_agent")
        if ua:
            headers["User-Agent"] = ua
        session.cookies.update(cookies)

    resp = None
    try:
        resp = session.get(url, timeout=timeout, headers=headers, allow_redirects=True, verify=True)
    except Exception as ssl_err:
        # Fallback 1: Retry with verify=False (solves unknown certificate issuer e.g. iiitd.ac.in)
        try:
            resp = session.get(url, timeout=timeout, headers=headers, allow_redirects=True, verify=False)
        except Exception:
            # Fallback 2: Retry with plain HTTP if HTTPS connection failed
            if url.startswith("https://"):
                try:
                    http_url = "http://" + url[8:]
                    resp = session.get(http_url, timeout=timeout, headers=headers, allow_redirects=True, verify=False)
                except Exception as e2:
                    return False, "", "", f"SSL / Connection error: {str(ssl_err)}"
            else:
                return False, "", "", f"Connection error: {str(ssl_err)}"

    if not resp:
        return False, "", "", "No response received"

    if resp.status_code == 200 and not is_blocked_or_challenged(resp.text, resp.status_code):
        md = trafilatura.extract(
            resp.text,
            output_format="markdown",
            include_links=True,
            include_images=False,
            favor_recall=True
        )
        if md and len(md.strip()) > 80:
            issue = classify_page_issues(resp.text, md)
            if issue and issue != "Page loaded but main content was empty or protected":
                return False, "", resp.text, issue
            set_cached_page(url, md, ttl_hours=48.0)
            return True, md, resp.text, None

    issue = classify_page_issues(resp.text, "")
    reason = issue if issue else f"HTTP {resp.status_code}"
    return False, "", resp.text, reason

def fetch_stealth_tier(url: str, timeout: int = 25) -> Tuple[bool, str, Optional[str]]:
    """
    Tier 2: Headless browser using Camoufox with ignore_https_errors=True.
    Solves complex challenges and invalid certificate chains.
    Returns: (success: bool, markdown: str, error_reason: Optional[str])
    """
    domain = extract_domain(url)
    try:
        from camoufox.sync_api import Camoufox
        with _BROWSER_LOCK:
            with Camoufox(headless=True) as browser:
                # ignore_https_errors prevents SEC_ERROR_UNKNOWN_ISSUER on self-signed / incomplete chains
                page = browser.new_page(ignore_https_errors=True)
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
                if md and len(md.strip()) > 80:
                    issue = classify_page_issues(html, md)
                    if issue and issue != "Page loaded but main content was empty or protected":
                        return False, "", issue
                    set_cached_page(url, md, ttl_hours=48.0)
                    return True, md, None
                
                issue = classify_page_issues(html, md or "")
                return False, "", issue or "Page loaded but content was empty or protected"
    except Exception as e:
        err_msg = str(e)
        if "SEC_ERROR" in err_msg:
            return False, "", "SSL Certificate Error (Untrusted Issuer)"
        if "Timeout" in err_msg:
            return False, "", "Page navigation timed out (server unreachable)"
        return False, "", f"Browser error: {err_msg[:80]}"

def fetch_page_detailed(url: str, force_stealth: bool = False, use_cache: bool = True) -> Dict[str, Any]:
    """
    Fetches a webpage and returns detailed status information.
    Guarantees 'content' is NEVER populated with an error string!
    """
    if use_cache and not force_stealth:
        cached = get_cached_page(url)
        if cached:
            return {
                "success": True,
                "content": cached,
                "error_reason": None,
                "cached": True
            }

    if not force_stealth:
        success, md, _, reason = fetch_fast_tier(url)
        if success:
            return {
                "success": True,
                "content": md,
                "error_reason": None,
                "cached": False
            }

    success, md, reason = fetch_stealth_tier(url)
    if success:
        return {
            "success": True,
            "content": md,
            "error_reason": None,
            "cached": False
        }

    return {
        "success": False,
        "content": "",
        "error_reason": reason or "Failed to retrieve accessible content",
        "cached": False
    }

def fetch_page(url: str, force_stealth: bool = False, use_cache: bool = True) -> str:
    """Convenience string fetcher for backward compatibility."""
    res = fetch_page_detailed(url, force_stealth=force_stealth, use_cache=use_cache)
    if res["success"]:
        return res["content"]
    return f"Failed to retrieve {url}: {res['error_reason']}"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: fetcher.py <url>")
        sys.exit(1)
    print(fetch_page(sys.argv[1]))
