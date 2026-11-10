#!/usr/bin/env python3
"""
CLI entry point for web search and information retrieval.
"""
import os
import sys
import json
import warnings
import logging

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore")
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("gliner").setLevel(logging.ERROR)

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from searcher import search_web, search_news
from fetcher import fetch_page
from chunker import get_table_of_contents, extract_section
from parallel_pipeline import fast_intelligent_search, format_fast_digest
from neural_fast import get_device

app = typer.Typer(help="Web Search & Information Retrieval CLI for AI Coding Agents")
console = Console()

@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    max_results: int = typer.Option(5, "--num", "-n", help="Number of search results"),
    perspective: str = typer.Option("tech", "--perspective", "-p", help="Perspective: tech (open-source), market (commercial), or dual (both)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output raw JSON")
):
    """Fast keyless web search with SQLite caching."""
    results = search_web(query, max_results=max_results, perspective=perspective)
    if json_output:
        print(json.dumps(results, indent=2))
        return

    table = Table(title=f"Search Results: {query} [Perspective: {perspective.upper()}]", show_lines=True)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Title", style="bold green")
    table.add_column("URL", style="blue")
    table.add_column("Perspective", style="yellow")
    table.add_column("Snippet", style="white")

    for idx, r in enumerate(results, 1):
        table.add_row(str(idx), r.get("title", ""), r.get("href", ""), r.get("perspective", "tech"), r.get("body", ""))

    console.print(table)

@app.command()
def fetch(
    url: str = typer.Argument(..., help="Webpage URL or PDF link to fetch"),
    force_stealth: bool = typer.Option(False, "--browser", "-b", help="Use browser rendering"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass SQLite cache"),
    skip_tls_verify: bool = typer.Option(True, "--skip-tls-verify/--verify-tls", help="Bypass TLS certificate verification"),
    as_pointer: bool = typer.Option(False, "--pointer", "-p", help="Stage document out-of-band and return manifest card"),
    output_file: str = typer.Option("", "--out", "-o", help="Save markdown to file")
):
    """Fetch any webpage or PDF document and convert to Markdown format."""
    console.print(f"[dim]Fetching {url}...[/dim]")
    if as_pointer:
        from daemon_client import daemon_request
        content = daemon_request("fetch", url=url, force_browser=force_stealth, skip_tls_verify=skip_tls_verify, as_pointer=True)
    else:
        content = fetch_page(url, force_stealth=force_stealth, use_cache=not no_cache, skip_tls_verify=skip_tls_verify)
    
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)
        console.print(f"[bold green]Saved to {output_file}[/bold green]")
    else:
        print(content)

@app.command("inspect")
def inspect_cmd(
    doc_id: str = typer.Argument(..., help="Document ID in vault (e.g. doc_123456789abc)"),
    section: str = typer.Option("", "--section", "-s", help="Section heading or numeric index"),
    query: str = typer.Option("", "--query", "-q", help="Query keywords or phrase"),
    max_chars: int = typer.Option(1500, "--max-chars", "-m", help="Maximum characters to return")
):
    """Inspect a section or query keywords inside a staged document in the vault."""
    from daemon_client import daemon_request
    res = daemon_request("inspect_vault_doc", doc_id=doc_id, query=query, section=section, max_chars=max_chars)
    print(res)

@app.command()
def toc(
    url: str = typer.Argument(..., help="Webpage URL to inspect"),
):
    """Get the Table of Contents outline of a documentation page (~150 tokens)."""
    console.print(f"[dim]Inspecting TOC for {url}...[/dim]")
    content = fetch_page(url, max_chars=None)
    outline = get_table_of_contents(content)
    print(outline)

@app.command()
def section(
    url: str = typer.Argument(..., help="Webpage URL"),
    target: str = typer.Argument(..., help="Heading name or index to extract")
):
    """Extract a single targeted section from a large documentation page."""
    console.print(f"[dim]Extracting section '{target}' from {url}...[/dim]")
    content = fetch_page(url, max_chars=None)
    sec = extract_section(content, target)
    print(sec)

@app.command()
def fast(
    query: str = typer.Argument(..., help="Search query"),
    instruction: str = typer.Option("", "--instruction", "-i", help="Task instruction for reranker"),
    perspective: str = typer.Option("tech", "--perspective", "-p", help="Perspective: tech (open-source), market (commercial), or dual (both)"),
    max_results: int = typer.Option(4, "--num", "-n", help="Number of pages to crawl"),
    density: str = typer.Option("compact", "--density", "-d", help="Output density: compact (default, ~800 tokens), balanced (~1900 tokens), raw (~5500 tokens)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output raw JSON")
):
    """Fast Neural Mode: Cross-Encoder Reranking + GLiNER Entity Extraction."""
    dev = get_device().upper()
    console.print(f"[bold cyan]Running Fast Neural Search on [{dev}] [Perspective: {perspective.upper()}] [Density: {density.upper()}] for: '{query}'[/bold cyan]")
    data = fast_intelligent_search(
        query,
        max_results=max_results,
        perspective=perspective,
        instruction=instruction or None,
        density=density
    )
    
    if json_output:
        print(json.dumps(data, indent=2))
    else:
        print(format_fast_digest(data, density=density))

@app.command()
def deep(
    query: str = typer.Argument(..., help="Search query"),
    intent: str = typer.Option("", "--intent", "-i", help="Specific reasoning or extraction intent"),
    perspective: str = typer.Option("tech", "--perspective", "-p", help="Perspective: tech (open-source), market (commercial), or dual (both)"),
    max_results: int = typer.Option(3, "--num", "-n", help="Number of pages to crawl"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output raw JSON")
):
    """Deep Reasoning Mode: Local quantized Gemma 4 SLM semantic extraction."""
    from parallel_pipeline import deep_reasoning_search, format_deep_digest
    console.print(f"[bold magenta]Running Deep SLM Reasoning [Perspective: {perspective.upper()}] for: '{query}'[/bold magenta]")
    data = deep_reasoning_search(
        query,
        perspective=perspective,
        reasoning_intent=intent or None,
        max_results=max_results
    )
    if json_output:
        print(json.dumps(data, indent=2))
    else:
        print(format_deep_digest(data))
@app.command()
def batch(
    queries: list[str] = typer.Argument(..., help="Search queries to execute in batch"),
    perspective: str = typer.Option("tech", "--perspective", "-p", help="Perspective: tech (open-source), market (commercial), or dual (both)"),
    max_results: int = typer.Option(3, "--num", "-n", help="Number of pages per query"),
    density: str = typer.Option("compact", "--density", "-d", help="Output density: compact (default, ~800 tokens), balanced (~1900 tokens), raw (~5500 tokens)")
):
    """Execute multiple search queries in a safe, sequential batch."""
    for idx, q in enumerate(queries, 1):
        console.print(f"[bold cyan]Batch Query [{idx}/{len(queries)}]: '{q}'[/bold cyan]")
        data = fast_intelligent_search(q, max_results=max_results, perspective=perspective, density=density)
        print(format_fast_digest(data, density=density))
        if idx < len(queries):
            print("\n" + "=" * 50 + "\n")

@app.command("record-fact")
def record_fact_cmd(
    category: str = typer.Argument(..., help="Research category (e.g. architecture, benchmarks)"),
    fact: str = typer.Argument(..., help="Fact text or established finding"),
    tier: int = typer.Option(1, "--tier", "-t", help="Depth tier (1=foundational, 2=deep, 3=edge)"),
    source: str = typer.Option("", "--source", "-s", help="Source URL"),
    title: str = typer.Option("", "--title", help="Source title"),
    pass_num: int = typer.Option(1, "--pass", "-p", help="Research pass number"),
    tag: list[str] = typer.Option([], "--tag", help="Keyword tags (can be repeated)"),
):
    """Record an established fact into the persistent research ledger."""
    from daemon_client import daemon_request
    res = daemon_request(
        "record_research_fact",
        category=category,
        tier=tier,
        fact_text=fact,
        source_url=source,
        source_title=title,
        pass_num=pass_num,
        tags=list(tag) if tag else []
    )
    console.print(f"[bold green]{res}[/bold green]")

@app.command("ledger")
def ledger_cmd(
    category: str = typer.Option("", "--category", "-c", help="Filter by research category"),
    tier: int = typer.Option(None, "--tier", "-t", help="Filter by tier"),
    clear: bool = typer.Option(False, "--clear", help="Clear ledger entries"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm deletion when clearing"),
):
    """View or manage the persistent research ledger briefing."""
    from daemon_client import daemon_request
    if clear:
        res = daemon_request("clear_research_ledger", category=category or None, confirm=confirm)
        console.print(res)
    else:
        res = daemon_request("get_research_ledger", category=category or None, tier=tier)
        print(res)

if __name__ == "__main__":
    app()
