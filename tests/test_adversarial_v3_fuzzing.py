#!/usr/bin/env python3
"""
Adversarial Red-Team Fuzzing and Vulnerability Verification Suite (Pass 5 - Remediated).
Verifies that all 15 latent vulnerabilities identified during Pass 5
have been completely remediated, hardened, and regression-proofed.
"""
import os
import sys
import unittest
from unittest.mock import patch

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

from chunker import (
    chunk_markdown_smart,
    extract_headings,
    _split_oversized_text,
    _clean_section_content,
)
from vault import (
    stage_document,
    get_staged_document,
    record_fact,
    get_ledger_entries,
    get_ledger_markdown,
    get_cached_search,
)
from daemon import (
    _handle_get_toc,
    _handle_read_section,
)
from parallel_pipeline import (
    format_fast_digest,
    format_deep_digest,
)
from fetcher import fetch_page_detailed
from neural_fast import _select_top_chunks


class TestAdversarialV3Remediated(unittest.TestCase):
    """
    Adversarial Red-Team empirical test suite (Remediated).
    Verifies that all 15 discovered vulnerabilities are fixed and hardened.
    """

    # --- Category 1: Markdown Integrity & Chunker Vulnerabilities ---

    def test_01_chunk_markdown_smart_offset_desync_prevented(self):
        """
        CONFIRMS DEF-01: Chunker heading offsets sliced from cleaned_md.
        Ensures stripped JavaScript warning/boilerplate is never re-injected into section chunks.
        """
        md = (
            "This site requires JavaScript to run correctly. Please unblock scripts.\n\n"
            "# Section 1\nFirst section content with enough words to form a section.\n\n"
            "# Section 2\nSecond section content with enough words to form a section.\n"
        )
        chunks = chunk_markdown_smart(md)
        self.assertGreater(len(chunks), 0)
        # Stripped boilerplate must never leak into chunk bodies
        for c in chunks:
            self.assertNotIn("JavaScript", c["content"])
            self.assertNotIn("unblock scripts", c["content"])
        self.assertIn("First section content", chunks[0]["content"])

    def test_02_tilde_code_block_heading_pollution_prevented(self):
        """
        CONFIRMS DEF-02: CODE_BLOCK_PATTERN supports CommonMark tildes (~~~).
        Comments starting with '#' inside ~~~ code blocks are ignored and not extracted as headings.
        """
        md = (
            "# Document Title\n\n"
            "~~~python\n"
            "# Fake Heading In Tildes\n"
            "x = 1\n"
            "~~~\n\n"
            "# Real Section 2\n"
            "Section 2 body text.\n"
        )
        headings = extract_headings(md)
        titles = [h["title"] for h in headings]
        self.assertNotIn("Fake Heading In Tildes", titles)
        self.assertIn("Real Section 2", titles)

    def test_03_oversized_tilde_code_block_preserves_fences(self):
        """
        CONFIRMS DEF-03: Oversized tilde (~~~) code blocks are partitioned with valid ~~~ fences.
        """
        cb = "~~~python\n" + ("x = 1;\n" * 500) + "~~~\n"
        chunks = chunk_markdown_smart("# Section 1\n" + cb)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertTrue(c["content"].startswith("~~~"), f"Chunk must start with ~~~: {c['content'][:20]}")
            self.assertTrue(c["content"].rstrip().endswith("~~~"), f"Chunk must end with ~~~: {c['content'][-20:]}")

    def test_04_static_code_slot_injection_prevented(self):
        """
        CONFIRMS DEF-04: Nonce-based placeholders prevent static slot collision and payload injection.
        """
        user_text = (
            "We explain how tokens work: __CODE_BLOCK_SLOT_0__ is replaced.\n\n"
            "```python\n"
            "print('PAYLOAD_INJECTED')\n"
            "```\n"
        )
        chunks = _split_oversized_text(user_text, "Overview", 100, 10)
        self.assertGreaterEqual(len(chunks), 1)
        # Literal string must be preserved
        self.assertIn("__CODE_BLOCK_SLOT_0__", chunks[0]["content"])
        self.assertEqual(chunks[0]["content"].count("print('PAYLOAD_INJECTED')"), 1)

    def test_05_clean_section_content_preserves_valid_short_heading(self):
        """
        CONFIRMS DEF-14: Length-guarded prefix check preserves valid short headings like '# C'.
        """
        content = "# C\nDetails about C programming."
        cleaned = _clean_section_content(content, "Common Questions")
        self.assertTrue(
            cleaned.startswith("# C"),
            f"Valid heading '# C' must not be deleted under 'Common Questions'! Got: {cleaned}"
        )

    # --- Category 2: Persistence & Vault Concurrency Vulnerabilities ---

    def test_06_get_ledger_markdown_handles_non_string_tags(self):
        """
        CONFIRMS DEF-05: get_ledger_markdown coerces tag elements to strings, preventing TypeError.
        """
        record_fact("v3_tag_fuzz", 1, "Fact with integer tags", tags=[42, 99])
        md = get_ledger_markdown(category="v3_tag_fuzz")
        self.assertIn("[42, 99]", md)

    def test_07_record_fact_handles_overflow_inf(self):
        """
        CONFIRMS DEF-06: record_fact safely handles float('inf') without unhandled OverflowError.
        """
        fact_id = record_fact("v3_inf_fuzz", float("inf"), "Fact with inf tier")
        self.assertIsInstance(fact_id, int)

    def test_08_get_ledger_entries_handles_overflow_inf(self):
        """
        CONFIRMS DEF-07: get_ledger_entries safely handles float('inf') without unhandled OverflowError.
        """
        entries = get_ledger_entries(tier=float("inf"))
        self.assertIsInstance(entries, list)

    def test_09_stage_document_rejects_negative_ttl_days(self):
        """
        CONFIRMS DEF-08: stage_document rejects negative TTL and defaults to 7.0 days.
        """
        res = stage_document("https://example.com/neg_ttl", "Title", "content", ttl_days=-5.0)
        doc = get_staged_document(res["doc_id"])
        self.assertIsNotNone(doc)
        self.assertGreater(doc["expires_at"], doc["created_at"])

    def test_10_normalize_query_handles_int(self):
        """
        CONFIRMS DEF-09: _normalize_query safely coerces integers/non-strings, preventing AttributeError.
        """
        try:
            res = get_cached_search(12345)
            self.assertTrue(res is None or isinstance(res, list))
        except AttributeError as e:
            self.fail(f"AttributeError raised on int query: {e}")

    def test_11_fetch_page_detailed_handles_none_url(self):
        """
        CONFIRMS DEF-10: fetch_page_detailed safely handles None URL without TypeError in extract_domain.
        """
        res = fetch_page_detailed(None)
        self.assertFalse(res["success"])
        self.assertIn("Missing or empty URL", res["error_reason"])

    # --- Category 3: IPC Pipeline, Error Handling & Presentation ---

    def test_12_format_fast_digest_displays_search_error(self):
        """
        CONFIRMS DEF-11: format_fast_digest cleanly renders search engine error instead of [None](None).
        """
        err_data = {
            "query": "rate limited query",
            "perspective": "tech",
            "elapsed_seconds": 0.5,
            "results": [{"error": "Search failed: Rate limit reached"}],
            "total_fetched": 0
        }
        res = format_fast_digest(err_data)
        self.assertNotIn("[None](None)", res)
        self.assertIn("Search Error", res)
        self.assertIn("Rate limit reached", res)

    def test_13_format_deep_digest_displays_search_error(self):
        """
        CONFIRMS DEF-12: format_deep_digest cleanly renders search engine error instead of [None](None).
        """
        err_data = {
            "query": "rate limited query",
            "perspective": "tech",
            "elapsed_seconds": 0.5,
            "results": [{"error": "Search failed: Rate limit reached"}],
            "total_fetched": 0
        }
        res = format_deep_digest(err_data)
        self.assertNotIn("[None](None)", res)
        self.assertIn("Search Error", res)
        self.assertIn("Rate limit reached", res)

    def test_14_daemon_get_toc_and_read_section_surface_fetch_errors(self):
        """
        CONFIRMS DEF-13: _handle_get_toc and _handle_read_section surface fetch errors directly.
        """
        error_msg = "Failed to retrieve https://example.com/api: 404 Not Found"
        with patch("daemon.fetch_page", return_value=error_msg):
            toc = _handle_get_toc({"url": "https://example.com/api"})
            self.assertIn("Failed to retrieve", toc)
            self.assertIn("404 Not Found", toc)
            sec = _handle_read_section({"url": "https://example.com/api", "section_name_or_index": "Intro"})
            self.assertIn("Failed to retrieve", sec)
            self.assertIn("404 Not Found", sec)

    # --- Category 4: Neural Pipeline & Syntax Integrity ---

    def test_15_neural_fast_safe_truncation_preserves_code_fences(self):
        """
        CONFIRMS DEF-15: _select_top_chunks uses safe_truncate_markdown, closing code fences cleanly.
        """
        from chunker import _get_unclosed_fences
        long_code = "```python\n" + ("x = 1;\n" * 400) + "```"
        chunks = [{"content": long_code, "relevance_score": 10.0}]
        res = _select_top_chunks(chunks, 1)
        self.assertEqual(
            _get_unclosed_fences(res[0]["content"]),
            [],
            f"Code block must be cleanly closed! Ended with: {res[0]['content'][-50:]}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
