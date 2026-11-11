# Remediation Plan V2: Hardening Against Fuzzing & Specification Vulnerabilities
**Target Repository:** `/home/prtyksh/tmp/web_search_tool`  
**Target Branch:** `feat/context-token-optimization`  
**Source Audit:** Blank-Slate Adversarial Fuzzing Audit (Pass 4)  
**Objective:** Remediate all 18 vulnerabilities identified in `adversarial_redteam_audit_report.md` and satisfy all test cases in `tests/test_adversarial_v2_fuzzing.py`.

---

## 1. Systemic Vulnerability & Component Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                CHUNKER (chunker.py)                                    │
│  🔴 Defect 1: CommonMark non-nesting fence tracking (stop extra backtick corruption)   │
│  🔴 Defect 2: Oversized code block fragmentation (>2200 chars without code fences)    │
│  🔴 Defect 3: Heading hijacking in extract_section (h_title in target_clean)           │
│  🔴 Defect 5: Cascading placeholder injection in sequential .replace()                 │
│  🟠 Defect 4: Bare heading returned when subheading immediately follows                │
│  🟠 Defect 5: Valid heading stripped on empty heading_title                            │
│  🟠 Defect 14: Quadratic O(H x C) regex scan in extract_headings                       │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 VAULT (vault.py)                                       │
│  🔴 Defect 8: Silent section suppression in extract_staged_slice                       │
│  🟠 Defect 6: Unhandled sqlite3.IntegrityError on NaN TTL in stage_document            │
│  🟠 Defect 7: Unexpirable ghost rows on float("inf") TTL                              │
│  🟠 Defect 8: AttributeError on non-string category in get_ledger_entries              │
│  🟡 Defect 9: Trailing slash URL hash divergence (/api/ vs /api)                       │
│  🟡 Defect 17: Zero TTL eviction on search_cache and page_cache                        │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                         DAEMON & SEARCHER (daemon.py, searcher.py)                     │
│  🟠 Defect 11: OverflowError in _safe_int on float("inf")                              │
│  🟡 Defect 12: Search rate limits/errors swallowed and emitted as [None](None)         │
│  🟡 Defect 16: cli.py batch command bypassing shared background daemon                 │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Root Cause Analysis & Technical Fix Specifications

### Issue 1: CommonMark Code Fence Tracking & Non-Nesting Compliance
* **File & Lines:** `chunker.py:17-50, 52-92`
* **Root Cause:** In the CommonMark specification (§4.5 Fenced code blocks), code fences **do not nest**. Any backticks inside an opening fence of length $N$ are literal code content. The current implementation treated shorter backtick sequences inside a 4-backtick fence as nested blocks, corrupting the fence stack and appending extraneous fences (`\n````\n`).
* **Fix Specification:**
  1. Replace stack-based fence tracking with a strict CommonMark state machine:
     ```python
     def _get_unclosed_fences(text: str) -> List[Tuple[str, str]]:
         active_char = None
         active_len = 0
         active_indent = ""

         for line in text.split("\n"):
             stripped_start = line.lstrip()
             indent = line[:len(line) - len(stripped_start)]
             
             # If inside an active block, only an equal or longer fence of the same char closes it
             if active_len > 0:
                 close_match = re.match(r'^(`{3,}|~{3,})\s*$', stripped_start)
                 if close_match:
                     fence_str = close_match.group(1)
                     if fence_str[0] == active_char and len(fence_str) >= active_len:
                         active_len = 0
                         active_char = None
             else:
                 # Check for opening fence
                 open_match = re.match(r'^(`{3,}|~{3,})', stripped_start)
                 if open_match:
                     fence_str = open_match.group(1)
                     active_char = fence_str[0]
                     active_len = len(fence_str)
                     active_indent = indent

         if active_len > 0:
             return [(active_char * active_len, active_indent)]
         return []
     ```
  2. If `active_len == 0`, `safe_truncate_markdown` appends **zero** extra fences.

---

### Issue 2: Oversized Code Block Chunking
* **File & Lines:** `chunker.py:375-387`
* **Root Cause:** When a code block exceeds 2,200 characters, `_split_oversized_text` subdivides it across lines/clauses, but does not wrap each sub-chunk in matching code fences.
* **Fix Specification:**
  Extract the language tag from the original code block (e.g. ````python`). When subdividing, wrap each sub-chunk in ````{lang}\n{sub_chunk}\n```` so every piece is syntactically valid markdown.

---

### Issue 3: Heading Hijacking in `extract_section`
* **File & Lines:** `chunker.py:194-196`
* **Root Cause:** Matching uses `if h_title in target_clean`. If a document has short headings like `# A` or `# In`, any query containing `"a"` (e.g. `"Advanced Architecture"`) matches `# A`.
* **Fix Specification:**
  1. Check exact match: `if target_clean == h_title or target_clean == h_raw_title:`
  2. Check substring match only in the correct direction: `if target_clean in h_title or target_clean in h_raw_title:`
  3. Never check `h_title in target_clean` unless matching on exact token boundaries (`re.search(r'\b' + re.escape(h_title) + r'\b', target_clean)`).
  4. Disallow empty query matches.

---

### Issue 4: Cascading Placeholder Injection in Code Block Masking
* **File & Lines:** `chunker.py:356-374, 511-514`
* **Root Cause:** Sequential `.replace(f"__CODE_BLOCK_SLOT_{idx}__", cb)` replaces previously restored code that happened to contain a slot token.
* **Fix Specification:**
  Use single-pass regex substitution:
  ```python
  def restore_code(text: str) -> str:
      return re.sub(
          r'__CODE_BLOCK_SLOT_(\d+)__',
          lambda m: code_blocks[int(m.group(1))] if int(m.group(1)) < len(code_blocks) else m.group(0),
          text
      )
  ```
  In single-pass `re.sub()`, restored content is never re-scanned, eliminating cascading injection.

---

### Issue 5: Bare Headings on Immediate Subheadings
* **File & Lines:** `chunker.py:541-554`
* **Root Cause:** If a matched heading is immediately followed by `## Subheading`, the loop breaks without attaching any body text.
* **Fix Specification:**
  Allow the loop to continue through subheadings until at least one substantive body paragraph is accumulated, guaranteeing that the returned passage contains actual documentation text.

---

### Issue 6: Empty `heading_title` in `_clean_section_content`
* **File & Lines:** `chunker.py:116`
* **Root Cause:** `if first_clean.lower().startswith(heading_title.lower()):` evaluates to `True` when `heading_title == ""`.
* **Fix Specification:**
  Guard with `if heading_title and heading_title.strip():`.

---

### Issue 7: NaN & Inf TTL Handling in `vault.py:stage_document`
* **File & Lines:** `vault.py:296-325`
* **Root Cause:** `float("nan")` passes `float()` conversion, but SQLite treats `nan` as SQL `NULL`, violating the `NOT NULL` constraint on `expires_at`. `float("inf")` creates unexpirable rows.
* **Fix Specification:**
  ```python
  try:
      ttl = float(ttl_days)
      if math.isnan(ttl) or math.isinf(ttl) or ttl <= 0:
          ttl = 7.0
      ttl = min(ttl, 365.0)  # Cap max TTL at 1 year
  except (ValueError, TypeError):
      ttl = 7.0
  ```

---

### Issue 8: Silent Section Suppression in `extract_staged_slice`
* **File & Lines:** `vault.py:444-460`
* **Root Cause:** When `section` is not found, if `query` is passed, it silently searches the entire document and returns unrelated passages.
* **Fix Specification:**
  When `section` is specified and not found, explicitly return an error:
  ```python
  available = ", ".join(f"`{h['title']}`" for h in headings[:8]) if headings else "None"
  return f"Error: Section '{sec_clean}' was not found in document '{doc_id}'. Available sections: {available}."
  ```

---

### Issue 9: Trailing Slash URL Normalization
* **File & Lines:** `vault.py:154`
* **Root Cause:** Slashes were only stripped if `path == "/"`. Subpaths `/docs/api/` and `/docs/api` produced divergent hashes.
* **Fix Specification:**
  Normalize subpaths: `path = path.rstrip("/") if path != "/" else "/"` before hashing.

---

### Issue 10: Non-String Category in Ledger
* **File & Lines:** `vault.py:543, 584`
* **Root Cause:** `category.strip()` throws `AttributeError` if `category` is an `int`.
* **Fix Specification:**
  `category = str(category).strip() if category is not None else None`.

---

### Issue 11: `OverflowError` on Infinity in `_safe_int`
* **File & Lines:** `daemon.py:98-104`
* **Root Cause:** Python raises `OverflowError: cannot convert float infinity to integer`.
* **Fix Specification:**
  Catch `(ValueError, TypeError, OverflowError)` in `_safe_int`.

---

### Issue 12: Swallowed Search Failures in `daemon.py:_handle_search`
* **File & Lines:** `daemon.py:125-130`
* **Root Cause:** `_handle_search` formats error dicts as `[None](None)`.
* **Fix Specification:**
  If results contain `{"error": ...}`, return `f"Search Error: {results[0]['error']}"`.

---

## 3. Implementation Assignment & File Boundaries

| Worker | Assigned Files (Strict) | Tasks Owned |
| :--- | :--- | :--- |
| **Worker A** | `chunker.py` | • CommonMark non-nesting fence tracking.<br>• Oversized code block fence re-wrapping.<br>• Exact/bidirectional heading matching in `extract_section`.<br>• Heading + body pairing on subheadings.<br>• Single-pass regex code block restoration.<br>• Empty heading title guard in `_clean_section_content`. |
| **Worker B** | `vault.py` | • NaN / Inf TTL validation & max TTL cap.<br>• Explicit error on missing section in `extract_staged_slice`.<br>• Subpath trailing-slash normalization in `_hash_url`.<br>• String coercion for `category` in ledger methods. |
| **Worker C** | `daemon.py`<br>`cli.py` | • Catch `OverflowError` in `_safe_int`.<br>• Format search errors cleanly instead of `[None](None)`.<br>• Route `cli.py batch` to shared daemon with in-process fallback. |

---

## 4. Verification Gate

After execution, all test suites must pass 100%:
1. `tests/test_adversarial_v2_fuzzing.py` (Must be updated to assert the fixed behavior and pass 13/13).
2. `tests/test_token_optimization.py` (Must pass 17/17).
3. `tests/test_adversarial_remediation.py` (Must pass 12/12).
