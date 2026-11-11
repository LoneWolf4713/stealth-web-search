# Remediation Plan V3: Hardening Against Fuzzing & Specification Vulnerabilities (Pass 5)
**Target Repository:** `/home/prtyksh/tmp/web_search_tool`  
**Target Branch:** `feat/context-token-optimization`  
**Source Audit:** Blank-Slate Adversarial Red-Team Audit (Pass 5)  
**Objective:** Remediate all 15 vulnerabilities documented in `adversarial_redteam_audit_report_v3.md` and satisfy all test cases in `tests/test_adversarial_v3_fuzzing.py`.

---

## 1. Vulnerability & File Responsibility Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              WORKER A: CHUNKER (chunker.py)                            │
│  🔴 DEF-01: Chunker offset desync: slice cleaned_md[start:end] instead of markdown    │
│  🟠 DEF-02: Support CommonMark tildes (~~~): update CODE_BLOCK_PATTERN                 │
│  🟠 DEF-03: Oversized tildes fragmentation: enable save_code for tildes                │
│  🟠 DEF-04: Static placeholder injection: replace with random session nonce slots     │
│  🟢 DEF-14: False-positive short heading deletion in _clean_section_content            │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        WORKER B: VAULT & FETCHER (vault.py, fetcher.py)                │
│  🟠 DEF-05: Unhandled TypeError on non-string tags in get_ledger_markdown              │
│  🟡 DEF-06: Unhandled OverflowError on float('inf') in record_fact                     │
│  🟡 DEF-07: Unhandled OverflowError on float('inf') in get_ledger_entries              │
│  🟡 DEF-08: Permissive negative TTL in stage_document: check ttl <= 0                  │
│  🟡 DEF-09: Unhandled AttributeError in _normalize_query on non-string query           │
│  🟡 DEF-10: Unhandled TypeError in fetch_page_detailed(None) & extract_domain(None)    │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│             WORKER C: PIPELINE, DAEMON & NEURAL (parallel_pipeline.py, daemon.py,      │
│                                                  neural_fast.py)                       │
│  🟡 DEF-11: format_fast_digest emits corrupted [None](None) links on search error      │
│  🟡 DEF-12: format_deep_digest emits corrupted [None](None) links on search error      │
│  🟡 DEF-13: Silent fetch error swallowing in daemon get_toc and read_section           │
│  🟢 DEF-15: Blind 1450-char slicing in neural_fast: use safe_truncate_markdown         │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Technical Remediation Specifications

### Worker A: `chunker.py`
1. **DEF-01 (Offset Desynchronization):**
   - In `chunk_markdown_smart(markdown, ...)`:
     Change `sec_text = markdown[start:end].strip()` to `sec_text = cleaned_md[start:end].strip()`.
     Also calculate `end = headings[i + 1]["start"] if i + 1 < len(headings) else len(cleaned_md)`.
2. **DEF-02 & DEF-03 (Tilde Fences):**
   - Update line 14:
     `CODE_BLOCK_PATTERN = re.compile(r'(`{3,}|~{3,})[\s\S]*?\1', re.MULTILINE)`
3. **DEF-04 (Nonce-Based Placeholders):**
   - In `_split_oversized_text` and `find_query_passages`:
     Generate a per-call uuid token: `slot_nonce = uuid.uuid4().hex[:8]`.
     Use `f"__CB_SLOT_{slot_nonce}_{len(code_blocks)-1}__"`.
     Restore with `re.sub(rf'__CB_SLOT_{slot_nonce}_(\d+)__', lambda m: ..., text)`.
4. **DEF-14 (Short Heading Deletion Guard):**
   - In `_clean_section_content`:
     Only perform prefix matching if `len(first_clean) >= 3 and len(ht_clean) >= 3`.
     If shorter, require exact case-insensitive equality: `first_clean.lower() == ht_clean.lower()`.

---

### Worker B: `vault.py` & `fetcher.py`
1. **DEF-05 (Tag Coercion in Ledger Markdown):**
   - In `vault.py:get_ledger_markdown`:
     Change `', '.join(tags)` to `', '.join(str(t) for t in tags)`.
2. **DEF-06 & DEF-07 (`OverflowError` Handling):**
   - In `vault.py:record_fact` and `vault.py:get_ledger_entries`:
     Catch `(ValueError, TypeError, OverflowError)` during `tier` conversion.
3. **DEF-08 (Negative TTL Guard):**
   - In `vault.py:stage_document`:
     Change `if math.isnan(ttl) or math.isinf(ttl) or ttl == 0:` to:
     `if math.isnan(ttl) or math.isinf(ttl) or ttl <= 0: ttl = 7.0`.
4. **DEF-09 (Non-String Query Normalization):**
   - In `vault.py:_normalize_query`:
     Change `query.strip()` to `str(query).strip() if query is not None else ""`.
5. **DEF-10 (`None` URL Guard in Fetcher & Domain Extraction):**
   - In `fetcher.py:fetch_page_detailed`:
     Early return if `not url or not str(url).strip()`:
     `{"success": False, "content": "", "error_reason": "Missing or empty URL", "cached": False}`.
   - In `vault.py:extract_domain`:
     Guard `if not url: return ""` and safely decode bytes if `urlparse` produces bytes.

---

### Worker C: `parallel_pipeline.py`, `daemon.py`, `neural_fast.py`
1. **DEF-11 & DEF-12 (Clean Search Error Formatting):**
   - In `parallel_pipeline.py:format_fast_digest` and `format_deep_digest`:
     Check if `results and isinstance(results[0], dict) and "error" in results[0]`:
     Render `f"> ❌ **Search Error**: {results[0]['error']}\n"` instead of broken `- [None](None)` links.
2. **DEF-13 (Surface Fetch Errors in TOC & Section Handlers):**
   - In `daemon.py:_handle_get_toc` and `_handle_read_section`:
     If `content.startswith("Failed to retrieve")`: return `content` immediately.
3. **DEF-15 (Syntax-Safe Truncation in Neural Selection):**
   - In `neural_fast.py:_select_top_chunks`:
     Replace raw string slicing with `safe_truncate_markdown(c["content"], 1500, suffix="\n[... Passage truncated ...]")`.

---

## 3. Verification Gate
1. Update `tests/test_adversarial_v3_fuzzing.py` to assert the **hardened, fixed** behaviors.
2. Run full test suite:
   - `tests/test_adversarial_v3_fuzzing.py` (15/15 PASS)
   - `tests/test_token_optimization.py` (17/17 PASS)
   - `tests/test_adversarial_remediation.py` (12/12 PASS)
   - `tests/test_adversarial_v2_fuzzing.py` (13/13 PASS)
   Total: 57 tests passing 100%.
