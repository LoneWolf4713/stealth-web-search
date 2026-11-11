#!/usr/bin/env python3
"""
Adversarial Red-Team Fuzzing and Verification Test Suite.
Exposes edge cases, crash points, markdown corruption, type mismatches, and data loss vulnerabilities.
"""
import os
import sys
import unittest
import time
from typing import Dict, Any, List

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

from chunker import (
    safe_truncate_markdown,
    extract_section,
    find_query_passages,
    extract_headings,
    _split_oversized_text,
    _clean_heading_title
)
from vault import (
    get_connection,
    stage_document,
    get_staged_document,
    clean_expired_staged_docs,
    extract_staged_slice,
    record_fact,
    get_ledger_entries,
    get_ledger_markdown,
    clear_ledger,
    _hash,
    _normalize_query
)
from daemon import (
    _safe_int,
    _handle_search,
    _handle_batch_search,
    _handle_record_research_fact,
    _handle_clear_research_ledger,
    _handle_inspect_vault_doc
)
from searcher import search_web


class TestAdversarialMarkdownAndChunking(unittest.TestCase):
    """Adversarial tests for text processing, markdown integrity, and chunking."""

    def test_oversized_single_paragraph_swallows_text(self):
        """Demonstrates that _split_oversized_text drops 50%+ of text when a paragraph exceeds 2200 chars."""
        # 1000 words (~5000 chars) in a single paragraph
        text = "Word " * 1000
        chunks = _split_oversized_text(text, "Test", max_chunk_words=350, min_chunk_words=25)
        total_words_preserved = sum(c["word_count"] for c in chunks)
        # Bug: Text was swallowed because it only splits on \n\n, then truncates to 2200 chars
        self.assertLess(total_words_preserved, 500, "CONFIRMED BUG: More than 500 words were dropped!")

    def test_nested_four_backtick_code_fence_not_closed(self):
        """Demonstrates that safe_truncate_markdown fails on 4-backtick code blocks."""
        md = "````markdown\n```python\nprint(1)\n```\n````"
        # Truncate inside python code block
        res = safe_truncate_markdown(md, 25)
        # ^``` matches both ```` and ```, making fence count 2 (even), leaving blocks open!
        self.assertEqual(res, "````markdown\n```python")
        self.assertFalse(res.endswith("\n```"), "CONFIRMED BUG: 4-backtick fence left unclosed!")

    def test_indented_code_fence_not_closed(self):
        """Demonstrates that safe_truncate_markdown ignores indented code blocks."""
        md = "Some text:\n    ```python\n    x = 1\n    y = 2\n    ```"
        res = safe_truncate_markdown(md, 35)
        self.assertNotIn("```\n", res[-6:], "CONFIRMED BUG: Indented fence was not closed!")

    def test_blockquoted_code_fence_not_closed(self):
        """Demonstrates that safe_truncate_markdown ignores blockquoted code blocks."""
        md = "> ```python\n> def foo():\n>     return True\n> ```"
        res = safe_truncate_markdown(md, 30)
        self.assertFalse(res.rstrip().endswith("```"), "CONFIRMED BUG: Blockquoted fence was not closed!")

    def test_short_text_with_unclosed_fence_untouched(self):
        """Demonstrates that short text with open fences is never checked or closed."""
        broken = "```python\ndef foo(): pass"
        res = safe_truncate_markdown(broken, 1500)
        self.assertEqual(res.count("```"), 1, "CONFIRMED BUG: Short unclosed fence left broken!")

    def test_mid_row_table_truncation(self):
        """Demonstrates that markdown tables are sliced mid-row despite docstring claims."""
        table = "| Col 1 | Col 2 | Col 3 |\n|---|---|---|\n| Very long value that continues here | Val 2 | Val 3 |"
        res = safe_truncate_markdown(table, 55)
        # Sliced mid-word inside a table row
        self.assertTrue(res.endswith("Very long val"), f"CONFIRMED BUG: Table sliced mid-row: {repr(res)}")

    def test_negative_max_chars_slice_bug(self):
        """Demonstrates Python negative slice bug when max_chars <= 0."""
        text = "Hello world"
        suffix = " [Truncated]"
        res = safe_truncate_markdown(text, -5, suffix=suffix)
        # suffix[:-5] produces ' [Trunc' instead of empty or <= 0 chars
        self.assertGreater(len(res), 0, "CONFIRMED BUG: Negative max_chars returned non-empty string!")

    def test_single_letter_headings_dropped(self):
        """Demonstrates that 1-letter headings like # C or # R are completely discarded."""
        md = "# C\n\nC language details.\n\n# R\n\nR language details."
        headings = extract_headings(md)
        self.assertEqual(len(headings), 0, "CONFIRMED BUG: Single-letter headings were discarded!")

    def test_long_heading_title_lookup_fails(self):
        """Demonstrates that exact heading lookups fail when the title > 70 chars."""
        title = "Comprehensive Guide to Configuring High Availability Distributed Systems in Production"
        md = f"# {title}\n\nContent here."
        res = extract_section(md, title)
        self.assertTrue(res.startswith(f"Section '{title}' not found"), "CONFIRMED BUG: Long heading lookup failed!")


class TestAdversarialSearchAndExtraction(unittest.TestCase):
    """Adversarial tests for keyword search and slice extraction."""

    def test_symbol_erasure_in_multiword_query(self):
        """Demonstrates that symbols and single letters like C++ are erased from multiword queries."""
        md = (
            "# Section 1\n\n"
            "C++ is a compiled language with zero cost abstractions and manual memory management.\n\n"
            "# Section 2\n\n"
            "Python benchmarks show lower runtime throughput for CPU bound numerical tasks."
        )
        res = find_query_passages(md, "C++ benchmarks")
        # Bug: C++ is erased, so Section 2 (Python) is matched instead of Section 1!
        self.assertIn("Python benchmarks", res)
        self.assertNotIn("zero cost abstractions", res, "CONFIRMED BUG: C++ was erased and Python matched!")

    def test_heading_only_match_swallows_body(self):
        """Demonstrates that a query matching only a heading returns 0 body text."""
        md = (
            "# Authentication Architecture\n\n"
            "All user requests require a signed JWT bearer token in the Authorization header.\n"
            "Tokens expire after 15 minutes."
        )
        res = find_query_passages(md, "Authentication Architecture")
        self.assertEqual(res, "# Authentication Architecture", "CONFIRMED BUG: Heading returned with zero body text!")


class TestAdversarialPersistenceAndCache(unittest.TestCase):
    """Adversarial tests for vault.py, caching, and database integrity."""

    def test_url_hash_collision_due_to_lowercasing(self):
        """Demonstrates that case-sensitive URLs collide and corrupt page cache."""
        url1 = "https://example.com/watch?v=AbC123XyZ"
        url2 = "https://example.com/watch?v=abc123xyz"
        h1 = _hash(url1)
        h2 = _hash(url2)
        self.assertEqual(h1, h2, "CONFIRMED BUG: URLs with different casing collided in hash!")

    def test_stage_document_crashes_on_none_parameters(self):
        """Demonstrates unhandled IntegrityError and TypeError on None inputs in stage_document."""
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            stage_document(url=None, title="T", content="C")
        with self.assertRaises(sqlite3.IntegrityError):
            stage_document(url="U", title=None, content="C")
        with self.assertRaises(TypeError):
            stage_document(url="U", title="T", content=None)
        with self.assertRaises(sqlite3.IntegrityError):
            stage_document(url="U", title="T", content="C", session_id=None)
        with self.assertRaises(TypeError):
            stage_document(url="U", title="T", content="C", ttl_days=None)


class TestAdversarialDaemonAndIPC(unittest.TestCase):
    """Adversarial tests for daemon command handlers and IPC payloads."""

    def test_clear_research_ledger_string_false_causes_data_loss(self):
        """Demonstrates that confirm='false' evaluates to True and deletes ledger data."""
        cat = "adversarial_test_cat"
        record_fact(cat, 1, "critical security finding")
        self.assertGreaterEqual(len(get_ledger_entries(cat)), 1)

        # Client sends confirm="false" (e.g. from JSON or CLI)
        res = _handle_clear_research_ledger({"category": cat, "confirm": "false"})
        self.assertIn("Cleared", res, "CONFIRMED BUG: confirm='false' was treated as True!")
        self.assertEqual(len(get_ledger_entries(cat)), 0, "CONFIRMED CRITICAL DATA LOSS: Data was deleted!")

    def test_search_web_crashes_on_string_max_results_dual(self):
        """Demonstrates unhandled TypeError when max_results is passed as string on dual perspective."""
        with self.assertRaises(TypeError):
            search_web("test query", max_results="4", perspective="dual")

    def test_search_web_crashes_on_none_max_results_dual(self):
        """Demonstrates unhandled TypeError when max_results is None on dual perspective."""
        with self.assertRaises(TypeError):
            search_web("test query", max_results=None, perspective="dual")

    def test_handle_record_research_fact_crashes_on_none_category_or_text(self):
        """Demonstrates unhandled AttributeError: 'NoneType' object has no attribute 'strip'."""
        with self.assertRaises(AttributeError):
            _handle_record_research_fact({"category": None, "fact_text": "hello"})
        with self.assertRaises(AttributeError):
            _handle_record_research_fact({"category": "test", "fact_text": None})


if __name__ == "__main__":
    unittest.main(verbosity=2)
