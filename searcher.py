"""
High-performance search module using ddgs with multi-region, time-range,
perspective targeting (tech vs. commercial market vs. dual), and SQLite query caching.
"""
import sys
import json
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
from ddgs import DDGS
from vault import get_cached_search, set_cached_search

def _raw_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    timelimit: Optional[str] = None,
    use_cache: bool = True
) -> List[Dict[str, Any]]:
    """Execute raw search with SQLite cache check."""
    if use_cache:
        cached = get_cached_search(query)
        if cached:
            return cached[:max_results]

    try:
        results = DDGS().text(
            query,
            region=region,
            safesearch="moderate",
            timelimit=timelimit,
            max_results=max_results
        )
        res_list = list(results) if results else []
        if res_list and "error" not in res_list[0]:
            set_cached_search(query, res_list, ttl_hours=12.0)
        return res_list
    except Exception as e:
        try:
            results = DDGS().text(query, region="us-en", max_results=max_results)
            res_list = list(results) if results else []
            if res_list:
                set_cached_search(query, res_list, ttl_hours=12.0)
            return res_list
        except Exception as e2:
            return [{"error": f"Search failed: {str(e2)}"}]

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
        market_query = f"{query} commercial companies startups products pricing alternatives"
        results = _raw_search(market_query, max_results=max_results, region=region, timelimit=timelimit, use_cache=use_cache)
        for r in results:
            r["perspective"] = "market"
        return results

    if perspective == "dual":
        half = max(2, max_results // 2)
        tech_q = f"{query} open source github architecture self hosted"
        market_q = f"{query} commercial companies startups products pricing alternatives"

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_tech = executor.submit(_raw_search, tech_q, half, region, timelimit, use_cache)
            fut_mkt = executor.submit(_raw_search, market_q, half, region, timelimit, use_cache)

            tech_results = fut_tech.result()
            market_results = fut_mkt.result()

        for r in tech_results:
            r["perspective"] = "tech"
        for r in market_results:
            r["perspective"] = "market"

        # Combine perspectives: tech first, then market
        combined = tech_results + market_results
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
