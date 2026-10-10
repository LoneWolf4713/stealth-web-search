"""
Parallel Search & Intelligent Retrieval Engine.
- Multi-core concurrent fetching with SQLite caching & Cloudflare cookie vault.
- Code-block preserving chunker with recursive sub-chunking & deduplication.
- Fast Neural Reranking via Cross-Encoder (calibrated percentage match).
- Zero-Shot Entity Extraction via GLiNER with confidence thresholding.
- Strict response character ceiling (24k chars) to prevent context token overflow.
- Clean source partitioning: failed/blocked sources moved to compact footer.

Pipeline shape: fetching is I/O-bound and runs fully concurrently across pages.
Neural scoring (reranker + NER) is CPU/GPU-bound and runs ONCE per search across
ALL pages in a single batched model call each, instead of once per page -- see
neural_fast.rerank_pages_batch / extract_entities_batch for why that matters.
"""
import os
import sys
import json
import time
import fcntl
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from searcher import search_web, search_news
from fetcher import fetch_page_detailed, classify_page_issues
from chunker import chunk_markdown_smart, get_table_of_contents, extract_section
from neural_fast import rerank_pages_batch, extract_entities_batch, get_device

# Throttled worker count to prevent memory exhaustion in constrained (WSL) environments
DEFAULT_WORKERS = min(3, os.cpu_count() or 2)
MAX_TOTAL_OUTPUT_CHARS = 25000  # Hard ceiling (25,000 characters)

class MLHardwareLock:
    """Cross-process lock to prevent concurrent ML model runs from exceeding system RAM/VRAM."""
    def __init__(self, lock_path="/tmp/web_search_ml.lock"):
        self.lock_path = lock_path
        self.fd = None

    def __enter__(self):
        try:
            self.fd = open(self.lock_path, "w")
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                self.fd.close()
            except Exception:
                pass

def _fetch_and_chunk_url(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch a single URL and chunk its content. Pure I/O + lightweight CPU work --
    no neural model is touched here, so this is safe to run with high concurrency.
    """
    url = item.get("href")
    title = item.get("title", "")
    snippet = item.get("body", "")
    base = {"title": title, "url": url or "", "snippet": snippet, "chunks": []}

    if not url:
        return {**base, "status": "failed", "error_reason": "Missing URL"}

    try:
        fetch_res = fetch_page_detailed(url)
        if not fetch_res.get("success", False):
            return {**base, "status": "failed", "error_reason": fetch_res.get("error_reason", "Access restricted / blocked")}

        content = fetch_res.get("content", "")
        if not content or len(content.strip()) < 80:
            return {**base, "status": "failed", "error_reason": "Page loaded but main content was empty or unparseable"}

        # Double-lock check: ensure no robot checks, JS-walls, or login walls masquerade as content
        block_issue = classify_page_issues(content[:2500], content[:2500], url=url)
        if block_issue and block_issue != "Page loaded but main content was empty or protected":
            return {**base, "status": "failed", "error_reason": block_issue}

        # Code-block preserving semantic chunking with recursive sub-chunking
        raw_chunks = chunk_markdown_smart(content, max_chunk_words=350)
        if not raw_chunks:
            return {**base, "status": "failed", "error_reason": "No text sections could be extracted"}

        return {**base, "chunks": raw_chunks, "status": "success", "error_reason": None}
    except Exception as e:
        return {**base, "status": "failed", "error_reason": f"Processing error: {str(e)[:80]}"}

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
    1. Keyless search with SQLite cache and URL deduplication.
    2. Parallel multi-threaded fetch with SSL fallback and content classification.
    3. ONE batched neural rerank + NER pass across every fetched page.
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

    # Phase 1: concurrent fetch + chunk (I/O-bound; no ML contention, so this can
    # run fully in parallel without touching MLHardwareLock at all).
    fetched: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_and_chunk_url, item): item for item in search_results}
        for f in as_completed(futures):
            res = f.result()
            orig = futures[f]
            res["perspective"] = orig.get("perspective", "tech")
            fetched.append(res)

    # Phase 2: single batched neural pass across every successfully-fetched page.
    # The lock here only guards actual model inference, not network I/O.
    successful_idx = [i for i, r in enumerate(fetched) if r["status"] == "success" and r.get("chunks")]
    if successful_idx:
        with MLHardwareLock():
            pages_chunks = [fetched[i]["chunks"] for i in successful_idx]
            ranked_per_page = rerank_pages_batch(query, pages_chunks, instruction=instruction, top_k=top_chunks_per_page)

            if extract_ner:
                entity_texts = [
                    "\n".join(c.get("content", "") for c in ranked) if ranked else ""
                    for ranked in ranked_per_page
                ]
                entities_per_page = extract_entities_batch(entity_texts)
            else:
                entities_per_page = [[] for _ in ranked_per_page]

            for pos, i in enumerate(successful_idx):
                fetched[i]["top_passages"] = ranked_per_page[pos]
                fetched[i]["entities"] = entities_per_page[pos]

    detailed_results = []
    for r in fetched:
        r.setdefault("top_passages", [])
        r.setdefault("entities", [])
        r.pop("chunks", None)
        detailed_results.append(r)

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
    """Format intelligent research results into token-dense Markdown with size ceiling & source partitioning."""
    perspective = data.get("perspective", "tech")
    results = data.get("results", [])
    elapsed = data.get("elapsed_seconds", 0)
    device = data.get("device", "cpu").upper()

    successful = [r for r in results if r.get("status") == "success" and r.get("top_passages")]
    failed = [r for r in results if r.get("status") != "success" or not r.get("top_passages")]

    lines = [
        f"# Research Digest: `{data.get('query')}`",
        f"*Perspective: {perspective.upper()} | Sources: {len(successful)}/{len(results)} accessible | Time: {elapsed}s on {device}*\n"
    ]

    # If completely 100% of sources failed, report clear notice
    if not successful:
        lines.append("> [!WARNING]")
        lines.append(f"> **All {len(results)} sources were inaccessible or blocked** (paywalls, login walls, or network errors). No usable content could be extracted.")
        lines.append("")
        if failed:
            lines.append("### Failed / Inaccessible Sources:")
            for r in failed:
                lines.append(f"- [{r.get('title')}]({r.get('url')}): *{r.get('error_reason', 'Access blocked')}*")
        return "\n".join(lines)

    # Extract Global Best Finding (Tavily/Perplexity-style Direct Answer)
    best_passage = None
    best_source = None
    best_score = -999.0
    for r in successful:
        for p in r.get("top_passages", []):
            score = p.get("relevance_score", 0.0)
            if score > best_score:
                best_score = score
                best_passage = p
                best_source = r

    if best_passage and best_score > 0.0:
        excerpt = best_passage.get("content", "").strip()
        paragraphs = [p.strip() for p in excerpt.split("\n\n") if len(p.strip()) > 30]
        lead_text = paragraphs[0] if paragraphs else excerpt
        if len(lead_text) > 420:
            lead_text = lead_text[:410] + "..."
        lines.append(f"> 💡 **Top Finding / Key Evidence** ([{best_source.get('title')}]({best_source.get('url')})):")
        lines.append(f"> {lead_text}\n")

    def render_entry(idx: int, r: Dict[str, Any]) -> str:
        out = [f"### {idx}. [{r.get('title')}]({r.get('url')})"]
        if r.get("snippet"):
            out.append(f"**Overview**: {r.get('snippet')}\n")

        entities = r.get("entities", [])
        if entities:
            ent_str = ", ".join([f"`{e['entity']}` *({e['label']})*" for e in entities[:6]])
            out.append(f"**Key Entities**: {ent_str}\n")

        passages = r.get("top_passages", [])
        if passages:
            out.append("#### Top Relevant Passages:")
            for p in passages:
                match_str = f" [{p.get('match_pct', '')}]" if p.get('match_pct') else ""
                heading = p.get('heading', 'Details')
                out.append(f"##### Section: {heading}{match_str}")
                out.append(p.get("content", "").strip())
                out.append("")
        out.append("\n---\n")
        return "\n".join(out)

    if perspective == "dual":
        tech_items = [r for r in successful if r.get("perspective") == "tech"]
        market_items = [r for r in successful if r.get("perspective") == "market"]

        if tech_items:
            lines.append("## 💻 Open-Source & Technical Architecture\n")
            for idx, r in enumerate(tech_items, 1):
                lines.append(render_entry(idx, r))

        if market_items:
            lines.append("\n## 🏢 Commercial & Product Landscape\n")
            for idx, r in enumerate(market_items, 1):
                lines.append(render_entry(idx, r))
    else:
        for idx, r in enumerate(successful, 1):
            lines.append(render_entry(idx, r))

    # Append failed sources in a clean, small footer
    if failed:
        lines.append("\n### ⚠️ Inaccessible / Blocked Sources:")
        for r in failed:
            lines.append(f"- [{r.get('title')}]({r.get('url')}): *{r.get('error_reason', 'Access blocked')}*")
        lines.append("")

    full_output = "\n".join(lines)

    # Enforce strict response character ceiling
    if len(full_output) > MAX_TOTAL_OUTPUT_CHARS:
        full_output = full_output[:MAX_TOTAL_OUTPUT_CHARS - 320] + (
            "\n\n> [!NOTE]\n"
            f"> *Output capped at {MAX_TOTAL_OUTPUT_CHARS:,} characters (truncated) to protect LLM context window. Showing most relevant passages.*"
        )

    # Estimate token count (chars / 4.2 approx) and prepend to metrics
    est_tokens = int(len(full_output) / 4.2)
    meta_line = f"*Tokens: ~{est_tokens:,} | Chars: {len(full_output):,} | Perspective: {perspective.upper()} | Time: {elapsed}s on {device}*\n"
    # Replace second line with updated token count
    lines_split = full_output.split("\n")
    if len(lines_split) > 1:
        lines_split[1] = meta_line
        full_output = "\n".join(lines_split)

    return full_output

def deep_reasoning_search(
    query: str,
    perspective: str = "tech",
    reasoning_intent: Optional[str] = None,
    max_results: int = 3,
    max_workers: int = DEFAULT_WORKERS
) -> Dict[str, Any]:
    """
    Phase 2 Deep Reasoning Search:
    Executes search -> fetches pages concurrently -> runs the local quantized
    Gemma 2 2B SLM sequentially (single model instance) strictly on ACCESSIBLE
    pages to extract deep semantic intelligence.
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

    # Phase 1: concurrent fetch (I/O-bound; no model lock needed here)
    fetched_pages = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(fetch_page_detailed, item.get("href", "")): item
            for item in search_results
            if item.get("href")
        }
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                fetch_res = future.result()
            except Exception as e:
                fetch_res = {"success": False, "error_reason": f"Fetch error: {str(e)[:80]}"}
            fetched_pages.append((item, fetch_res))

    # Phase 2: sequential SLM reasoning -- single Llama instance, must stay serialized
    detailed_results = []
    with MLHardwareLock():
        for item, fetch_res in fetched_pages:
            url = item.get("href", "")
            title = item.get("title", "")
            perspective_tag = item.get("perspective", "tech")
            snippet = item.get("body", "")

            if not fetch_res.get("success", False):
                detailed_results.append({
                    "title": title,
                    "url": url,
                    "perspective": perspective_tag,
                    "snippet": snippet,
                    "extracted_intelligence": "",
                    "status": "failed",
                    "error_reason": fetch_res.get("error_reason", "Access restricted / blocked")
                })
                continue

            content = fetch_res.get("content", "")
            if not content or len(content.strip()) < 80:
                detailed_results.append({
                    "title": title,
                    "url": url,
                    "perspective": perspective_tag,
                    "snippet": snippet,
                    "extracted_intelligence": "",
                    "status": "failed",
                    "error_reason": "Page content was empty or unparseable"
                })
                continue

            try:
                intelligence = extract_deep_intelligence(content, query, reasoning_intent)
                detailed_results.append({
                    "title": title,
                    "url": url,
                    "perspective": perspective_tag,
                    "snippet": snippet,
                    "extracted_intelligence": intelligence,
                    "status": "success",
                    "error_reason": None
                })
            except Exception as e:
                detailed_results.append({
                    "title": title,
                    "url": url,
                    "perspective": perspective_tag,
                    "snippet": snippet,
                    "extracted_intelligence": "",
                    "status": "failed",
                    "error_reason": f"Reasoning error: {str(e)[:80]}"
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
    """Format deep reasoning results into clean Markdown with size ceiling & source partitioning."""
    perspective = data.get("perspective", "tech")
    results = data.get("results", [])
    elapsed = data.get("elapsed_seconds", 0)

    successful = [r for r in results if r.get("status") == "success" and r.get("extracted_intelligence")]
    failed = [r for r in results if r.get("status") != "success" or not r.get("extracted_intelligence")]

    lines = [
        f"# Deep Reasoning Intelligence: `{data.get('query')}`",
        f"*Perspective: {perspective.upper()} | Sources: {len(successful)}/{len(results)} accessible | Time: {elapsed}s on GPU*\n"
    ]

    if not successful:
        lines.append("> [!WARNING]")
        lines.append(f"> **All {len(results)} sources were inaccessible or blocked**. No intelligence could be extracted.")
        lines.append("")
        if failed:
            lines.append("### Failed / Inaccessible Sources:")
            for r in failed:
                lines.append(f"- [{r.get('title')}]({r.get('url')}): *{r.get('error_reason', 'Access blocked')}*")
        return "\n".join(lines)

    def render_entry(idx: int, r: Dict[str, Any]) -> str:
        out = [f"### {idx}. [{r.get('title')}]({r.get('url')})"]
        if r.get("snippet"):
            out.append(f"**Overview**: {r.get('snippet')}\n")
        out.append("#### Extracted Intelligence & Details:")
        intel = r.get("extracted_intelligence", "").strip()
        # Per-source length safety cap
        if len(intel) > 2500:
            intel = intel[:2450] + "\n[... Intelligence truncated ...]"
        out.append(intel)
        out.append("\n---\n")
        return "\n".join(out)

    if perspective == "dual":
        tech_items = [r for r in successful if r.get("perspective") == "tech"]
        market_items = [r for r in successful if r.get("perspective") == "market"]

        if tech_items:
            lines.append("## 💻 Open-Source & Technical Architecture\n")
            for idx, r in enumerate(tech_items, 1):
                lines.append(render_entry(idx, r))

        if market_items:
            lines.append("\n## 🏢 Commercial & Product Landscape\n")
            for idx, r in enumerate(market_items, 1):
                lines.append(render_entry(idx, r))
    else:
        for idx, r in enumerate(successful, 1):
            lines.append(render_entry(idx, r))

    if failed:
        lines.append("\n### ⚠️ Inaccessible / Blocked Sources:")
        for r in failed:
            lines.append(f"- [{r.get('title')}]({r.get('url')}): *{r.get('error_reason', 'Access blocked')}*")
        lines.append("")

    full_output = "\n".join(lines)

    if len(full_output) > MAX_TOTAL_OUTPUT_CHARS:
        full_output = full_output[:MAX_TOTAL_OUTPUT_CHARS - 320] + (
            "\n\n> [!NOTE]\n"
            f"> *Output capped at {MAX_TOTAL_OUTPUT_CHARS:,} characters (truncated) to protect LLM context window.*"
        )

    est_tokens = int(len(full_output) / 4.2)
    meta_line = f"*Tokens: ~{est_tokens:,} | Chars: {len(full_output):,} | Perspective: {perspective.upper()} | Time: {elapsed}s on GPU*\n"
    lines_split = full_output.split("\n")
    if len(lines_split) > 1:
        lines_split[1] = meta_line
        full_output = "\n".join(lines_split)

    return full_output

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parallel_pipeline.py <query> [max_results]")
        sys.exit(1)
    q = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    res = fast_intelligent_search(q, max_results=n)
    print(format_fast_digest(res))
