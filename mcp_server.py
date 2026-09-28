#!/usr/bin/env python3
"""
Fast / MCPServer for Stealth Web Search & Neural Retrieval.
Exposes tools natively to Claude Code and Google Antigravity.
Compatible with MCP SDK v1 and v2.
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer("stealth-web-retriever")
except (ImportError, ModuleNotFoundError):
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("stealth-web-retriever")

from searcher import search_web, search_news
from fetcher import fetch_page
from chunker import get_table_of_contents, extract_section
from parallel_pipeline import fast_intelligent_search, format_fast_digest

@mcp.tool()
def search(query: str, max_results: int = 5, perspective: str = "tech") -> str:
    """
    Search the live web without API keys or rate limits (cached via SQLite).
    perspective options:
      - 'tech' (default): Open-source repositories, developer architecture, implementation details.
      - 'market': Commercial startups, consumer products, pricing, competitors.
      - 'dual': Concurrent balanced split across technical code bases AND commercial landscape.
    Returns structured results including titles, links, perspective tags, and snippets.
    """
    results = search_web(query, max_results=max_results, perspective=perspective)
    if not results:
        return "No results found."
    
    out = []
    for idx, r in enumerate(results, 1):
        p_tag = f" [{r.get('perspective', 'tech').upper()}]" if perspective == "dual" else ""
        out.append(f"{idx}. [{r.get('title')}]({r.get('href')}){p_tag}")
        out.append(f"   {r.get('body')}\n")
    return "\n".join(out)

@mcp.tool()
def fetch(url: str, force_stealth: bool = False) -> str:
    """
    Fetch any webpage bypassing Cloudflare, Akamai, or anti-bot protections.
    Converts HTML into clean, token-efficient Markdown with cookie vault caching.
    """
    return fetch_page(url, force_stealth=force_stealth)

@mcp.tool()
def get_toc(url: str) -> str:
    """
    Get the Table of Contents outline of a large documentation page (~150 tokens).
    Use this to see all section headings before reading full content.
    """
    content = fetch_page(url)
    return get_table_of_contents(content)

@mcp.tool()
def read_section(url: str, section_name_or_index: str) -> str:
    """
    Read only a specific section from a webpage or documentation by heading name or index.
    Saves context tokens by extracting only the requested sub-topic.
    """
    content = fetch_page(url)
    return extract_section(content, section_name_or_index)

@mcp.tool()
def fast_neural_search(query: str, instruction: str = "", num_pages: int = 4, perspective: str = "tech") -> str:
    """
    Fast Neural Search Mode:
    Searches web -> parallel crawls pages -> preserves code blocks -> neural cross-encoder
    reranks most relevant passages -> GLiNER extracts named entities and dates.
    Zero information loss, 80%+ token reduction.
    perspective options: 'tech' (default), 'market', or 'dual'.
    """
    data = fast_intelligent_search(
        query,
        max_results=num_pages,
        perspective=perspective,
        instruction=instruction if instruction else None
    )
    return format_fast_digest(data)

@mcp.tool()
def deep_reasoning_search(query: str, reasoning_intent: str = "", num_pages: int = 3, perspective: str = "tech") -> str:
    """
    Deep Reasoning Search Mode (Phase 2):
    Use this when queries are broad, vague, or require semantic deduction
    (e.g., 'opportunities for me next year', complex bug synthesis across forums).
    Uses a local quantized Gemma 2 2B SLM to extract exact entities, dates, and solutions
    without information loss.
    perspective options: 'tech' (default), 'market', or 'dual'.
    """
    from parallel_pipeline import deep_reasoning_search as run_deep, format_deep_digest
    data = run_deep(
        query,
        perspective=perspective,
        reasoning_intent=reasoning_intent if reasoning_intent else None,
        max_results=num_pages
    )
    return format_deep_digest(data)

if __name__ == "__main__":
    mcp.run()
