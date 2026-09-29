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
    url: str = typer.Argument(..., help="Webpage URL to fetch"),
    force_stealth: bool = typer.Option(False, "--browser", "-b", help="Use browser rendering"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass SQLite cache"),
    output_file: str = typer.Option("", "--out", "-o", help="Save markdown to file")
):
    """Fetch any webpage and convert to Markdown format."""
    console.print(f"[dim]Fetching {url}...[/dim]")
    content = fetch_page(url, force_stealth=force_stealth, use_cache=not no_cache)
    
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)
        console.print(f"[bold green]Saved to {output_file}[/bold green]")
    else:
        print(content)

@app.command()
def toc(
    url: str = typer.Argument(..., help="Webpage URL to inspect"),
):
    """Get the Table of Contents outline of a documentation page (~150 tokens)."""
    console.print(f"[dim]Inspecting TOC for {url}...[/dim]")
    content = fetch_page(url)
    outline = get_table_of_contents(content)
    print(outline)

@app.command()
def section(
    url: str = typer.Argument(..., help="Webpage URL"),
    target: str = typer.Argument(..., help="Heading name or index to extract")
):
    """Extract a single targeted section from a large documentation page."""
    console.print(f"[dim]Extracting section '{target}' from {url}...[/dim]")
    content = fetch_page(url)
    sec = extract_section(content, target)
    print(sec)

@app.command()
def fast(
    query: str = typer.Argument(..., help="Search query"),
    instruction: str = typer.Option("", "--instruction", "-i", help="Task instruction for reranker"),
    perspective: str = typer.Option("tech", "--perspective", "-p", help="Perspective: tech (open-source), market (commercial), or dual (both)"),
    max_results: int = typer.Option(4, "--num", "-n", help="Number of pages to crawl"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output raw JSON")
):
    """Fast Neural Mode: Cross-Encoder Reranking + GLiNER Entity Extraction."""
    dev = get_device().upper()
    console.print(f"[bold cyan]Running Fast Neural Search on [{dev}] [Perspective: {perspective.upper()}] for: '{query}'[/bold cyan]")
    data = fast_intelligent_search(
        query,
        max_results=max_results,
        perspective=perspective,
        instruction=instruction or None
    )
    
    if json_output:
        print(json.dumps(data, indent=2))
    else:
        print(format_fast_digest(data))

@app.command()
def deep(
    query: str = typer.Argument(..., help="Search query"),
    intent: str = typer.Option("", "--intent", "-i", help="Specific reasoning or extraction intent"),
    perspective: str = typer.Option("tech", "--perspective", "-p", help="Perspective: tech (open-source), market (commercial), or dual (both)"),
    max_results: int = typer.Option(3, "--num", "-n", help="Number of pages to crawl"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output raw JSON")
):
    """Deep Reasoning Mode: Local quantized Gemma 2 SLM semantic extraction."""
    from parallel_pipeline import deep_reasoning_search, format_deep_digest
    console.print(f"[bold magenta]Running Deep SLM Reasoning [Perspective: {perspective.upper()}] for: '{query}'[/bold magenta]")
    data = deep_reasoning_search(
        query,
        perspective=perspective,
        reasoning_intent=intent or None,
        max_results=max_results
    )
@app.command()
def batch(
    queries: list[str] = typer.Argument(..., help="Search queries to execute in batch"),
    perspective: str = typer.Option("tech", "--perspective", "-p", help="Perspective: tech (open-source), market (commercial), or dual (both)"),
    max_results: int = typer.Option(3, "--num", "-n", help="Number of pages per query")
):
    """Execute multiple search queries in a safe, sequential batch."""
    for idx, q in enumerate(queries, 1):
        console.print(f"[bold cyan]Batch Query [{idx}/{len(queries)}]: '{q}'[/bold cyan]")
        data = fast_intelligent_search(q, max_results=max_results, perspective=perspective)
        print(format_fast_digest(data))
        if idx < len(queries):
            print("\n" + "=" * 50 + "\n")

if __name__ == "__main__":
    app()
