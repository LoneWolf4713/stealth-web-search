# Blank-Slate Adversarial Red-Team Audit Report (Pass 6)
**Target Repository:** `/home/prtyksh/tmp/web_search_tool`  
**Target Branch:** `feat/context-token-optimization` (Audited against base `main` commit `e648dc8`)  
**Auditor:** Independent Adversarial Red-Team Code Auditor (Pass 6)  
**Audit Mindset:** "Guilty Until Proven Innocent" (Zero Trust, Blank Slate)  
**Execution Environment:** Linux / Python Virtualenv (`.venv/bin/python`)  
**Verification Suite:** `tests/test_adversarial_v4_fuzzing.py` (15/15 tests passing, empirically proving all vulnerabilities)

---

## 1. Executive Summary & Audit Scope

This adversarial audit conducted an unsparing, blank-slate vulnerability assessment across the four architectural domains of `/home/prtyksh/tmp/web_search_tool` on branch `feat/context-token-optimization`:
1. **Markdown Integrity, Semantic Chunker & Truncation** (`chunker.py`, `parallel_pipeline.py`, `fetcher.py`)
2. **Persistence, Out-of-Band Staging & SQLite Concurrency** (`vault.py`)
3. **IPC Pipeline, Daemon, Client & CLI Contracts** (`daemon.py`, `daemon_client.py`, `cli.py`, `mcp_server.py`)
4. **Neural & Retrieval Pipeline** (`searcher.py`, `parallel_pipeline.py`, `neural_fast.py`)

A total of **15 latent vulnerabilities** were uncovered and empirically verified through automated fuzzing and stress testing. These include severe data corruption (wrapping plain documentation prose into executable code blocks), silent data loss (unconditional deletion of sections smaller than 25 words), markdown truncation bugs causing 96%+ text loss on short headers, unhandled fatal `TypeError` and `AttributeError` crashes, and cache invalidation flaws causing redundant network fetches.

---

## 2. Vulnerability Severity Matrix

| ID | Component | Severity | Vulnerability Summary | Status |
|:---|:---|:---:|:---|:---:|
| **DEF-01** | `chunker.py:448-464` | **CRITICAL** | Multiple Code Blocks Expansion Scrambles Syntax & Swallows Prose into Code Blocks | Confirmed |
| **DEF-02** | `chunker.py:533-540` | **CRITICAL** | Silent Data Loss: Unconditional Deletion of Sections `< min_chunk_words` (25 words) | Confirmed |
| **DEF-03** | `chunker.py:15, 138-145` | **HIGH** | Nested Code Fence Slicing & Premature Termination in `CODE_BLOCK_PATTERN` | Confirmed |
| **DEF-04** | `chunker.py:87-90` | **HIGH** | Catastrophic 96%+ Text Loss on Short Headers via `(target_len - last_newline <= 200)` | Confirmed |
| **DEF-05** | `vault.py:485, 493` | **HIGH** | Unhandled `TypeError: string indices must be integers` on Non-Dict Headings | Confirmed |
| **DEF-06** | `vault.py:142-160` | **MEDIUM** | URL Fragment Preservation Breaks Cache Deduplication & Forces Redundant Fetches | Confirmed |
| **DEF-07** | `vault.py:342, 535` | **MEDIUM** | Unhandled `TypeError` in `record_fact` and `stage_document` on Non-Serializable Input | Confirmed |
| **DEF-08** | `vault.py:649` | **MEDIUM** | Unhandled `ValueError`/`OverflowError` in `get_ledger_markdown` on Corrupted Timestamps | Confirmed |
| **DEF-09** | `searcher.py:37-38` | **MEDIUM** | Unhandled `AttributeError` on Non-String URL Inputs in `_canonical_url` | Confirmed |
| **DEF-10** | `searcher.py:131` | **MEDIUM** | Query Cleaning Failure on 2-Letter Technical Queries (`AI`, `C#`, `Go`, `R`) | Confirmed |
| **DEF-11** | `parallel_pipeline.py:194-209, 446-455` | **MEDIUM** | Zero Search Results Falsely Reported as "All Sources Blocked by Paywalls" | Confirmed |
| **DEF-12** | `parallel_pipeline.py:201, 248, 252, 297` | **MEDIUM** | Corrupted `[None](None)` Markdown Links Emitted on Missing Title or URL | Confirmed |
| **DEF-13** | `chunker.py:123-125` | **LOW** | Sub-Heading Erasure via Overly Aggressive 3-Character Prefix Matching in `_clean_section_content` | Confirmed |
| **DEF-14** | `chunker.py:18-55` | **LOW** | Single-Line Inline Code Spans Falsely Flagged as Unclosed Code Fences | Confirmed |
| **DEF-15** | `vault.py:308-314` | **LOW** | Redundant Duplicate SQL Cache Purges on Hourly Maintenance Cleanup | Confirmed |

---

## 3. Deep-Dive Vulnerability Analysis & Proof of Concept

---

### DEF-01: Multiple Code Blocks Expansion Scrambles Syntax & Swallows Prose into Code Blocks
* **Component:** `chunker.py:448-464` (`_split_oversized_text`)
* **Severity:** **CRITICAL**
* **Proof of Concept Reproduction:**
  ```python
  from chunker import _split_oversized_text

  cb1 = "```python\n# BLOCK 1\n" + ("x = 1\n" * 200) + "```"
  prose = "This is critical architectural documentation explaining the design."
  cb2 = "```python\n# BLOCK 2\n" + ("y = 2\n" * 200) + "```"
  text = f"{cb1}\n\n{prose}\n\n{cb2}"

  chunks = _split_oversized_text(text, "Architecture", 350, 25)
  for c in chunks:
      if prose in c["content"] and c["content"].startswith("```python"):
          print("PROSE WAS WRAPPED IN PYTHON CODE FENCE!")
  ```
* **Failure Mechanism:**
  In `_split_oversized_text`, code blocks are masked with 20-character tags (`__CB_SLOT_{nonce}_{idx}__`). When multiple code blocks and intervening text fit into a single piece under 2,200 chars while masked, `restore_code` expands them back to their full size (> 2,200 chars). Line 452 then naively assumes that any restored piece exceeding 2,200 characters is a *single* code block and executes:
  `cb_sub_pieces = _split_oversized_code_block(chunk_body, max_words=max_chunk_words, max_chars=2200)`
  `_split_oversized_code_block` matches the opening fence of the first block and closing fence of the last block, treating everything between (including plain markdown paragraphs and closing/opening fences) as inner code! It wraps intermediate slices in ````python ... ````, transforming plain documentation text into Python syntax and producing corrupted double fences (` ```python\n```python ` and ` ```\n``` `).
* **Impact:** Corrupts code syntax, mangles markdown structure, and misrepresents architectural documentation as executable code to LLMs.
* **Remediation:**
  In `_split_oversized_text`, after `restore_code(piece)`, check if `chunk_body` contains multiple code fences before passing to `_split_oversized_code_block`. If multiple blocks or mixed prose and code are present, slice between fence boundaries rather than treating the whole piece as a single code block.

---

### DEF-02: Silent Data Loss: Unconditional Deletion of Sections `< min_chunk_words` (25 words)
* **Component:** `chunker.py:533-540` (`chunk_markdown_smart`)
* **Severity:** **CRITICAL**
* **Proof of Concept Reproduction:**
  ```python
  from chunker import chunk_markdown_smart

  md = """# Introduction
  This is the primary introduction to the framework with more than twenty five words so it qualifies as a valid chunk under min_chunk_words rule.

  # Security Notice
  The master encryption key is SECRET_KEY_12345. Never commit this to git.

  # Deployment
  This is the final section of our deployment guide which contains sufficient words to exceed the minimum threshold and remain intact.
  """
  chunks = chunk_markdown_smart(md, min_chunk_words=25)
  headings = [c["heading"] for c in chunks]
  assert "Security Notice" not in headings  # Section 2 was silently dropped!
  ```
* **Failure Mechanism:**
  In `chunk_markdown_smart`:
  ```python
  if w <= max_chunk_words and len(cleaned_sec) <= 2200:
      if w >= min_chunk_words or not raw_chunks:
          raw_chunks.append({
              "heading": h["title"],
              "content": cleaned_sec,
              "word_count": w
          })
  else:
      sub_chunks = _split_oversized_text(...)
      raw_chunks.extend(sub_chunks)
  ```
  If `w < min_chunk_words` (e.g. 11 words) and `raw_chunks` is not empty (it is not the very first section in the document), the inner `if` condition is False, and there is NO `else` branch! The section is silently ignored. It is neither appended, nor merged with the preceding chunk, nor subdivided.
* **Impact:** Permanent, silent deletion of all short sections (API keys, security notices, single-sentence requirements, installation steps, FAQs) across indexed documentation.
* **Remediation:**
  If `w < min_chunk_words`, either append it to `raw_chunks` directly or merge it with `raw_chunks[-1]["content"]` (if doing so does not exceed 2,200 chars), ensuring zero sections are discarded.

---

### DEF-03: Nested Code Fence Slicing & Premature Termination in `CODE_BLOCK_PATTERN`
* **Component:** `chunker.py:15, 138-145` (`CODE_BLOCK_PATTERN`)
* **Severity:** **HIGH**
* **Proof of Concept Reproduction:**
  ```python
  from chunker import extract_headings

  text = """```markdown
  Outer block contains 4 backticks:
  ````python
  # False Heading Inside Code
  def foo():
      pass
  ````
  ```"""
  headings = extract_headings(text)
  assert any(h["title"] == "False Heading Inside Code" for h in headings)
  ```
* **Failure Mechanism:**
  `CODE_BLOCK_PATTERN` is defined as:
  `CODE_BLOCK_PATTERN = re.compile(r'(`{3,}|~{3,})[\s\S]*?\1', re.MULTILINE)`
  When group 1 matches an opening fence of 3 backticks (` ``` `), `\1` matches the literal string ` ``` `. If an inner code fence uses 4 backticks (` ```` `), `\1` matches the first 3 backticks of the 4-backtick fence! The regex terminates the match prematurely, leaving `python\n# False Heading Inside Code\n` outside the code block. `extract_headings` then extracts the comment as a document heading, desynchronizing the Table of Contents outline and cutting sections in half.
* **Impact:** Corrupts document Table of Contents, breaks section extraction (`extract_section`), and slices code blocks into broken fragments.
* **Remediation:**
  Ensure the closing fence backreference `\1` is followed by a negative lookahead `(?![`~])` or enforce CommonMark fence line rules where opening and closing fences must be isolated on their own lines and closing fence length must be `>=` opening fence length.

---

### DEF-04: Catastrophic 96%+ Text Loss on Short Headers via `(target_len - last_newline <= 200)`
* **Component:** `chunker.py:87-90` (`safe_truncate_markdown`)
* **Severity:** **HIGH**
* **Proof of Concept Reproduction:**
  ```python
  from chunker import safe_truncate_markdown

  text = "# Title\n" + ("This is substantive text explaining key concepts in detail. " * 5)
  res = safe_truncate_markdown(text, 205)
  assert res == "# Title"  # 198 characters were discarded!
  ```
* **Failure Mechanism:**
  Line 87 checks:
  `if last_newline > 0 and (is_table_row or last_newline > int(target_len * 0.7) or (target_len - last_newline <= 200)):`
  When `target_len <= 200` (e.g. 200 or 150), `target_len - last_newline <= 200` is mathematically TRUE for ANY `last_newline > 0`, even if `last_newline` occurs at index 1! As a result, any text starting with a short line (e.g. `# Title\n`) will unconditionally slice at the title, throwing away all subsequent paragraphs even though they fit well within `max_chars`.
* **Impact:** Massive text loss during snippet generation and passage truncation, wasting 90-96% of the token budget and returning empty or one-line snippets.
* **Remediation:**
  Remove `or (target_len - last_newline <= 200)` or replace with `(target_len - last_newline <= min(50, int(target_len * 0.2)))` so that slicing at newline is only permitted when preserving >= 80% of the target length.

---

### DEF-05: Unhandled `TypeError: string indices must be integers` on Non-Dict Headings
* **Component:** `vault.py:485, 493` (`extract_staged_slice`)
* **Severity:** **HIGH**
* **Proof of Concept Reproduction:**
  ```python
  from vault import stage_document, extract_staged_slice

  doc = stage_document("http://example.com/test", "Title", "Body", headings=["Section A", "Section B"])
  extract_staged_slice(doc["doc_id"], section="Nonexistent")
  # Raises TypeError: string indices must be integers, not 'str'
  ```
* **Failure Mechanism:**
  In `extract_staged_slice`, when a requested section or query is not found, lines 485 and 493 format the available sections:
  `sections_summary = ", ".join(f"`{h['title']}`" for h in headings[:8]) if headings else "None"`
  If `headings` is a list of strings (or non-dict items), `h['title']` raises an unhandled `TypeError: string indices must be integers, not 'str'`, crashing the MCP/daemon request with a 500 error instead of returning an error message.
* **Impact:** Crashes `inspect_vault_doc` tool calls whenever custom or string headings are stored in SQLite.
* **Remediation:**
  Sanitize heading title extraction with:
  `h_title = h.get("title", "") if isinstance(h, dict) else str(h)`

---

### DEF-06: URL Fragment Preservation Breaks Cache Deduplication & Forces Redundant Fetches
* **Component:** `vault.py:142-160` (`_hash_url`)
* **Severity:** **MEDIUM**
* **Proof of Concept Reproduction:**
  ```python
  from vault import _hash_url

  h1 = _hash_url("https://example.com/docs#overview")
  h2 = _hash_url("https://example.com/docs#installation")
  assert h1 != h2  # Different hashes for identical HTTP GET target!
  ```
* **Failure Mechanism:**
  `_hash_url` retains `parts.fragment` in `canonical_url`:
  `canonical_url = urlunsplit((scheme, netloc, norm_path, parts.query, parts.fragment))`
  Under HTTP/1.1 and RFC 3986, fragments are client-side only and never sent to servers. Scraping `https://example.com/page#sec1` and `https://example.com/page#sec2` produces different cache hashes, resulting in cache misses, duplicate page storage in `page_cache`, and redundant network requests.
* **Impact:** Defeats caching for anchor/deep links, increases network latency, and bloats the SQLite database with duplicate pages.
* **Remediation:**
  Strip `parts.fragment` in `_hash_url` by setting fragment to `""`:
  `canonical_url = urlunsplit((scheme, netloc, norm_path, parts.query, ""))`

---

### DEF-07: Unhandled `TypeError` in `record_fact` and `stage_document` on Non-Serializable Input
* **Component:** `vault.py:342, 535` (`record_fact`, `stage_document`)
* **Severity:** **MEDIUM**
* **Proof of Concept Reproduction:**
  ```python
  import datetime
  from vault import record_fact, stage_document

  record_fact("security", 1, "Fact", tags=[datetime.datetime.now()])
  # Raises TypeError: Object of type datetime is not JSON serializable
  ```
* **Failure Mechanism:**
  `record_fact` and `stage_document` call `json.dumps(tags)` and `json.dumps(headings)` without custom default serializes or type coercion. If a caller passes lists containing non-serializable objects (e.g. `datetime`, `set`, custom models), `json.dumps` crashes with unhandled `TypeError`.
* **Impact:** Unhandled fatal exception crashing database operations on non-string tags or metadata.
* **Remediation:**
  Use `default=str` or sanitize elements:
  `tags_json = json.dumps([str(t) for t in tags] if isinstance(tags, (list, tuple, set)) else [])`

---

### DEF-08: Unhandled `ValueError`/`OverflowError` in `get_ledger_markdown` on Corrupted Timestamps
* **Component:** `vault.py:649` (`get_ledger_markdown`)
* **Severity:** **MEDIUM**
* **Proof of Concept Reproduction:**
  ```python
  from vault import get_ledger_markdown, get_connection

  with get_connection() as conn:
      conn.execute("INSERT INTO research_ledger (category, tier, pass_num, fact_text, source_url, source_title, tags_json, created_at) VALUES ('test', 1, 1, 'fact', '', '', '[]', 1e16)")
      conn.commit()

  get_ledger_markdown(category="test")
  # Raises ValueError: year 31690708 is out of range
  ```
* **Failure Mechanism:**
  Line 649 calls:
  `dt_str = datetime.fromtimestamp(item["created_at"], timezone.utc).strftime("%Y-%m-%d")`
  If `created_at` in SQLite is corrupted, extremely large (e.g. `1e16`), negative, or `float('nan')`/`float('inf')`, `fromtimestamp` raises unhandled `ValueError` or `OverflowError`, crashing briefing generation for the entire ledger.
* **Impact:** Total failure of `get_research_ledger` when a single row contains an anomalous timestamp.
* **Remediation:**
  Wrap timestamp conversion in a try/except block falling back to current UTC date:
  ```python
  try:
      dt_str = datetime.fromtimestamp(float(item["created_at"]), timezone.utc).strftime("%Y-%m-%d")
  except (ValueError, TypeError, OverflowError, OSError):
      dt_str = "unknown-date"
  ```

---

### DEF-09: Unhandled `AttributeError` on Non-String URL Inputs in `_canonical_url`
* **Component:** `searcher.py:37-38` (`_canonical_url`)
* **Severity:** **MEDIUM**
* **Proof of Concept Reproduction:**
  ```python
  from searcher import _canonical_url

  _canonical_url(12345)
  # Raises AttributeError: 'int' object has no attribute 'rstrip'
  ```
* **Failure Mechanism:**
  In `_canonical_url`, if `url` is not a string (e.g. int, dict), `urlparse` raises an exception caught by `except Exception:`. The handler then attempts `return url.rstrip('/')`, which crashes with `AttributeError` on non-string inputs.
* **Impact:** Unhandled crash during search result deduplication when malformed URL items are received.
* **Remediation:**
  Change exception fallback to `return str(url).rstrip('/')` or validate `if not isinstance(url, str): return ""`.

---

### DEF-10: Query Cleaning Failure on 2-Letter Technical Queries (`AI`, `C#`, `Go`, `R`)
* **Component:** `searcher.py:131` (`clean_agent_query`)
* **Severity:** **MEDIUM**
* **Proof of Concept Reproduction:**
  ```python
  from searcher import clean_agent_query

  assert clean_agent_query("What is AI?") == "What is AI?"
  assert clean_agent_query("Please search for C#") == "Please search for C#"
  assert clean_agent_query("Can you find Go?") == "Can you find Go?"
  ```
* **Failure Mechanism:**
  Line 131 enforces:
  `return q if len(q) >= 3 else str(query).strip()`
  When conversational filler is stripped from questions regarding 2-letter technical terms (`AI`, `C#`, `Go`, `R`, `ML`), the cleaned term length is 2 characters (`< 3`). The function rejects the cleaned term and returns the original query with all conversational fluff intact.
* **Impact:** Lowers search quality and reranker precision for short technical programming languages and concepts.
* **Remediation:**
  Change condition to: `return q if len(q) >= 1 and q.isalnum() or any(c in q for c in '+#$') else str(query).strip()`.

---

### DEF-11: Zero Search Results Falsely Reported as "All Sources Blocked by Paywalls"
* **Component:** `parallel_pipeline.py:194-209, 446-455` (`format_fast_digest`, `format_deep_digest`)
* **Severity:** **MEDIUM**
* **Proof of Concept Reproduction:**
  ```python
  from parallel_pipeline import format_fast_digest

  data = {"query": "nonexistent_query_12345", "results": [], "elapsed_seconds": 0.1, "device": "cpu"}
  digest = format_fast_digest(data)
  assert "All 0 sources were inaccessible or blocked" in digest
  ```
* **Failure Mechanism:**
  When a search returns 0 results (`results = []`), `successful` is `[]`. The formatting logic immediately triggers:
  ```python
  if not successful:
      lines.append(f"> **All {len(results)} sources were inaccessible or blocked** (paywalls, login walls, or network errors).")
  ```
  It tells the user and AI agent that 0 sources were blocked by paywalls or bot challenges instead of stating that no results were found.
* **Impact:** Misinforms AI agents into believing content was blocked and attempting futile browser escalations.
* **Remediation:**
  Add an explicit check:
  ```python
  if not results:
      lines.append(f"> ℹ️ **No search results found** for `{data.get('query')}`. Try refining search terms.\n")
      return "\n".join(lines)
  ```

---

### DEF-12: Corrupted `[None](None)` Markdown Links Emitted on Missing Title or URL
* **Component:** `parallel_pipeline.py:201, 248, 252, 297, 453, 457`
* **Severity:** **MEDIUM**
* **Proof of Concept Reproduction:**
  ```python
  from parallel_pipeline import format_fast_digest

  data = {
      "query": "test",
      "results": [{"title": None, "url": None, "status": "success", "top_passages": [{"heading": "Details", "content": "Text", "relevance_score": 5.0}]}],
      "elapsed_seconds": 0.1
  }
  digest = format_fast_digest(data)
  assert "[None](None)" in digest
  ```
* **Failure Mechanism:**
  Formatting templates directly interpolate `f"[{r.get('title')}]({r.get('url')})"`. If `title` or `url` is `None` or empty, Python converts `None` to the string `"None"`, producing broken markdown links `[None](None)` in headings, key evidence banners, and failed source lists.
* **Impact:** Emits malformed markdown links to LLMs, which may hallucinate URL destinations.
* **Remediation:**
  Sanitize title and URL:
  `title = r.get('title') or r.get('url') or 'Untitled Source'`
  `url = r.get('url') or '#'`

---

### DEF-13: Sub-Heading Erasure via Overly Aggressive 3-Character Prefix Matching in `_clean_section_content`
* **Component:** `chunker.py:123-125` (`_clean_section_content`)
* **Severity:** **LOW**
* **Proof of Concept Reproduction:**
  ```python
  from chunker import _clean_section_content

  content = "## Applications in Robotics\nRobotics details and architecture."
  cleaned = _clean_section_content(content, "App")
  assert "Applications in Robotics" not in cleaned  # Sub-heading was erased!
  ```
* **Failure Mechanism:**
  `_clean_section_content` removes the first line if `first_clean.lower().startswith(ht_clean[:18].lower())`. When `ht_clean` is short (e.g. `"App"`), any sub-heading that starts with those letters (e.g. `Applications in Robotics`) is falsely classified as a duplicate heading and erased.
* **Impact:** Loss of structural context in sub-headings for short section titles.
* **Remediation:**
  Only remove the first line if it matches the heading title exactly or if `ht_clean` has at least 8 characters before using prefix matching.

---

### DEF-14: Single-Line Inline Code Spans Falsely Flagged as Unclosed Code Fences
* **Component:** `chunker.py:18-55` (`_get_unclosed_fences`)
* **Severity:** **LOW**
* **Proof of Concept Reproduction:**
  ```python
  from chunker import _get_unclosed_fences

  unclosed = _get_unclosed_fences("```python print('hello') ```")
  assert len(unclosed) > 0  # Falsely flagged as unclosed!
  ```
* **Failure Mechanism:**
  `_get_unclosed_fences` iterates line-by-line. On a line starting with ````, it sets `active_len = 3`. Because it does not check whether the fence also closes on the same line, it reports the fence as unclosed, causing `safe_truncate_markdown` to append an unneeded `\n```\n`.
* **Impact:** Appends extraneous closing fences to valid single-line markdown.
* **Remediation:**
  In `_get_unclosed_fences`, if a line matches `^(`{3,}|~{3,}).*\1\s*$`, treat it as closed on the same line.

---

### DEF-15: Redundant Duplicate SQL Cache Purges on Hourly Maintenance Cleanup
* **Component:** `vault.py:308-314` (`stage_document`)
* **Severity:** **LOW**
* **Proof of Concept Reproduction:**
  ```python
  from unittest.mock import patch
  import vault

  vault._last_cleanup = 0.0
  with patch("vault.clean_expired_staged_docs") as m1, patch("vault.clean_expired_cache") as m2:
      vault.stage_document("http://test.com", "Title", "Content")
      assert m1.called and m2.called
  ```
* **Failure Mechanism:**
  `stage_document` calls `clean_expired_staged_docs()` followed immediately by `clean_expired_cache()`. `clean_expired_staged_docs()` already deletes expired rows from `staged_documents`, `search_cache`, `page_cache`, and `cookie_vault`. Calling `clean_expired_cache()` right after executes the identical 4 SQL DELETE queries a second time.
* **Impact:** Redundant disk I/O and unnecessary write lock contention on the SQLite database.
* **Remediation:**
  Consolidate into a single call to `clean_expired_cache()`.

---

## 4. Remediation Specifications

1. **`chunker.py` (DEF-01, DEF-02, DEF-03, DEF-04, DEF-13, DEF-14):**
   - In `_split_oversized_text`: Before calling `_split_oversized_code_block`, check if `chunk_body` has multiple code blocks or intervening prose. Split at code fence boundaries instead of wrapping the entire piece.
   - In `chunk_markdown_smart`: If `w < min_chunk_words`, do NOT drop the section! Either append it or merge with `raw_chunks[-1]`.
   - In `CODE_BLOCK_PATTERN`: Add negative lookahead `(?![`~])` to group 1 backreference: `r'(`{3,}|~{3,})[\s\S]*?\1(?![`~])'`.
   - In `safe_truncate_markdown`: Remove `or (target_len - last_newline <= 200)` and ensure newline slicing only applies when `last_newline >= int(target_len * 0.75)`.
   - In `_clean_section_content`: Require exact match or `len(ht_clean) >= 8` for prefix matching.
   - In `_get_unclosed_fences`: Handle single-line closed code spans.

2. **`vault.py` (DEF-05, DEF-06, DEF-07, DEF-08, DEF-15):**
   - In `extract_staged_slice`: Safely extract heading titles with `h.get("title", str(h)) if isinstance(h, dict) else str(h)`.
   - In `_hash_url`: Strip `parts.fragment` so anchor links share the same cache key as the root page.
   - In `record_fact` and `stage_document`: Add `default=str` to `json.dumps`.
   - In `get_ledger_markdown`: Catch `(ValueError, OverflowError, OSError)` on `datetime.fromtimestamp`.
   - In `stage_document`: Remove redundant second call to `clean_expired_staged_docs()`.

3. **`searcher.py` (DEF-09, DEF-10):**
   - In `_canonical_url`: Handle non-string inputs safely in exception handler: `return str(url).rstrip('/') if url else ""`.
   - In `clean_agent_query`: Allow 2-letter technical terms (`len(q) >= 2`) when stripping conversational fluff.

4. **`parallel_pipeline.py` (DEF-11, DEF-12):**
   - In `format_fast_digest` and `format_deep_digest`: Explicitly handle `if not results:` with a "No search results found" message instead of "All sources blocked by paywalls".
   - In all markdown formatting templates: Replace `f"[{r.get('title')}]({r.get('url')})"` with sanitized fallbacks: `title = r.get('title') or 'Source'`, `url = r.get('url') or '#'`.

---

## 5. Conclusion

The adversarial audit confirms that while previous passes established core optimizations, critical latent edge cases in markdown segmentation, section retention, type safety, and URL caching remained unaddressed. All 15 vulnerabilities have been empirically verified via `tests/test_adversarial_v4_fuzzing.py`. Applying the remediation specifications above will bring the repository to bulletproof reliability.
