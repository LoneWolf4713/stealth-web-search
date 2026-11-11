# Blank-Slate Adversarial Red-Team Audit Report (Pass 5)
**Target Repository:** `/home/prtyksh/tmp/web_search_tool`  
**Target Branch:** `feat/context-token-optimization` (Audited against base `main` commit `e648dc8`)  
**Auditor:** Independent Adversarial Red-Team Code Auditor  
**Audit Mindset:** "Guilty Until Proven Innocent" (Zero Trust, Blank Slate)  
**Execution Environment:** Linux / Python Virtualenv (`.venv/bin/python`)  
**Verification Suite:** `tests/test_adversarial_v3_fuzzing.py` (15/15 tests passing, empirically proving all vulnerabilities)

---

## 1. Executive Summary & Audit Scope

This adversarial audit performed an exhaustive, blank-slate vulnerability assessment of the entire codebase across all four core domains:
1. **Markdown Integrity, Semantic Chunker & Truncation** (`chunker.py`, `parallel_pipeline.py`, `fetcher.py`)
2. **Persistence, Out-of-Band Staging & SQLite Concurrency** (`vault.py`)
3. **IPC Pipeline, Daemon, Client & CLI Contracts** (`daemon.py`, `daemon_client.py`, `cli.py`, `mcp_server.py`)
4. **Neural & Retrieval Pipeline** (`searcher.py`, `parallel_pipeline.py`, `neural_fast.py`)

A total of **15 latent vulnerabilities** were uncovered, ranging from critical data scrambling and silent error suppression to unhandled fatal crashes and prompt injection risks.

---

## 2. Vulnerability Severity Matrix

| ID | Component | Severity | Vulnerability Summary | Status |
|:---|:---|:---:|:---|:---:|
| **DEF-01** | `chunker.py:508-523` | **CRITICAL** | Chunker Heading Offset Desynchronization & Content Scrambling | Confirmed |
| **DEF-02** | `chunker.py:14, 133, 429, 578` | **HIGH** | `CODE_BLOCK_PATTERN` Omission of CommonMark Tilde (`~~~`) Fences | Confirmed |
| **DEF-03** | `chunker.py:375-407` | **HIGH** | Oversized Tilde (`~~~`) Code Block Fragmentation Without Fences | Confirmed |
| **DEF-04** | `chunker.py:423, 427, 433, 576`| **HIGH** | Static Predictable Placeholder Collision & Payload Injection | Confirmed |
| **DEF-05** | `vault.py:638` | **HIGH** | Unhandled `TypeError` in `get_ledger_markdown` on Non-String Tags | Confirmed |
| **DEF-06** | `vault.py:518-524` | **MEDIUM** | Unhandled `OverflowError` on `float('inf')` in `record_fact` | Confirmed |
| **DEF-07** | `vault.py:567-572` | **MEDIUM** | Unhandled `OverflowError` on `float('inf')` in `get_ledger_entries` | Confirmed |
| **DEF-08** | `vault.py:314` | **MEDIUM** | Permissive Negative TTL in `stage_document` Creating Dead Rows | Confirmed |
| **DEF-09** | `vault.py:136-137` | **MEDIUM** | Unhandled `AttributeError` on Non-String Query in `_normalize_query` | Confirmed |
| **DEF-10** | `fetcher.py:178, vault.py:252` | **MEDIUM** | Unhandled `TypeError` in `fetch_page_detailed(None)` via `extract_domain` | Confirmed |
| **DEF-11** | `parallel_pipeline.py:196-197` | **MEDIUM** | Corrupted `[None](None)` Link & Error Masking in `format_fast_digest` | Confirmed |
| **DEF-12** | `parallel_pipeline.py:444-445` | **MEDIUM** | Corrupted `[None](None)` Link & Error Masking in `format_deep_digest` | Confirmed |
| **DEF-13** | `daemon.py:165-174` | **MEDIUM** | Silent Fetch Error Swallowing in Daemon `get_toc` & `read_section` | Confirmed |
| **DEF-14** | `chunker.py:122` | **LOW** | False-Positive Heading Deletion in `_clean_section_content` | Confirmed |
| **DEF-15** | `neural_fast.py:222, 231` | **LOW** | Syntax-Violating Blind 1,450-Char Slicing in `_select_top_chunks` | Confirmed |

---

## 3. Deep-Dive Vulnerability Analysis

---

### DEF-01: Chunker Heading Offset Desynchronization & Silent Content Scrambling
* **Component:** `chunker.py`
* **File & Lines:** `chunker.py:508-523`
* **Severity:** **CRITICAL**
* **Failure Mechanism:**
  In `chunk_markdown_smart`:
  ```python
  cleaned_md = re.sub(r'(?i)This site requires JavaScript...', '', markdown, ...)
  headings = extract_headings(cleaned_md)
  ...
  for i, h in enumerate(headings):
      start = h["start"]
      end = headings[i + 1]["start"] if i + 1 < len(headings) else len(markdown)
      sec_text = markdown[start:end].strip()
  ```
  `extract_headings` calculates start/end character offsets from `cleaned_md` (where JavaScript banners, recommendation widgets, and citation notices have been stripped). However, the loop slices `markdown[start:end]` from the **uncleaned original `markdown`**!
  Because characters were removed at the beginning of `cleaned_md`, the offsets do not align with `markdown`. Slicing `markdown[start:end]` shifts all section content backwards into the stripped boilerplate.
* **Empirical Reproduction:**
  ```python
  md = "This site requires JavaScript to run correctly. Please unblock scripts.\n\n# Section 1\nFirst section content.\n\n# Section 2\nSecond section content.\n"
  chunks = chunk_markdown_smart(md)
  # Chunk 0 heading: 'Section 1'
  # Chunk 0 content: "'s site requires JavaScript to run co'"
  ```
  The content of "Section 1" literally contains the JavaScript warning that was supposedly removed!
* **Architectural Root Cause:** Slicing from `markdown` instead of `cleaned_md`.
* **Remediation Specification:**
  In `chunker.py:522-523`, slice from `cleaned_md`:
  ```python
  end = headings[i + 1]["start"] if i + 1 < len(headings) else len(cleaned_md)
  sec_text = cleaned_md[start:end].strip()
  ```

---

### DEF-02: `CODE_BLOCK_PATTERN` Omission of CommonMark Tilde (`~~~`) Fences
* **Component:** `chunker.py`
* **File & Lines:** `chunker.py:14, 133, 429, 578`
* **Severity:** **HIGH**
* **Failure Mechanism:**
  Line 14 defines:
  ```python
  CODE_BLOCK_PATTERN = re.compile(r'(`{3,})[\s\S]*?\1', re.MULTILINE)
  ```
  This regex strictly matches backtick fences and completely ignores tildes (`~~~`). Under CommonMark Specification (§4.5), tildes `~~~` are standard code fences.
  Because `extract_headings` uses `CODE_BLOCK_PATTERN` to ignore code blocks:
  ```python
  code_ranges = [(m.start(), m.end()) for m in CODE_BLOCK_PATTERN.finditer(markdown)]
  ```
  Any comment line inside a `~~~` block starting with `#` is treated as a real document heading!
* **Empirical Reproduction:**
  ```python
  md = "# Title\n\n~~~python\n# Fake Heading In Tildes\nx = 1\n~~~\n\n# Section 2\nBody"
  headings = extract_headings(md)
  # headings contains: 'Fake Heading In Tildes'
  ```
* **Architectural Root Cause:** Incomplete CommonMark fence regex.
* **Remediation Specification:**
  Update `CODE_BLOCK_PATTERN` to support both backticks and tildes:
  ```python
  CODE_BLOCK_PATTERN = re.compile(r'(`{3,}|~{3,})[\s\S]*?\1', re.MULTILINE)
  ```

---

### DEF-03: Oversized Tilde (`~~~`) Code Block Fragmentation Without Fences
* **Component:** `chunker.py`
* **File & Lines:** `chunker.py:375-407, 416-430`
* **Severity:** **HIGH**
* **Failure Mechanism:**
  When an oversized code block (>2,200 chars) uses `~~~` fences, `CODE_BLOCK_PATTERN.sub` fails to match it because of DEF-02. As a result, the code block is treated as plain text and split across word/sentence boundaries. Sub-chunks are emitted with raw executable code and no enclosing fences, breaking downstream LLM formatting.
* **Empirical Reproduction:**
  ```python
  cb = "~~~python\n" + ("x = 1;\n" * 500) + "~~~\n"
  chunks = chunk_markdown_smart("# Section 1\n" + cb)
  # Chunk 1 starts without '~~~' and ends without '~~~'
  ```
* **Architectural Root Cause:** Consequence of DEF-02.
* **Remediation Specification:**
  Fixing DEF-02 enables `save_code` to intercept `~~~` blocks and delegate to `_split_oversized_code_block`, which already supports `~{3,}`.

---

### DEF-04: Static Predictable Placeholder Collision & Payload Injection
* **Component:** `chunker.py`
* **File & Lines:** `chunker.py:423, 427, 433, 576, 583`
* **Severity:** **HIGH**
* **Failure Mechanism:**
  `_split_oversized_text` and `find_query_passages` replace code blocks with predictable placeholders:
  `f"__CODE_BLOCK_SLOT_{idx}__"`.
  When `restore_code` runs:
  ```python
  def restore_code(t: str) -> str:
      return re.sub(
          r'__CODE_BLOCK_SLOT_(\d+)__',
          lambda m: code_blocks[int(m.group(1))] if int(m.group(1)) < len(code_blocks) else m.group(0),
          t
      )
  ```
  If user content or technical documentation naturally contains the string `__CODE_BLOCK_SLOT_0__`, the regex replaces the user's text with an arbitrary code block from elsewhere in the document.
* **Empirical Reproduction:**
  ```python
  text = "The sentinel is __CODE_BLOCK_SLOT_0__.\n\n```python\nprint('INJECTED')\n```\n"
  chunks = _split_oversized_text(text, "Overview", 100, 10)
  # chunks[0]['content'] becomes: "The sentinel is ```python\nprint('INJECTED')\n```."
  ```
* **Architectural Root Cause:** Static, deterministic placeholder format vulnerable to collision.
* **Remediation Specification:**
  Generate a unique, per-call session nonce using `uuid.uuid4().hex[:8]`:
  ```python
  slot_nonce = uuid.uuid4().hex[:8]
  placeholder = f"__CB_SLOT_{slot_nonce}_{idx}__"
  pattern = re.compile(rf'__CB_SLOT_{slot_nonce}_(\d+)__')
  ```

---

### DEF-05: Unhandled `TypeError` in `get_ledger_markdown` on Non-String Tags
* **Component:** `vault.py`
* **File & Lines:** `vault.py:638`
* **Severity:** **HIGH**
* **Failure Mechanism:**
  Line 638 formats tags as:
  ```python
  tags = item.get("tags", [])
  tag_str = f" `[{', '.join(tags)}]`" if tags else ""
  ```
  If `record_fact` is called with tags containing non-string elements (e.g. `tags=[42, 99]`), Python's `str.join()` raises:
  `TypeError: sequence item 0: expected str instance, int found`.
  This permanently bricks `get_ledger_markdown` for the entire category or database.
* **Empirical Reproduction:**
  ```python
  record_fact("fuzz_tag", 1, "Testing tags", tags=[42, 99])
  get_ledger_markdown(category="fuzz_tag")
  # Raises TypeError: sequence item 0: expected str instance, int found
  ```
* **Architectural Root Cause:** Missing string coercion in list join.
* **Remediation Specification:**
  Coerce tags to strings before joining:
  ```python
  tag_str = f" `[{', '.join(str(t) for t in tags)}]`" if tags else ""
  ```

---

### DEF-06 & DEF-07: Unhandled `OverflowError` on `float('inf')` in `record_fact` & `get_ledger_entries`
* **Component:** `vault.py`, `daemon.py`
* **File & Lines:** `vault.py:518-524, 567-572`, `daemon.py:272-277`
* **Severity:** **MEDIUM**
* **Failure Mechanism:**
  Converting `float('inf')` to `int` in Python raises `OverflowError`:
  ```python
  >>> int(float('inf'))
  OverflowError: cannot convert float infinity to integer
  ```
  `record_fact` and `get_ledger_entries` catch `(ValueError, TypeError)` but omit `OverflowError`.
* **Empirical Reproduction:**
  ```python
  record_fact("cat", float("inf"), "fact")
  # Raises OverflowError: cannot convert float infinity to integer
  get_ledger_entries(tier=float("inf"))
  # Raises OverflowError: cannot convert float infinity to integer
  ```
* **Architectural Root Cause:** Incomplete exception tuple.
* **Remediation Specification:**
  Add `OverflowError` to the exception handling:
  ```python
  except (ValueError, TypeError, OverflowError):
  ```

---

### DEF-08: Permissive Negative TTL in `stage_document` Creating Dead Rows
* **Component:** `vault.py`
* **File & Lines:** `vault.py:314-320`
* **Severity:** **MEDIUM**
* **Failure Mechanism:**
  Line 314 checks:
  ```python
  if math.isnan(ttl) or math.isinf(ttl) or ttl == 0:
      ttl = 7.0
  ```
  Unlike `set_cached_search` and `set_cached_page` (which check `ttl <= 0`), `stage_document` checks `ttl == 0`. Passing negative values (e.g. `ttl_days = -5.0`) calculates `expires_at = now - 432000`, creating a document that is immediately expired and purged on retrieval.
* **Empirical Reproduction:**
  ```python
  res = stage_document("https://example.com", "Title", "content", ttl_days=-5.0)
  doc = get_staged_document(res["doc_id"])
  # doc is None (immediately dead-on-arrival)
  ```
* **Architectural Root Cause:** Inconsistent boundary check (`ttl == 0` instead of `ttl <= 0`).
* **Remediation Specification:**
  ```python
  if math.isnan(ttl) or math.isinf(ttl) or ttl <= 0:
      ttl = 7.0
  ```

---

### DEF-09: Unhandled `AttributeError` on Non-String Query in `_normalize_query`
* **Component:** `vault.py`
* **File & Lines:** `vault.py:136-137`
* **Severity:** **MEDIUM**
* **Failure Mechanism:**
  `_normalize_query(query)` calls `query.strip()`. If `query` is an integer or non-string object, it crashes with `AttributeError: 'int' object has no attribute 'strip'`.
  Because `norm_q = _normalize_query(query)` in `get_cached_search` occurs before the `try:` block, it propagates as an unhandled crash.
* **Empirical Reproduction:**
  ```python
  get_cached_search(12345)
  # Raises AttributeError: 'int' object has no attribute 'strip'
  ```
* **Architectural Root Cause:** Missing string coercion in `_normalize_query`.
* **Remediation Specification:**
  ```python
  def _normalize_query(query: Any) -> str:
      if not query:
          return ""
      return " ".join(str(query).strip().lower().split())
  ```

---

### DEF-10: Unhandled `TypeError` in `fetch_page_detailed(None)` via `extract_domain`
* **Component:** `fetcher.py`, `vault.py`
* **File & Lines:** `fetcher.py:178, 401`, `vault.py:162-164, 252`
* **Severity:** **MEDIUM**
* **Failure Mechanism:**
  When `fetch_page_detailed(None)` is called, `extract_domain(None)` calls `urllib.parse.urlparse(None)`, which returns bytes `b''` for `netloc`.
  Then `get_domain_cookies(b'')` evaluates:
  `if "://" in url_or_domain:`
  Checking `"str" in bytes` raises `TypeError: a bytes-like object is required, not 'str'`.
* **Empirical Reproduction:**
  ```python
  fetch_page_detailed(None)
  # Raises TypeError: a bytes-like object is required, not 'str'
  ```
* **Architectural Root Cause:** Lack of early validation for `None` URL in `fetcher.py` and `extract_domain`.
* **Remediation Specification:**
  In `fetcher.py:fetch_page_detailed`:
  ```python
  if not url or not str(url).strip():
      return {"success": False, "content": "", "error_reason": "Missing or empty URL", "cached": False}
  ```
  In `vault.py:extract_domain`:
  ```python
  def extract_domain(url: str) -> str:
      if not url:
          return ""
      parsed = urlparse(str(url))
      netloc = parsed.netloc if isinstance(parsed.netloc, str) else parsed.netloc.decode("utf-8", "ignore")
      return netloc.lower()
  ```

---

### DEF-11 & DEF-12: Corrupted `[None](None)` Link & Error Masking in `format_fast_digest` & `format_deep_digest`
* **Component:** `parallel_pipeline.py`
* **File & Lines:** `parallel_pipeline.py:196-197, 444-445`
* **Severity:** **MEDIUM**
* **Failure Mechanism:**
  When a search query fails (rate limit, empty query, DNS failure), `results` contains `[{'error': 'Search failed: ...'}]`.
  `format_fast_digest` and `format_deep_digest` treat this error dict as an inaccessible webpage:
  ```python
  lines.append(f"- [{r.get('title')}]({r.get('url')}): *{r.get('error_reason', 'Access blocked')}*")
  ```
  Because `title`, `url`, and `error_reason` are `None`, it outputs:
  `- [None](None): *Access blocked*`
  The real error message is completely masked and replaced with a bogus broken link.
* **Empirical Reproduction:**
  ```python
  err_data = {"query": "test", "results": [{"error": "Rate limit reached"}]}
  res = format_fast_digest(err_data)
  # res contains: "- [None](None): *Access blocked*"
  ```
* **Architectural Root Cause:** Failure to distinguish between search-level engine failures and page-level crawl failures.
* **Remediation Specification:**
  Check for search errors before rendering source footers:
  ```python
  if results and isinstance(results[0], dict) and "error" in results[0]:
      lines.append(f"> ❌ **Search Failure**: {results[0]['error']}")
      return "\n".join(lines)
  ```

---

### DEF-13: Silent Fetch Error Swallowing in Daemon `get_toc` & `read_section`
* **Component:** `daemon.py`, `cli.py`
* **File & Lines:** `daemon.py:165-174`, `cli.py:159-178`
* **Severity:** **MEDIUM**
* **Failure Mechanism:**
  When `fetch_page` fails, it returns an error string: `"Failed to retrieve <url>: <error_reason>"`.
  `_handle_get_toc` and `_handle_read_section` directly feed this error string into `get_table_of_contents()` and `extract_section()`:
  - `get_table_of_contents("Failed to retrieve...")` returns `"Document has no structured headings. Total word count: ~14 words."`
  - `extract_section("Failed to retrieve...", "Intro")` returns `"Section 'Intro' not found: document contains no structured markdown headings."`
  The caller is led to believe the webpage was successfully retrieved and simply lacked headings.
* **Empirical Reproduction:**
  ```python
  with patch("daemon.fetch_page", return_value="Failed to retrieve https://example.com: 404 Not Found"):
      toc = _handle_get_toc({"url": "https://example.com"})
      # Returns: "Document has no structured headings. Total word count: ~7 words."
  ```
* **Architectural Root Cause:** Missing failure check on return value of `fetch_page`.
* **Remediation Specification:**
  ```python
  def _handle_get_toc(kwargs):
      with _FETCH_SEMAPHORE:
          content = fetch_page(str(kwargs.get("url") or ""), max_chars=None)
          if content.startswith("Failed to retrieve"):
              return content
          return get_table_of_contents(content)
  ```

---

### DEF-14: False-Positive Heading Deletion in `_clean_section_content`
* **Component:** `chunker.py`
* **File & Lines:** `chunker.py:122`
* **Severity:** **LOW**
* **Failure Mechanism:**
  `_clean_section_content` checks:
  ```python
  if first_clean.lower().startswith(ht_clean[:18].lower()) or ht_clean.lower().startswith(first_clean[:18].lower()):
      lines = lines[1:]
  ```
  If `first_clean` is a short heading like `# C` or `## A` (length 1), `ht_clean.lower().startswith("c")` is evaluated. Any parent section whose title starts with that letter (e.g. `"Common Questions"`, `"Advanced Features"`) deletes the valid subheading.
* **Empirical Reproduction:**
  ```python
  cleaned = _clean_section_content("# C\nDetails about C.", "Common Questions")
  # Result: "Details about C." (Heading # C was deleted!)
  ```
* **Architectural Root Cause:** Asymmetric prefix check without minimum length restriction.
* **Remediation Specification:**
  Require `len(first_clean) >= 3` and `len(ht_clean) >= 3` for prefix matching:
  ```python
  if len(first_clean) >= 3 and len(ht_clean) >= 3:
      if first_clean.lower().startswith(ht_clean[:18].lower()) or ht_clean.lower().startswith(first_clean[:18].lower()):
          lines = lines[1:]
  elif first_clean.lower() == ht_clean.lower():
      lines = lines[1:]
  ```

---

### DEF-15: Syntax-Violating Raw Truncation in `neural_fast._select_top_chunks`
* **Component:** `neural_fast.py`
* **File & Lines:** `neural_fast.py:222, 231`
* **Severity:** **LOW**
* **Failure Mechanism:**
  Lines 221-222 and 230-231 truncate long passages using blind character slicing:
  ```python
  if len(c["content"]) > 1500:
      c["content"] = c["content"][:1450] + "\n[... Passage truncated ...]"
  ```
  If a code block or table spans across character 1,450, it is sliced mid-block without closing fences, polluting the final prompt with unclosed backticks.
* **Empirical Reproduction:**
  ```python
  long_code = "```python\n" + ("x = 1;\n" * 400) + "```"
  res = _select_top_chunks([{"content": long_code, "relevance_score": 10.0}], 1)
  # res[0]['content'] ends with 'x = 1\n[... Passage truncated ...]' without closing ```
  ```
* **Architectural Root Cause:** Bypassing `safe_truncate_markdown`.
* **Remediation Specification:**
  Use `safe_truncate_markdown(c["content"], 1500, suffix="\n[... Passage truncated ...]")`.

---

## 4. Verification Test Suite Summary

The verification suite `tests/test_adversarial_v3_fuzzing.py` was implemented and executed:

```bash
$ /home/prtyksh/tmp/web_search_tool/.venv/bin/python -m unittest tests/test_adversarial_v3_fuzzing.py -v
test_01_chunk_markdown_smart_offset_desync_corrupts_sections ... ok
test_02_tilde_code_block_heading_pollution ... ok
test_03_oversized_tilde_code_block_leaks_raw_code ... ok
test_04_static_code_slot_injection_vulnerability ... ok
test_05_clean_section_content_deletes_valid_short_heading ... ok
test_06_get_ledger_markdown_crashes_on_non_string_tags ... ok
test_07_record_fact_raises_unhandled_overflow_error_on_inf ... ok
test_08_get_ledger_entries_raises_unhandled_overflow_error_on_inf ... ok
test_09_stage_document_allows_negative_ttl_days ... ok
test_10_normalize_query_crashes_on_int ... ok
test_11_fetch_page_detailed_crashes_on_none_url ... ok
test_12_format_fast_digest_emits_none_link_on_search_error ... ok
test_13_format_deep_digest_emits_none_link_on_search_error ... ok
test_14_daemon_get_toc_and_read_section_swallow_fetch_errors ... ok
test_15_neural_fast_raw_truncation_breaks_markdown_syntax ... ok

----------------------------------------------------------------------
Ran 15 tests in 0.052s

OK
```

All 15 empirical tests successfully reproduced and verified every vulnerability in the report.
