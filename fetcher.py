"""
Webpage Fetcher with Multi-Tier Resolution, PDF-to-Text Parsing, SSL Fallback,
Wayback Archive Fallback, and Anti-Bot/Paywall Classification.
Tier 1: Fast TLS impersonation via curl_cffi with automatic SSL fallback (verify=False).
Tier 2: Headless browser via Camoufox with ignore_https_errors=True to solve complex pages & JS-walls.
Tier 3: Internet Archive Wayback Machine fallback for SSL/connection failure recovery.
"""
import io
import re
import sys
import logging
import subprocess
import threading
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse
from curl_cffi import requests
import trafilatura
import pypdf

from vault import (
    get_cached_page,
    set_cached_page,
    get_domain_cookies,
    save_domain_cookies,
    extract_domain
)

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
    (re.compile(r"JavaScript is disabled|Please enable JavaScript|You need to enable JavaScript|requires JavaScript|turn on JavaScript|enable JavaScript to run|unblock scripts|JavaScript seems to be disabled", re.I), "JavaScript required"),
    (re.compile(r"Join for free to read|Sign in to ResearchGate|Log in to access|Sign in or create an account|Discover the world's research|researchgate\.net/login", re.I), "Blocked by ResearchGate login wall"),
    (re.compile(r"Are you a robot\?|Robot Check|verify you are human|Please verify you are a human|solve this captcha|human verification|Security check to access", re.I), "Bot check / Human verification required"),
    (re.compile(r"Access Denied|403 Forbidden|Access to this page is restricted", re.I), "Access denied by host"),
    (re.compile(r"Subscribe to view|Purchase this article|Institutional access|Purchase PDF", re.I), "Paywall / Subscription required"),
    (re.compile(r"cf-chl-widget|challenges\.cloudflare\.com|Checking your browser before accessing", re.I), "Anti-bot challenge active"),
]

def clean_web_content(markdown: str) -> str:
    """Strip academic boilerplate widgets and blank fields from extracted markdown."""
    if not markdown:
        return ""
    # Strip recommendation sections like "Similar content being viewed by others"
    text = re.sub(
        r'(?i)^#{1,6}\s*(Similar content|Related research|People also read|Recommended articles|More like this|Citations \(\d+\)|References \(\d+\))[\s\S]*?(?=^#{1,6}\s|\Z)',
        '',
        markdown,
        flags=re.MULTILINE
    )
    # Strip blank academic dates/fields (Received:, Accepted:, Published:, Revised:)
    text = re.sub(r'(?i)\b(Received|Accepted|Published|Revised):\s*(\n|$)', '', text)
    # Strip common JS fallback boilerplate
    text = re.sub(r'(?i)This site requires JavaScript to run correctly\..*?(?:unblock scripts|\Z)', '', text, flags=re.DOTALL)
    # Strip repetitive empty lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def extract_pdf_to_text(pdf_bytes: bytes, url: str = "") -> Tuple[bool, str, Optional[str]]:
    """Extract readable text from PDF bytes. Never returns raw bytes."""
    if not pdf_bytes or len(pdf_bytes) < 10:
        return False, "", "Empty PDF document"
    
    # 1. Try pypdf
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for idx, page in enumerate(reader.pages, 1):
            t = page.extract_text() or ""
            t_clean = t.strip()
            if t_clean:
                pages_text.append(f"## Page {idx}\n\n{t_clean}")
        if pages_text:
            filename = url.split("?")[0].split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ").title()
            title = filename or "Document"
            full_text = f"# PDF Document: {title}\n\n" + "\n\n---\n\n".join(pages_text)
            return True, full_text, None
    except Exception as e:
        logger.debug(f"pypdf extraction failed for {url}: {e}")

    # 2. Fallback to /usr/bin/pdftotext CLI
    try:
        proc = subprocess.run(
            ["/usr/bin/pdftotext", "-", "-"],
            input=pdf_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )
        if proc.returncode == 0 and proc.stdout:
            txt = proc.stdout.decode("utf-8", errors="replace").strip()
            if len(txt) > 80:
                filename = url.split("?")[0].split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ").title()
                title = filename or "Document"
                return True, f"# PDF Document: {title}\n\n{txt}", None
    except Exception as e:
        logger.debug(f"pdftotext CLI failed: {e}")

    return False, "", "Scanned PDF document (contains no selectable text or OCR required)"

def classify_page_issues(html: str, text: str, url: str = "") -> Optional[str]:
    """Classify why a page returned empty, blocked, or unusable text."""
    clean_html = re.sub(r'(?is)<noscript[^>]*>.*?</noscript>', '', html or "")
    text_stripped = (text or "").strip()
    
    # If text is substantial (>= 250 chars), only flag if the extracted text itself is a block screen
    if len(text_stripped) >= 250:
        for pattern, description in WALL_AND_BLOCK_PATTERNS:
            if len(text_stripped) < 500 and pattern.search(text_stripped[:500]):
                return description
    else:
        combined = (clean_html[:4000] + " " + text_stripped[:2000]).strip()
        for pattern, description in WALL_AND_BLOCK_PATTERNS:
            if pattern.search(combined):
                return description

    if "researchgate.net" in (url or "").lower() and len(text_stripped) < 150:
        return "Blocked by ResearchGate login wall"
    if len(text_stripped) < 80:
        return "Page loaded but main content was empty or protected"
    return None

def is_blocked_or_challenged(html: str, status_code: int) -> bool:
    """Detect if response is an anti-bot challenge or block."""
    if status_code in (403, 429, 503):
        return True
    if not html:
        return True
    return any(ind in html for ind in CLOUDFLARE_INDICATORS)

def fetch_wayback_archive(url: str, timeout: int = 12) -> Tuple[bool, str, Optional[str]]:
    """Fetch closest archived copy from the Wayback Machine when direct access suffers SSL/connection failure."""
    try:
        archive_url = f"https://web.archive.org/web/{url}"
        session = requests.Session(impersonate="chrome120")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = session.get(archive_url, timeout=timeout, headers=headers, verify=False)
        if resp.status_code == 200 and resp.text:
            md = trafilatura.extract(
                resp.text,
                output_format="markdown",
                include_links=True,
                include_images=False,
                favor_recall=True
            )
            if md and len(md.strip()) > 80:
                cleaned = clean_web_content(md)
                return True, f"> *[Archived snapshot from Wayback Machine]*\n\n{cleaned}", None
    except Exception as e:
        logger.debug(f"Wayback fallback error for {url}: {e}")
    return False, "", "Wayback archive copy not available"

def fetch_fast_tier(
    url: str,
    timeout: int = 12,
    skip_tls_verify: bool = True
) -> Tuple[bool, str, bytes, Optional[str]]:
    """
    Tier 1: TLS impersonation using curl_cffi.
    Includes automated SSL certificate fallback and HTTP downgrade fallback.
    Returns: (success: bool, markdown: str, raw_bytes: bytes, error_reason: Optional[str])
    """
    domain = extract_domain(url)
    vault_entry = get_domain_cookies(domain)
    
    session = requests.Session(impersonate="chrome120")
    
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
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
    verify_flag = not skip_tls_verify
    try:
        resp = session.get(url, timeout=timeout, headers=headers, allow_redirects=True, verify=verify_flag)
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
                    return False, "", b"", f"SSL / Connection error: {str(ssl_err)}"
            else:
                return False, "", b"", f"Connection error: {str(ssl_err)}"

    if not resp:
        return False, "", b"", "No response received"

    # Detect PDF document (Content-Type or magic bytes %PDF-)
    is_pdf = (
        resp.status_code == 200 and
        (
            resp.content.startswith(b"%PDF-") or
            "application/pdf" in resp.headers.get("content-type", "").lower() or
            (url.lower().split("?")[0].endswith(".pdf") and not resp.content.startswith(b"<!DOC") and not resp.content.startswith(b"<html"))
        )
    )
    if is_pdf:
        ok, pdf_text, err = extract_pdf_to_text(resp.content, url)
        if ok:
            cleaned = clean_web_content(pdf_text)
            set_cached_page(url, cleaned, ttl_hours=48.0)
            return True, cleaned, resp.content, None
        return False, "", resp.content, err or "Failed to parse PDF document"

    if resp.status_code == 404:
        return False, "", resp.content, "HTTP 404 (Not Found)"
    if resp.status_code == 410:
        return False, "", resp.content, "HTTP 410 (Gone)"

    if resp.status_code == 200 and not is_blocked_or_challenged(resp.text, resp.status_code):
        md = trafilatura.extract(
            resp.text,
            output_format="markdown",
            include_links=True,
            include_images=False,
            favor_recall=True
        )
        if md and len(md.strip()) > 80:
            issue = classify_page_issues(resp.text, md, url=url)
            if issue and issue != "Page loaded but main content was empty or protected":
                return False, "", resp.content, issue
            cleaned = clean_web_content(md)
            set_cached_page(url, cleaned, ttl_hours=48.0)
            return True, cleaned, resp.content, None

    issue = classify_page_issues(resp.text, "", url=url)
    reason = issue if issue else f"HTTP {resp.status_code}"
    return False, "", resp.content, reason

def fetch_stealth_tier(url: str, timeout: int = 25) -> Tuple[bool, str, Optional[str]]:
    """
    Tier 2: Headless browser using Camoufox with ignore_https_errors=True.
    Solves complex challenges, SPAs, and invalid certificate chains.
    Returns: (success: bool, markdown: str, error_reason: Optional[str])
    """
    domain = extract_domain(url)
    try:
        from camoufox.sync_api import Camoufox
        with _BROWSER_LOCK:
            with Camoufox(headless=True) as browser:
                page = browser.new_page(ignore_https_errors=True)
                nav_resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                if nav_resp and nav_resp.status == 404:
                    return False, "", "HTTP 404 (Not Found)"
                if nav_resp and nav_resp.status == 410:
                    return False, "", "HTTP 410 (Gone)"
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
                
                # Check if browser loaded a PDF directly
                if html.startswith("%PDF-") or 'type="application/pdf"' in html:
                    pass

                md = trafilatura.extract(
                    html,
                    output_format="markdown",
                    include_links=True,
                    include_images=False,
                    favor_recall=True
                )
                if not md or len(md.strip()) < 80:
                    try:
                        body_text = page.evaluate("() => document.body ? document.body.innerText : ''")
                        if body_text and len(body_text.strip()) > 80:
                            md = body_text.strip()
                    except Exception:
                        pass

                if md and len(md.strip()) > 80:
                    cleaned = clean_web_content(md)
                    issue = classify_page_issues(html, cleaned, url=url)
                    if issue and issue != "Page loaded but main content was empty or protected":
                        return False, "", issue
                    set_cached_page(url, cleaned, ttl_hours=48.0)
                    return True, cleaned, None
                
                issue = classify_page_issues(html, md or "", url=url)
                return False, "", issue or "Page loaded but content was empty or protected"
    except Exception as e:
        err_msg = str(e)
        if "SEC_ERROR" in err_msg:
            return False, "", "SSL Certificate Error (Untrusted Issuer)"
        if "Timeout" in err_msg:
            return False, "", "Page navigation timed out (server unreachable)"
        return False, "", f"Browser error: {err_msg[:80]}"

def fetch_page_detailed(
    url: str,
    force_stealth: bool = False,
    use_cache: bool = True,
    skip_tls_verify: bool = True
) -> Dict[str, Any]:
    """
    Fetches a webpage with multi-tier resolution:
    1. Fast curl_cffi with PDF parsing & SSL fallback.
    2. Auto-escalation to Camoufox stealth browser on JS walls or bot challenges.
    3. Auto-fallback to Wayback Machine archive copy on SSL/connection dropouts.
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

    reason = None
    if not force_stealth:
        success, md, _, reason = fetch_fast_tier(url, skip_tls_verify=skip_tls_verify)
        if success:
            return {
                "success": True,
                "content": md,
                "error_reason": None,
                "cached": False
            }
        # If Tier 1 hit a JS requirement or bot challenge, auto-escalate to Tier 2 (browser)
        if reason and any(k in reason.lower() for k in ("javascript", "robot", "human verification", "anti-bot", "challenge")):
            logger.info(f"Auto-escalating {url} to browser mode due to: {reason}")

    # Tier 2: Camoufox stealth browser
    success, md, reason = fetch_stealth_tier(url)
    if success:
        return {
            "success": True,
            "content": md,
            "error_reason": None,
            "cached": False
        }

    # Tier 3: Wayback Archive Fallback (for SSL or connection dropouts e.g. IIIT-Delhi)
    if reason and any(k in reason.lower() for k in ("ssl", "connection", "certificate", "unreachable", "timed out")):
        logger.info(f"Attempting Wayback Machine archive fallback for {url} due to: {reason}")
        arch_ok, arch_md, _ = fetch_wayback_archive(url)
        if arch_ok:
            return {
                "success": True,
                "content": arch_md,
                "error_reason": None,
                "cached": False,
                "archived": True
            }

    # Explicit handling for ResearchGate
    if "researchgate.net" in url.lower() and (not reason or "empty" in reason.lower()):
        reason = "Blocked by ResearchGate login wall"

    return {
        "success": False,
        "content": "",
        "error_reason": reason or "Failed to retrieve accessible content",
        "cached": False
    }

def fetch_page(
    url: str,
    force_stealth: bool = False,
    use_cache: bool = True,
    skip_tls_verify: bool = True
) -> str:
    """Convenience string fetcher with 25k character hard cap and (truncated) notice."""
    res = fetch_page_detailed(url, force_stealth=force_stealth, use_cache=use_cache, skip_tls_verify=skip_tls_verify)
    if res["success"]:
        content = res["content"]
        if len(content) > 25000:
            content = content[:24600] + (
                "\n\n> [!NOTE]\n"
                f"> *Webpage content capped at 25,000 characters (truncated). Use `get_toc('{url}')` and `read_section` to read specific sections.*"
            )
        return content
    return f"Failed to retrieve {url}: {res['error_reason']}"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: fetcher.py <url>")
        sys.exit(1)
    print(fetch_page(sys.argv[1]))
