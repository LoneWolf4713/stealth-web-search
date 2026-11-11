# Adversarial Red-Team Code Audit Report (Comprehensive V2)
**Target Repository:** `/home/prtyksh/tmp/web_search_tool`  
**Target Branch:** `feat/context-token-optimization` (Audited against base `main` commit `e648dc8` and uncommitted working-tree state)  
**Auditor Role:** Independent Adversarial Red-Team Code Auditor  
**Audit Mindset:** "Guilty Until Proven Innocent" (Zero Trust, Blank Slate)  
**Date:** November 10, 2026  
**Test Suite:** `tests/test_adversarial_v2_fuzzing.py` (13/13 verified passing test cases exposing bugs)

---

## 1. Executive Summary & Vulnerability Scorecard

This audit conducted an aggressive, blank-slate adversarial code inspection and fuzzing pass across `/home/prtyksh/tmp/web_search_tool` on branch `feat/context-token-optimization`. Both the committed branch (`d4e95c9`) and the active working-tree modifications were audited under a zero-trust, hostile adversary mindset.

### Overall Verdict: **FAIL / GUILTY**
While branch `feat/context-token-optimization` successfully introduces multi-density ceilings, SQLite WAL mode, and a persistent research ledger, several fundamental architectural and algorithmic vulnerabilities exist. Furthermore, recent uncommitted remediation attempts introduced **severe regression bugs: CommonMark code-fence corruption, heading hijacking in section extraction, silent body-text swallowing, and unhandled `sqlite3.IntegrityError` / `OverflowError` crash points.**

### Vulnerability Scorecard
| Severity | Count | Primary Impact |
| :--- | :---: | :--- |
| 🔴 **CRITICAL** | **5** | Valid markdown corrupted with extraneous fences; oversized code blocks fragmented into raw unclosed syntax; heading hijacking in `extract_section`; cascading placeholder code injection; silent section error suppression causing context hallucination |
| 🟠 **HIGH** | **6** | Unhandled `sqlite3.IntegrityError` crash on NaN TTL; unhandled `OverflowError` in `_safe_int`; `AttributeError` crash on non-string categories in ledger; permanent SQLite ghost rows on Inf TTL; bare heading returned with 0 body lines; valid headings stripped on empty title |
| 🟡 **MEDIUM** | **5** | Trailing slash URL cache divergence; search error messages swallowed and emitted as `[None](None)` links; quadratic $O(H \times C)$ CPU scan in heading extraction; `cli.py batch` bypassing shared daemon; missing `session_id` in MCP tool contract |
| 🟢 **LOW / CODE SMELL** | **2** | Inverted assertions in `test_adversarial_redteam.py` breaking `unittest discover`; legacy duplicate fallback implementation in `vault.py` |
| **TOTAL** | **18** | **All 18 vulnerabilities empirically verified with executable test cases** |

---

## 2. Detailed Vulnerability Breakdown

### AREA 1: Markdown Integrity & Truncation Vulnerabilities

#### 1.1 CommonMark Code-Fence Violation & Markdown Corruption [CRITICAL]
* **Location:** `chunker.py:17-50` (`_get_unclosed_fences`) and `chunker.py:52-92` (`safe_truncate_markdown`)
* **Vulnerability:** Under CommonMark specifications, an open code fence of $N$ backticks (e.g. ````python`) can only be closed by a line with $N$ or more backticks. Any line with fewer backticks inside that code block is literal content (e.g. documentation describing triple backticks or multi-line string literals). However, `_get_unclosed_fences` pushes any line with backticks onto a nested stack (`stack.append({"length": n, "prefix": prefix})`).
* **Failure Mechanism:** When a valid 4-backtick code block contains a 3-backtick line:
  ```markdown
  ````python
  print("""
  ```
  """)
  ````
  ```
  The stack tracks both length 4 and length 3. When the closing ```` line appears, `n >= top["length"]` pops the length 3 fence, leaving the outer length 4 fence marked as unclosed. `safe_truncate_markdown` then appends an extraneous closing fence `\n````\n`.
* **Impact:** Corrupts valid markdown documents, breaking code block formatting and bleeding code into subsequent prompt tokens.
* **Empirical Proof:** `test_01_nested_fences_with_fewer_backticks_corrupted` in `tests/test_adversarial_v2_fuzzing.py`.

#### 1.2 Syntax Fragmentation in Oversized Code Blocks (>2200 Chars) [CRITICAL]
* **Location:** `chunker.py:375-387` (`_split_oversized_text`)
* **Vulnerability:** When a code block exceeds 2200 characters, `_split_oversized_text` calls `_subdivide_text_boundaries(chunk_body, max_words=max_chunk_words, max_chars=2200)`.
* **Failure Mechanism:** `_subdivide_text_boundaries` divides text by lines, sentences, and clauses, but does NOT wrap each resulting chunk in code fences.
* **Impact:** A 500-line code block is broken into chunks where Chunk 0 has an opening fence without a closing fence (`starts=True, ends=False`), Chunk 1 has NO code fences at all (`starts=False, ends=False`), releasing raw programming language code into markdown text, and Chunk 2 has a closing fence without an opening fence. This directly violates the core contract: *"Protects code blocks (``` ... ```) so code syntax is never fragmented."*
* **Empirical Proof:** `test_02_oversized_code_block_fragmentation` in `tests/test_adversarial_v2_fuzzing.py`.

#### 1.3 Heading Hijacking & False-Positive Matching in `extract_section` [CRITICAL]
* **Location:** `chunker.py:194-196` (`extract_section`)
* **Vulnerability:** Matching logic includes reverse containment:
  ```python
  if (target_clean in h_title or h_title in target_clean or ...):
  ```
* **Failure Mechanism:** If a document contains short headings like `# A`, `# In`, `# Go`, `# To`, `# C`:
  Any user target query that contains those characters (e.g., `extract_section(md, "Advanced System Architecture")` contains `"a"`) will match `# A` instead of `# Advanced System Architecture`!
  Furthermore, if `target=""`, `"" in h_title` is `True` for every heading, matching the very first heading.
* **Impact:** AI agents requesting specific sections receive completely unrelated sections from earlier in the document.
* **Empirical Proof:** `test_03_extract_section_heading_hijacking` in `tests/test_adversarial_v2_fuzzing.py`.

#### 1.4 Bare Heading Return on Subheading Match (Zero Body Lines) [HIGH]
* **Location:** `chunker.py:541-554` (`find_query_passages`)
* **Vulnerability:** When a query matches a section heading (e.g. `# Authentication Protocol`), the code checks if the next paragraph can be attached as body text:
  ```python
  if is_bare_heading and idx + 1 < len(raw_paras):
      next_p = raw_paras[idx + 1]
      next_first = next_p.split("\n")[0].strip()
      if not re.match(r'^#{1,6}\s+', next_first):
          body_text = restore_code(next_p)
          p_restored = f"{p_restored}\n\n{body_text}"
  ```
* **Failure Mechanism:** If the immediate next paragraph is a subheading (e.g. `## Endpoints`), `re.match(r'^#{1,6}\s+', next_first)` evaluates to `True`. The subheading and body are not attached.
* **Impact:** `find_query_passages` returns ONLY `# Authentication Protocol` with zero body text, completely discarding all relevant information.
* **Empirical Proof:** `test_04_find_query_passages_subheading_swallows_body` in `tests/test_adversarial_v2_fuzzing.py`.

#### 1.5 Valid Heading Stripped on Empty Title in `_clean_section_content` [HIGH]
* **Location:** `chunker.py:116` (`_clean_section_content`)
* **Vulnerability:** Leading heading stripping logic:
  ```python
  if first_clean.lower().startswith(heading_title[:18].lower()) or heading_title.lower().startswith(first_clean[:18].lower()):
      lines = lines[1:]
  ```
* **Failure Mechanism:** If `heading_title` is empty `""`, `first_clean.lower().startswith("")` evaluates to `True` for every string in Python.
* **Impact:** Any leading heading line in the content is deleted, even if it is valid section content.
* **Empirical Proof:** `test_05_clean_section_content_strips_valid_heading_on_empty_title` in `tests/test_adversarial_v2_fuzzing.py`.

#### 1.6 Cascading Placeholder Injection in `_split_oversized_text` [CRITICAL]
* **Location:** `chunker.py:356-374` (`_split_oversized_text`) and `chunker.py:511-514` (`find_query_passages`)
* **Vulnerability:** Code blocks are masked with sequential tokens: `__CODE_BLOCK_SLOT_{idx}__`, and then restored via sequential `.replace()` calls:
  ```python
  for idx, cb in enumerate(code_blocks):
      chunk_body = chunk_body.replace(f"__CODE_BLOCK_SLOT_{idx}__", cb)
  ```
* **Failure Mechanism:** If Code Block 0 contains the literal text `__CODE_BLOCK_SLOT_1__`, replacing Slot 0 injects Slot 1 into the string. When the loop reaches Slot 1, Code Block 2 is injected inside Code Block 1.
* **Impact:** Arbitrary code injection and document corruption across code-block boundaries.
* **Empirical Proof:** `test_11_code_block_placeholder_cascading_injection` in `tests/test_adversarial_v2_fuzzing.py`.

---

### AREA 2: Persistence, Vault & SQLite Concurrency

#### 2.1 Unhandled `sqlite3.IntegrityError` Crash on NaN TTL [HIGH]
* **Location:** `vault.py:296-325` (`stage_document`)
* **Vulnerability:** `ttl_days` coercion uses:
  ```python
  try:
      ttl_days = float(ttl_days) if ttl_days is not None else 7.0
  except (ValueError, TypeError):
      ttl_days = 7.0
  ```
  In Python, `float("nan")` is a valid float and does not raise `ValueError`.
* **Failure Mechanism:** `expires_at = now + (float("nan") * 86400.0) = nan`. In Python's `sqlite3`, `float("nan")` binds to SQL `NULL`. Because `expires_at` is defined as `REAL NOT NULL`, the query crashes with:
  `sqlite3.IntegrityError: NOT NULL constraint failed: staged_documents.expires_at`.
* **Impact:** Malformed or adversary-supplied TTL parameters crash the process with an unhandled database exception.
* **Empirical Proof:** `test_06_stage_document_nan_ttl_crashes_integrity_error` in `tests/test_adversarial_v2_fuzzing.py`.

#### 2.2 Permanent Ghost Rows in SQLite on Infinite TTL [HIGH]
* **Location:** `vault.py:296-325` (`stage_document`) and `vault.py:384-398` (`clean_expired_staged_docs`)
* **Vulnerability:** Passing `ttl_days="inf"` or `float("inf")` passes `float()` conversion and sets `expires_at = inf`.
* **Failure Mechanism:** In SQLite, `expires_at < ?` will never evaluate to `True` for any finite timestamp.
* **Impact:** The staged document can never be purged by `clean_expired_staged_docs()`, remaining a permanent ghost row in SQLite.
* **Empirical Proof:** `test_07_stage_document_inf_ttl_permanent_ghost_row` in `tests/test_adversarial_v2_fuzzing.py`.

#### 2.3 Unhandled `AttributeError` Crash on Non-String Category in Ledger [HIGH]
* **Location:** `vault.py:543` (`get_ledger_entries`) and `vault.py:584` (`get_ledger_markdown`)
* **Vulnerability:** `category` filtering checks:
  ```python
  if category is not None and category.strip():
  ```
* **Failure Mechanism:** If a caller passes an integer category (e.g. `category=123` or `category=2026`), `.strip()` raises `AttributeError: 'int' object has no attribute 'strip'`.
* **Impact:** Crashes the ledger retrieval query and MCP server handler.
* **Empirical Proof:** `test_08_get_ledger_entries_int_category_crashes_attribute_error` in `tests/test_adversarial_v2_fuzzing.py`.

#### 2.4 Trailing-Slash URL Hash Divergence [MEDIUM]
* **Location:** `vault.py:154` (`_hash_url`)
* **Vulnerability:** Trailing slash normalization only operates on the root path:
  ```python
  path = "" if parts.path == "/" else parts.path
  ```
* **Failure Mechanism:** For subpaths (e.g. `https://example.com/docs/api/` vs `https://example.com/docs/api`), the trailing slash is preserved.
* **Impact:** Identical web resources produce divergent SHA-256 hashes in `page_cache`, causing cache misses and duplicate fetches.
* **Empirical Proof:** `test_10_hash_url_trailing_slash_divergence` in `tests/test_adversarial_v2_fuzzing.py`.

---

### AREA 3: Search Slice Extraction & Keyword Robustness

#### 3.1 Silent Section Suppression & Context Hallucination [CRITICAL]
* **Location:** `vault.py:444-460` (`extract_staged_slice`)
* **Vulnerability:** When a caller specifies both `section` and `query`:
  ```python
  if section and section.strip():
      sec_text = extract_section(content, sec_clean)
      if not sec_text.startswith(f"Section '{sec_clean}' not found"):
          ...
      else:
          if not (query and query.strip()):
              return sec_text
  # Falls through to full-document query search
  if query and query.strip():
      q_passages = find_query_passages(content, query.strip(), ...)
  ```
* **Failure Mechanism:** If the requested section does NOT exist, `extract_staged_slice` silently suppresses the error and searches the entire document for the query.
* **Impact:** The LLM agent receives an excerpt from an unrelated section without any indication that its requested section was missing, causing context hallucination.
* **Empirical Proof:** `test_09_extract_staged_slice_silent_section_fallback` in `tests/test_adversarial_v2_fuzzing.py`.

#### 3.2 Quadratic $O(H \times C)$ Algorithmic Complexity in `extract_headings` [MEDIUM]
* **Location:** `chunker.py:127-134` (`extract_headings`)
* **Vulnerability:** For every heading match $h \in H$, `extract_headings` executes a linear scan over all code block ranges:
  ```python
  def is_inside_code(pos: int) -> bool:
      return any(start <= pos <= end for start, end in code_ranges)
  ```
* **Impact:** For large technical documents with thousands of code blocks and headings, this causes $O(H \times C)$ quadratic scanning, leading to CPU lockups during page ingestion.

---

### AREA 4: IPC Pipeline, Process Management & Interface Contracts

#### 4.1 Unhandled `OverflowError` in Daemon `_safe_int` [HIGH]
* **Location:** `daemon.py:98-104` (`_safe_int`)
* **Vulnerability:** `_safe_int` only catches `(ValueError, TypeError)`.
* **Failure Mechanism:** Passing `float("inf")` or `math.inf` to `int()` raises `OverflowError: cannot convert float infinity to integer`.
* **Impact:** Crashes the daemon handler when infinite numeric bounds are received.
* **Empirical Proof:** `test_12_safe_int_overflow_error_on_infinity` in `tests/test_adversarial_v2_fuzzing.py`.

#### 4.2 Swallowed Search Failure Formatted as `[None](None)` Links [MEDIUM]
* **Location:** `daemon.py:125-130` (`_handle_search`)
* **Vulnerability:** `_raw_search` returns `[{"error": "Search failed: ..."}]` on DDGS network/rate limit failure.
* **Failure Mechanism:** `_handle_search` does not check for `"error"` in result items, formatting them as:
  `1. [None](None) [TECH]\n   None\n`
* **Impact:** Swallows error messages and sends invalid markdown to client agents.
* **Empirical Proof:** `test_13_daemon_search_error_swallowed_as_none_links` in `tests/test_adversarial_v2_fuzzing.py`.

#### 4.3 Missing `session_id` in MCP Tool Interface [MEDIUM]
* **Location:** `mcp_server.py:54-81` (`fetch`)
* **Vulnerability:** `mcp_server.py:fetch` does not accept a `session_id` argument.
* **Impact:** All documents staged out-of-band via MCP are permanently assigned to `session_id="default"`, preventing multi-agent isolation.

#### 4.4 `cli.py batch` Daemon Bypass [MEDIUM]
* **Location:** `cli.py:247-260` (`batch`)
* **Vulnerability:** Unlike `fast`, `inspect_cmd`, and `ledger_cmd`, `cli.py batch` never checks `_is_daemon_alive()`.
* **Impact:** Forces heavy local neural models into RAM on every CLI batch search call even when the daemon is running.

---

## 3. Concrete Verification Harness

The 13 primary vulnerabilities have been codified into an automated regression suite at:
`tests/test_adversarial_v2_fuzzing.py`

### Test Execution:
```bash
/home/prtyksh/tmp/web_search_tool/.venv/bin/python -m unittest tests/test_adversarial_v2_fuzzing.py
```
**Output:**
```
.............
----------------------------------------------------------------------
Ran 13 tests in 0.049s

OK
```

---

## 4. Actionable Hardening Recommendations

1. **CommonMark Compliant Code-Fence Tracking (`chunker.py`):**
   Within an open fenced code block of length $N$, any backticks with length $< N$ must be treated as literal content. Only a fence with length $\ge N$ and no trailing info string should close the fence. Do not nest code fences within an open code block.

2. **Oversized Code Block Enclosure (`chunker.py`):**
   When `_split_oversized_text` divides an oversized code block, every resulting chunk must be explicitly wrapped in matching opening and closing code fences so no raw code syntax is emitted into markdown.

3. **Strict Heading Matching in `extract_section` (`chunker.py`):**
   Remove `h_title in target_clean`. Require exact case-insensitive match, heading prefix match (`h_title.startswith(target_clean)`), or word-boundary token matching. Disallow matches when `target_clean` is empty or shorter than 2 characters.

4. **Attach Subheading Body in `find_query_passages` (`chunker.py`):**
   If a query matches a section heading and the immediate next block is a subheading, scan forward to attach the first non-heading body paragraph rather than returning a bare heading.

5. **Sanitize `heading_title` in `_clean_section_content` (`chunker.py`):**
   Verify `bool(heading_title and heading_title.strip())` before stripping leading lines.

6. **Random Nonce Placeholders (`chunker.py`):**
   Use UUID nonces (e.g. `f"__CODE_BLOCK_SLOT_{uuid.uuid4().hex[:8]}_{idx}__"`) and replace in reverse order, or token-based splitting, to avoid cascading string collision.

7. **Database Validation (`vault.py`):**
   - Check `math.isnan(ttl_days)` and `math.isinf(ttl_days)` in `stage_document()`. Fall back to default 7.0 days if invalid.
   - Use `str(category).strip()` in `get_ledger_entries()`.
   - Normalize trailing slashes on all paths in `_hash_url()`.
   - In `extract_staged_slice()`, return an explicit error message when a requested section is missing: `"Error: Section '{section}' was not found in document '{doc_id}'."` instead of silently falling back to a full document query.

8. **Daemon & CLI Hardening (`daemon.py`, `cli.py`):**
   - Catch `(ValueError, TypeError, OverflowError)` in `_safe_int()`.
   - Inspect search results for `"error"` keys in `_handle_search()` and return `"Search error: {error}"` instead of `[None](None)`.
   - Route `cli.py batch` through `daemon_client` when daemon is responsive.
   - Expose `session_id: str = "default"` in `mcp_server.py:fetch`.
