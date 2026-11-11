# Architectural Evolution & Optimization Log: `web_search_tool`

**Branch:** `feat/markdownify-integration`  
**Date:** November 11, 2026  
**Status:** Validated & Production-Ready  
**Related Documents:** [`AB_BENCHMARK_CHIEF_ARCHITECT_REPORT.md`](./AB_BENCHMARK_CHIEF_ARCHITECT_REPORT.md), [`HANDOFF.md`](./HANDOFF.md), [`markdownify_integration_plan.md`](./markdownify_integration_plan.md)

---

## 1. Executive Summary

This document chronicles the complete end-to-end architecture overhaul of `web_search_tool` (published as `stealth-web-search`), designed to eliminate **O(N²) context window exhaustion** in recursive agentic research loops while guaranteeing **zero factual information loss** and strict **CommonMark syntax integrity**.

Across multiple development cycles, 7 adversarial red-team audit passes, an empirical A/B benchmark evaluation between `main` (`e648dc8`) and `feat/markdownify-integration` (`201b241`), and a final post-benchmark parser remediation, the system has achieved:
* **61.5% token reduction** in default neural search (`compact` mode: 873 tokens vs 2,270 baseline).
* **78.0% token reduction** on large documentation fetches (1,524 tokens vs 6,943 baseline).
* **Zero unforced factual loss**, with full interactive recovery paths via `get_toc`, `read_section`, and `inspect_vault_doc`.
* **Complete CommonMark compliance**, resolving edge-case code fence pollution, unclosed markdown links, and table corruption.

---

## 2. Chronological Architecture Phases

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│     Phase 1     │     │     Phase 2     │     │     Phase 3     │
│  Multi-Density  │ ──> │ Pointer Protocol│ ──> │ Research Ledger │
│ & Fetch Ceiling │     │  & SQLite Vault │     │   Persistence   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│     Phase 4     │     │     Phase 5     │     │     Phase 6     │
│   Markdownify   │ ──> │ Empirical A/B   │ ──> │ Inline `<pre>`  │
│   Integration   │     │ Benchmarking    │     │ Fence Remediation│
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

---

### Phase 1: Multi-Density Compression & Fetch Ceiling (Commit `a1a068e`)
* **Problem Solved:** Legacy neural search dumped massive verbose passages with raw code dumps and boilerplate, quickly exhausting the context window in iterative research passes.
* **Implementation:**
  * Added `density` parameter to `fast_intelligent_search` and `fast_neural_search`:
    * `compact` (default): ~800 tokens max, 1 top-ranked passage per source, key entities, top evidence quote.
    * `balanced`: ~1,900 tokens max, 2 top-ranked passages per source with deeper contextual surrounding text.
    * `raw`: Legacy verbose output (~5,500 tokens max), 3 passages per source with full extracted listings.
  * Implemented a default **6,000-character fetch ceiling** on `fetch` with an automated notice informing LLMs how to read deeper using `get_toc` and `read_section`.
  * Hardened SQLite database schemas and concurrency locks in `vault.py`.

---

### Phase 2: Out-of-Band Pointer Protocol & SQLite Vault (Commit `387ecf3`)
* **Problem Solved:** Agents frequently read entire 10,000+ token documentation manuals just to extract one function signature or parameter explanation.
* **Implementation:**
  * Implemented `as_pointer` mode in `fetch`: full scraped HTML/Markdown is staged out-of-band in `vault.sqlite`, and the tool returns a compact ~120-token **manifest card** containing:
    * Document ID (`doc_xxxxxxxxxxxx`)
    * Title & Source URL
    * Total size (tokens and characters)
    * Table of Contents summary
    * Lead finding snippet
  * Introduced `inspect_vault_doc(doc_id, query, section, max_chars)` allowing agents to selectively pull targeted 50–200 token excerpts without ever pulling the entire document into context.

---

### Phase 3: Research Ledger & State Persistence (Commit `d4e95c9`)
* **Problem Solved:** Long multi-turn agent investigations lose historical context during LLM context compaction or window flushes, causing the agent to repeatedly search the same queries.
* **Implementation:**
  * Added persistent research ledger tools in `mcp_server.py`, `cli.py`, and `vault.py`:
    * `record_research_fact(category, text, source_url, confidence, tier)`
    * `get_research_ledger(category, tier)`
    * `clear_research_ledger(category, confirm=True)`
  * Allows agents to resume deep research loops across context flushes in <250 tokens.

---

### Phase 4: `markdownify` Integration Pipeline (Commit `201b241`)
* **Problem Solved:** The custom hand-rolled markdown parsing and chunking engine in `chunker.py` was repeatedly breaking across 7 adversarial audit rounds due to dirty, malformed Markdown emitted by `trafilatura`.
* **Architectural Decision (Option B):**
  * `trafilatura` extracts semantic HTML (`output_format="html"`), performing ML heuristic main-content scoring (removing ads, navbars, and cookie banners).
  * `markdownify` converts clean semantic HTML to strict CommonMark using custom `CommonMarkConverter` extensions.
  * Added unit test suite `tests/test_markdownify_integration.py` covering table formatting, code fences, preamble preservation, and graceful fallback.

---

### Phase 5: Empirical A/B Benchmark Audit
* **Methodology:** Isolated sequential sub-agent testing between Baseline (`main` @ `e648dc8`) and Feature (`feat/markdownify-integration` @ `201b241`) with complete process, socket, and SQLite cache isolation.
* **Results:**
  * Search consistency: 100% deterministic parity.
  * Neural Search: **61.5% token reduction** (873 tokens vs 2,270 tokens baseline).
  * Web Fetch: **78.0% token reduction** (1,524 tokens vs 6,943 tokens baseline).
  * Detailed in [`AB_BENCHMARK_CHIEF_ARCHITECT_REPORT.md`](./AB_BENCHMARK_CHIEF_ARCHITECT_REPORT.md).

---

### Phase 6: Inline `<pre>` Code Fence Remediation (Current Commit)
* **The Root Cause Discovered in A/B Testing:**
  * `trafilatura.extract(output_format="html")` converted inline `<code>` tags with HTML classes into `<pre>...</pre>`.
  * `markdownify`'s default converter treated all `<pre>` tags as multiline block code fences (`\n\n```\n...\n```\n\n`).
  * This corrupted inline markdown hyperlinks (`[```\nVAR\n```](url)`), broke table cells (`| ``` .md ``` |`), and split inline sentences across block fences.
* **The Remediation Implemented in `fetcher.py`:**
  * Updated `CommonMarkConverter:convert_pre` to inspect the DOM ancestor hierarchy and content semantics:
    * If `<pre>` is enclosed in `<a>` (links), `<td>`/`<th>` (tables), or `<h1>`–`<h6>` (headings), it is strictly rendered as inline code `` `...` ``.
    * If `<pre>` is inside `<p>`, `<li>`, `<span>`, `<blockquote>` as a short token (<120 characters with no newlines), it is rendered as inline code.
    * Real multiline code blocks (`<pre><code>...</code></pre>`) continue to be rendered as pristine fenced code blocks.
  * Added 3 comprehensive regression unit tests in `tests/test_markdownify_integration.py` (`test_inline_pre_in_hyperlink_rendered_as_inline_code`, `test_inline_pre_in_table_cell_rendered_as_inline_code`, `test_inline_pre_single_token_in_paragraph`).

---

## 3. Summary of Files Changed

| File | Nature of Changes |
| :--- | :--- |
| [`fetcher.py`](./fetcher.py) | Two-stage CommonMark pipeline, `CommonMarkConverter` inline `<pre>` defense, 6k fetch ceiling, clean web content filtering. |
| [`tests/test_markdownify_integration.py`](./tests/test_markdownify_integration.py) | 19 unit tests covering CommonMark code blocks, pipe tables, preambles, and inline `<pre>` regressions. |
| [`AB_BENCHMARK_CHIEF_ARCHITECT_REPORT.md`](./AB_BENCHMARK_CHIEF_ARCHITECT_REPORT.md) | Official Chief Architect empirical comparison report with exact token, character, and latency statistics. |
| [`CHANGES_AND_OPTIMIZATIONS_OVERVIEW.md`](./CHANGES_AND_OPTIMIZATIONS_OVERVIEW.md) | Comprehensive engineering changelog and architectural specification. |

---

## 4. Verification & Testing Confirmation

All test suites were executed in the isolated project environment (`CUDA_VISIBLE_DEVICES=""`):
* `test_markdownify_integration.py`: **19 / 19 passed** (0.045s)
* `test_token_optimization.py`: **17 / 17 passed** (0.134s)
* `tests/test_adversarial_v5_fuzzing.py`: **Passed**
* Live test against `https://docs.python.org/3/library/asyncio.html`: **Verified zero `[``` ` link corruptions, zero unclosed fences**.
* Live test against `https://en.wikipedia.org/wiki/Markdown`: **Verified clean pipe tables, zero block fence spills in prose**.
