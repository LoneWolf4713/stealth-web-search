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
  - **Fast Mode**: Rapidly fetches pages, extracts key entities, and reranks relevant passages (< 1s).
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

Download the lightweight Gemma 2 (2B-IT) model:
```bash
mkdir -p ~/.cache/web_search_tool/models
aria2c -x 4 -s 4 -d ~/.cache/web_search_tool/models \
  "https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf"
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

### 2. Fetch Webpage as Markdown
```bash
./run_tool.sh fetch "https://docs.python.org/3/library/asyncio.html"
```

### 3. Progressive Documentation Reading
```bash
# 1. Inspect Table of Contents outline (~150 tokens)
./run_tool.sh toc "https://docs.rs/axum/latest/axum"

# 2. Read only the specific section you need (~400 tokens)
./run_tool.sh section "https://docs.rs/axum/latest/axum" "Routing"
```

### 4. Fast Neural Search
Searches multiple pages, extracts named entities, and ranks key passages:
```bash
./run_tool.sh fast "Next.js 15 Server Actions" --num 3
./run_tool.sh fast "AI time tracking" --perspective dual --num 2
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
./run_tool.sh batch "rust async runtime comparison" "tokio vs smol benchmarks" --num 2
```

---

## MCP Server Setup

The tool includes a built-in MCP server (`mcp_server.py`) that lets AI coding assistants search and browse the web directly.

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

### Available MCP Tools

| Tool Name | Purpose | Parameters |
|---|---|---|
| `search` | Quick web search with summaries | `query` (str), `max_results` (int), `perspective` ('tech'/'market'/'dual') |
| `fetch` | Fetch page content as Markdown (capped at 20k chars) | `url` (str), `force_browser` (bool) |
| `get_toc` | View document table of contents (~150 tokens) | `url` (str) |
| `read_section` | Read a specific section of a document (~400 tokens) | `url` (str), `section_name_or_index` (str) |
| `fast_neural_search` | Multi-source search with passage ranking | `query` (str), `instruction` (str), `num_pages` (int), `perspective` |
| `batch_search` | Safe sequential multi-query batch search | `queries` (list[str]), `max_results_per_query` (int), `perspective` |
| `deep_reasoning_search` | In-depth synthesized research | `query` (str), `reasoning_intent` (str), `num_pages` (int), `perspective` |

### Best Practices for AI Agents
- **Token Safety & Bounded Output**: All responses report estimated token and character counts in the header. Responses are strictly capped to ~24,000 characters (~5k tokens) to prevent context overflow.
- **Clean Source Isolation**: Inaccessible or blocked pages (such as login walls or invalid certs) are cleanly separated into a footer section so failed fetches never masquerade as content.
- **Batch Searches**: When investigating multiple related topics, use `batch_search` instead of firing parallel tool calls in a single turn. Hardware semaphores serialize model runs to protect host memory.

---

## License

MIT License.
