# Production Implementation Plan: `markdownify` Integration & Pipeline Hardening

**Repository:** `/home/prtyksh/tmp/web_search_tool`  
**Branch:** `feat/context-token-optimization`  
**Reference Document:** `HANDOFF.md`  
**Goal:** Transition the HTML-to-Markdown conversion pipeline from `trafilatura`'s direct markdown generation to a dual-stage architecture (`trafilatura` ML extraction → `markdownify` CommonMark conversion). Eliminate the root-cause bug factory in `chunker.py` and `fetcher.py` with zero regressions.

---

## 1. Executive Summary & Observations from `HANDOFF.md`

### 1.1 The Problem & Root Cause
Over 7 consecutive red-team adversarial audit cycles (`v1` through `v5`), the codebase suffered from a continuous "whack-a-mole" cycle where fixing edge cases in markdown parsing introduced subtle regressions elsewhere.
- **The Core Vulnerability:** `trafilatura` is an outstanding ML content *extractor* (stripping ads, navigation headers, sidebars, cookie banners), but its direct Markdown serialization is imperfect: it frequently produces unclosed or inconsistent code fences, flattens markdown tables into plain text, drops document preambles before `# Heading 1`, and leaks HTML comments as false headings.
- **The Spiral:** Because incoming Markdown was dirty and inconsistent, `chunker.py` had to implement hundreds of lines of defensive regex heuristics (`_get_unclosed_fences`, `_clean_section_content`, nonce slot substitutions, heading regexes). Each defensive patch was fragile against adversarial inputs.

### 1.2 The Evaluated Alternatives
- **Firecrawl (`mendableai/firecrawl`):** Ruled out. Cloud/managed API requiring heavy infrastructure (Postgres, Redis, RabbitMQ, Docker Compose) without adding anything our local daemon does not already do.
- **Scrapling (`D4Vinci/Scrapling`):** Evaluated for scraping and browser stealth. While Scrapling is a capable scraper, our custom architecture already has superior local features (pointer protocol, token budgeting, SQLite state ledger, cross-encoder neural reranking, Camoufox stealth browser). Crucially, Scrapling relies on **`markdownify`** for its HTML-to-Markdown conversion.

### 1.3 The Chosen Strategy: "Option B"
Combine the distinct strengths of both specialized libraries:
$$\text{Raw HTML} \xrightarrow{\text{trafilatura (ML Filtering)}} \text{Clean Semantic HTML} \xrightarrow{\text{markdownify (AST Parsing)}} \text{Strict CommonMark Markdown}$$

1. **`trafilatura.extract(html, output_format='html')`** performs noise removal and boiler-plate stripping.
2. **`markdownify(extracted_html)`** performs strict, standard-compliant CommonMark generation (fenced backticks, pipe tables, clean ATX headings).
3. Downstream consumers (`chunker.py`, `vault.py`, `daemon.py`, `mcp_server.py`) receive clean, well-formed Markdown, rendering existing edge-case traps moot.

---

## 2. Architecture & Design Specification

### 2.1 The Two-Stage HTML Pipeline
```
┌────────────────────────────────────────────────────────────────────────┐
│                        FETCH STAGE (fetcher.py)                        │
│                                                                        │
│   Fast Tier: curl_cffi  │  Stealth Tier: Camoufox  │  Tier 3: Wayback  │
│   (TLS Impersonation)   │  (Headless Stealth)      │  (Archive.org)    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Raw HTML
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   STAGE 1: ML EXTRACTION (trafilatura)                 │
│                                                                        │
│   trafilatura.extract(raw_html, output_format="html",                  │
│                       include_links=True, include_tables=True,         │
│                       favor_recall=True)                               │
│   • Strips ads, navbars, sidebars, cookie banners, tracking widgets    │
│   • Retains clean DOM structure with <h1>..<h6>, <p>, <table>, <pre>   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Clean Semantic HTML
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                STAGE 2: COMMONMARK CONVERSION (markdownify)             │
│                                                                        │
│   CustomMarkdownConverter(heading_style="ATX", strip=['script', ...]) │
│   • Converts HTML elements to CommonMark AST                           │
│   • Normalizes <pre> inside headings into inline code: `# `code``     │
│   • Unwraps double <pre><pre> into single clean ``` code blocks        │
│   • Formats <table> into clean GitHub Flavored Markdown (GFM) tables   │
│   • Automatically strips HTML comments (eliminating heading leaks)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Clean Markdown String
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│               DOWNSTREAM INGESTION (Unchanged Contracts)               │
│                                                                        │
│   chunker.py ──► neural_fast.py ──► vault.py ──► daemon.py ──► MCP    │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Defensive Fallback Strategy (Zero-Crash Guarantee)
To guarantee zero regressions under unpredictable network conditions and malformed web inputs:
1. **Empty / None Handling:** If `trafilatura` fails to identify main content (returning `None`), maintain existing fallback paths (such as `document.body.innerText` extraction in browser mode or classified error reasons).
2. **Exception Shielding:** If `markdownify` encounters unexpected malformed HTML entities, the converter catches the exception and falls back to `trafilatura.extract(output_format="markdown")`. A fetch operation will **never** fail due to a conversion error.
3. **Preamble Protection:** Ensure headings and leading paragraphs maintain natural separation with clean double-newlines.

---

## 3. Step-by-Step Implementation Roadmap

### Step 1: Dependencies (`requirements.txt`)
- Add `markdownify>=1.2.0` to `requirements.txt`.
- Verify clean virtual environment resolution via `uv pip install "markdownify>=1.2.0"`.

### Step 2: Custom CommonMark Converter (`fetcher.py`)
Implement an optimized converter subclass in `fetcher.py`:
```python
from markdownify import MarkdownConverter

class CommonMarkConverter(MarkdownConverter):
    """
    Enhanced MarkdownConverter ensuring clean ATX headings and proper code fence handling.
    """
    def convert_pre(self, el, text, parent_tags):
        # If <pre> is nested inside a heading tag (e.g. Python docutils), convert to inline code
        parent = el.parent
        while parent:
            if parent.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                return f" `{text.strip()}` "
            parent = parent.parent
        # If <pre> is nested inside another <pre>, unwrap without duplicating fences
        if el.find_parent("pre"):
            return text
        return super().convert_pre(el, text, parent_tags)
```

Implement the conversion helper:
```python
def _html_to_clean_markdown(html_content: str, fallback_url: str = "") -> str:
    """
    Two-stage HTML to CommonMark pipeline:
    1. trafilatura extracts main content as clean HTML.
    2. markdownify converts semantic HTML to strict CommonMark.
    Falls back gracefully if either step fails.
    """
    if not html_content or not html_content.strip():
        return ""
    
    extracted_html = None
    try:
        extracted_html = trafilatura.extract(
            html_content,
            output_format="html",
            include_links=True,
            include_images=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception as e:
        logger.debug(f"trafilatura extraction error: {e}")

    if extracted_html:
        try:
            converter = CommonMarkConverter(
                heading_style="ATX",
                strip=["script", "style", "head", "noscript", "iframe"],
                newline_style="backslash"
            )
            md = converter.convert(extracted_html)
            if md and len(md.strip()) > 40:
                return md.strip()
        except Exception as e:
            logger.warning(f"markdownify conversion error, attempting raw markdown fallback: {e}")

    # Fallback: direct trafilatura markdown extraction
    try:
        fallback_md = trafilatura.extract(
            html_content,
            output_format="markdown",
            include_links=True,
            include_images=False,
            favor_recall=True
        )
        return (fallback_md or "").strip()
    except Exception:
        return ""
```

### Step 3: Wire into All Fetch Tiers (`fetcher.py`)
Replace direct `trafilatura.extract(..., output_format="markdown")` in:
1. `fetch_fast_tier` (curl_cffi path)
2. `fetch_stealth_tier` (Camoufox browser path)
3. `fetch_wayback_archive` (Wayback Machine fallback)

### Step 4: Verification & Automated Test Suite
1. Create `tests/test_markdownify_integration.py` covering:
   - Proper code fencing (triple backticks, no extraneous fences).
   - Proper GFM pipe tables with headers.
   - Preamble preservation before `# Heading 1`.
   - HTML comment stripping without leaking into headings.
   - Safe fallback when trafilatura returns `None`.
2. Run full 87-test regression battery across all test suites:
   - `tests/test_token_optimization.py` (17 tests)
   - `tests/test_adversarial_remediation.py` (12 tests)
   - `tests/test_adversarial_v2_fuzzing.py` (13 tests)
   - `tests/test_adversarial_v3_fuzzing.py` (15 tests)
   - `tests/test_adversarial_v4_fuzzing.py` (15 tests)
   - `tests/test_adversarial_v5_fuzzing.py` (15 tests)
3. Execute real CLI smoke tests:
   - Basic fetch: `./run_tool.sh fetch "https://docs.python.org/3/library/asyncio.html"`
   - Documentation with complex code blocks: `./run_tool.sh fetch "https://docs.rs/tokio/latest/tokio/"`
   - Fast neural search: `./run_tool.sh fast "rust tokio async runtime" --density compact --num 3`
   - Headless browser fetch: `./run_tool.sh fetch "https://github.com/tokio-rs/tokio" --force-browser`

---

## 4. Architectural Safeguards (Why Regressions Are Prevented)

| Potential Risk | Prevention Mechanism |
| :--- | :--- |
| **Interface Breakage** | `fetcher.py:fetch_page` continues to return `str`; `fetch_page_detailed` returns `Dict[str, Any]` with identical keys (`success`, `content`, `error_reason`, `cached`). |
| **Context Explosion** | The 6,000 character ceiling in `fetch_page` and safe truncation with notice block are retained in `fetcher.py`. |
| **Noise & Boilerplate** | `trafilatura` remains the first filter (`output_format="html"`), so ads, cookie banners, and navigation bars continue to be removed before `markdownify` runs. |
| **Nested Code Fences** | `CommonMarkConverter` unwraps `<pre><pre>` tags and converts `<pre>` inside headings into inline code spans. |
| **Parsing Exceptions** | Tri-layer fallback: Stage 2 (`markdownify`) → Stage 1 fallback (`trafilatura markdown`) → Browser DOM innerText fallback. |
