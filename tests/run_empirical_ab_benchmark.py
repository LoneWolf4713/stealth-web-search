#!/usr/bin/env python3
"""
Empirical A/B Benchmarking & Information-Fidelity Auditor
Compares baseline 'main' (commit e648dc8) against 'feat/context-token-optimization'.
Measures:
- Exact cl100k_base tokens (tiktoken)
- Character length & line count
- Wall-clock CPU latency
- Markdown syntax integrity (code block fence balance, table formatting)
- Information fidelity & recovery capabilities via Pointer Protocol
"""
import os
import sys
import time
import json
import signal
import subprocess
import tiktoken
import re
from typing import Dict, Any, List

ENC = tiktoken.get_encoding("cl100k_base")

BASELINE_DIR = "/home/prtyksh/tmp/web_search_tool_baseline"
FEATURE_DIR = "/home/prtyksh/tmp/web_search_tool"
RESULTS_DIR = "/home/prtyksh/tmp/benchmark_results"
RUNTIME_DIR = os.path.expanduser("~/.cache/web_search_tool")
SOCKET_PATH = os.path.join(RUNTIME_DIR, "daemon.sock")
LOCK_PATH = os.path.join(RUNTIME_DIR, "daemon.lock")

def stop_any_daemon():
    """Ensure all background daemons are terminated and socket cleaned up."""
    # Kill any existing daemon processes
    try:
        out = subprocess.check_output(["pgrep", "-f", "daemon.py"]).decode().strip()
        for pid_str in out.splitlines():
            pid = int(pid_str.strip())
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        time.sleep(1.0)
        # Force kill if still lingering
        out2 = subprocess.check_output(["pgrep", "-f", "daemon.py"]).decode().strip()
        for pid_str in out2.splitlines():
            pid = int(pid_str.strip())
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
    except subprocess.CalledProcessError:
        pass

    # Clean up socket and lock
    for p in [SOCKET_PATH, LOCK_PATH]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    time.sleep(0.5)

def analyze_markdown(text: str) -> Dict[str, Any]:
    """Analyze markdown structural integrity."""
    tokens = len(ENC.encode(text))
    chars = len(text)
    lines = len(text.splitlines())

    # Count code fences
    fence_matches = re.findall(r'^(`{3,}|~{3,})', text, re.MULTILINE)
    code_blocks = re.findall(r'(`{3,})[^\n]*\n[\s\S]*?\n\1', text)
    
    # Check unclosed fences using CommonMark rule
    unclosed = 0
    active_len = 0
    active_char = None
    for line in text.split("\n"):
        stripped = line.strip()
        if active_len > 0:
            m = re.match(r'^(`{3,}|~{3,})\s*$', stripped)
            if m and m.group(1)[0] == active_char and len(m.group(1)) >= active_len:
                active_len = 0
                active_char = None
        else:
            m = re.match(r'^(`{3,}|~{3,})', stripped)
            if m:
                active_char = m.group(1)[0]
                active_len = len(m.group(1))

    unclosed = 1 if active_len > 0 else 0

    # Count table rows
    table_rows = [l for l in text.splitlines() if l.strip().startswith("|") and l.strip().endswith("|")]

    return {
        "tokens": tokens,
        "chars": chars,
        "lines": lines,
        "code_blocks_count": len(code_blocks),
        "total_fence_tags": len(fence_matches),
        "unclosed_fences": unclosed,
        "table_rows": len(table_rows),
    }

def run_command_timed(cmd: List[str], cwd: str, timeout: int = 120) -> Dict[str, Any]:
    """Execute a CLI command, record elapsed time and capture output."""
    t0 = time.perf_counter()
    p = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout
    )
    elapsed = round(time.perf_counter() - t0, 3)
    stdout = p.stdout
    stderr = p.stderr

    metrics = analyze_markdown(stdout)
    metrics["latency_sec"] = elapsed
    metrics["exit_code"] = p.returncode
    metrics["stderr_sample"] = stderr[:300] if stderr else ""
    return metrics, stdout

def main():
    os.makedirs(f"{RESULTS_DIR}/baseline_main", exist_ok=True)
    os.makedirs(f"{RESULTS_DIR}/optimized_feat", exist_ok=True)

    results = {
        "baseline_main": {},
        "optimized_feat": {},
        "comparisons": {}
    }

    # ==========================================
    # PHASE 1: BASELINE (main - e648dc8)
    # ==========================================
    print("\n" + "="*60)
    print(">>> STAGE 1: BENCHMARKING BASELINE (main: e648dc8)")
    print("="*60)
    stop_any_daemon()

    baseline_py = f"{BASELINE_DIR}/run_tool.sh"

    # Query 1: Technical Code / API
    q1 = "Rust axum routing middleware handlers 2024"
    print(f"\n[Baseline] Running Query 1: {q1} ...")
    m1, out1 = run_command_timed([baseline_py, "fast", q1, "-n", "4"], cwd=BASELINE_DIR)
    results["baseline_main"]["q1_tech_fast"] = m1
    with open(f"{RESULTS_DIR}/baseline_main/q1_tech_fast.md", "w") as f:
        f.write(out1)
    print(f" -> Output: {m1['chars']} chars | {m1['tokens']} tokens | {m1['latency_sec']}s | Code Blocks: {m1['code_blocks_count']}")

    # Query 2: Comparative Tabular
    q2 = "vector database performance benchmarks qdrant milvus pgvector"
    print(f"\n[Baseline] Running Query 2: {q2} ...")
    m2, out2 = run_command_timed([baseline_py, "fast", q2, "-n", "4"], cwd=BASELINE_DIR)
    results["baseline_main"]["q2_bench_fast"] = m2
    with open(f"{RESULTS_DIR}/baseline_main/q2_bench_fast.md", "w") as f:
        f.write(out2)
    print(f" -> Output: {m2['chars']} chars | {m2['tokens']} tokens | {m2['latency_sec']}s | Table Rows: {m2['table_rows']}")

    # Query 3: Deep Doc Scraping
    url3 = "https://docs.python.org/3/library/asyncio.html"
    print(f"\n[Baseline] Running Query 3: fetch {url3} ...")
    m3, out3 = run_command_timed([baseline_py, "fetch", url3], cwd=BASELINE_DIR)
    results["baseline_main"]["q3_fetch_asyncio"] = m3
    with open(f"{RESULTS_DIR}/baseline_main/q3_fetch_asyncio.md", "w") as f:
        f.write(out3)
    print(f" -> Output: {m3['chars']} chars | {m3['tokens']} tokens | {m3['latency_sec']}s")

    # Stop baseline daemon
    print("\nStopping baseline daemon...")
    stop_any_daemon()

    # ==========================================
    # PHASE 2: OPTIMIZED (feat/context-token-optimization)
    # ==========================================
    print("\n" + "="*60)
    print(">>> STAGE 2: BENCHMARKING OPTIMIZED (feat/context-token-optimization)")
    print("="*60)

    feat_py = f"{FEATURE_DIR}/run_tool.sh"

    # Query 1 across densities
    for density in ["compact", "balanced", "raw"]:
        print(f"\n[Optimized] Running Query 1 ({density}): {q1} ...")
        m, out = run_command_timed([feat_py, "fast", q1, "-n", "4", "--density", density], cwd=FEATURE_DIR)
        key = f"q1_tech_{density}"
        results["optimized_feat"][key] = m
        with open(f"{RESULTS_DIR}/optimized_feat/{key}.md", "w") as f:
            f.write(out)
        print(f" -> Output: {m['chars']} chars | {m['tokens']} tokens | {m['latency_sec']}s | Code Blocks: {m['code_blocks_count']} | Unclosed: {m['unclosed_fences']}")

    # Query 2 across densities
    for density in ["compact", "balanced", "raw"]:
        print(f"\n[Optimized] Running Query 2 ({density}): {q2} ...")
        m, out = run_command_timed([feat_py, "fast", q2, "-n", "4", "--density", density], cwd=FEATURE_DIR)
        key = f"q2_bench_{density}"
        results["optimized_feat"][key] = m
        with open(f"{RESULTS_DIR}/optimized_feat/{key}.md", "w") as f:
            f.write(out)
        print(f" -> Output: {m['chars']} chars | {m['tokens']} tokens | {m['latency_sec']}s | Table Rows: {m['table_rows']} | Unclosed: {m['unclosed_fences']}")

    # Query 3: Fetch with default ceiling (6,000 chars)
    print(f"\n[Optimized] Running Query 3 (Default Fetch Ceiling): fetch {url3} ...")
    m3_std, out3_std = run_command_timed([feat_py, "fetch", url3], cwd=FEATURE_DIR)
    results["optimized_feat"]["q3_fetch_default_ceiling"] = m3_std
    with open(f"{RESULTS_DIR}/optimized_feat/q3_fetch_default_ceiling.md", "w") as f:
        f.write(out3_std)
    print(f" -> Output: {m3_std['chars']} chars | {m3_std['tokens']} tokens | {m3_std['latency_sec']}s | Unclosed: {m3_std['unclosed_fences']}")

    # Query 3: Fetch as Pointer Manifest
    print(f"\n[Optimized] Running Query 3 (Pointer Protocol): fetch {url3} --pointer ...")
    m3_ptr, out3_ptr = run_command_timed([feat_py, "fetch", url3, "--pointer"], cwd=FEATURE_DIR)
    results["optimized_feat"]["q3_fetch_pointer"] = m3_ptr
    with open(f"{RESULTS_DIR}/optimized_feat/q3_fetch_pointer.md", "w") as f:
        f.write(out3_ptr)
    print(f" -> Output: {m3_ptr['chars']} chars | {m3_ptr['tokens']} tokens | {m3_ptr['latency_sec']}s")

    # Extract doc_id from pointer card
    doc_id_match = re.search(r'doc_[a-f0-9]{12}', out3_ptr)
    doc_id = doc_id_match.group(0) if doc_id_match else None
    print(f" -> Staged Document ID: {doc_id}")

    if doc_id:
        # Test inspect section
        print(f"\n[Optimized] Testing inspect_vault_doc on doc_id={doc_id} section='Runners' ...")
        m_sec, out_sec = run_command_timed([feat_py, "inspect", doc_id, "--section", "Runners", "--query", "asyncio.run"], cwd=FEATURE_DIR)
        results["optimized_feat"]["q3_inspect_section_runners"] = m_sec
        with open(f"{RESULTS_DIR}/optimized_feat/q3_inspect_section_runners.md", "w") as f:
            f.write(out_sec)
        print(f" -> Output: {m_sec['chars']} chars | {m_sec['tokens']} tokens | {m_sec['latency_sec']}s")

        # Test inspect query keywords
        print(f"\n[Optimized] Testing inspect_vault_doc on doc_id={doc_id} query='TaskGroup' ...")
        m_grp, out_grp = run_command_timed([feat_py, "inspect", doc_id, "--query", "TaskGroup"], cwd=FEATURE_DIR)
        results["optimized_feat"]["q3_inspect_query_taskgroup"] = m_grp
        with open(f"{RESULTS_DIR}/optimized_feat/q3_inspect_query_taskgroup.md", "w") as f:
            f.write(out_grp)
        print(f" -> Output: {m_grp['chars']} chars | {m_grp['tokens']} tokens | {m_grp['latency_sec']}s")

    # Clean up feature daemon
    print("\nStopping feature daemon...")
    stop_any_daemon()

    # Save complete benchmark results JSON
    with open(f"{RESULTS_DIR}/benchmark_summary.json", "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "="*60)
    print(">>> BENCHMARK COMPLETE! Summary written to benchmark_summary.json")
    print("="*60)

if __name__ == "__main__":
    main()
