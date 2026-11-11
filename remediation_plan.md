# Comprehensive Remediation Plan & Adversarial Fix Specification
**Target Branch:** `feat/context-token-optimization`  
**Repository:** `/home/prtyksh/tmp/web_search_tool`  
**Audit Source:** Sub-Agent 1 Red-Team Adversarial Code Audit  
**Objective:** Resolve all identified architectural bugs, markdown corruption vulnerabilities, database connection leaks, unhandled type exceptions, and dead code before executing empirical A/B benchmarks.

---

## 1. Systemic Architecture & Vulnerability Map

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 MCP SERVER (mcp_server.py)                              │
│  ❌ Bug 6: Mutable default `tags: list[str] = []` in `record_research_fact`            │
│  ❌ Bug 5: `session_id` never exposed or forwarded from clients                        │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │ IPC / JSON payload
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              WORKER DAEMON (daemon.py)                                 │
│  ❌ Bug 4: Unhandled `TypeError: int(None)` on `tier` and `pass_num`                   │
│  ❌ Bug 4: Missing `int` coercion on `max_chars` in `_handle_inspect_vault_doc`         │
│  ❌ Bug 7: Double regex heading extraction on multi-MB pages                           │
│  ❌ Bug 2: `clean_expired_staged_docs()` is dead code (never scheduled in watchdog)    │
└─────────────────────┬────────────────────────────────────────────────────┬─────────────┘
                      │                                                    │
                      ▼                                                    ▼
┌──────────────────────────────────────────┐    ┌────────────────────────────────────────┐
│        PERSISTENCE (vault.py)            │    │    FORMATTING (parallel_pipeline.py)   │
│  ❌ Bug 3: Database-wide connection leaks│    │  ❌ Bug 1: Naive character truncation  │
│     (`conn.close()` never called)        │    │     breaks code fences (```) and tables│
│  ❌ Bug 5: Silent query search fallbacks │    │  ❌ Bug 1: Duplicate truncation blocks  │
│     dumping raw doc intro on 0 matches   │    │     corrupting metadata header         │
└─────────────────────┬────────────────────┘    └───────────────────┬────────────────────┘
                      │                                             │
                      └──────────────────────┬──────────────────────┘
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                            TEXT PROCESSING (chunker.py)                                │
│  ❌ Bug 1: Raw character slicing in `find_query_passages` & `extract_section_snippet`  │
│  ❌ Bug 5: Queries with symbols/single chars (`C++`, `C#`, `$100`) stripped to []      │
│  ❌ Bug 5: `extract_section()` returns full document if no markdown headings exist     │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detailed Issue Catalog & Mitigation Specifications

### Issue 1: Universal Markdown Corruption & Syntax Destruction
* **Severity:** Critical (Corrupts LLM Context & Agent Prompt Structure)
* **Locations:**
  * `parallel_pipeline.py:265-267` (`p_content[:max_passage_chars - 35]`)
  * `parallel_pipeline.py:299-303, 314-318` (`full_output[:max_total_chars - 320]`)
  * `chunker.py:321, 329, 401-404` (`result[:max_chars - len(trunc_msg)]`)
  * `vault.py:332-336` (`text[:limit - len(indicator)]`)
  * `fetcher.py:457-461` (`content[:max_chars - 300]`)

#### The Failure Mechanism
Every truncation point in the pipeline relies on raw character-level slicing (`text[:limit]`). When markdown containing code blocks (````python ... ````), tables (`| col1 | col2 |`), or inline links (`[text](url)`) is cut at an arbitrary character offset:
1. The slice cuts inside a fenced code block, discarding the closing ````.
2. The code block is never closed.
3. In `format_fast_digest`, all subsequent sources, summary blocks, and the agent's response prompt are rendered **inside the unclosed code block**.
4. Markdown tables are cut mid-row, resulting in broken table syntax.

#### The Technical Solution
Implement a centralized, syntax-aware markdown truncation utility in `chunker.py` and reuse it across `parallel_pipeline.py`, `vault.py`, and `fetcher.py`.

```python
def safe_truncate_markdown(text: str, max_chars: int, suffix: str = "") -> str:
    """
    Truncate markdown text cleanly to max_chars without breaking markdown syntax:
    1. Slices cleanly on newline / paragraph boundaries when possible.
    2. Automatically balances and closes unclosed code fences (```).
    3. Prevents mid-row table truncation.
    4. Appends a structured notification suffix.
    """
    if not text or len(text) <= max_chars:
        return text

    target_len = max(0, max_chars - len(suffix))
    if target_len <= 0:
        return suffix[:max_chars]

    # Attempt to slice at a line break near the boundary
    slice_candidate = text[:target_len]
    last_newline = slice_candidate.rfind("\n")
    if last_newline > int(target_len * 0.8):
        truncated = slice_candidate[:last_newline]
    else:
        truncated = slice_candidate

    # Check for unclosed code fences (```)
    # Count occurrences of ``` at line starts or boundaries
    fence_matches = re.findall(r'(?m)^```', truncated)
    if len(fence_matches) % 2 != 0:
        # Odd number of fences means an open code block: close it cleanly
        truncated = truncated.rstrip() + "\n```"

    return truncated + suffix
```

---

### Issue 2: Dead Code & Unbounded SQLite Vault Storage Bloat
* **Severity:** High (Resource Leak / Disk Depletion)
* **Locations:**
  * `vault.py:303-313` (`clean_expired_staged_docs`)
  * `daemon.py:272-284` (`_idle_watchdog`)

#### The Failure Mechanism
`clean_expired_staged_docs()` is defined in `vault.py`, but it is **never invoked anywhere during the daemon's runtime**. The inline deletion in `init_vault()` only executes once when the module is imported. During extended research loops, multi-megabyte staged documents accumulate in `vault.sqlite` indefinitely.

#### The Technical Solution
1. In `daemon.py`: Import `clean_expired_staged_docs` and call it periodically in `_idle_watchdog()`:
   ```python
   def _idle_watchdog():
       last_clean = time.time()
       while True:
           time.sleep(30)
           now = time.time()
           # Periodically clean expired staged docs (every 10 minutes)
           if now - last_clean > 600:
               try:
                   purged = clean_expired_staged_docs()
                   if purged > 0:
                       logger.info(f"Purged {purged} expired staged documents from vault.")
               except Exception as e:
                   logger.warning(f"Error purging expired docs: {e}")
               last_clean = now
           ...
   ```
2. In `vault.py:stage_document()`: Opportunistically trigger cleanup if more than 1 hour has elapsed since last purge, ensuring standalone CLI executions also clean expired records.

---

### Issue 3: Database-Wide Connection Leaks
* **Severity:** High (File Descriptor Depletion & WAL Shared Memory Locks)
* **Locations:**
  * `vault.py:24-29` (`get_connection()`)
  * All functions across `vault.py` using `with get_connection() as conn:`

#### The Failure Mechanism
In Python's standard `sqlite3` module, the context manager `with conn:` handles transaction semantics (`COMMIT` / `ROLLBACK`); **it does NOT close the connection**. 
Because `conn.close()` is never called anywhere in `vault.py`, every single read, write, cache check, and ledger operation abandons a connection object to Python's garbage collector. Under multi-threaded daemon workloads (`_ThreadingUnixServer`), this leads to locked shared memory and file descriptor starvation.

#### The Technical Solution
Refactor `get_connection()` into a context manager using `contextlib.contextmanager` that guarantees connection closure in a `finally` block:

```python
from contextlib import contextmanager

@contextmanager
def get_connection():
    """Context manager providing thread-safe SQLite connection with guaranteed closure."""
    conn = sqlite3.connect(VAULT_DB_PATH, timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        yield conn
    finally:
        conn.close()
```

---

### Issue 4: Type Conversion Crashes in Daemon Handlers
* **Severity:** High (Crash / Unhandled 500 Responses to MCP Clients)
* **Locations:**
  * `daemon.py:189` (`max_chars` in `_handle_inspect_vault_doc`)
  * `daemon.py:194, 198` (`tier` and `pass_num` in `_handle_record_research_fact`)

#### The Failure Mechanism
1. In `_handle_record_research_fact`:
   ```python
   tier = int(kwargs.get("tier", 1))
   ```
   If a client sends `{"tier": null}` in JSON, `kwargs.get("tier", 1)` returns `None`. Calling `int(None)` raises `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'`.
2. In `_handle_inspect_vault_doc`:
   ```python
   max_chars = kwargs.get("max_chars", 1500)
   ```
   If passed as a string (e.g. `"1500"`), `apply_truncation` fails with `TypeError: '>' not supported between instances of 'int' and 'str'`.

#### The Technical Solution
Apply safe type coercion with explicit fallback defaults across all daemon handlers:

```python
def _safe_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

# In _handle_inspect_vault_doc:
max_chars = _safe_int(kwargs.get("max_chars"), 1500)

# In _handle_record_research_fact:
tier = _safe_int(kwargs.get("tier"), 1)
pass_num = _safe_int(kwargs.get("pass_num"), 1)
```

---

### Issue 5: Search Masking & Silent Failure in `inspect_vault_doc`
* **Severity:** Medium-High (Agent Hallucination & Erroneous Context Injection)
* **Locations:**
  * `chunker.py:96-98` (`extract_section`)
  * `chunker.py:326-331` (`find_query_passages`)
  * `vault.py:344-348, 354-361` (`extract_staged_slice`)

#### The Failure Mechanism
1. **Documents without Headings:** In `chunker.py:96-98`, if a document contains no markdown `#` headings, `extract_section()` returns the raw document. In `vault.py:342`, this is treated as "section found", returning the document intro as if the requested section was present.
2. **Missing Query Inside Section:** In `vault.py:344-348`, if an agent queries `inspect_vault_doc(doc_id, section="API", query="delete_user")`, and `delete_user` does NOT exist in that section, `q_passages` returns empty. The code falls through and returns the first 1,500 chars of the section without warning the agent that 0 matches were found.
3. **Symbol / Short Keyword Erasure:** In `chunker.py:326`, `q_terms` filters with `if len(w) > 1`. Queries like `C++`, `C#`, `R`, or `$100` produce `q_terms = []`. Instead of searching for the exact literal query, the code dumps the start of the document.

#### The Technical Solution
1. In `chunker.py:extract_section()`:
   ```python
   headings = extract_headings(markdown)
   if not headings:
       return f"Section '{target}' not found: document contains no structured markdown headings."
   ```
2. In `chunker.py:find_query_passages()`:
   Ensure literal substring search works even when individual regex words are $\le 1$ character:
   ```python
   query_clean = query.strip()
   q_terms = [w.lower() for w in re.findall(r'[\w\-]+', query_clean)]
   # If no terms or terms are short, rely strictly on literal substring matching
   ```
   If no passages match, strictly return `""` instead of dumping document prefixes.
3. In `vault.py:extract_staged_slice()`:
   Explicitly report when a query has no matches inside a section:
   ```python
   if query and query.strip():
       q_passages = find_query_passages(sec_text, query.strip(), max_chars=max_chars)
       if q_passages:
           return apply_truncation(q_passages, max_chars)
       return f"No passages found matching query '{query}' in section '{sec_clean}' of document '{doc_id}'."
   ```

---

### Issue 6: Mutable Default Arguments in MCP Tool Signatures
* **Severity:** Medium (Python Anti-Pattern / Shared State Leak)
* **Location:** `mcp_server.py:209` (`tags: list[str] = []`)

#### The Failure Mechanism
Using `tags: list[str] = []` in the function signature binds a single mutable list instance to the function object. Any in-place modification affects subsequent calls.

#### The Technical Solution
```python
@mcp.tool()
def record_research_fact(
    category: str,
    tier: int,
    fact_text: str,
    source_url: str = "",
    source_title: str = "",
    pass_num: int = 1,
    tags: Optional[list[str]] = None
) -> str:
    tags = tags or []
```

---

### Issue 7: Redundant Regex AST Heading Extraction
* **Severity:** Low-Medium (Unnecessary Latency Overhead)
* **Locations:**
  * `daemon.py:116` (`headings = extract_headings(content)`)
  * `vault.py:231` (`headings = extract_headings(content)` inside `stage_document`)

#### The Technical Solution
Update `stage_document()` to accept optional pre-parsed `headings`:
```python
def stage_document(
    url: str,
    title: str,
    content: str,
    session_id: str = "default",
    ttl_days: float = 7.0,
    headings: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    if headings is None:
        headings = extract_headings(content)
```
Pass `headings` directly from `daemon.py:_handle_fetch()` to eliminate redundant regex scanning.

---

### Issue 8: Disconnected CLI Architecture vs. Daemon
* **Severity:** Medium (Developer Experience & High Latency in CLI)
* **Location:** `cli.py:127-137`

#### The Technical Solution
Update `cli.py:fast()` to attempt routing through `daemon_client.daemon_request()` first when the daemon is available, taking advantage of the warm in-memory model weights (<500ms response time), while falling back to in-process execution if the daemon is stopped.

---

### Issue 9: Hardcoded User Paths in README
* **Severity:** Low (Code Hygiene)
* **Location:** `README.md:168-169`

#### The Technical Solution
Replace `/home/prtyksh/tmp/web_search_tool/...` with generic path configurations:
```json
{
  "mcpServers": {
    "web-search": {
      "command": "python",
      "args": ["/path/to/web_search_tool/mcp_server.py"]
    }
  }
}
```

---

### Issue 10: Commit Real Automated Test Suite
* **Severity:** High (Integrity & Regression Prevention)
* **Location:** Create `tests/test_token_optimization.py`

#### The Technical Solution
Implement a complete pytest/unittest suite verifying:
1. `test_multi_density_ceilings`: Verify `compact` ($\le 3,500$ chars), `balanced` ($\le 8,000$ chars), `raw` modes.
2. `test_markdown_safe_truncation`: Verify unclosed code blocks (````) are cleanly closed, and tables are preserved.
3. `test_vault_staging_and_inspection`: Verify staging, section slicing, query slicing, and explicit error messages.
4. `test_symbol_queries`: Verify queries like `C++` and `$100` match correctly.
5. `test_daemon_type_resilience`: Verify `tier=None`, `pass_num=None`, and `max_chars="1500"` do not crash.
6. `test_research_ledger_lifecycle`: Verify record, markdown rendering, filtering by category/tier, and clearing.
7. `test_staged_docs_cleanup`: Verify expired records are purged cleanly.

---

## 3. Step-by-Step Remediation Checklist

- [ ] **Step 1: Core Markdown Safety Utility**
  - Implement `safe_truncate_markdown` in `chunker.py`.
  - Update `parallel_pipeline.py` to use `safe_truncate_markdown`.
  - Update `vault.py` to use `safe_truncate_markdown`.
  - Update `fetcher.py` to use `safe_truncate_markdown`.
- [ ] **Step 2: Database Connection & Resource Safety**
  - Refactor `vault.py:get_connection()` to `@contextmanager` with guaranteed `conn.close()`.
  - Wire `clean_expired_staged_docs()` into `daemon.py:_idle_watchdog()`.
  - Add opportunistic cleanup in `vault.py:stage_document()`.
- [ ] **Step 3: Daemon & MCP Handler Hardening**
  - Add `_safe_int()` helper in `daemon.py` and apply to `tier`, `pass_num`, and `max_chars`.
  - Fix mutable default `tags: list[str] = []` in `mcp_server.py`.
  - Pass pre-parsed headings into `stage_document()` to eliminate double regex parsing.
- [ ] **Step 4: Search Slice & Symbol Query Fixes**
  - Fix `chunker.py:extract_section()` for documents without headings.
  - Fix `chunker.py:find_query_passages()` to preserve symbols and return empty string on 0 matches.
  - Fix `vault.py:extract_staged_slice()` to return explicit error messages on missing queries inside sections.
- [ ] **Step 5: Documentation & Path Cleanup**
  - Remove developer-specific paths from `README.md`.
- [ ] **Step 6: Automated Test Suite Implementation**
  - Create and run `tests/test_token_optimization.py`.
  - Verify 100% test pass rate before running final empirical A/B benchmarks.
