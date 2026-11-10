# Web Search & Information Retrieval Suite

A fast, lightweight web search and information retrieval tool for developers and AI coding assistants. Gathers information, reads documentation, and synthesizes answers while keeping context token usage minimal.

---

## Capabilities

- **Keyless Web Search**: Multi-engine search for documentation, packages, articles, and news.
- **Perspective Filtering (`--perspective`)**:
  - `tech` *(default)*: Developer guides, open-source repositories, and technical architecture.
  - `market`: Products, companies, pricing, and comparisons.
  - `dual`: Simultaneously searches and groups both technical and commercial results.
- **Markdown Web Page Fetching**: Converts web pages into clean, readable Markdown.
- **Progressive Documentation Reader**:
  - `toc`: Reads the table of contents outline of large documentation pages (~150 tokens).
  - `section`: Reads only a specific section by heading name or index.
- **Two Retrieval Modes**:
  - **Fast Mode**: Rapidly fetches pages, extracts key entities, and reranks relevant passages. Sub-second once the shared daemon is warm and results are cached; the first call after the daemon starts (or a new query) typically takes 5-25s while models load and pages are fetched.
  - **Deep Mode**: Comprehensive analysis using a local language model to synthesize findings into structured notes.
- **MCP Server Support**: Plug-and-play Model Context Protocol server for Claude Code, Antigravity, and any MCP client.

---

## Quick Start & Installation

### 1. Clone & Set Up Environment
```bash
git clone https://github.com/LoneWolf4713/stealth-web-search.git
cd stealth-web-search

# Create virtual environment
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 2. Optional: GPU Acceleration for Deep Mode
To enable GPU acceleration for the local language model:
```bash
export CMAKE_ARGS="-DGGML_CUDA=on -DGGML_AVX512=off -DCMAKE_CUDA_ARCHITECTURES=86"
uv pip install --no-binary llama-cpp-python --reinstall llama-cpp-python
```

Download the lightweight Gemma 4 (E2B-IT) model:
```bash
mkdir -p ~/.cache/web_search_tool/models
aria2c -x 4 -s 4 -d ~/.cache/web_search_tool/models \
  "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
```

---

## CLI Usage

Run tools directly using the `./run_tool.sh` launcher:

### 1. Web Search
```bash
# Basic search
./run_tool.sh search "python asyncio tutorial" --num 5

# Search with perspective filter
./run_tool.sh search "time tracking software" --perspective market --num 4
./run_tool.sh search "screen time tracking" --perspective dual --num 4
```

### 2. Fetch Webpage as Markdown & Pointer Protocol
```bash
# Standard fetch (capped at 6,000 characters to protect context)
./run_tool.sh fetch "https://docs.python.org/3/library/asyncio.html"

# Out-of-band Pointer Protocol: Stage document in vault and get a ~120-token manifest card
./run_tool.sh fetch "https://docs.python.org/3/library/asyncio.html" --pointer

# Inspect targeted section or query within a staged document (~350 tokens)
./run_tool.sh inspect "doc_9f70141897b7" --section "Routing"
./run_tool.sh inspect "doc_9f70141897b7" --query "work-stealing scheduler"
```

### 3. Progressive Documentation Reading
```bash
# 1. Inspect Table of Contents outline (~150 tokens)
./run_tool.sh toc "https://docs.rs/axum/latest/axum"

# 2. Read only the specific section you need (~400 tokens)
./run_tool.sh section "https://docs.rs/axum/latest/axum" "Routing"
```

### 4. Fast Neural Search (Multi-Density)
Searches multiple pages, extracts named entities, and ranks key passages:
```bash
# Compact density (default): Token-optimized digest (~800 tokens max, <=3,500 chars)
./run_tool.sh fast "Next.js 15 Server Actions" --density compact --num 3

# Balanced density: Moderate depth (~1,900 tokens max, <=8,000 chars)
./run_tool.sh fast "rust tokio vs async-std" --density balanced --num 3

# Raw density: Verbose full extracts (~5,500 tokens max, legacy output)
./run_tool.sh fast "AI time tracking" --density raw --perspective dual --num 2
```

### 5. Deep Research Mode
Synthesizes comprehensive research across pages using local model reasoning:
```bash
./run_tool.sh deep "AI productivity tools" --perspective dual --num 2
./run_tool.sh deep "rust web frameworks" --intent "Compare performance and developer experience"
```

---

### 6. Batch Search
Run multiple queries in a safe, sequential batch:
```bash
./run_tool.sh batch "rust async runtime comparison" "tokio vs smol benchmarks" --density compact --num 2
```

---

### 7. Persistent Research State Ledger
Checkpoint findings and preserve facts across session flushes:
```bash
# Record an established fact into the SQLite ledger
./run_tool.sh record-fact "architecture" "Axum uses Tower services under the hood" \
  --tier 1 --source "https://docs.rs/axum" --title "Axum Docs" --pass 1 --tag "rust" --tag "web"

# View the full structured Markdown briefing
./run_tool.sh ledger

# Filter briefing by category or tier
./run_tool.sh ledger --category "architecture"
./run_tool.sh ledger --tier 1

# Clear ledger entries
./run_tool.sh ledger --category "architecture" --clear --confirm
./run_tool.sh ledger --clear --confirm
```

---

## MCP Server Setup

The tool includes a built-in MCP server (`mcp_server.py`) that lets AI coding assistants search and browse the web directly.

**Shared daemon**: `mcp_server.py` is a thin stdio shim. On first tool call, it auto-spawns `daemon.py` (a background process holding the loaded models and the shared browser) and forwards all requests to it over a Unix socket at `~/.cache/web_search_tool/daemon.sock`. This matters if you run multiple MCP client apps at once (Claude Code, Claude Desktop, Antigravity, a local LLM's MCP client, ...) -- each one launches its own `mcp_server.py` process, but they all share the ONE daemon, so models and the browser are only ever loaded once regardless of how many client apps are open. The daemon auto-exits after 30 minutes of inactivity. Logs: `~/.cache/web_search_tool/daemon.log`.

### Configuration for Claude Code / Claude Desktop
Add this to your `~/.claude.json` or desktop configuration:

```json
{
  "mcpServers": {
    "web-search": {
      "command": "/home/prtyksh/tmp/web_search_tool/.venv/bin/python",
      "args": ["/home/prtyksh/tmp/web_search_tool/mcp_server.py"]
    }
  }
}
```

### Configuration for Google Antigravity
Add this to your `~/.gemini/antigravity/mcp_config.json` or project settings:

```json
{
  "mcpServers": {
    "web-search": {
      "command": "/home/prtyksh/tmp/web_search_tool/.venv/bin/python",
      "args": ["/home/prtyksh/tmp/web_search_tool/mcp_server.py"]
    }
  }
}
```

### Available MCP Tools

| Tool Name | Purpose | Parameters |
|---|---|---|
| `search` | Quick web search with summaries | `query` (str), `max_results` (int), `perspective` ('tech'/'market'/'dual') |
| `fetch` | Fetch page content as Markdown (strictly capped at 6,000 chars) or stage out-of-band via pointer protocol | `url` (str), `force_browser` (bool), `skip_tls_verify` (bool), `as_pointer` (bool) |
| `inspect_vault_doc` | Inspect specific section or query within a staged document (~350 tokens) | `doc_id` (str), `query` (str), `section` (str), `max_chars` (int) |
| `get_toc` | View document table of contents outline (~150 tokens) | `url` (str) |
| `read_section` | Read a specific section of a document (~400 tokens) | `url` (str), `section_name_or_index` (str) |
| `fast_neural_search` | Multi-source neural search with cross-encoder ranking and entity extraction | `query` (str), `instruction` (str), `num_pages` (int), `perspective`, `density` ('compact'/'balanced'/'raw') |
| `batch_search` | Safe sequential multi-query batch search | `queries` (list[str]), `max_results_per_query` (int), `perspective`, `density` ('compact'/'balanced'/'raw') |
| `deep_reasoning_search` | In-depth synthesized research using local quantized SLM | `query` (str), `reasoning_intent` (str), `num_pages` (int), `perspective` |
| `record_research_fact` | Checkpoint established fact into persistent SQLite ledger | `category` (str), `tier` (int), `fact_text` (str), `source_url` (str), `source_title` (str), `pass_num` (int), `tags` (list[str]) |
| `get_research_ledger` | Retrieve concise, token-dense briefing (<250 tokens) across categories and tiers | `category` (str, optional), `tier` (int, optional) |
| `clear_research_ledger` | Clear ledger entries (by category or all) | `category` (str, optional), `confirm` (bool) |

### Best Practices for AI Agents

- **Default to `density="compact"` for Research Loops**:
  `fast_neural_search` defaults to `compact` mode, which delivers token-optimized structural passages capped at 3,500 characters (~800 tokens max), achieving a **>75% token reduction** over raw multi-page dumps.
- **Use the Pointer Protocol for Large Documentation**:
  When inspecting large manuals or specs (e.g. library docs > 20 pages), call `fetch(url, as_pointer=True)`. The full text is staged out-of-band in the local SQLite vault, returning a ~120-token manifest card with section headings. Then call `inspect_vault_doc(doc_id, section="...")` to pull only the exact paragraphs required.
- **Multi-Pass Research Workflow (10 Categories × 5 Tiers × 5 Passes)**:
  1. *Search & Analyze*: Execute queries sequentially using `fast_neural_search` (`density="compact"`).
  2. *Checkpoint State*: As conclusions or findings are verified, record them immediately via `record_research_fact`.
  3. *Context Flush*: When conversation context approaches limits, summarize progress and flush conversational history.
  4. *Resume Without Token Bloat*: At the start of the next turn, call `get_research_ledger()`. It re-injects all accumulated knowledge in under 250 tokens, preventing provider quota exhaustion and context explosion.
- **Hardware Semaphores & Concurrency**:
  The shared background daemon uses thread semaphores to serialize model inference (GLiNER, Cross-Encoder, Gemma SLM) while permitting concurrent non-ML fetches and SQLite reads. SQLite operates in WAL mode with `PRAGMA busy_timeout = 30000;` to guarantee zero lock collisions across multiple active MCP clients.
- **Daemon Lifecycle**:
  The background daemon automatically starts on the first MCP call and automatically shuts down after 30 minutes of inactivity to release RAM/VRAM. Logs are stored in `~/.cache/web_search_tool/daemon.log`.

---

## License

MIT License.
