# Stealth Web Search & Neural Retrieval Suite (2026)

A high-performance, keyless, anti-bot-evading web search and local intelligent retrieval engine designed specifically for AI coding agents (**Claude Code** and **Google Antigravity**).

Eliminates raw 40,000-token web dumps and context-window pollution while guaranteeing **zero loss** of obscure bug fixes, unvoted forum solutions, exact dates, and working code blocks.

---

## Key Features

- **Zero API Quotas & Rate Limits**: Keyless multi-engine search (`ddgs`) aggregating DuckDuckGo, Brave, and Yahoo.
- **Dual-Perspective Engine (`--perspective [tech|market|dual]`)**:
  - `tech` *(default)*: Open-source repositories, developer architecture, source code, and self-hosted solutions.
  - `market`: Commercial SaaS products, startup competitors, pricing tiers, and market alternatives.
  - `dual`: Fires concurrent multi-threaded queries across both angles and outputs segregated, structured intelligence.
- **Hardware-Accelerated Dual Retrieval Tiers**:
  - **Phase 1: Fast Neural Mode (< 1s)**: Joint query-document Cross-Encoder (`ms-marco-MiniLM-L-6-v2`) reranking with zero-shot GLiNER (`gliner_small-v2.1`) entity extraction.
  - **Phase 2: Deep SLM Reasoning (~4-10s)**: Local quantized **Gemma 2 (2B-IT)** running on NVIDIA CUDA GPU (Ampere/RTX 3050+) via `llama-cpp-python` with sliding-window attention (SWA) and negative-constraint prompts.
- **Strict Code-Block Preservation**: Custom markdown chunker that never fragments or splits ```` ``` ```` code blocks, ensuring compiler-ready code.
- **Progressive Documentation Disclosure**:
  - `toc <url>`: Generates a ~150-token Table of Contents outline for massive documentation sites.
  - `section <url> <name_or_index>`: Extracts only targeted sections (~400 tokens) to minimize agent context waste.
- **Two-Tier Anti-Bot Bypass & Cookie Vault**:
  - **Tier 1 (Fast Path)**: `curl_cffi` spoofing modern Chrome TLS (JA3/JA4) and HTTP/2 handshakes (< 150ms).
  - **Tier 2 (Stealth Browser)**: C++ anti-detect headless browser (`Camoufox`) to solve Cloudflare Turnstile.
  - **SQLite Cookie Vault**: Vaults harvested `cf_clearance` cookies (4h TTL) to bypass repeated Turnstile challenges across subdomains.
- **First-Class AI Agent Integration**:
  - **Claude Code**: Registered globally via Model Context Protocol (`mcp_server.py`) and skill.
  - **Google Antigravity**: Integrated as a native skill in `~/.gemini/config/skills/stealth-web-search/SKILL.md`.

---

## Architecture

```
[Agent Query]
      │
      ├──> Perspective Router (tech | market | dual)
      │          │
      │          ├──> DuckDuckGo / Brave / Yahoo (Keyless DDGS)
      │          │          │ (SQLite 12h Cache)
      │          ▼
      ├──> Parallel Stealth Fetcher (ThreadPoolExecutor)
      │          ├──> Tier 1: curl_cffi + Vaulted cf_clearance (< 150ms)
      │          └──> Tier 2: Camoufox C++ Browser (Turnstile Solver)
      │
      ├──> Smart Markdown Parser (Preserves ``` Code Blocks)
      │
      ├──> Retrieval Strategy Selection:
      │          │
      │          ├── [Fast Mode] ──> Cross-Encoder Reranking (< 50ms)
      │          │                     + GLiNER Zero-Shot NER
      │          │
      │          └── [Deep Mode] ──> Gemma 2 (2B-IT) Q4_K_M SLM
      │                                (Local NVIDIA GPU via CUDA 12)
      ▼
[Structured Token-Dense Markdown Digest with Zero Info Loss]
```

---

## Directory Structure

```
.
├── cli.py                  # Typer & Rich CLI (search, fetch, toc, section, fast, deep)
├── searcher.py             # Keyless multi-engine search with perspective routing
├── fetcher.py              # Two-tier stealth fetcher with vaulted cf_clearance
├── chunker.py              # Code-block preserver, markdown chunker & TOC generator
├── vault.py                # SQLite cache and Cloudflare cookie vault (~/.cache/web_search_tool/)
├── neural_fast.py          # GLiNER entity extraction + Cross-Encoder reranker
├── deep_reasoner.py        # Local Gemma 2 2B SLM inference on CUDA
├── parallel_pipeline.py    # Multi-threaded deep research engine
├── mcp_server.py           # Model Context Protocol server (compatible with MCP v1 & v2)
├── run_tool.sh             # Executable launcher with automatic CUDA environment paths
├── requirements.txt        # Python dependency manifest
└── README.md
```

---

## Setup & Installation

### 1. Prerequisites
- Python 3.10+
- NVIDIA GPU with CUDA 12+ (or multi-core CPU fallback)
- `aria2c` for high-speed multi-connection model downloads

### 2. Environment Setup
```bash
git clone https://github.com/LoneWolf4713/stealth-web-search.git
cd stealth-web-search

# Create virtual environment using uv or venv
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 3. Install CUDA-Accelerated `llama-cpp-python` (GPU Mode)
To compile native CUDA kernels for your GPU (e.g., RTX 3050 Ampere `sm_86`) without AVX-512 crashes on modern Intel CPUs:

```bash
export PATH=/usr/local/cuda-12.6/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda-12.6/lib64:$LD_LIBRARY_PATH
export CUDACXX=/usr/local/cuda-12.6/bin/nvcc
export CMAKE_ARGS="-DGGML_CUDA=on -DGGML_AVX512=off -DCMAKE_CUDA_ARCHITECTURES=86"

uv pip install --no-binary llama-cpp-python --reinstall llama-cpp-python
```

### 4. Download Gemma 2 SLM Weights
Download the open quantized Gemma 2 2B GGUF model (~1.7 GB) using `aria2c` at maximum network bandwidth:

```bash
mkdir -p ~/.cache/web_search_tool/models
aria2c -x 4 -s 4 -d ~/.cache/web_search_tool/models \
  "https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf"
```

---

## CLI Usage Guide

### 1. Keyless Search (Cached)
```bash
# Default technical perspective
./run_tool.sh search "axum authentication middleware" --num 5

# Commercial market perspective
./run_tool.sh search "AI screen time trackers" --perspective market --num 4

# Dual perspective (parallel technical repos + commercial products)
./run_tool.sh search "vector database" --perspective dual --num 4
```

### 2. Stealth Page Fetch (Bypassing Cloudflare)
```bash
./run_tool.sh fetch "https://protected-site.com"
```
* Pass `--stealth` to force C++ browser emulation.
* Pass `--no-cache` to bypass the SQLite cache.

### 3. Progressive Documentation Reading
```bash
# View table of contents outline (~150 tokens)
./run_tool.sh toc "https://docs.rs/axum/latest/axum"

# Read only section 4 (~400 tokens)
./run_tool.sh section "https://docs.rs/axum/latest/axum" "4"
```

### 4. Fast Neural Mode (< 1s)
Runs keyless search -> parallel stealth fetch -> code-block preserving chunker -> **Cross-Encoder passage reranking** -> **GLiNER zero-shot entity extraction**:
```bash
./run_tool.sh fast "Next.js 15 Server Actions" --num 3

# With dual perspective
./run_tool.sh fast "AI screen time life tracker" --perspective dual --num 2

# With task instruction for relevance weighting
./run_tool.sh fast "web3 grants 2027" --instruction "Identify hackathons, grants, and deadlines" --num 4
```

### 5. Deep SLM Reasoning Mode (~4-10s on GPU)
Uses local **Gemma 2 (2B-IT)** in VRAM on the GPU to extract exact entities, dates, prizes, unvoted forum solutions, and technical implementations without hallucination:
```bash
./run_tool.sh deep "AI screen time life tracker" --perspective dual --num 2

# With specific reasoning intent
./run_tool.sh deep "opportunities for AI engineers 2027" \
  --intent "Extract hackathons, fellowships, grants, and deadlines" \
  --perspective dual --num 2
```

---

## MCP Server Integration

The suite includes an MCP server (`mcp_server.py`) exposing native tools to Claude Code and Google Antigravity.

### Registered Tools
| Tool | Description | Key Parameters |
|---|---|---|
| `search` | Keyless search with SQLite cache | `query`, `max_results`, `perspective` (`tech`/`market`/`dual`) |
| `fetch` | Stealth fetcher with Turnstile cookie vault | `url`, `force_stealth` |
| `get_toc` | Outline table of contents (~150 tokens) | `url` |
| `read_section` | Targeted section reader (~400 tokens) | `url`, `section_name_or_index` |
| `fast_neural_search` | Cross-Encoder reranking + GLiNER NER | `query`, `instruction`, `num_pages`, `perspective` |
| `deep_reasoning_search` | Gemma 2 SLM extraction on GPU | `query`, `reasoning_intent`, `num_pages`, `perspective` |

### Adding to Claude Code
Add to `~/.claude.json`:
```json
{
  "mcpServers": {
    "stealth-search": {
      "command": "/home/prtyksh/tmp/web_search_tool/.venv/bin/python",
      "args": ["/home/prtyksh/tmp/web_search_tool/mcp_server.py"]
    }
  }
}
```

---

## License

MIT License. Designed and optimized for local AI agent workflows.
