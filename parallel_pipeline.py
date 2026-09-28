"""
Parallel Search & Intelligent Retrieval Engine.
- Multi-core concurrent fetching with SQLite caching & Cloudflare cookie vault.
- Code-block preserving chunker (never splits ``` code blocks).
- Fast Neural Reranking via Cross-Encoder (CUDA / CPU auto-sensing).
- Zero-Shot Entity Extraction via GLiNER.
"""
import os
import sys
import json
import time
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from searcher import search_web, search_news
from fetcher import fetch_page
from chunker import chunk_markdown_smart, get_table_of_contents, extract_section
from neural_fast import rerank_chunks, extract_entities, get_device

DEFAULT_WORKERS = max(8, (os.cpu_count() or 4) * 2)

def process_single_url(
    item: Dict[str, Any],
    query: str,
    instruction: Optional[str] = None,
    extract_ner: bool = True,
    top_chunks_per_page: int = 3
) -> Dict[str, Any]:
    """Fetch, chunk, neural-rerank, and extract entities for a single search result."""
    url = item.get("href")
    title = item.get("title", "")
    snippet = item.get("body", "")

    if not url:
        return {
            "title": title,
            "url": "",
            "snippet": snippet,
            "top_passages": [],
            "entities": [],
            "status": "missing_url"
        }

    try:
        content = fetch_page(url)
        if not content or len(content.strip()) < 50:
            return {
                "title": title,
                "url": url,
                "snippet": snippet,
                "top_passages": [],
                "entities": [],
                "status": "empty_content"
            }

        # 1. Code-block preserving semantic chunking
        raw_chunks = chunk_markdown_smart(content, max_chunk_words=450)

        # 2. Neural Cross-Encoder Reranker (CUDA/CPU)
        ranked = rerank_chunks(query, raw_chunks, instruction=instruction, top_k=top_chunks_per_page)

        # 3. GLiNER Entity Extraction
        entities = []
        if extract_ner:
            # Extract entities from the top ranked passages
            combined_top = "\n".join([c.get("content", "") for c in ranked])
            entities = extract_entities(combined_top)

        return {
            "title": title,
            "url": url,
            "snippet": snippet,
            "top_passages": ranked,
            "entities": entities,
            "status": "success"
        }
    except Exception as e:
        return {
            "title": title,
            "url": url,
            "snippet": snippet,
            "top_passages": [],
            "entities": [],
            "status": f"error: {str(e)}"
        }

def fast_intelligent_search(
    query: str,
    max_results: int = 4,
    perspective: str = "tech",
    instruction: Optional[str] = None,
    extract_ner: bool = True,
    top_chunks_per_page: int = 3,
    max_workers: int = DEFAULT_WORKERS
) -> Dict[str, Any]:
    """
    End-to-End Fast Neural Search with Perspective Routing (tech, market, dual):
    1. Keyless search with SQLite cache.
    2. Parallel multi-threaded stealth fetch.
    3. Neural chunk reranking + GLiNER entity extraction.
    """
    t0 = time.perf_counter()
    search_results = search_web(query, max_results=max_results, perspective=perspective)

    if not search_results or "error" in search_results[0]:
        return {
            "query": query,
            "perspective": perspective,
            "device": get_device(),
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
            "results": search_results,
            "total_fetched": 0
        }

    detailed_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_single_url,
                item,
                query,
                instruction,
                extract_ner,
                top_chunks_per_page
            ): item
            for item in search_results
        }
        for f in as_completed(futures):
            res = f.result()
            orig = futures[f]
            res["perspective"] = orig.get("perspective", "tech")
            detailed_results.append(res)

    elapsed = round(time.perf_counter() - t0, 3)
    return {
        "query": query,
        "perspective": perspective,
        "device": get_device(),
        "elapsed_seconds": elapsed,
        "workers_used": max_workers,
        "total_results": len(detailed_results),
        "results": detailed_results
    }

def format_fast_digest(data: Dict[str, Any]) -> str:
    """Format intelligent research results into token-dense, zero-loss Markdown with perspective grouping."""
    perspective = data.get("perspective", "tech")
    lines = [
        f"# Intelligent Research Digest: `{data.get('query')}`",
        f"*Perspective: {perspective.upper()} | Processed in {data.get('elapsed_seconds')}s on {data.get('device', 'cpu').upper()} using {data.get('workers_used')} threads*\n"
    ]

    results = data.get("results", [])

    def render_entry(idx: int, r: Dict[str, Any]):
        out = [f"### {idx}. [{r.get('title')}]({r.get('url')})"]
        out.append(f"**Snippet**: {r.get('snippet')}\n")
        entities = r.get("entities", [])
        if entities:
            ent_str = ", ".join([f"`{e['entity']}` *({e['label']})*" for e in entities[:8]])
            out.append(f"**Identified Entities**: {ent_str}\n")
        passages = r.get("top_passages", [])
        if passages:
            out.append("#### Key Passages & Solutions:")
            for p in passages:
                score_str = f" [Relevance: {p.get('relevance_score')}]" if "relevance_score" in p else ""
                out.append(f"##### Section: {p.get('heading', 'Details')}{score_str}")
                out.append(p.get("content", "").strip())
                out.append("")
        else:
            out.append("*(No passages extracted)*\n")
        out.append("\n---\n")
        return "\n".join(out)

    if perspective == "dual":
        tech_items = [r for r in results if r.get("perspective") == "tech"]
        market_items = [r for r in results if r.get("perspective") == "market"]

        lines.append("## 💻 Open-Source & Technical Architecture Ground Truth\n")
        for idx, r in enumerate(tech_items, 1):
            lines.append(render_entry(idx, r))

        lines.append("\n## 🏢 Commercial & Startup Market Landscape\n")
        for idx, r in enumerate(market_items, 1):
            lines.append(render_entry(idx, r))
    else:
        for idx, r in enumerate(results, 1):
            lines.append(render_entry(idx, r))

    return "\n".join(lines)

def deep_reasoning_search(
    query: str,
    perspective: str = "tech",
    reasoning_intent: Optional[str] = None,
    max_results: int = 3,
    max_workers: int = DEFAULT_WORKERS
) -> Dict[str, Any]:
    """
    Phase 2 Deep Reasoning Search:
    Executes search -> parallel crawls -> uses local quantized Gemma 2 2B SLM
    to extract deep semantic intelligence, obscure entities, and solutions.
    """
    t0 = time.perf_counter()
    from deep_reasoner import extract_deep_intelligence

    search_results = search_web(query, max_results=max_results, perspective=perspective)
    if not search_results or "error" in search_results[0]:
        return {
            "query": query,
            "perspective": perspective,
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
            "results": search_results,
            "total_fetched": 0
        }

    detailed_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(fetch_page, item.get("href", "")): item
            for item in search_results
            if item.get("href")
        }
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            url = item.get("href", "")
            title = item.get("title", "")
            try:
                content = future.result()
                intelligence = extract_deep_intelligence(content, query, reasoning_intent)
                detailed_results.append({
                    "title": title,
                    "url": url,
                    "perspective": item.get("perspective", "tech"),
                    "snippet": item.get("body", ""),
                    "extracted_intelligence": intelligence,
                    "status": "success"
                })
            except Exception as e:
                detailed_results.append({
                    "title": title,
                    "url": url,
                    "perspective": item.get("perspective", "tech"),
                    "snippet": item.get("body", ""),
                    "extracted_intelligence": f"Error during reasoning: {str(e)}",
                    "status": "error"
                })

    elapsed = round(time.perf_counter() - t0, 3)
    return {
        "query": query,
        "perspective": perspective,
        "reasoning_intent": reasoning_intent or query,
        "elapsed_seconds": elapsed,
        "total_results": len(detailed_results),
        "results": detailed_results
    }

def format_deep_digest(data: Dict[str, Any]) -> str:
    """Format deep reasoning results into clean, high-utility Markdown with perspective grouping."""
    perspective = data.get("perspective", "tech")
    lines = [
        f"# Deep Reasoning Intelligence Digest: `{data.get('query')}`",
        f"*Perspective: {perspective.upper()} | Extracted using Local Gemma 2 SLM in {data.get('elapsed_seconds')}s across {data.get('total_results', 0)} sources*\n"
    ]

    results = data.get("results", [])

    def render_entry(idx: int, r: Dict[str, Any]):
        out = [f"### {idx}. [{r.get('title')}]({r.get('url')})"]
        out.append(f"**Source Summary**: {r.get('snippet')}\n")
        out.append("#### Extracted Intelligence & Actionable Details:")
        out.append(r.get("extracted_intelligence", "No intelligence extracted."))
        out.append("\n---\n")
        return "\n".join(out)

    if perspective == "dual":
        tech_items = [r for r in results if r.get("perspective") == "tech"]
        market_items = [r for r in results if r.get("perspective") == "market"]

        lines.append("## 💻 Open-Source & Technical Architecture Ground Truth\n")
        for idx, r in enumerate(tech_items, 1):
            lines.append(render_entry(idx, r))

        lines.append("\n## 🏢 Commercial & Startup Market Landscape\n")
        for idx, r in enumerate(market_items, 1):
            lines.append(render_entry(idx, r))
    else:
        for idx, r in enumerate(results, 1):
            lines.append(render_entry(idx, r))

    return "\n".join(lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parallel_pipeline.py <query> [max_results]")
        sys.exit(1)
    q = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    res = fast_intelligent_search(q, max_results=n)
    print(format_fast_digest(res))

