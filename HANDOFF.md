# Architectural Handoff: `markdownify` Integration

**Document type:** Developer handoff — context, rationale, and precise change spec  
**Branch:** `feat/context-token-optimization`  
**Prepared by:** Architect review session (November 11, 2026)  
**Intended reader:** Developer LLM implementing the changes  

---

## 1. What This Project Is

`web_search_tool` (published as `stealth-web-search`) is a **self-hosted MCP server** that gives LLMs (Claude Code, Antigravity, any MCP client) the ability to search the web and read documentation — without an API key — while keeping token consumption minimal.

The core problem it solves: a naive 250-pass research loop (10 categories × 5 tiers × 5 passes) where each web fetch returns ~4,000 tokens accumulates roughly **125 million cumulative input tokens** due to O(N²) context growth. This tool fights that with four architectural strategies:

1. **Cross-encoder reranking** — returns only the most relevant passages, not full pages
2. **Out-of-band pointer protocol** — full docs go into SQLite vault, only a ~120-token manifest card lands in LLM context
3. **Persistent state ledger** — research facts survive context flushes; resuming costs <250 tokens
4. **Multi-density output** — `compact` mode caps at ~800 tokens, `raw` is legacy behavior

The tool exposes 10 MCP tools: `search`, `fetch`, `inspect_vault_doc`, `get_toc`, `read_section`, `fast_neural_search`, `batch_search`, `deep_reasoning_search`, `record_research_fact`, `get_research_ledger`, `clear_research_ledger`.

---

## 2. Why This Change Is Being Made

### 2.1 The Red-Team Audit History

The codebase underwent **7 rounds of adversarial red-team auditing** on the `feat/context-token-optimization` branch. Each round found **15–18 new bugs**. The accumulated audit reports are:

- `adversarial_redteam_audit_report.md` — Pass 2: 18 bugs (5 CRITICAL)
- `adversarial_redteam_audit_report_v3.md` — Pass 4: 15 bugs (1 CRITICAL)
- `adversarial_redteam_audit_report_v4.md` — Pass 6: 15 bugs (2 CRITICAL)
- `adversarial_redteam_audit_report_v5.md` — Pass 7: 15 bugs (1 CRITICAL)

The remediation plans are in `remediation_plan.md` through `remediation_plan_v5.md`.

### 2.2 The Root Cause Pattern

Across all 7 audit passes, **the same files kept breaking** with the same categories of bugs:

- `chunker.py` — the hand-rolled markdown parser/chunker. Every single audit found new critical bugs here: CommonMark fence tracking failures, nested fence premature termination, placeholder placeholder collision, preamble loss, table row backtracking data loss, unclosed fence heading pollution, HTML comment leakage into headings.
- `vault.py` — type safety crashes, NaN/Inf TTL bugs, URL cache divergence
- `parallel_pipeline.py` — `[None](None)` link corruption, search failure masking

The architectural root cause for the `chunker.py` bug cycle: **`chunker.py` receives dirty, inconsistently-structured Markdown output from `trafilatura`**, and its defensive code to handle edge cases is itself the source of new bugs each round.

### 2.3 What Was Investigated

A comparative analysis was done against two GitHub alternatives:

**Firecrawl (`mendableai/firecrawl`)** — Ruled out. It is a managed API service requiring an API key and a heavy Docker Compose self-hosting stack (Node.js, Redis, RabbitMQ, PostgreSQL). It does not add anything this project doesn't already handle locally.

**Scrapling (`D4Vinci/Scrapling`)** — Partially relevant. Scrapling is a full Python scraping framework (lxml parser + curl_cffi + patchright stealth browser + Scrapy-like spiders). It uses `markdownify` for its `page.markdown()` method. The investigation revealed that neither Scrapling nor Firecrawl supersedes this tool — neither has the token budgeting, pointer protocol, state ledger, or cross-encoder reranking that this project provides.

**The key finding:** Scrapling's `page.markdown()` delegates to **`markdownify`** — a dedicated, purpose-built HTML→Markdown conversion library. This is what this project should adopt.

---

## 3. The Decision: Option B

### 3.1 The Two Tools Being Compared

**`trafilatura` (current):**
- An *extraction* tool — uses ML heuristics to identify "main content" (strips nav bars, ads, footers, cookie banners, sidebars)
- After extraction, it converts the kept content to Markdown
- Does two jobs: (1) ML content scoring/filtering, (2) Markdown conversion

**`markdownify` (proposed addition):**
- A *conversion* tool only — walks the HTML DOM tree and converts every element to proper Markdown
- Has zero opinion about what is "main content"
- Does one job: (2) Markdown conversion

### 3.2 Why Not Just Replace trafilatura Entirely?

If you blindly replace `trafilatura.extract(html)` with `markdownify(html)`, you lose the ML noise removal. For messy pages (news sites, landing pages with sidebars/ads), trafilatura's content scoring is genuinely valuable.

For clean documentation pages (docs.python.org, docs.rs, GitHub READMEs, technical articles) which are this tool's primary targets, the difference is small — but trafilatura still handles the extraction step better.

### 3.3 The Chosen Solution: Use Both

```
trafilatura extracts → markdownify converts
```

Specifically:

```python
# Step 1: trafilatura does what it's good at — ML content identification
# output_format='html' makes it return cleaned HTML instead of Markdown
extracted_html = trafilatura.extract(
    raw_html,
    output_format='html',
    include_tables=True,
    include_images=False,
    include_links=True,
)

# Step 2: markdownify does what it's good at — faithful, CommonMark-correct conversion
from markdownify import markdownify as md
clean_markdown = md(extracted_html, strip=['script', 'style'])
```

This gives you:
- trafilatura's ML noise removal (nav bars, ads, boilerplate stripped) ✅
- markdownify's CommonMark-correct Markdown output ✅
- Proper code block fencing (backtick, not broken regex) ✅
- Proper table conversion (pipe tables, not flattened text) ✅
- Preamble/abstract text preserved before first heading ✅
- HTML comments stripped before conversion ✅
- Zero GPU, zero ML model loading in this step ✅

---

## 4. How the Current Pipeline Works vs After the Change

### 4.1 Before (current state)

```
URL
 │
 ├─ curl_cffi.requests.get(url)        [fast HTTP, TLS fingerprint spoofing]
 │  or camoufox browser                [JS rendering for dynamic pages]
 │
 ▼
raw HTML string
 │
 ▼
trafilatura.extract(html)              [ML extraction → produces Markdown directly]
 │                                     ← THIS IS THE PROBLEM STEP
 │  Produces Markdown that is:
 │    - Not fully CommonMark-compliant
 │    - Has inconsistent code fence handling
 │    - Drops document preamble
 │    - Leaks HTML comments as headings
 │    - Drops short sections silently
 │
 ▼
chunker.py receives dirty Markdown
 │
 ▼  [defensive code runs: _get_unclosed_fences, _clean_section_content,
 │   CODE_BLOCK_PATTERN matching, placeholder substitution, etc.]
 │   ← BUG FACTORY: each defensive measure introduces new edge cases
 │
 ▼
clean passages → reranker → vault → daemon → MCP
```

### 4.2 After (proposed)

```
URL
 │
 ├─ curl_cffi.requests.get(url)        [unchanged]
 │  or camoufox browser                [unchanged]
 │
 ▼
raw HTML string
 │
 ▼
trafilatura.extract(html,              [ML extraction → produces clean HTML]
  output_format='html',               ← only change: ask for HTML output, not Markdown
  include_tables=True)
 │
 ▼
clean, noise-removed HTML
 │
 ▼
markdownify(extracted_html)            [faithful, CommonMark-correct conversion]
 │
 │  Produces Markdown that is:
 │    - Fully CommonMark-compliant
 │    - Proper backtick fenced code blocks
 │    - Proper pipe tables
 │    - Preamble text preserved
 │    - HTML comments already stripped by markdownify
 │
 ▼
chunker.py receives CLEAN Markdown
 │
 ▼  [defensive code is now handling well-formed input]
 │   [most edge cases never trigger because input is clean]
 │
 ▼
clean passages → reranker → vault → daemon → MCP [all unchanged]
```

---

## 5. Exact Changes Required

### 5.1 New dependency

Add to `requirements.txt`:
```
markdownify>=1.2.0
```

That is the only new dependency. `markdownify` is ~50KB of pure Python. No model downloads, no CUDA, no GPU. Its only dependency is `beautifulsoup4` (which `trafilatura` already pulls in transitively).

### 5.2 `fetcher.py` — the only file that changes

**Find the section** where `trafilatura.extract()` is called (you will see `import trafilatura` at the top of the file and a call like `trafilatura.extract(html_content, ...)` or `trafilatura.extract(downloaded, ...)`).

**Change 1: Add import at top of file**
```python
from markdownify import markdownify as md_convert
```

**Change 2: Modify the trafilatura call**

Before:
```python
# Something like this (exact call may vary — check the actual function):
content = trafilatura.extract(
    html_content,
    include_tables=True,
    include_images=False,
    # ... other args
)
```

After:
```python
# Step 1: trafilatura extracts main content as HTML (not Markdown)
extracted_html = trafilatura.extract(
    html_content,
    output_format='html',       # ← KEY CHANGE: get clean HTML, not Markdown
    include_tables=True,
    include_images=False,
    # ... keep all other existing args unchanged
)

# Step 2: markdownify converts to proper CommonMark Markdown
if extracted_html:
    content = md_convert(
        extracted_html,
        strip=['script', 'style', 'head'],
        heading_style='ATX',    # Uses # ## ### style headings
        newline_style='backslash',
    )
else:
    content = ''
```

**Important:** If `trafilatura.extract()` returns `None` (which it does when it can't identify main content), handle that gracefully before passing to `md_convert`. The `if extracted_html:` guard above handles this.

**Change 3: Fallback when trafilatura returns None**

trafilatura sometimes returns `None` for pages it cannot parse (paywalled content, login-required pages, etc.). Your existing code likely already handles this — keep that handling. The only change is that when trafilatura succeeds, the result flows through markdownify before being returned.

### 5.3 Nothing else changes

The following files are **completely unchanged**:

| File | Status | Reason |
|---|---|---|
| `chunker.py` | ✅ Unchanged | Receives the same type (str of Markdown). Input will be cleaner. |
| `vault.py` | ✅ Unchanged | No dependency on how Markdown was produced |
| `daemon.py` | ✅ Unchanged | No dependency on how Markdown was produced |
| `daemon_client.py` | ✅ Unchanged | No dependency on how Markdown was produced |
| `mcp_server.py` | ✅ Unchanged | No dependency on how Markdown was produced |
| `parallel_pipeline.py` | ✅ Unchanged | No dependency on how Markdown was produced |
| `neural_fast.py` | ✅ Unchanged | No dependency on how Markdown was produced |
| `searcher.py` | ✅ Unchanged | No dependency on how Markdown was produced |
| `camoufox` browser path | ✅ Unchanged | JS rendering already works, Scrapling not needed |

---

## 6. JavaScript Sites — Already Handled

The question was raised whether JavaScript-rendered pages (SPAs, Cloudflare-protected pages, dynamically loaded content) are supported.

**Answer: yes, already.** The current stack has two fetch modes:

1. **`curl_cffi`** — Fast HTTP client with TLS fingerprint spoofing. Does NOT execute JavaScript. Used for static pages, most documentation, GitHub.

2. **`camoufox`** — Full stealth browser (Firefox-based, Playwright-powered). DOES execute JavaScript. Used for JS-rendered pages, Cloudflare challenges, bot-protected content. Triggered by `force_browser=True` or when plain HTTP fails.

Scrapling's `StealthyFetcher` does the same thing (Chromium-based instead of Firefox-based). This project does not need to adopt Scrapling's fetcher. The camoufox path already handles JavaScript rendering.

After the browser renders the page, the resulting HTML flows through the same trafilatura → markdownify pipeline described above.

---

## 7. What This Change Does NOT Fix

The following bug categories from the audit reports are **not addressed by this change** and still require their own fixes:

**In `chunker.py` (structural Markdown splitting — not HTML conversion):**
- `safe_truncate_markdown` table row backtracking (DEF-V5-02, Pass 7) — 95%+ text loss on pages starting with tables
- `chunk_markdown_smart` short section silent deletion (DEF-02, Pass 6) — sections < `min_chunk_words` dropped silently

**In `vault.py` (all individually trivial fixes):**
- NaN/Inf TTL handling in `stage_document`
- Negative TTL check (`ttl == 0` → should be `ttl <= 0`)
- Non-string category crash in `get_ledger_entries`
- URL fragment preservation breaking cache deduplication
- Non-serializable tag crash in `get_ledger_markdown`
- Corrupted timestamp crash in `get_ledger_markdown`

**In `parallel_pipeline.py`:**
- Zero results falsely reported as "All sources blocked by paywalls"
- `[None](None)` link emission on missing title/URL

**In `daemon.py`:**
- Phantom document staging (error strings staged as valid documents)
- Silent socket disconnect on non-JSON-serializable result

**In `searcher.py`:**
- 2-letter technical query cleaning failure (`AI`, `Go`, `C#`, `R`)

These are all correctness bugs that should be fixed separately from this change. The remediation plans in `remediation_plan_v3.md` through `remediation_plan_v5.md` contain the exact fix specifications for each.

---

## 8. Expected Impact of This Change

### Bugs expected to disappear or become much rarer

The following bug categories traced to dirty/inconsistent HTML→Markdown conversion from trafilatura will be eliminated or heavily reduced once markdownify produces clean CommonMark output:

| Bug | Root cause | Expected outcome |
|---|---|---|
| CommonMark tilde fence bugs | trafilatura's Markdown had inconsistent fence syntax | ✅ Eliminated — markdownify outputs only backtick fences |
| JS banner desync (content sliced from wrong string offset) | HTML cleaning applied before/after wrong step | ✅ Eliminated — markdownify processes clean HTML |
| Preamble silent data loss | chunker skips text before first `#` heading | ✅ Eliminated — markdownify outputs preamble as plain paragraphs |
| HTML comment heading leakage | HTML comments reached chunker as `<!-- # Heading -->` | ✅ Eliminated — markdownify strips HTML comments before conversion |
| Unclosed code fence from inline `<code>` tags | trafilatura produced partial code fences | ✅ Eliminated — markdownify properly closes all code spans |
| False headings from code comments (`# comment` inside code blocks) | Unclosed fences let comments reach heading regex | ✅ Eliminated — markdownify always closes code fences |

### No regressions expected

Since `fetcher.py` returns a `str` of Markdown both before and after the change, and all downstream code consumes that string without caring how it was produced, there are **zero interface changes** and **zero expected regressions** in the daemon, vault, MCP server, CLI, or reranker.

---

## 9. Testing Guidance

After the change, run the existing test suites to verify no regressions:

```bash
cd /home/prtyksh/tmp/web_search_tool

# Run all adversarial test suites (these should still pass)
.venv/bin/python -m unittest tests/test_adversarial_v2_fuzzing.py
.venv/bin/python -m unittest tests/test_adversarial_v3_fuzzing.py
.venv/bin/python -m unittest tests/test_adversarial_v4_fuzzing.py
.venv/bin/python -m unittest tests/test_adversarial_v5_fuzzing.py
```

Manual smoke tests:

```bash
# Test basic fetch → markdown conversion
./run_tool.sh fetch "https://docs.python.org/3/library/asyncio.html"
# Expected: clean Markdown, ≤6,000 chars, with proper code blocks

# Test a documentation page with code examples
./run_tool.sh fetch "https://docs.rs/tokio/latest/tokio/"
# Expected: Markdown with properly fenced Rust code blocks

# Test fast neural search (exercises full pipeline)
./run_tool.sh fast "rust tokio async runtime" --density compact --num 3
# Expected: ≤3,500 chars, structured Markdown, no [None](None) links

# Test a JS-rendered page (exercises camoufox path)
./run_tool.sh fetch "https://github.com/tokio-rs/tokio" --force-browser
# Expected: clean Markdown from browser-rendered HTML
```

---

## 10. Summary

| Aspect | Before | After |
|---|---|---|
| HTML→Markdown conversion | `trafilatura.extract(html)` → Markdown directly | `trafilatura.extract(html, output_format='html')` → `markdownify(extracted_html)` |
| ML noise removal | ✅ trafilatura | ✅ trafilatura (unchanged) |
| CommonMark compliance | ⚠️ Approximate | ✅ Proper |
| Code block fidelity | ⚠️ Sometimes broken | ✅ Always correct |
| Table handling | ⚠️ Often flattened | ✅ Proper pipe tables |
| Preamble preservation | ⚠️ Sometimes lost | ✅ Always kept |
| HTML comment handling | ⚠️ Could leak as headings | ✅ Stripped before conversion |
| New dependencies | — | `markdownify>=1.2.0` (50KB, pure Python) |
| Files changed | — | `fetcher.py` only |
| Files unchanged | — | All other 8 core files |
| GPU required | No | No |
| JS page support | ✅ camoufox | ✅ camoufox (unchanged) |
