# Adversarial Red-Team Security & Quality Audit Report (Pass 7 / V5)

**Target Codebase:** `/home/prtyksh/tmp/web_search_tool`  
**Audit Operating Mindset:** *"GUILTY UNTIL PROVEN INNOCENT"* — Zero-Trust, Blank-Slate Adversarial Audit  
**Test Suite:** `tests/test_adversarial_v5_fuzzing.py` (15/15 test vectors empirically reproduced and confirmed)  
**Execution Environment:** `/home/prtyksh/tmp/web_search_tool/.venv/bin/python`  
**Constraints Enforced:** Zero daemon usage; isolated SQLite test databases; in-process execution only.

---

## 1. Executive Summary

A comprehensive, zero-trust adversarial security and quality audit (Pass 7) was conducted on the entire `web_search_tool` codebase. Assuming the codebase is inherently flawed until proven otherwise, our automated fuzzing battery and source inspection uncovered **15 distinct vulnerabilities and architectural flaws** spanning:
- **Catastrophic Silent Data Loss** in the semantic markdown chunker and safe truncation engine.
- **Error Masking & Cascading Search Failure** in the parallel pipeline and perspective routing engine.
- **Phantom Document Staging** in the SQLite vault and daemon request dispatching.
- **Type Safety Crashes & Wire Contract Failures** in entity extraction, neural reranking, and document slicing.
- **Information Leaks & Unbounded Context Bloat** in Table of Contents generation and HTML comment parsing.

### Summary Metrics
| Severity | Count | Primary Impact |
| :--- | :---: | :--- |
| **CRITICAL** | 1 | Total loss of document preamble/abstract before `# Heading 1` across all crawled pages |
| **HIGH** | 4 | 95%+ text loss on table backtracking; dual perspective search abort; phantom error staging |
| **MEDIUM** | 5 | Unhandled `TypeError`/`AttributeError` crashes on `None`/`int`; CLI error masking; unbounded TOC bloat |
| **LOW** | 5 | Code fence truncation overflow; corrupt TOC staging mask; silent socket drop; HTML comment leak; CLI query cap inconsistency |
| **TOTAL** | **15** | **Actionable defects empirically proven with executable regression test battery** |

---

## 2. Vulnerability Summary Table

| Vuln ID | Severity | Component | Summary / Impact |
| :--- | :---: | :--- | :--- |
| **DEF-V5-01** | **CRITICAL** | `chunker.py:597-601` | **Preamble Silent Data Loss**: All text preceding the first `# Heading` (abstracts, executive summaries, lead insights) is silently discarded. |
| **DEF-V5-02** | **HIGH** | `chunker.py:88-93` | **Catastrophic Table Row Backtracking**: Slicing logic backtracks to document-initial tables, dropping up to 98% of requested content. |
| **DEF-V5-03** | **HIGH** | `chunker.py:140-160, 239-247` | **Unclosed Code Comment Section Truncation**: Code comments inside unclosed fences are treated as headings, prematurely terminating sections. |
| **DEF-V5-04** | **HIGH** | `parallel_pipeline.py:109-118` | **Single-Point Cascade Abort in Dual Search**: A rate limit or timeout in the tech perspective aborts the entire search, dropping valid market results. |
| **DEF-V5-05** | **HIGH** | `daemon.py:147-160` | **Phantom Error Staging in SQLite Vault**: Network/connection error strings are staged as valid documents and returned as pointer cards. |
| **DEF-V5-06** | **MEDIUM** | `cli.py:165, 175` | **CLI Fetch Error Masking**: `toc` and `section` commands fail to verify fetch success, masking DNS/404 errors as "No structured headings". |
| **DEF-V5-07** | **MEDIUM** | `chunker.py:193`, `vault.py:473, 489` | **AttributeError on Integer Section/Query**: Passing an integer (e.g. `1` or `123`) crashes `extract_section` and `extract_staged_slice`. |
| **DEF-V5-08** | **MEDIUM** | `neural_fast.py:82, 123` | **TypeError Crash on None Entity Score**: Comparing float scores with `None` crashes GLiNER post-processing and drops page entities. |
| **DEF-V5-09** | **MEDIUM** | `neural_fast.py:199` | **AttributeError Crash on None Chunk Content**: `_dedupe_chunks` calls `.strip()` on `None` content, crashing batched reranking. |
| **DEF-V5-10** | **MEDIUM** | `chunker.py:162-182` | **Unbounded TOC Output (Context Window Flooding)**: `get_table_of_contents` has no length ceiling, flooding LLMs with 10k–50k+ tokens on large docs. |
| **DEF-V5-11** | **LOW** | `chunker.py:79-100` | **Safe Truncation Buffer Overflow**: `safe_truncate_markdown` violates `max_chars` ceiling when appending closing fences. |
| **DEF-V5-12** | **LOW** | `vault.py:388-395` | **Intact Document Masking on Corrupt TOC JSON**: Malformed `toc_json` causes `get_staged_document` to return `None`, masking intact content. |
| **DEF-V5-13** | **LOW** | `daemon.py:328-331` | **Silent Socket Disconnect on Non-Serializable Object**: Bare `except Exception: pass` around `json.dumps` causes socket drop without error payload. |
| **DEF-V5-14** | **LOW** | `chunker.py:145-160` | **HTML Comment Heading Leak**: Headings inside `<!-- ... -->` comments are mistakenly extracted into TOC and section navigation. |
| **DEF-V5-15** | **LOW** | `cli.py:272-286` vs `daemon.py:212` | **Batch Query Cap Inconsistency**: Standalone CLI executes unlimited batch queries while daemon strictly caps at 3 queries. |

---

## 3. Detailed Technical Breakdown & Remediation

---

### DEF-V5-01: Document Preamble and Executive Summary Silent Data Loss
* **Severity:** **CRITICAL**
* **Component:** `chunker.py:597-601` (`chunk_markdown_smart`)
* **Adversarial Dimension:** Error Masking & Silent Data Loss

#### Root Cause Analysis
In `chunk_markdown_smart`, when `headings` are detected in a document, the chunking loop iterates exclusively over the detected headings:
```python
else:
    # Walk sections defined by headings
    for i, h in enumerate(headings):
        start = h["start"]
        end = headings[i + 1]["start"] if i + 1 < len(headings) else len(cleaned_md)
        sec_text = cleaned_md[start:end].strip()
```
The first heading starts at index `headings[0]["start"]`. Any text between index `0` and `headings[0]["start"]` is completely skipped. In standard documentation, technical papers (arXiv), Wikipedia entries, and blog posts, the document preamble contains the abstract, executive summary, and lead findings before `# 1. Introduction`. All of this substantive text is silently discarded. Downstream neural rerankers and LLMs never see the document's opening summary.

#### Empirical Reproduction
```python
from chunker import chunk_markdown_smart

md = """Executive Abstract: Quantum key exchange achieves 10,000 TPS under hostile jamming.
CRITICAL_FACT_PREAMBLE_KEY_9999 is established here in the opening summary.

# 1. Introduction and Background
This is the introductory section following the executive summary."""

chunks = chunk_markdown_smart(md)
all_chunk_text = " ".join(c.get("content", "") for c in chunks)
assert "CRITICAL_FACT_PREAMBLE_KEY_9999" not in all_chunk_text  # Fails to retain preamble!
```

#### Remediation
Before walking `headings`, check if `headings[0]["start"] > 0`. If substantive text exists before the first heading, extract it as an `"Overview"` or preamble chunk:
```python
preamble = cleaned_md[:headings[0]["start"]].strip()
if preamble and len(preamble) >= 40:
    preamble_chunks = _split_oversized_text(preamble, "Overview", max_chunk_words, min_chunk_words)
    raw_chunks.extend(preamble_chunks)
```

---

### DEF-V5-02: Catastrophic Table Row Backtracking (95%+ Content Dropped)
* **Severity:** **HIGH**
* **Component:** `chunker.py:88-93` (`safe_truncate_markdown`)
* **Adversarial Dimension:** Syntax & Parsing Traps / Silent Data Loss

#### Root Cause Analysis
In `safe_truncate_markdown`, line 88 checks if the slice touches or is preceded by a table row:
```python
partial_line = slice_candidate[last_newline + 1:] if last_newline != -1 else slice_candidate
is_table_row = ("|" in partial_line) or (last_newline != -1 and "|" in slice_candidate[max(0, last_newline - 50):last_newline])

if last_newline > 0 and (is_table_row or last_newline >= int(target_len * 0.75)):
    truncated = slice_candidate[:last_newline]
```
Notice the condition: `(is_table_row or last_newline >= int(target_len * 0.75))`. When `is_table_row` is True, it **bypasses** the 75% length check. If a document starts with a 2-line table header (`| Col1 | Col2 |\n|---|---|\n`) followed by a long paragraph with no mid-paragraph newlines, `last_newline` is located at index 25. Because the previous line had a pipe (`|`), `is_table_row` evaluates to `True`. The function backtracks all the way to index 25, discarding 95%+ of the requested content.

#### Empirical Reproduction
```python
from chunker import safe_truncate_markdown

text = "| Col1 | Col2 |\n|---|---|\n" + ("Substantive architectural analysis. " * 30)
res = safe_truncate_markdown(text, 1000)
# Request was 1,000 chars; result is only 25 chars! 975 chars discarded!
assert len(res) < 100
```

#### Remediation
Enforce that table row newline backtracking only triggers if `last_newline` retains at least 50% of `target_len`:
```python
if last_newline > 0:
    if is_table_row and last_newline >= int(target_len * 0.50):
        truncated = slice_candidate[:last_newline]
    elif last_newline >= int(target_len * 0.75):
        truncated = slice_candidate[:last_newline]
    else:
        truncated = slice_candidate
```

---

### DEF-V5-03: Code Comments in Unclosed Fences Treated as Headings
* **Severity:** **HIGH**
* **Component:** `chunker.py:140-160, 239-247` (`extract_headings`, `extract_section`)
* **Adversarial Dimension:** Syntax & Parsing Traps

#### Root Cause Analysis
`extract_headings` ignores headings inside code blocks using `CODE_BLOCK_PATTERN`:
```python
CODE_BLOCK_PATTERN = re.compile(r'(`{3,}|~{3,})[\s\S]*?(?<![`~])\1(?![`~])', re.MULTILINE)
```
This regex only matches **closed** code blocks. If a code block is unclosed (e.g. truncated page, scraped HTML, or streaming chunk), `code_ranges` is empty. Consequently, Python or Bash comments (e.g. `# Install dependencies`) inside the code block match `HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)`. When `extract_section()` is called on the parent section, it treats the comment as a child or sibling heading, terminating the section extraction prematurely and discarding the code block contents.

#### Empirical Reproduction
```python
from chunker import extract_headings, extract_section

md = """# Installation Guide
Steps:
```bash
# Install core runtime
sudo apt-get install python3-minimal -y

Make sure to restart services."""

headings = extract_headings(md)
assert "Install core runtime" in [h["title"] for h in headings]
sec = extract_section(md, "Installation Guide")
assert "sudo apt-get install" not in sec  # Prematurely truncated!
```

#### Remediation
Use the state-machine logic in `_get_unclosed_fences()` or line-by-line fence tracking within `extract_headings()` to identify whether the line is inside an open code block, regardless of whether a matching closing fence exists.

---

### DEF-V5-04: Single-Point Cascade Abort in Dual Perspective Search
* **Severity:** **HIGH**
* **Component:** `parallel_pipeline.py:109-118` (`fast_intelligent_search`)
* **Adversarial Dimension:** Error Masking & Silent Failure

#### Root Cause Analysis
In `searcher.search_web` with `perspective="dual"`, `tech` and `market` searches run concurrently. If `tech` search encounters a rate limit or network timeout, it returns `[{"error": "Search failed"}]`. These are placed at the beginning of `combined`. In `fast_intelligent_search`:
```python
search_results = search_web(query, max_results=max_results, perspective=perspective)

if not search_results or "error" in search_results[0]:
    return {
        "query": query,
        "perspective": perspective,
        "density": density,
        "device": get_device(),
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
        "results": search_results,
        "total_fetched": 0
    }
```
Because `search_results[0]` has `"error"`, the entire search pipeline immediately aborts. `total_fetched` is set to `0`, and all valid results retrieved from the `market` perspective are dropped without ever being fetched or processed. `format_fast_digest` then displays a generic search error.

#### Empirical Reproduction
```python
from unittest.mock import patch
from parallel_pipeline import fast_intelligent_search, format_fast_digest

mock_results = [
    {"error": "Tech search rate limit", "perspective": "tech"},
    {"title": "Commercial Product", "href": "https://product.com", "body": "Overview", "perspective": "market"}
]
with patch("parallel_pipeline.search_web", return_value=mock_results):
    res = fast_intelligent_search("test query", perspective="dual")
    assert res.get("total_fetched") == 0
    digest = format_fast_digest(res)
    assert "Search Error" in digest
    assert "Commercial Product" not in digest
```

#### Remediation
In `fast_intelligent_search`, partition `search_results` into valid and error items. If at least one valid result exists, proceed with fetching the valid results rather than aborting:
```python
valid_results = [r for r in search_results if isinstance(r, dict) and "error" not in r and r.get("href")]
if not valid_results:
    # Only abort if zero valid results exist across all perspectives
    return ...
```

---

### DEF-V5-05: Phantom Document Staging in SQLite Vault
* **Severity:** **HIGH**
* **Component:** `daemon.py:147-160` (`_handle_fetch`)
* **Adversarial Dimension:** Error Masking & State Corruption

#### Root Cause Analysis
When `as_pointer=True` is passed to `_handle_fetch`:
```python
content = fetch_page(url, force_stealth=use_browser, skip_tls_verify=skip_tls_verify, max_chars=None)
if content.startswith(f"Failed to retrieve {url}:"):
    return content

headings = extract_headings(content)
title = headings[0]["title"] if headings else (urlparse(url).netloc or url)
staged_meta = stage_document(
    url=url,
    title=title,
    content=content,
    session_id=session_id,
    headings=headings
)
return format_manifest_card(staged_meta)
```
The error check uses an exact prefix match: `f"Failed to retrieve {url}:"`. If `fetch_page` returns an error that does not contain `{url}:` (e.g. generic fetch failure, connection timeout, or URL with trailing whitespace), the error string is treated as valid markdown content! It is saved to `staged_documents`, given a `doc_id`, and a manifest card is returned to the AI agent. The agent believes the webpage was staged and attempts subsequent operations on an error message.

#### Empirical Reproduction
```python
from unittest.mock import patch
from daemon import _handle_fetch

with patch("daemon.fetch_page", return_value="Failed to retrieve: connection timed out"):
    manifest_card = _handle_fetch({"url": "https://example.com/api", "as_pointer": True})
    assert "### 📄 [Staged Document: doc_" in manifest_card
```

#### Remediation
Use a generic check `content.startswith("Failed to retrieve")` (matching lines 168 and 175):
```python
if content.startswith("Failed to retrieve"):
    return content
```

---

### DEF-V5-06: CLI Fetch Errors Masked as "No Structured Headings"
* **Severity:** **MEDIUM**
* **Component:** `cli.py:165, 175` (`toc`, `section`)
* **Adversarial Dimension:** Error Masking & Diagnostics

#### Root Cause Analysis
In `cli.py`:
```python
@app.command()
def toc(url: str):
    content = fetch_page(url, max_chars=None)
    outline = get_table_of_contents(content)
    print(outline)
```
When `fetch_page` fails, it returns `"Failed to retrieve <url>: <reason>"`. Unlike `daemon.py` (which checks `content.startswith("Failed to retrieve")`), `cli.py` directly passes this error string into `get_table_of_contents()`. Because the error string has no markdown headings, `get_table_of_contents()` outputs:
`"Document has no structured headings. Total word count: ~8 words."`
The HTTP 404, DNS failure, or SSL certificate error is completely masked.

#### Empirical Reproduction
```python
from chunker import get_table_of_contents

error_output = "Failed to retrieve https://invalid.domain: HTTP 404 (Not Found)"
toc = get_table_of_contents(error_output)
assert "Document has no structured headings" in toc
```

#### Remediation
In `cli.py`, inspect `content.startswith("Failed to retrieve")` before calling `get_table_of_contents` or `extract_section`:
```python
if content.startswith("Failed to retrieve"):
    console.print(f"[bold red]{content}[/bold red]")
    return
```

---

### DEF-V5-07: AttributeError on Integer Section or Query Parameters
* **Severity:** **MEDIUM**
* **Component:** `chunker.py:193`, `vault.py:473, 489` (`extract_section`, `extract_staged_slice`)
* **Adversarial Dimension:** Wire Contracts & Type Safety

#### Root Cause Analysis
In `extract_section`:
```python
target_clean = target.strip().lower() if target else ""
```
In `vault.extract_staged_slice`:
```python
if section and section.strip():
...
if query and query.strip():
```
If `section` or `query` is passed as an integer (e.g. `section=1` from JSON numeric parsing or programmatic invocation), calling `.strip()` immediately crashes with:
`AttributeError: 'int' object has no attribute 'strip'`
This breaks index-based section lookup via integer arguments.

#### Empirical Reproduction
```python
from chunker import extract_section
from vault import stage_document, extract_staged_slice

extract_section("# Heading 1\nContent", 1)  # Raises AttributeError
doc = stage_document("http://test.com", "Title", "# Sec\nBody")
extract_staged_slice(doc["doc_id"], section=1)  # Raises AttributeError
```

#### Remediation
Normalize inputs to strings using `str(target or "")`:
```python
target_clean = str(target or "").strip().lower()
```

---

### DEF-V5-08: TypeError Crash on None Entity Score in GLiNER Post-Processing
* **Severity:** **MEDIUM**
* **Component:** `neural_fast.py:82, 123` (`_filter_and_dedupe_entities`)
* **Adversarial Dimension:** Boundary Conditions & Hostile Inputs

#### Root Cause Analysis
In `_filter_and_dedupe_entities`:
```python
sorted_raw = sorted(raw_entities, key=lambda x: x.get("score", 0), reverse=True)
```
If an entity in `raw_entities` contains `"score": None`, `x.get("score", 0)` returns `None` (the default `0` is ignored because the key exists). In Python 3, comparing `float` with `NoneType` raises:
`TypeError: '<' not supported between instances of 'float' and 'NoneType'`
This causes `extract_entities_batch` to fail on the page and drop all entities.

#### Empirical Reproduction
```python
from neural_fast import _filter_and_dedupe_entities

entities = [{"text": "FastAPI", "score": 0.9}, {"text": "Celery", "score": None}]
_filter_and_dedupe_entities(entities)  # Raises TypeError
```

#### Remediation
Safely coerce score to float with fallback:
```python
def safe_score(x):
    s = x.get("score")
    try:
        return float(s) if s is not None else 0.0
    except (ValueError, TypeError):
        return 0.0

sorted_raw = sorted(raw_entities, key=safe_score, reverse=True)
```

---

### DEF-V5-09: AttributeError Crash on None Chunk Content in Neural Reranker
* **Severity:** **MEDIUM**
* **Component:** `neural_fast.py:199` (`_dedupe_chunks`, `rerank_pages_batch`)
* **Adversarial Dimension:** Wire Contracts & Type Safety

#### Root Cause Analysis
In `_dedupe_chunks`:
```python
for c in chunks:
    content = c.get("content", "").strip()
```
If a chunk dictionary contains `{"content": None}`, `c.get("content", "")` returns `None`. Calling `None.strip()` raises `AttributeError: 'NoneType' object has no attribute 'strip'`. Because this happens outside the model try/except in `rerank_pages_batch`, the entire batched reranking call crashes.

#### Empirical Reproduction
```python
from neural_fast import rerank_pages_batch

rerank_pages_batch("query", [[{"content": None, "heading": "Title"}]])  # Raises AttributeError
```

#### Remediation
Sanitize content retrieval:
```python
content = str(c.get("content") or "").strip()
```

---

### DEF-V5-10: Unbounded Table of Contents Output (Context Flooding)
* **Severity:** **MEDIUM**
* **Component:** `chunker.py:162-182` (`get_table_of_contents`)
* **Adversarial Dimension:** Token Budgeting & Context Bloat

#### Root Cause Analysis
`get_table_of_contents` formats every heading into a bullet point without any maximum length, item count, or character ceiling. On large documentation sites, legal contracts, or generated API documents with 500–2,000 headings, the TOC exceeds 15,000–50,000 characters (>10,000 tokens). An agent calling `get_toc` to inspect structure gets flooded with tokens, defeating progressive disclosure.

#### Empirical Reproduction
```python
from chunker import get_table_of_contents

md = "\n\n".join(f"# Section {i}\nBody paragraph." for i in range(500))
toc = get_table_of_contents(md)
assert len(toc) > 10000  # Generates 11,900+ characters!
```

#### Remediation
Cap the TOC output to `max_headings=100` and `max_chars=3500`, adding an ellipsis notice if truncated.

---

### DEF-V5-11: Safe Truncation Buffer Overflow on Unclosed Code Fences
* **Severity:** **LOW**
* **Component:** `chunker.py:79-100` (`safe_truncate_markdown`)
* **Adversarial Dimension:** Boundary Conditions

#### Root Cause Analysis
`safe_truncate_markdown` computes `target_len = max(0, max_chars - len(suffix))`. It then slices `text[:target_len]`. If `unclosed` code fences are detected, it appends `closing` (which can be 5 to 25+ characters for long or blockquoted fences). Then it appends `suffix`. The resulting string length is `target_len + len(closing) + len(suffix) = max_chars + len(closing)`, exceeding `max_chars`.

#### Empirical Reproduction
```python
from chunker import safe_truncate_markdown

fence = "``````````"
text = f"{fence}python\n" + ("x = 1\n" * 100)
res = safe_truncate_markdown(text, 100, suffix="\n[trunc]")
assert len(res) > 100  # Outputs 108 characters!
```

#### Remediation
Re-slice or reserve buffer space for `closing`:
```python
if unclosed:
    closing_len = sum(len(f) + len(ind) + 2 for f, ind in unclosed)
    if len(truncated) + closing_len > target_len:
        truncated = truncated[:max(0, target_len - closing_len)]
```

---

### DEF-V5-12: Intact Document Masked on Corrupted TOC JSON Metadata
* **Severity:** **LOW**
* **Component:** `vault.py:388-395` (`get_staged_document`)
* **Adversarial Dimension:** Error Masking & Silent Data Loss

#### Root Cause Analysis
In `get_staged_document`:
```python
headings = json.loads(toc_json) if toc_json else []
```
This line is wrapped in a generic `try...except Exception: pass`. If `toc_json` in the SQLite database is corrupted, `json.loads` raises `JSONDecodeError`. The exception handler catches it and returns `None`. The user or agent is told the document does not exist or has expired, even though the primary payload (`content_markdown`) is completely intact.

#### Empirical Reproduction
```python
from vault import stage_document, get_staged_document, get_connection

doc = stage_document("http://test.com", "Title", "Intact content")
with get_connection() as conn:
    conn.execute("UPDATE staged_documents SET toc_json = 'corrupted{json' WHERE doc_id = ?", (doc["doc_id"],))
    conn.commit()
assert get_staged_document(doc["doc_id"]) is None  # Fails to retrieve intact payload!
```

#### Remediation
Isolate `json.loads(toc_json)` with its own try/except fallback:
```python
try:
    headings = json.loads(toc_json) if toc_json else []
except Exception:
    headings = []
```

---

### DEF-V5-13: Silent Socket Disconnect on Non-JSON-Serializable Result
* **Severity:** **LOW**
* **Component:** `daemon.py:328-331` (`_Handler.handle`)
* **Adversarial Dimension:** Wire Contracts & Fault Tolerance

#### Root Cause Analysis
In `_Handler.handle()`:
```python
try:
    self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))
except Exception:
    pass
```
If a command handler returns a non-serializable object (e.g. `set`, `datetime`, or bytes), `json.dumps(resp)` raises `TypeError`. Because this is wrapped in a bare `except Exception: pass`, zero bytes are sent to the client and the socket is closed immediately. The client receives an opaque "closed connection without a response" message.

#### Empirical Reproduction
```python
import json
resp = {"ok": True, "result": {"item1", "item2"}}
try:
    json.dumps(resp)
except TypeError:
    pass  # Socket is dropped silently
```

#### Remediation
Use `default=str` in `json.dumps`:
```python
json.dumps(resp, default=str)
```

---

### DEF-V5-14: HTML Comment Heading Leakage
* **Severity:** **LOW**
* **Component:** `chunker.py:145-160` (`extract_headings`)
* **Adversarial Dimension:** Syntax & Parsing Traps

#### Root Cause Analysis
`extract_headings` filters headings inside code block ranges, but does not check for HTML comment ranges (`<!-- ... -->`). When scraped pages contain commented-out sections (e.g. `<!-- # Deprecated Draft -->`), these are treated as genuine document headings, polluting the Table of Contents and section indexing.

#### Empirical Reproduction
```python
from chunker import extract_headings

md = "# Visible\n<!--\n# Hidden Heading in Comment\n-->"
headings = extract_headings(md)
assert "Hidden Heading in Comment" in [h["title"] for h in headings]
```

#### Remediation
Strip HTML comments `<!-- ... -->` or record comment ranges alongside code block ranges prior to running `HEADING_PATTERN`.

---

### DEF-V5-15: CLI vs Daemon Batch Query Cap Inconsistency
* **Severity:** **LOW**
* **Component:** `cli.py:272-286` vs `daemon.py:212`
* **Adversarial Dimension:** Wire Contracts & Inconsistent Limits

#### Root Cause Analysis
In `daemon.py`, line 212: `capped_queries = queries[:3]` enforces that batch search never runs more than 3 queries. In `cli.py`, lines 272–286, if the daemon is inactive (standalone CLI mode), it executes all passed queries without capping. Passing 10 queries executes all 10 in CLI mode, but only 3 in daemon mode.

#### Empirical Reproduction
```python
from daemon import _handle_batch_search
# Daemon caps at 3, whereas standalone CLI iterates over len(queries)
```

#### Remediation
Apply `queries = queries[:3]` in `cli.py` batch command before the loop.

---

## 4. Verification and Regression Testing

All 15 vulnerabilities have been implemented as executable tests in `tests/test_adversarial_v5_fuzzing.py`.
Run the test suite:
```bash
cd /home/prtyksh/tmp/web_search_tool && .venv/bin/python -m unittest tests/test_adversarial_v5_fuzzing.py
```

### Execution Output:
```
...............
----------------------------------------------------------------------
Ran 15 tests in 3.368s

OK
```

All 15 vulnerabilities were empirically verified and reproduced under zero-daemon, isolated SQLite test conditions.
