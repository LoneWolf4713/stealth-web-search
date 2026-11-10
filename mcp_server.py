#!/usr/bin/env python3
"""
MCP Server for Web Search & Information Retrieval.
Provides search, webpage fetching, documentation outline inspection,
and summarized multi-source research tools for AI coding assistants.

This process is a thin stdio shim: every tool call is forwarded to a single
shared daemon (daemon.py) over a Unix domain socket. That daemon is the one
process that actually holds the loaded models (GLiNER, the cross-encoder,
the Gemma SLM) and the shared browser instance. This matters because each
MCP client app (Claude Code, Claude Desktop, Antigravity, a local LLM's MCP
client, ...) launches its own copy of THIS file -- if this file did the
heavy lifting itself, every running client app would carry its own full
copy of every model in RAM/VRAM. Keeping this process import-light (no
torch/transformers/gliner/camoufox here at all) means opening N client apps
costs N lightweight stdio processes plus exactly one heavy daemon, not N
heavy processes.
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

from daemon_client import daemon_request

@mcp.tool()
def search(query: str, max_results: int = 5, perspective: str = "tech") -> str:
    """
    Search the web for up-to-date information, technical documentation, or industry data.

    IMPORTANT: Execute queries sequentially. Await each response before issuing another search.

    Args:
        query: The search terms or question.
        max_results: Maximum number of search results to return (default: 5).
        perspective: Search perspective filter:
            - 'tech' (default): Technical documentation, repositories, developer guides.
            - 'market': Products, startups, pricing, and comparison articles.
            - 'dual': Balanced search retrieving both technical and market perspectives.
    """
    return daemon_request("search", query=query, max_results=max_results, perspective=perspective)

@mcp.tool()
def fetch(
    url: str,
    force_browser: bool = False,
    force_stealth: bool = False,
    skip_tls_verify: bool = True,
    as_pointer: bool = False
) -> str:
    """
    Fetch the content of a webpage or PDF document and convert it to clean, readable Markdown format.
    Automatically parses PDF documents, handles SSL certificates, and escalates to browser mode for dynamic pages.

    Args:
        url: The web URL or PDF document link to fetch.
        force_browser: Set to True to use browser rendering for dynamic JavaScript pages.
        force_stealth: Legacy alias for force_browser.
        skip_tls_verify: Set to True (default) to bypass invalid or self-signed TLS certificates (e.g. academic sites).
        as_pointer: Set to True to stage the full document out-of-band in the vault and receive a compact
            manifest pointer card (~120 tokens) instead of reading the entire webpage into context. Use
            inspect_vault_doc to selectively inspect sections or query keywords within staged documents.
    """
    return daemon_request(
        "fetch",
        url=url,
        force_browser=force_browser,
        force_stealth=force_stealth,
        skip_tls_verify=skip_tls_verify,
        as_pointer=as_pointer
    )

@mcp.tool()
def inspect_vault_doc(doc_id: str, query: str = "", section: str = "", max_chars: int = 1500) -> str:
    """
    Inspect a specific section or search for keywords inside a document previously staged in the vault.
    Returns a small, focused excerpt (~350 tokens) instead of reading the entire webpage into context.

    Args:
        doc_id: The document identifier (e.g. 'doc_7a9f2b8c1d3e') returned in a document manifest.
        query: Optional search keyword or phrase to locate within the document.
        section: Optional section heading name or index number (from document TOC).
        max_chars: Maximum characters to return (default: 1500).
    """
    return daemon_request("inspect_vault_doc", doc_id=doc_id, query=query, section=section, max_chars=max_chars)

@mcp.tool()
def get_toc(url: str) -> str:
    """
    Get the Table of Contents outline of a documentation page or article.
    Use this first on long pages to locate specific sections without reading the entire document.

    Args:
        url: The web URL to inspect.
    """
    return daemon_request("get_toc", url=url)

@mcp.tool()
def read_section(url: str, section_name_or_index: str) -> str:
    """
    Read a specific section from a webpage or document by its heading name or index number.
    Extracts only the requested section to save context space.

    Args:
        url: The webpage URL.
        section_name_or_index: Heading title or numerical index obtained from get_toc.
    """
    return daemon_request("read_section", url=url, section_name_or_index=section_name_or_index)

@mcp.tool()
def fast_neural_search(
    query: str,
    instruction: str = "",
    num_pages: int = 4,
    perspective: str = "tech",
    density: str = "compact"
) -> str:
    """
    Perform an intelligent multi-source web search that fetches relevant pages,
    reranks the most pertinent passages, and extracts key entities and facts.

    IMPORTANT FOR AI AGENTS: Execute queries sequentially (one at a time) rather than in parallel.
    The local neural engine serializes queries in a queue to maintain system stability.

    Args:
        query: The search topic or question.
        instruction: Optional guidance for prioritizing specific types of information.
        num_pages: Number of pages to analyze (default: 4).
        perspective: 'tech' (default), 'market', or 'dual'.
        density: Output formatting density level:
            - 'compact' (default): Token-optimized digest (~800 tokens max) with top-1 passage per source,
              ideal for repetitive agentic research loops to eliminate context window exhaustion.
            - 'balanced': Moderate density (~1,900 tokens max) with top-2 passages per source.
            - 'raw': Legacy verbose output (~5,500 tokens max) with top-3 passages per source.
    """
    return daemon_request(
        "fast_neural_search",
        query=query,
        instruction=instruction,
        num_pages=num_pages,
        perspective=perspective,
        density=density
    )

@mcp.tool()
def batch_search(
    queries: list[str],
    max_results_per_query: int = 3,
    perspective: str = "tech",
    density: str = "compact"
) -> str:
    """
    Execute multiple search queries in a safe, sequential batch.
    Use this when you need answers for 2 or more related questions without making separate parallel tool calls.

    Args:
        queries: List of search query strings (max 3 queries per batch).
        max_results_per_query: Number of pages per query (default: 3).
        perspective: 'tech' (default), 'market', or 'dual'.
        density: Output formatting density level:
            - 'compact' (default): Token-optimized digest (~800 tokens per query).
            - 'balanced': Moderate density (~1,900 tokens per query).
            - 'raw': Legacy verbose output (~5,500 tokens per query).
    """
    return daemon_request(
        "batch_search",
        queries=queries,
        max_results_per_query=max_results_per_query,
        perspective=perspective,
        density=density
    )

@mcp.tool()
def deep_reasoning_search(query: str, reasoning_intent: str = "", num_pages: int = 3, perspective: str = "tech") -> str:
    """
    Perform comprehensive in-depth research using local language model analysis.
    Crawls source pages and synthesizes structured findings, key details, and solutions.

    IMPORTANT FOR AI AGENTS: Execute queries sequentially (one at a time) rather than in parallel.
    The local model serializes queries in a queue to maintain system stability.

    Args:
        query: The research query or topic.
        reasoning_intent: Specific research goals or requirements to focus on.
        num_pages: Number of pages to analyze (default: 3).
        perspective: 'tech' (default), 'market', or 'dual'.
    """
    return daemon_request(
        "deep_reasoning_search", query=query, reasoning_intent=reasoning_intent, num_pages=num_pages, perspective=perspective
    )

@mcp.tool()
def record_research_fact(
    category: str,
    tier: int,
    fact_text: str,
    source_url: str = "",
    source_title: str = "",
    pass_num: int = 1,
    tags: list[str] = []
) -> str:
    """
    Record an established research fact, finding, or milestone into the persistent SQLite research ledger.
    
    Context-Flushing Pattern for AI Agents:
    During multi-pass or multi-category research loops (e.g. 10 categories x 5 tiers x 5 passes),
    record your findings into this ledger as you go. When your context window grows large,
    summarize progress, flush/clear conversational history, and call get_research_ledger to reload
    all established state in a compact, token-dense briefing (<250 tokens) without context explosion.

    Args:
        category: Research domain or topic category (e.g. 'architecture', 'benchmarks', 'security').
        tier: Depth or importance tier (e.g. 1 = core/foundational, 2 = architecture, 3 = edge cases).
        fact_text: Concise, concrete statement of fact or key conclusion.
        source_url: URL where the fact was verified.
        source_title: Optional human-readable title of the source.
        pass_num: Research pass iteration number (default: 1).
        tags: Optional list of keyword tags for filtering and indexing.
    """
    return daemon_request(
        "record_research_fact",
        category=category,
        tier=tier,
        fact_text=fact_text,
        source_url=source_url,
        source_title=source_title,
        pass_num=pass_num,
        tags=tags
    )

@mcp.tool()
def get_research_ledger(category: str = None, tier: int = None) -> str:
    """
    Retrieve established research facts from the persistent SQLite ledger as a token-dense Markdown briefing.
    
    Context-Flushing Pattern for AI Agents:
    Call this tool at the start of a fresh conversation session after flushing conversational history.
    It returns a structured, categorized briefing grouped by # Category and ## Tier that provides
    all accumulated research state in under 250 tokens for immediate reasoning without context bloat.

    Args:
        category: Optional category filter. If omitted, returns facts across all categories.
        tier: Optional tier filter. If omitted, returns facts across all tiers.
    """
    return daemon_request("get_research_ledger", category=category, tier=tier)

@mcp.tool()
def clear_research_ledger(category: str = None, confirm: bool = False) -> str:
    """
    Clear research ledger entries from the SQLite database.
    Requires confirm=True to prevent accidental data loss.

    Args:
        category: Optional category to clear. If omitted, clears all entries in the ledger.
        confirm: Confirmation flag. Must be set to True to execute deletion.
    """
    return daemon_request("clear_research_ledger", category=category, confirm=confirm)

if __name__ == "__main__":
    mcp.run()
