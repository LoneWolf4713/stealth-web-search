# Remediation Plan V5: Comprehensive Hardening Against Pass 7 Vulnerabilities

**Target Codebase:** `/home/prtyksh/tmp/web_search_tool`  
**Reference Report:** `adversarial_redteam_audit_report_v5.md`  
**Target Regression Test Suite:** `tests/test_adversarial_v5_fuzzing.py`

---

## 1. Work Allocation & Responsibility Matrix

### Domain 1: Markdown Parsing, Heading Integrity, and Token Budgeting (`chunker.py`)
- **DEF-V5-01 (CRITICAL)**: Retain document preambles/abstracts before the first `# Heading` in `chunk_markdown_smart`.
- **DEF-V5-02 (HIGH)**: Enforce minimum retention threshold (`last_newline >= int(target_len * 0.50)`) on table row newline backtracking in `safe_truncate_markdown`.
- **DEF-V5-03 (HIGH)**: State-machine fence tracking in `extract_headings` so code comments in unclosed code fences are not mistaken for headings.
- **DEF-V5-07 (part 1) (MEDIUM)**: Sanitize `target` in `extract_section` using `str(target or "")` to prevent `AttributeError` on integer arguments.
- **DEF-V5-10 (MEDIUM)**: Cap `get_table_of_contents` to `max_headings=100` and `max_chars=3500` with truncation notice to prevent context window flooding.
- **DEF-V5-11 (LOW)**: Reserve buffer space for closing code fences in `safe_truncate_markdown` so output length never exceeds `max_chars`.
- **DEF-V5-14 (LOW)**: Filter HTML comment blocks (`<!-- ... -->`) in `extract_headings`.

### Domain 2: Vault Persistence, Neural Type Safety & Model Pipelines (`vault.py`, `neural_fast.py`)
- **DEF-V5-07 (part 2) (MEDIUM)**: Sanitize `section` and `query` in `vault.extract_staged_slice` with `str(x or "")`.
- **DEF-V5-08 (MEDIUM)**: In `neural_fast._filter_and_dedupe_entities`, safely coerce entity score to `float` handling `None` gracefully without `TypeError`.
- **DEF-V5-09 (MEDIUM)**: In `neural_fast._dedupe_chunks`, safely handle `{"content": None}` without `AttributeError`.
- **DEF-V5-12 (LOW)**: In `vault.get_staged_document`, isolate `json.loads(toc_json)` with fallback to `[]` so corrupt TOC metadata does not mask intact markdown content.

### Domain 3: Parallel Pipeline, Daemon Wire Protocol & CLI UX (`parallel_pipeline.py`, `daemon.py`, `cli.py`)
- **DEF-V5-04 (HIGH)**: In `parallel_pipeline.fast_intelligent_search`, partition results into valid and error entries so errors in one perspective do not abort valid results in another.
- **DEF-V5-05 (HIGH)**: In `daemon._handle_fetch`, use `content.startswith("Failed to retrieve")` to prevent staging network error strings in SQLite vault.
- **DEF-V5-06 (MEDIUM)**: In `cli.py` (`toc` and `section`), check `content.startswith("Failed to retrieve")` and report error directly instead of masking as "No structured headings".
- **DEF-V5-13 (LOW)**: In `daemon._Handler.handle`, serialize JSON using `default=str` to prevent silent socket drops on non-serializable objects.
- **DEF-V5-15 (LOW)**: In `cli.py` (`batch`), cap standalone queries at `queries[:3]` to enforce consistent limits with daemon mode.

---

## 2. Regression Testing Strategy
1. Implement fixes across all 5 files (`chunker.py`, `vault.py`, `neural_fast.py`, `parallel_pipeline.py`, `daemon.py`, `cli.py`).
2. Update `tests/test_adversarial_v5_fuzzing.py` assertions from defect-proving assertions to remediated/hardened assertions.
3. Verify `tests/test_adversarial_v5_fuzzing.py` (15/15 PASS).
4. Run full 87-test regression battery across all 6 test suites:
   - `tests/test_token_optimization.py` (17 tests)
   - `tests/test_adversarial_remediation.py` (12 tests)
   - `tests/test_adversarial_v2_fuzzing.py` (13 tests)
   - `tests/test_adversarial_v3_fuzzing.py` (15 tests)
   - `tests/test_adversarial_v4_fuzzing.py` (15 tests)
   - `tests/test_adversarial_v5_fuzzing.py` (15 tests)
   - **Total: 87/87 PASS.**
