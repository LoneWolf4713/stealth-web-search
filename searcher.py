"""
High-performance search module using ddgs with multi-region, time-range,
perspective targeting (tech vs. commercial market vs. dual), URL deduplication,
snippet sanitization, and SQLite query caching.
"""
import re
import sys
import json
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
from ddgs import DDGS
from vault import get_cached_search, set_cached_search

def _canonical_url(url: str) -> str:
    """Normalize URL by stripping tracking parameters and trailing slashes."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        # Strip tracking queries
        query_params = [
            (k, v) for k, v in parse_qsl(parsed.query)
            if not k.startswith("utm_") and k not in ("ref", "fbclid", "gclid", "trk")
        ]
        clean_path = parsed.path.rstrip('/')
        clean_query = urlencode(query_params)
        normalized = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path,
            parsed.params,
            clean_query,
            ""  # strip fragments
        ))
        return normalized
    except Exception:
        return url.rstrip('/')

def _clean_snippet(text: str) -> str:
    """Sanitize snippet text, removing fused sitelinks, excess whitespace, and raw HTML."""
    if not text:
        return ""
    # Remove HTML tags if present
    clean = re.sub(r'<[^>]+>', ' ', text)
    # Collapse duplicate whitespace / newlines
    clean = re.sub(r'[\r\n\t]+', ' ', clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()
    return clean

def _raw_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    timelimit: Optional[str] = None,
    use_cache: bool = True
) -> List[Dict[str, Any]]:
    """Execute raw search with URL deduplication, snippet cleaning, and SQLite cache."""
    if use_cache:
        cached = get_cached_search(query)
        if cached:
            return cached[:max_results]

    raw_items = []
    try:
        results = DDGS().text(
            query,
            region=region,
            safesearch="moderate",
            timelimit=timelimit,
            max_results=max_results * 2  # fetch slightly more to allow for deduplication
        )
        raw_items = list(results) if results else []
    except Exception:
        try:
            results = DDGS().text(query, region="us-en", max_results=max_results * 2)
            raw_items = list(results) if results else []
        except Exception as e2:
            return [{"error": f"Search failed: {str(e2)}"}]

    # Deduplicate by canonical URL and clean snippets
    seen_urls = set()
    cleaned_results = []

    for item in raw_items:
        if "error" in item:
            continue
        href = item.get("href", "")
        canon = _canonical_url(href)
        if not canon or canon in seen_urls:
            continue
        seen_urls.add(canon)

        cleaned_results.append({
            "title": _clean_snippet(item.get("title", "")),
            "href": href,
            "body": _clean_snippet(item.get("body", ""))
        })
        if len(cleaned_results) >= max_results:
            break

    if cleaned_results and use_cache:
        set_cached_search(query, cleaned_results, ttl_hours=12.0)

    return cleaned_results

def search_web(
    query: str,
    max_results: int = 5,
    perspective: str = "tech",
    region: str = "wt-wt",
    timelimit: Optional[str] = None,
    use_cache: bool = True
) -> List[Dict[str, Any]]:
    """
    Search web with perspective routing:
    - 'tech' (default): Focuses on implementation, open source, documentation, architecture.
    - 'market': Focuses on commercial startups, products, pricing, and industry landscape.
    - 'dual': Executes concurrent parallel searches across both perspectives and tags results.
    """
    perspective = (perspective or "tech").lower()

    if perspective == "market":
        market_query = f"{query} products software tools pricing alternatives"
        results = _raw_search(market_query, max_results=max_results, region=region, timelimit=timelimit, use_cache=use_cache)
        for r in results:
            r["perspective"] = "market"
        return results

    if perspective == "dual":
        half = max(2, max_results // 2)
        tech_q = f"{query} open source github architecture library"
        market_q = f"{query} products tools software pricing"

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_tech = executor.submit(_raw_search, tech_q, half, region, timelimit, use_cache)
            fut_mkt = executor.submit(_raw_search, market_q, half, region, timelimit, use_cache)

            tech_results = fut_tech.result()
            market_results = fut_mkt.result()

        seen_urls = set()
        combined = []

        for r in tech_results:
            canon = _canonical_url(r.get("href", ""))
            seen_urls.add(canon)
            r["perspective"] = "tech"
            combined.append(r)

        for r in market_results:
            canon = _canonical_url(r.get("href", ""))
            if canon in seen_urls:
                continue
            seen_urls.add(canon)
            r["perspective"] = "market"
            combined.append(r)

        return combined

    # Default 'tech' perspective
    results = _raw_search(query, max_results=max_results, region=region, timelimit=timelimit, use_cache=use_cache)
    for r in results:
        r["perspective"] = "tech"
    return results

def search_news(query: str, max_results: int = 5, use_cache: bool = True) -> List[Dict[str, Any]]:
    """Search latest news for real-time / breaking events."""
    if use_cache:
        cached = get_cached_search(f"news::{query}")
        if cached:
            return cached[:max_results]

    try:
        results = DDGS().news(query, max_results=max_results)
        res_list = list(results) if results else []
        if res_list and "error" not in res_list[0]:
            set_cached_search(f"news::{query}", res_list, ttl_hours=6.0)
        return res_list
    except Exception as e:
        return [{"error": f"News search failed: {str(e)}"}]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: searcher.py <query> [max_results] [perspective: tech|market|dual]")
        sys.exit(1)
    q = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    p = sys.argv[3] if len(sys.argv) > 3 else "tech"
    res = search_web(q, max_results=n, perspective=p)
    print(json.dumps(res, indent=2))
