#!/usr/bin/env python3
"""
Comprehensive Regression Test Suite for Context Token Optimization & Hardening.
Tests all fixes implemented from remediation_plan.md:
1. Safe Markdown Truncation (Code block fences, table integrity, boundary splitting)
2. Heading Extraction & Section Matching (Handling heading-less documents)
3. Query Search Robustness (Symbol/Short queries: C++, C#, $100, and 0-match handling)
4. SQLite Connection Management (Guaranteed closure via context manager)
5. Out-of-Band Staging & Pre-parsed Headings
6. Targeted Slice Extraction & Explicit Error Signaling
7. Expired Document Purging (TTL / Cleanup)
8. Daemon Safe Type Coercion & Handler Resilience (Handling NoneType & strings)
9. MCP Tool Signature Verification (No mutable default arguments)
10. Multi-Density Output Ceilings (Compact, Balanced, Raw)
"""
import os
import sys
import unittest
import time
import inspect
from typing import Dict, Any, List

# Ensure repository root is on sys.path
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

from chunker import safe_truncate_markdown, extract_section, find_query_passages, extract_headings
from vault import (
    get_connection,
    stage_document,
    get_staged_document,
    clean_expired_staged_docs,
    extract_staged_slice,
    record_fact,
    get_ledger_markdown,
    clear_ledger
)
from daemon import _safe_int, _handle_inspect_vault_doc, _handle_record_research_fact
from parallel_pipeline import format_fast_digest
import mcp_server


class TestSafeMarkdownTruncation(unittest.TestCase):
    """Verifies that markdown is never corrupted during truncation."""

    def test_text_within_limit_untouched(self):
        text = "# Title\n\nThis is a short paragraph."
        self.assertEqual(safe_truncate_markdown(text, 100), text)

    def test_unclosed_code_fence_is_automatically_closed(self):
        markdown_with_code = (
            "Here is the setup:\n\n"
            "```python\n"
            "def calculate_metrics(items):\n"
            "    total = sum(x.value for x in items)\n"
            "    average = total / len(items)\n"
            "    return {'total': total, 'avg': average}\n"
            "```\n\n"
            "Next steps follow."
        )
        # Truncate inside the python code block (around 65 chars)
        truncated = safe_truncate_markdown(markdown_with_code, 75, suffix="\n[Truncated]")
        # Count occurrences of triple backtick fences
        import re
        fences = re.findall(r'(?m)^```', truncated)
        self.assertEqual(len(fences) % 2, 0, f"Code block was left unclosed! Output:\n{truncated}")
        self.assertTrue(truncated.endswith("\n[Truncated]"))

    def test_newline_boundary_slicing(self):
        para1 = "Paragraph 1 with some text.\n\n"
        para2 = "Paragraph 2 with additional details and context."
        full = para1 + para2
        # Max chars set between para1 and para2
        res = safe_truncate_markdown(full, len(para1) + 10, suffix="...")
        self.assertTrue(res.startswith(para1.strip()))


class TestChunkerAndQueryRobustness(unittest.TestCase):
    """Verifies section extraction and symbol query handling."""

    def test_extract_section_no_headings_returns_clean_error(self):
        plain_text = "This is a document with zero markdown headings.\nJust plain paragraphs."
        res = extract_section(plain_text, "Architecture")
        self.assertIn("Section 'Architecture' not found: document contains no structured markdown headings.", res)

    def test_symbol_queries_preserved(self):
        doc = (
            "# Programming Languages\n\n"
            "We use C++ for high performance compute kernels.\n\n"
            "C# is utilized for the desktop user interface.\n\n"
            "The pricing starts at $100 per license."
        )
        # Search for C++
        res_cpp = find_query_passages(doc, "C++", max_chars=500)
        self.assertIn("C++", res_cpp)

        # Search for $100
        res_money = find_query_passages(doc, "$100", max_chars=500)
        self.assertIn("$100", res_money)

    def test_zero_match_query_returns_empty_string(self):
        doc = "# Systems\n\nLinux and FreeBSD are Unix-like operating systems."
        res = find_query_passages(doc, "nonexistent_cryptocurrency_token_xyz", max_chars=500)
        self.assertEqual(res, "", "0-match query should return empty string instead of dumping document prefix!")


class TestVaultDatabaseAndConcurrency(unittest.TestCase):
    """Verifies SQLite connection context manager and closure."""

    def test_connection_closes_cleanly(self):
        conn_ref = None
        with get_connection() as conn:
            conn_ref = conn
            cur = conn.cursor()
            cur.execute("SELECT 1;")
            self.assertEqual(cur.fetchone()[0], 1)
        
        # Verify that connection is closed after context exit
        import sqlite3
        with self.assertRaises(sqlite3.ProgrammingError):
            conn_ref.cursor()

    def test_connection_closes_on_exception(self):
        conn_ref = None
        try:
            with get_connection() as conn:
                conn_ref = conn
                raise RuntimeError("Simulated failure")
        except RuntimeError:
            pass
        
        import sqlite3
        with self.assertRaises(sqlite3.ProgrammingError):
            conn_ref.cursor()

    def test_staging_with_preparsed_headings(self):
        content = "# Section Alpha\n\nContent for alpha.\n\n# Section Beta\n\nContent for beta."
        headings = extract_headings(content)
        meta = stage_document(
            url="https://example.com/test",
            title="Preparsed Test",
            content=content,
            headings=headings
        )
        self.assertTrue(meta["doc_id"].startswith("doc_"))
        self.assertEqual(len(meta["headings"]), 2)

        # Retrieve and verify
        retrieved = get_staged_document(meta["doc_id"])
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["title"], "Preparsed Test")

    def test_slice_extraction_explicit_query_miss_in_section(self):
        content = "# Architecture\n\nThis section discusses distributed microservices and message queues."
        headings = extract_headings(content)
        meta = stage_document(
            url="https://example.com/arch",
            title="Architecture Doc",
            content=content,
            headings=headings
        )
        doc_id = meta["doc_id"]

        # Request valid section with non-existent query
        res = extract_staged_slice(doc_id, section="Architecture", query="quantum_teleportation")
        self.assertIn("No passages found matching query 'quantum_teleportation' in section 'Architecture'", res)

    def test_staged_docs_cleanup(self):
        """Verifies clean_expired_staged_docs properly purges documents past their TTL."""
        meta = stage_document(
            url="https://example.com/expired",
            title="Expired Doc",
            content="This document is expired.",
            ttl_days=7.0
        )
        doc_id = meta["doc_id"]
        # Explicitly backdate expires_at in SQLite to simulate post-TTL state (since DEF-08 guards against negative TTL)
        with get_connection() as conn:
            conn.execute("UPDATE staged_documents SET expires_at = ? WHERE doc_id = ?", (time.time() - 86400.0, doc_id))
            conn.commit()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM staged_documents WHERE doc_id = ?", (doc_id,))
            self.assertEqual(cur.fetchone()[0], 1)

        purged = clean_expired_staged_docs()
        self.assertGreaterEqual(purged, 1)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM staged_documents WHERE doc_id = ?", (doc_id,))
            self.assertEqual(cur.fetchone()[0], 0)


class TestDaemonAndMCPHardening(unittest.TestCase):
    """Verifies type resilience and tool signatures."""

    def test_safe_int_helper(self):
        self.assertEqual(_safe_int(None, 1), 1)
        self.assertEqual(_safe_int("42", 1), 42)
        self.assertEqual(_safe_int("invalid", 5), 5)
        self.assertEqual(_safe_int(10, 1), 10)

    def test_handle_record_research_fact_resilience(self):
        # Pass None for tier and pass_num (which previously caused TypeError: int(None))
        payload = {
            "category": "testing",
            "tier": None,
            "pass_num": None,
            "fact_text": "Resilience test passed without crash.",
            "source_url": "https://example.com",
            "source_title": "Test Source",
            "tags": ["unit-test"]
        }
        res = _handle_record_research_fact(payload)
        self.assertIn("Recorded fact #", res)
        self.assertIn("Tier 1, Pass 1", res)

    def test_handle_inspect_vault_doc_string_max_chars(self):
        # Staged document
        meta = stage_document("https://example.com/types", "Type Coercion Doc", "# Header\n\nSome body text.")
        payload = {
            "doc_id": meta["doc_id"],
            "max_chars": "500",  # String instead of int
            "query": "",
            "section": ""
        }
        res = _handle_inspect_vault_doc(payload)
        self.assertNotIn("Error", res)
        self.assertIn("Some body text", res)

    def test_mcp_tool_mutable_default_eliminated(self):
        # Inspect mcp_server.record_research_fact signature
        sig = inspect.signature(mcp_server.record_research_fact)
        tags_param = sig.parameters.get("tags")
        self.assertIsNotNone(tags_param)
        self.assertIsNone(tags_param.default, "mcp_server.record_research_fact must NOT use mutable default list []!")

    def test_research_ledger_lifecycle(self):
        """Verifies recording facts, rendering markdown, filtering, and clearing with confirmation."""
        # Clean test category first
        clear_ledger(category="test_lifecycle", confirm=True)

        fact_id = record_fact(
            category="test_lifecycle",
            tier=1,
            fact_text="SQLite WAL mode guarantees non-blocking concurrent reads.",
            source_url="https://sqlite.org/wal.html",
            source_title="SQLite WAL Docs",
            pass_num=1,
            tags=["database", "concurrency"]
        )
        self.assertIsInstance(fact_id, int)

        # Markdown representation
        briefing = get_ledger_markdown(category="test_lifecycle", tier=1)
        self.assertIn("# test_lifecycle", briefing)
        self.assertIn("## Tier 1", briefing)
        self.assertIn("SQLite WAL mode", briefing)
        self.assertIn("database, concurrency", briefing)

        # Clearing without confirm must abort
        abort_res = clear_ledger(category="test_lifecycle", confirm=False)
        self.assertIn("Action aborted", abort_res)

        # Clearing with confirm must succeed
        clean_res = clear_ledger(category="test_lifecycle", confirm=True)
        self.assertIn("Cleared 1 facts", clean_res)

        # Verify empty
        empty_briefing = get_ledger_markdown(category="test_lifecycle", tier=1)
        self.assertIn("No research facts recorded in ledger", empty_briefing)


class TestMultiDensityCeilings(unittest.TestCase):
    """Verifies output format character and token ceilings."""

    def test_compact_density_ceiling(self):
        dummy_data = {
            "query": "asyncio python",
            "perspective": "tech",
            "results": [
                {
                    "title": f"Doc {i}",
                    "url": f"https://docs.python.org/{i}",
                    "snippet": "Overview snippet of the asyncio event loop.",
                    "status": "success",
                    "entities": [{"entity": "asyncio", "label": "library"}],
                    "top_passages": [
                        {"heading": f"Section {j}", "content": "Passage content " * 40, "match_pct": "95%", "relevance_score": 5.0}
                        for j in range(3)
                    ]
                }
                for i in range(4)
            ],
            "elapsed_seconds": 1.2,
            "device": "cpu"
        }
        digest = format_fast_digest(dummy_data, density="compact")
        self.assertLessEqual(len(digest), 3800, f"Compact digest exceeded ceiling: {len(digest)} chars")
        self.assertIn("Density: COMPACT", digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
