#!/usr/bin/env python3
"""
MCP Server for Web Search & Intelligent Information Retrieval.
Provides search, webpage fetching, documentation outline inspection,
and summarized multi-source research tools for AI coding assistants.
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer("web-search-tools")
except (ImportError, ModuleNotFoundError):
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("web-search-tools")

from searcher import search_web, search_news
from fetcher import fetch_page
from chunker import get_table_of_contents, extract_section
from parallel_pipeline import fast_intelligent_search, format_fast_digest

@mcp.tool()
def search(query: str, max_results: int = 5, perspective: str = "tech") -> str:
    """
    Search the web for up-to-date information, technical documentation, or industry data.
    
    Args:
        query: The search terms or question.
        max_results: Maximum number of search results to return (default: 5).
        perspective: Search perspective filter:
            - 'tech' (default): Technical documentation, repositories, developer guides.
            - 'market': Products, startups, pricing, and comparison articles.
            - 'dual': Balanced search retrieving both technical and market perspectives.
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
def fetch(url: str, force_browser: bool = False, force_stealth: bool = False) -> str:
    """
    Fetch the content of a webpage and convert it to clean, readable Markdown format.
    
    Args:
        url: The web URL to fetch.
        force_browser: Set to True to use browser rendering for dynamic JavaScript pages.
        force_stealth: Legacy alias for force_browser.
    """
    use_browser = force_browser or force_stealth
    return fetch_page(url, force_stealth=use_browser)

@mcp.tool()
def get_toc(url: str) -> str:
    """
    Get the Table of Contents outline of a documentation page or article.
    Use this first on long pages to locate specific sections without reading the entire document.
    
    Args:
        url: The web URL to inspect.
    """
    content = fetch_page(url)
    return get_table_of_contents(content)

@mcp.tool()
def read_section(url: str, section_name_or_index: str) -> str:
    """
    Read a specific section from a webpage or document by its heading name or index number.
    Extracts only the requested section to save context space.
    
    Args:
        url: The webpage URL.
        section_name_or_index: Heading title or numerical index obtained from get_toc.
    """
    content = fetch_page(url)
    return extract_section(content, section_name_or_index)

@mcp.tool()
def fast_neural_search(query: str, instruction: str = "", num_pages: int = 4, perspective: str = "tech") -> str:
    """
    Perform an intelligent multi-source web search that fetches relevant pages,
    reranks the most pertinent passages, and extracts key entities and facts.
    
    Args:
        query: The search topic or question.
        instruction: Optional guidance for prioritizing specific types of information.
        num_pages: Number of pages to analyze (default: 4).
        perspective: 'tech' (default), 'market', or 'dual'.
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
    Perform comprehensive in-depth research using local language model analysis.
    Crawls source pages and synthesizes structured findings, key details, and solutions.
    
    Args:
        query: The research query or topic.
        reasoning_intent: Specific research goals or requirements to focus on.
        num_pages: Number of pages to analyze (default: 3).
        perspective: 'tech' (default), 'market', or 'dual'.
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
