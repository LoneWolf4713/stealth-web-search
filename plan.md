# Production Implementation Plan & Verification Checklist
**Branch:** `feat/context-token-optimization`  
**Target:** Eliminate context explosion and provider quota exhaustion in 250-pass research loops (10 categories × 5 tiers × 5 passes) while hardening concurrency, preserving markdown integrity, and maintaining full backward compatibility.

---

## 1. Architectural Design Specifications (Post Red-Team Review)

Following the adversarial review in `red_team_report.md`, this plan adopts four hardened architectural decisions:

1. **Safe Paragraph & Structural Excerpts (No Naive Regex Sentence Splitting):**
   * Splitting raw markdown into isolated sentences breaks code blocks, fragments markdown tables, and creates noisy cross-encoder scores on 40-word snippets.
   * Instead, we implement **length-bounded structural passages** (600 characters max in `compact` mode) paired with section headings. This preserves markdown syntax, retains local context, and guarantees reliable cross-encoder ranking.
2. **Multi-Client Namespace Isolation for Staged Docs:**
   * `doc_id` is generated as `doc_<uuid12>` (e.g., `doc_7a9f2b8c1d3e`).
   * Staged documents include explicit `created_at` and `expires_at` (7-day default TTL) with explicit `"Doc expired / not found"` handling to prevent multi-client overwrites and silent data loss.
3. **Database Concurrency Hardening:**
   * Explicitly set `PRAGMA busy_timeout = 30000;` (30 seconds) on all SQLite connections to eliminate `database is locked` risks across worker threads and client connections.
4. **Decoupled 4-File IPC Contract:**
   * Every new MCP tool and parameter is strictly synchronized across:
     `vault.py` (Persistence) $\rightarrow$ `daemon.py` (Worker handler) $\rightarrow$ `daemon_client.py` (IPC) $\rightarrow$ `mcp_server.py` (MCP Tool Registry).

---

## 2. Detailed Step-by-Step Implementation Checklist

### PHASE 1: Zero-Risk Core Token Compression & Database Hardening

#### [x] 1.1 Database Concurrency Hardening (`vault.py`)
- [x] In `vault.py:get_connection()`, verify WAL mode and add `PRAGMA busy_timeout = 30000;`:
  ```python
  conn = sqlite3.connect(VAULT_DB_PATH, timeout=30.0)
  conn.execute("PRAGMA journal_mode=WAL;")
  conn.execute("PRAGMA synchronous=NORMAL;")
  conn.execute("PRAGMA busy_timeout=30000;")
  ```
- [x] Add query string normalization helper `_normalize_query(query: str) -> str` to strip leading/trailing whitespace, collapse internal spaces, and lowercase before hashing.

#### [x] 1.2 Strict Ceiling on `fetch()` Output (`daemon.py` & `fetcher.py`)
- [x] In `daemon.py:_handle_fetch()`, reduce the hard truncation ceiling from 25,000 characters to **6,000 characters** (~1,400 tokens):
  ```python
  MAX_FETCH_CHARS = 6000
  if len(content) > MAX_FETCH_CHARS:
      content = content[:MAX_FETCH_CHARS - 300] + (
          f"\n\n> [!NOTE]\n"
          f"> *Webpage content capped at {MAX_FETCH_CHARS:,} characters to protect context window. "
          f"Use `get_toc('{url}')` and `read_section` to inspect specific sections.*"
      )
  ```
- [x] In `fetcher.py:fetch_page()`, ensure raw fetch limits align with daemon expectations.

#### [x] 1.3 Multi-Density Formatting Engine (`parallel_pipeline.py`)
- [x] Add `density` parameter (`"compact"`, `"balanced"`, `"raw"`) to `fast_intelligent_search` and `format_fast_digest`.
- [x] Configure density specifications:
  * **`density="compact"` (Default for agentic research loops):**
    * Top finding lead excerpt capped at **280 characters**.
    * Max passages per accessible page: **1** (or 2 if total accessible pages $\le 2$).
    * Max characters per passage excerpt: **600 characters** (instead of 1,500).
    * Max entities shown per source: **4** (instead of 6).
    * Total digest ceiling: **3,500 characters** (~800 tokens max, down from ~5,500 tokens).
  * **`density="balanced"`:**
    * Top finding lead: 420 characters.
    * Max passages per page: 2.
    * Max characters per passage excerpt: 1,000 characters.
    * Total digest ceiling: **8,000 characters** (~1,900 tokens).
  * **`density="raw"` (Legacy behavior):**
    * 3 passages per page, 1,500 chars/passage, total cap 25,000 characters.
- [x] Update estimated token calculation header in `format_fast_digest` to accurately report density level and token count:
  `*Tokens: ~{est_tokens:,} | Chars: {len(full_output):,} | Density: {density.upper()} | Time: {elapsed}s on {device}*`

#### [x] 1.4 Update MCP Tool Signatures & CLI Launcher (`mcp_server.py`, `daemon.py`, `cli.py`)
- [x] In `mcp_server.py`:
  * Update `fast_neural_search(query: str, instruction: str = "", num_pages: int = 4, perspective: str = "tech", density: str = "compact")`.
  * Update `batch_search` to support `density: str = "compact"`.
  * Update tool docstrings with explicit guidance for AI agents on token savings.
- [x] In `daemon.py`:
  * Update `_handle_fast_neural_search` and `_handle_batch_search` to forward `density`.
- [x] In `cli.py`:
  * Add `--density` (`compact` / `balanced` / `raw`) option to `fast` and `batch` commands.

#### [x] 1.5 Phase 1 Verification & Test Suite
- [x] Restart daemon: kill running daemon process and ensure socket recreates cleanly.
- [x] Test 1: Run `fast_neural_search` with `density="compact"` via CLI. Verify output length $\le 3,500$ chars (~800 tokens).
- [x] Test 2: Run `fast_neural_search` with `density="raw"`. Verify backwards compatibility.
- [x] Test 3: Run `fetch` on a long documentation URL (e.g. `docs.python.org/3/library/asyncio.html`). Verify output length $\le 6,000$ chars with truncation warning.

---

### PHASE 2: Multi-Client Out-of-Band Staging & Pointer Protocol

#### [x] 2.1 SQLite Staging Schema & Management (`vault.py`)
- [x] Add `staged_documents` table in `vault.py:init_vault()`:
  ```sql
  CREATE TABLE IF NOT EXISTS staged_documents (
      doc_id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      url TEXT NOT NULL,
      title TEXT NOT NULL,
      content_markdown TEXT NOT NULL,
      toc_json TEXT NOT NULL,
      char_count INTEGER NOT NULL,
      token_count INTEGER NOT NULL,
      created_at REAL NOT NULL,
      expires_at REAL NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_staged_expires ON staged_documents(expires_at);
  CREATE INDEX IF NOT EXISTS idx_staged_session ON staged_documents(session_id);
  ```
- [x] Implement `stage_document(url: str, title: str, content: str, session_id: str = "default", ttl_days: float = 7.0) -> Dict[str, Any]`:
  * Generate UUID-based ID: `doc_` + `uuid.uuid4().hex[:12]`.
  * Extract TOC outline using `chunker.extract_headings(content)`.
  * Insert or replace into `staged_documents`.
  * Return metadata dict: `{doc_id, url, title, char_count, token_count, headings}`.
- [x] Implement `get_staged_document(doc_id: str) -> Optional[Dict[str, Any]]`:
  * Query by `doc_id`. Check `time.time() < expires_at`.
  * Return document dict or `None` if expired / not found.
- [x] Implement `clean_expired_staged_docs()` to purge records where `expires_at < now`.

#### [x] 2.2 Section & Query Slice Extraction (`vault.py` & `chunker.py`)
- [x] Implement `extract_staged_slice(doc_id: str, query: str = "", section: str = "", max_chars: int = 1500) -> str`:
  * Fetch doc from `vault.py`. If missing/expired, return clean error:
    `"Error: Document {doc_id} not found or expired. Please re-fetch the original URL."`
  * If `section` specified: call `chunker.extract_section(content, section)`.
  * If `query` specified: run lightweight keyword/regex scan for best paragraph containing query terms.
  * Cap returned text at `max_chars` (default 1,500 chars / ~350 tokens) with clear offset indicator.

#### [x] 2.3 Implement `inspect_vault_doc` Across the 4-File Pipeline
- [x] In `vault.py`: Expose `inspect_staged_doc(doc_id: str, query: str = "", section: str = "", max_chars: int = 1500) -> str`.
- [x] In `daemon.py`: Add `_handle_inspect_vault_doc(kwargs)` to `HANDLERS`.
- [x] In `daemon_client.py`: Ensure `daemon_request("inspect_vault_doc", ...)` routes seamlessly.
- [x] In `mcp_server.py`: Register `@mcp.tool() inspect_vault_doc(doc_id: str, query: str = "", section: str = "", max_chars: int = 1500) -> str`.
  * Add clear docstring instructing the LLM on how to inspect staged documents.

#### [x] 2.4 Pointer Manifest Card Generator
- [x] Implement helper `format_manifest_card(staged_meta: Dict[str, Any], lead_snippet: str = "") -> str`:
  ```markdown
  ### 📄 [Staged Document: {doc_id}] {title}
  * **Source**: {url}
  * **Total Size**: ~{token_count:,} tokens ({char_count:,} chars) [Stored Out-of-Band in Vault]
  * **Available Sections**: {sections_summary}
  * **Lead Snippet**: {lead_snippet}
  * **How to Read**: Call `inspect_vault_doc('{doc_id}', section='...')` or `inspect_vault_doc('{doc_id}', query='...')` to pull targeted excerpts without filling context.
  ```
- [x] In `daemon.py:_handle_fetch()`, add support for `as_pointer: bool = False`. If True, stages document and returns the manifest card (~120 tokens).

#### [x] 2.5 Phase 2 Verification & Test Suite
- [x] Test 1: Stage a document via daemon client. Verify row exists in `staged_documents` in `vault.sqlite`.
- [x] Test 2: Call `inspect_vault_doc` with valid `doc_id` and `section="Routing"`. Verify targeted section returns in <15ms.
- [x] Test 3: Call `inspect_vault_doc` with an invalid/expired ID. Verify clean, non-crashing error message.
- [x] Test 4: Verify multi-client safety: ensure concurrent calls with different session IDs generate unique collision-free `doc_id`s.

---

### PHASE 3: Persistent Research State Ledger & Session Checkpointing

#### [x] 3.1 SQLite Research Ledger Schema (`vault.py`)
- [x] Add `research_ledger` table in `vault.py:init_vault()`:
  ```sql
  CREATE TABLE IF NOT EXISTS research_ledger (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      category TEXT NOT NULL,
      tier INTEGER NOT NULL,
      pass_num INTEGER NOT NULL,
      fact_text TEXT NOT NULL,
      source_url TEXT,
      source_title TEXT,
      tags_json TEXT NOT NULL,
      created_at REAL NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_ledger_cat_tier ON research_ledger(category, tier);
  ```
- [x] Implement `record_fact(category: str, tier: int, fact_text: str, source_url: str = "", source_title: str = "", pass_num: int = 1, tags: List[str] = None) -> int`.
- [x] Implement `get_ledger_markdown(category: Optional[str] = None, tier: Optional[int] = None) -> str`:
  * Returns structured, compact Markdown summary of all established facts grouped by category and tier.
  * Formatted specifically to be injected as the initial prompt context for a fresh agent session.
- [x] Implement `clear_ledger(category: Optional[str] = None, confirm: bool = False) -> str`.

#### [x] 3.2 Wire Ledger Tools to MCP Server (`daemon.py` & `mcp_server.py`)
- [x] In `daemon.py`: Add `_handle_record_research_fact`, `_handle_get_research_ledger`, `_handle_clear_research_ledger`.
- [x] In `mcp_server.py`: Expose:
  * `@mcp.tool() record_research_fact(category: str, tier: int, fact_text: str, source_url: str = "", source_title: str = "", pass_num: int = 1, tags: list[str] = []) -> str`
  * `@mcp.tool() get_research_ledger(category: str = None, tier: int = None) -> str`
  * `@mcp.tool() clear_research_ledger(category: str = None, confirm: bool = False) -> str`

#### [x] 3.3 Phase 3 Verification & Test Suite
- [x] Test 1: Record 3 facts across 2 categories and 2 tiers.
- [x] Test 2: Call `get_research_ledger()`. Verify clean Markdown table/bullet format (~200 tokens).
- [x] Test 3: Verify context flushing paradigm: simulate an agent starting a new turn with only the ledger output as input context.

---

### PHASE 4: End-to-End Regression Testing, Profiling & Documentation

#### [x] 4.1 CLI & Script Regressions
- [x] Run `./run_tool.sh search "asyncio event loop" --num 3` (Verify standard search unaffected).
- [x] Run `./run_tool.sh fetch "https://docs.python.org/3/library/asyncio.html"` (Verify 6k cap).
- [x] Run `./run_tool.sh fast "rust tokio vs async-std" --density compact` (Verify ~800 tokens).
- [x] Run `./run_tool.sh fast "rust tokio vs async-std" --density raw` (Verify legacy behavior).
- [x] Run `./run_tool.sh toc "https://docs.rs/axum/latest/axum"` (Verify TOC generator intact).
- [x] Run `./run_tool.sh section "https://docs.rs/axum/latest/axum" "Routing"` (Verify section reader intact).

#### [x] 4.2 Benchmark Profiling & Token Metrics
- [x] Measure exact character and token count for typical research queries in `compact` vs `raw` mode.
- [x] Verify average token reduction is $\ge \mathbf{75\%}$ on fast neural search.
- [x] Verify query latency on CPU remains $\le \mathbf{3.5 \text{ seconds}}$ for warm cache/daemon.

#### [x] 4.3 Documentation & Usage Guide Update (`README.md`)
- [x] Document `--density` flag (`compact`, `balanced`, `raw`) in README.
- [x] Document `inspect_vault_doc` tool and Pointer Protocol workflow.
- [x] Document `record_research_fact` and `get_research_ledger` for high-depth research loops.
- [x] Provide explicit configuration examples for Claude Code and Antigravity.

---

## 3. Definition of Done (DoD) & Acceptance Criteria

| Criteria | Target | Verification Method |
| :--- | :--- | :--- |
| **`fetch()` Output Ceiling** | $\le 6,000$ characters | Automated test against long documentation page |
| **`fast_neural_search` Compact Output** | $\le 3,500$ characters (~800 tokens) | Automated test across 5 benchmark queries |
| **Passage Excerpt Quality** | No broken markdown tables or code blocks | Manual inspection of rendered markdown outputs |
| **Database Concurrency** | 0 lock errors under 3 concurrent workers | Threaded stress test against `vault.sqlite` |
| **Multi-Client Staging Safety** | Unique `doc_id`s, no overwrites | Concurrent staging test with differing session IDs |
| **Backward Compatibility** | All existing CLI commands and MCP tools work | Full regression suite passes with 0 errors |
| **CPU Latency Overhead** | $\le 3.5$ seconds total elapsed time | Benchmarked timing across warm daemon runs |
