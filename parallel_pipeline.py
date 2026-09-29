"""
Parallel Search & Intelligent Retrieval Engine.
- Multi-core concurrent fetching with SQLite caching & Cloudflare cookie vault.
- Code-block preserving chunker with recursive sub-chunking & deduplication.
- Fast Neural Reranking via Cross-Encoder (calibrated percentage match).
- Zero-Shot Entity Extraction via GLiNER with confidence thresholding.
- Strict response character ceiling (24k chars) to prevent context token overflow.
- Clean source partitioning: failed/blocked sources moved to compact footer.
"""
import os
import sys
import json
import time
import fcntl
import gc
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from searcher import search_web, search_news
from fetcher import fetch_page_detailed
from chunker import chunk_markdown_smart, get_table_of_contents, extract_section
from neural_fast import rerank_chunks, extract_entities, get_device

# Throttled worker count to prevent memory exhaustion in constrained (WSL) environments
DEFAULT_WORKERS = min(3, os.cpu_count() or 2)
MAX_TOTAL_OUTPUT_CHARS = 24000  # Hard ceiling (~5,000 tokens)

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

def _cleanup_memory():
    """Explicitly clean up garbage and release CUDA VRAM back to OS."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

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
            "status": "failed",
            "error_reason": "Missing URL"
        }

    try:
        fetch_res = fetch_page_detailed(url)
        if not fetch_res.get("success", False):
            return {
                "title": title,
                "url": url,
                "snippet": snippet,
                "top_passages": [],
                "entities": [],
                "status": "failed",
                "error_reason": fetch_res.get("error_reason", "Access restricted / blocked")
            }

        content = fetch_res.get("content", "")
        if not content or len(content.strip()) < 80:
            return {
                "title": title,
                "url": url,
                "snippet": snippet,
                "top_passages": [],
                "entities": [],
                "status": "failed",
                "error_reason": "Page loaded but main content was empty or unparseable"
            }

        # 1. Code-block preserving semantic chunking with recursive sub-chunking
        raw_chunks = chunk_markdown_smart(content, max_chunk_words=350)
        if not raw_chunks:
            return {
                "title": title,
                "url": url,
                "snippet": snippet,
                "top_passages": [],
                "entities": [],
                "status": "failed",
                "error_reason": "No text sections could be extracted"
            }

        # 2. Neural Cross-Encoder Reranker (calibrated percentage match)
        ranked = rerank_chunks(query, raw_chunks, instruction=instruction, top_k=top_chunks_per_page)

        # 3. GLiNER Entity Extraction (only on top ranked passages)
        entities = []
        if extract_ner and ranked:
            combined_top = "\n".join([c.get("content", "") for c in ranked])
            entities = extract_entities(combined_top)

        return {
            "title": title,
            "url": url,
            "snippet": snippet,
            "top_passages": ranked,
            "entities": entities,
            "status": "success",
            "error_reason": None
        }
    except Exception as e:
        return {
            "title": title,
            "url": url,
            "snippet": snippet,
            "top_passages": [],
            "entities": [],
            "status": "failed",
            "error_reason": f"Processing error: {str(e)[:80]}"
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
    1. Keyless search with SQLite cache and URL deduplication.
    2. Parallel multi-threaded fetch with SSL fallback and content classification.
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
    with MLHardwareLock():
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
        _cleanup_memory()

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
        full_output = full_output[:MAX_TOTAL_OUTPUT_CHARS - 300] + (
            "\n\n> [!NOTE]\n"
            f"> *Output capped at {MAX_TOTAL_OUTPUT_CHARS:,} characters to protect LLM context window. Showing most relevant passages.*"
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
    Executes search -> fetches pages -> runs local quantized Gemma 2 2B SLM
    strictly on ACCESSIBLE pages to extract deep semantic intelligence.
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
    with MLHardwareLock():
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_item = {
                executor.submit(fetch_page_detailed, item.get("href", "")): item
                for item in search_results
                if item.get("href")
            }
            for future in as_completed(future_to_item):
                item = future_to_item[future]
                url = item.get("href", "")
                title = item.get("title", "")
                perspective_tag = item.get("perspective", "tech")
                snippet = item.get("body", "")

                try:
                    fetch_res = future.result()
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

                    # Only run SLM reasoning on accessible, real content
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
        _cleanup_memory()

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
        full_output = full_output[:MAX_TOTAL_OUTPUT_CHARS - 300] + (
            "\n\n> [!NOTE]\n"
            f"> *Output capped at {MAX_TOTAL_OUTPUT_CHARS:,} characters to protect LLM context window.*"
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
