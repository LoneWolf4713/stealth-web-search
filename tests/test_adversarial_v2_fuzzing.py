#!/usr/bin/env python3
"""
Adversarial Red-Team Regression Suite (Plan V2 Hardened).
Asserts that all 13 fuzzing vulnerabilities discovered in Pass 4 are cleanly resolved:
- CommonMark fence tracking (no extra backticks)
- Oversized code block fence re-wrapping
- Heading hijacking prevention in extract_section
- Subheadings followed by body in find_query_passages
- Empty heading_title guard in _clean_section_content
- NaN / Inf TTL protection in stage_document
- Non-string category guard in get_ledger_entries
- Explicit error on missing section in extract_staged_slice
- Trailing slash normalization in _hash_url
- Single-pass code restoration (no cascading slot injection)
- OverflowError caught in _safe_int
- Search error formatting (no [None](None) links)
"""
import os
import sys
import unittest
import math
from unittest.mock import patch

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

from chunker import (
    safe_truncate_markdown,
    chunk_markdown_smart,
    extract_section,
    find_query_passages,
    _clean_section_content,
    _split_oversized_text,
)
from vault import (
    stage_document,
    get_staged_document,
    clean_expired_staged_docs,
    extract_staged_slice,
    get_ledger_entries,
    _hash_url,
)
from daemon import (
    _safe_int,
    _handle_search,
)


class TestAdversarialHardenedV2(unittest.TestCase):
    """Verifies that all 13 adversarial fuzzing vulnerabilities are resolved."""

    def test_01_nested_fences_with_fewer_backticks_not_corrupted(self):
        """Confirms safe_truncate_markdown respects CommonMark non-nesting and adds zero extraneous fences."""
        md_nested = '````python\nprint("""\n```\n""")\n````'
        res = safe_truncate_markdown(md_nested, 1000)
        self.assertFalse(
            res.endswith("````\n````\n"),
            f"Vulnerability still present: extra fence appended: {repr(res)}"
        )
        self.assertEqual(res.rstrip(), md_nested.rstrip())

    def test_02_oversized_code_block_wrapped_cleanly(self):
        """Confirms chunk_markdown_smart wraps every sub-chunk in code fences so raw code never leaks."""
        code_block = "```python\n" + ("x = 1;\n" * 500) + "```"
        chunks = chunk_markdown_smart(code_block)
        self.assertGreater(len(chunks), 1)
        for idx, chunk in enumerate(chunks):
            content = chunk["content"].strip()
            self.assertTrue(
                content.startswith("```"),
                f"Chunk {idx} does not start with code fence: {content[:40]}"
            )
            self.assertTrue(
                content.endswith("```"),
                f"Chunk {idx} does not end with code fence: {content[-40:]}"
            )

    def test_03_extract_section_heading_hijacking_prevented(self):
        """Confirms exact and prefix matching prevents short headings from hijacking queries."""
        md = "# A\nFirst short heading.\n\n# Advanced System Architecture\nThe real target."
        extracted = extract_section(md, "Advanced System Architecture")
        self.assertTrue(
            extracted.startswith("# Advanced System Architecture"),
            f"Expected '# Advanced System Architecture', got: {extracted[:50]}"
        )

    def test_04_find_query_passages_subheading_attaches_body(self):
        """Confirms query matching heading followed by subheading returns the section body."""
        md = "# Authentication Protocol\n\n## Endpoints\nPOST /api/login authenticates users."
        passages = find_query_passages(md, "Authentication")
        self.assertIn("POST /api/login authenticates users", passages)
        self.assertIn("Authentication Protocol", passages)

    def test_05_clean_section_content_preserves_heading_on_empty_title(self):
        """Confirms empty heading_title does not strip valid first-line headings."""
        content = "# Legitimate Heading\nSome content"
        cleaned = _clean_section_content(content, "")
        self.assertTrue(
            cleaned.startswith("# Legitimate Heading"),
            f"Valid heading was incorrectly stripped: {cleaned}"
        )

    def test_06_stage_document_nan_ttl_handled_safely(self):
        """Confirms 'nan' ttl_days defaults to 7.0 days instead of crashing SQLite."""
        res = stage_document("https://example.com/nan", "Test", "content", ttl_days="nan")
        self.assertIsNotNone(res["doc_id"])
        doc = get_staged_document(res["doc_id"])
        self.assertIsNotNone(doc)
        self.assertFalse(math.isnan(doc["expires_at"]))

    def test_07_stage_document_inf_ttl_capped_safely(self):
        """Confirms 'inf' ttl_days is capped at 365 days instead of creating permanent ghost rows."""
        res = stage_document("https://example.com/inf", "Inf Doc", "content", ttl_days="inf")
        doc_id = res["doc_id"]
        doc = get_staged_document(doc_id)
        self.assertIsNotNone(doc)
        self.assertFalse(math.isinf(doc["expires_at"]))
        # Verify finite expires_at
        self.assertLess(doc["expires_at"], doc["created_at"] + (366 * 86400))

    def test_08_get_ledger_entries_int_category_handled(self):
        """Confirms non-string category does not crash get_ledger_entries with AttributeError."""
        try:
            entries = get_ledger_entries(category=123)
            self.assertIsInstance(entries, list)
        except AttributeError as e:
            self.fail(f"AttributeError raised on int category: {e}")

    def test_09_extract_staged_slice_explicit_missing_section_error(self):
        """Confirms extract_staged_slice explicitly reports missing section instead of silent fallback."""
        staged = stage_document("https://example.com/sec", "Doc", "# Chapter 1\nInfo 1\n\n# Chapter 2\nInfo 2")
        res = extract_staged_slice(staged["doc_id"], section="NonExistentChapter", query="Info")
        self.assertIn("not found", res.lower())
        self.assertIn("NonExistentChapter", res)

    def test_10_hash_url_trailing_slash_normalized(self):
        """Confirms _hash_url normalizes trailing slashes on subpaths, preventing cache divergence."""
        h1 = _hash_url("https://example.com/docs/api/")
        h2 = _hash_url("https://example.com/docs/api")
        self.assertEqual(h1, h2, "Trailing slash on subpaths must produce identical hashes!")

    def test_11_code_block_placeholder_cascading_injection_prevented(self):
        """Confirms single-pass code restoration prevents cascading placeholder injection."""
        md = 'Here is block 1:\n```python\nprint("__CODE_BLOCK_SLOT_1__")\n```\n\nHere is block 2:\n```python\nprint("INJECTED")\n```\n'
        chunks = _split_oversized_text(md, "Title", 5, 1)
        self.assertGreaterEqual(len(chunks), 2, "Expected at least 2 chunks")
        c0 = chunks[0]["content"]
        self.assertNotIn('print("INJECTED")', c0)
        self.assertIn('print("__CODE_BLOCK_SLOT_1__")', c0)

    def test_12_safe_int_handles_infinity_gracefully(self):
        """Confirms _safe_int catches OverflowError on float('inf') and returns default."""
        val = _safe_int(float("inf"), 5)
        self.assertEqual(val, 5)

        val_nan = _safe_int(float("nan"), 10)
        self.assertEqual(val_nan, 10)

    def test_13_daemon_search_error_formatted_cleanly(self):
        """Confirms _handle_search outputs explicit error message instead of [None](None) links."""
        with patch("daemon.search_web", return_value=[{"error": "Rate limit exceeded"}]):
            res = _handle_search({"query": "test", "perspective": "dual"})
            self.assertNotIn("[None](None)", res)
            self.assertIn("Search Error: Rate limit exceeded", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
