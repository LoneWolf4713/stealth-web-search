# Remediation Plan V4: Hardening Against Fuzzing & Specification Vulnerabilities (Pass 6)
**Target Repository:** `/home/prtyksh/tmp/web_search_tool`  
**Target Branch:** `feat/context-token-optimization`  
**Source Audit:** Blank-Slate Adversarial Red-Team Audit (Pass 6)  
**Objective:** Remediate all 15 vulnerabilities documented in `adversarial_redteam_audit_report_v4.md` and satisfy all test cases in `tests/test_adversarial_v4_fuzzing.py`.

---

## 1. Vulnerability & File Responsibility Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              WORKER A: CHUNKER (chunker.py)                            │
│  🔴 DEF-01: Multi-block expansion: split at code fences; prevent prose from being wrapped│
│  🔴 DEF-02: Silent data loss: merge/retain sections < min_chunk_words (25 words)       │
│  🟠 DEF-03: CODE_BLOCK_PATTERN negative lookahead: stop 3-tick fence early termination │
│  🟠 DEF-04: safe_truncate_markdown heuristic: remove target_len - last_newline <= 200  │
│  🟢 DEF-13: Overly aggressive prefix matching in _clean_section_content                │
│  🟢 DEF-14: Single-line inline code spans in _get_unclosed_fences                       │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                      WORKER B: VAULT & SEARCHER (vault.py, searcher.py)                │
│  🟠 DEF-05: Non-dict heading title formatting in extract_staged_slice                  │
│  🟡 DEF-06: Strip URL fragment in _hash_url for proper cache deduplication             │
│  🟡 DEF-07: default=str in json.dumps for record_fact and stage_document               │
│  🟡 DEF-08: Guard datetime.fromtimestamp in get_ledger_markdown on extreme timestamps  │
│  🟡 DEF-09: Safe string handling in searcher._canonical_url on non-string inputs       │
│  🟡 DEF-10: Allow 2-letter technical terms (AI, C#, Go, R) in clean_agent_query        │
│  🟢 DEF-15: Consolidate duplicate cache purge in stage_document                        │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        WORKER C: PIPELINE (parallel_pipeline.py)                       │
│  🟡 DEF-11: "No search results found" message on 0 results (not "All 0 sources blocked")│
│  🟡 DEF-12: Fallbacks for title and URL to eliminate corrupted [None](None) links      │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Technical Remediation Specifications

### Worker A: `chunker.py`
1. **DEF-01 (Multi-Block Expansion in `_split_oversized_text`):**
   - In `_split_oversized_text`:
     When `len(chunk_body) > 2200`:
     Before calling `_split_oversized_code_block`, check whether `chunk_body` starts and ends as a single isolated code block.
     If `chunk_body` contains multiple code blocks or mixed prose and code blocks (e.g. `cb_matches = list(CODE_BLOCK_PATTERN.finditer(chunk_body))` and `len(cb_matches) > 1` or prose text exists between them), partition `chunk_body` at paragraph and block boundaries rather than wrapping the entire multi-block piece inside `_split_oversized_code_block`.
2. **DEF-02 (Retain Sections `< min_chunk_words`):**
   - In `chunk_markdown_smart`:
     ```python
     if w <= max_chunk_words and len(cleaned_sec) <= 2200:
         if w >= min_chunk_words or not raw_chunks:
             raw_chunks.append({
                 "heading": h["title"],
                 "content": cleaned_sec,
                 "word_count": w
             })
         else:
             # Merge small section into preceding chunk if budget allows, else keep separate
             combined = raw_chunks[-1]["content"] + "\n\n" + cleaned_sec
             if len(combined) <= 2200:
                 raw_chunks[-1]["content"] = combined
                 raw_chunks[-1]["word_count"] = len(combined.split())
             else:
                 raw_chunks.append({
                     "heading": h["title"],
                     "content": cleaned_sec,
                     "word_count": w
                 })
     ```
3. **DEF-03 (Nested Code Fence Termination in `CODE_BLOCK_PATTERN`):**
   - Update line 15:
     `CODE_BLOCK_PATTERN = re.compile(r'(`{3,}|~{3,})[\s\S]*?\1(?![`~])', re.MULTILINE)`
4. **DEF-04 (`safe_truncate_markdown` Heuristic Bug):**
   - In `safe_truncate_markdown`:
     Line 87: change `(is_table_row or last_newline > int(target_len * 0.7) or (target_len - last_newline <= 200))` to:
     `(is_table_row or last_newline >= int(target_len * 0.75))`
     This completely eliminates the catastrophic drop of body text when `target_len <= 200`.
5. **DEF-13 (Tighten Prefix Matching in `_clean_section_content`):**
   - In `_clean_section_content`:
     Only perform `startswith` stripping if `first_clean.lower() == ht_clean.lower()` or `len(ht_clean) >= 8`.
6. **DEF-14 (Single-Line Inline Code Spans in `_get_unclosed_fences`):**
   - In `_get_unclosed_fences`:
     Check if a line contains a closed fence on the same line:
     `if re.match(r'^([\s>]*)(`{3,}|~{3,}).*\2\s*$', line): continue`

---

### Worker B: `vault.py` & `searcher.py`
1. **DEF-05 (Non-Dict Heading Title Extraction in `extract_staged_slice`):**
   - In `vault.py:extract_staged_slice`:
     Lines 485, 493:
     `sections_summary = ", ".join(f"`{h.get('title', str(h)) if isinstance(h, dict) else str(h)}`" for h in headings[:8]) if headings else "None"`
2. **DEF-06 (Strip URL Fragments in `_hash_url`):**
   - In `vault.py:_hash_url`:
     Set `parts.fragment` to `""` before reconstructing `canonical_url`:
     `canonical_url = urlunsplit((scheme, netloc, norm_path, parts.query, ""))`
3. **DEF-07 (JSON Serialization `default=str`):**
   - In `vault.py:record_fact` and `stage_document`:
     Use `json.dumps(..., default=str)` for `tags_json` and `headings_json`.
4. **DEF-08 (Corrupted Timestamps Guard):**
   - In `vault.py:get_ledger_markdown`:
     Wrap `datetime.fromtimestamp(...)` in try/except `(ValueError, TypeError, OverflowError, OSError)` defaulting to `"unknown-date"`.
5. **DEF-09 (Safe String URL Handling in `_canonical_url`):**
   - In `searcher.py:_canonical_url`:
     Ensure exception handler returns `str(url).rstrip('/') if url is not None else ""` instead of failing on integers.
6. **DEF-10 (Support 2-Letter Technical Terms in `clean_agent_query`):**
   - In `searcher.py:clean_agent_query`:
     Change condition to: `return q if (len(q) >= 2 or any(c in q for c in '+#$')) and q.strip() else str(query).strip()`
7. **DEF-15 (Consolidate Cache Purge):**
   - In `vault.py:stage_document`:
     Remove the redundant second call to `clean_expired_cache()`.

---

### Worker C: `parallel_pipeline.py`
1. **DEF-11 (Clear Zero-Result Messaging):**
   - In `parallel_pipeline.py:format_fast_digest` and `format_deep_digest`:
     ```python
     if not results:
         lines.append(f"> ℹ️ **No search results found** for `{data.get('query')}`. Try refining search terms.\n")
         return "\n".join(lines)
     ```
2. **DEF-12 (Sanitized Markdown Links for Missing Titles/URLs):**
   - In `parallel_pipeline.py`:
     Whenever generating source links:
     `title = str(r.get('title') or r.get('url') or 'Untitled Source')`
     `url = str(r.get('url') or '#')`
     Never output `[None](None)`.

---

## 3. Verification Gate
1. Update `tests/test_adversarial_v4_fuzzing.py` assertions to assert the fixed behavior (15/15 PASS).
2. Run full regression suite:
   - `tests/test_token_optimization.py` (17/17 PASS)
   - `tests/test_adversarial_remediation.py` (12/12 PASS)
   - `tests/test_adversarial_v2_fuzzing.py` (13/13 PASS)
   - `tests/test_adversarial_v3_fuzzing.py` (15/15 PASS)
   - `tests/test_adversarial_v4_fuzzing.py` (15/15 PASS)
   Total: 72 tests passing 100%.
