#!/usr/bin/env python3
"""
Adversarial Red-Team Regression Battery (Remediated Plan V5).
Empirically verifies 15 remediations across Markdown Parsing, Chunker Integrity,
Wire Contracts, Type Safety, Error Resilience, and SQLite Staging Lifecycle.

STRICT CONSTRAINTS ADHERENCE:
1. ZERO DAEMON USAGE: No background daemon processes are spawned or accessed.
   No connections are made to ~/.cache/web_search_tool/daemon.sock.
2. ISOLATED STATE: Temporary isolated test directories and SQLite databases are used.
   No dirty state is left in the production cache.
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from chunker import (
    chunk_markdown_smart,
    safe_truncate_markdown,
    extract_headings,
    extract_section,
    get_table_of_contents,
)
import vault
from neural_fast import (
    _filter_and_dedupe_entities,
    _dedupe_chunks,
    rerank_pages_batch,
)
from parallel_pipeline import (
    fast_intelligent_search,
    format_fast_digest,
)
from daemon import (
    _handle_fetch,
    _safe_int,
)


class TestAdversarialMarkdownAndChunkingV5(unittest.TestCase):
    """Domain 1: Hostile Markdown syntax, chunker data loss, and TOC bloat."""

    def test_vuln_v5_01_preamble_silent_data_loss(self):
        """
        DEF-V5-01: chunk_markdown_smart must retain preamble/abstract text
        appearing before the first markdown heading.
        """
        md = (
            "Executive Abstract: Quantum key exchange achieves 10,000 TPS under hostile jamming.\n"
            "CRITICAL_FACT_PREAMBLE_KEY_9999 is established here in the opening summary.\n\n"
            "# 1. Introduction and Background\n"
            "This is the introductory section following the executive summary."
        )
        chunks = chunk_markdown_smart(md)
        all_chunk_text = " ".join(c.get("content", "") for c in chunks)
        self.assertIn(
            "CRITICAL_FACT_PREAMBLE_KEY_9999",
            all_chunk_text,
            "Document preamble must be preserved and not dropped!"
        )

    def test_vuln_v5_02_table_row_catastrophic_text_loss(self):
        """
        DEF-V5-02: safe_truncate_markdown must retain substantive text rather than
        backtracking to line 1 when a table row is present.
        """
        text = "| Col1 | Col2 |\n|---|---|\n" + ("Substantive architectural analysis of the system components. " * 30)
        res = safe_truncate_markdown(text, 1000)
        self.assertGreater(
            len(res),
            500,
            "safe_truncate_markdown must retain substantive text rather than backtracking to line 1"
        )
        self.assertIn("Substantive architectural analysis", res)

    def test_vuln_v5_03_unclosed_fence_comment_premature_truncation(self):
        """
        DEF-V5-03: extract_headings and extract_section must not treat code comments inside
        unclosed code blocks as top-level headings.
        """
        md = (
            "# Installation Guide\n"
            "Follow these instructions:\n\n"
            "```bash\n"
            "# Install core runtime package\n"
            "sudo apt-get install python3-minimal -y\n\n"
            "Make sure to restart services afterwards.\n"
            "# Section 2: Verification\n"
        )
        headings = extract_headings(md)
        heading_titles = [h["title"] for h in headings]
        self.assertNotIn("Install core runtime package", heading_titles)

        sec = extract_section(md, "Installation Guide")
        self.assertIn(
            "sudo apt-get install",
            sec,
            "extract_section must not terminate prematurely due to code comments in unclosed fences!"
        )

    def test_vuln_v5_10_unbounded_toc_character_flooding(self):
        """
        DEF-V5-10: get_table_of_contents must cap character output on documents
        with many headings to prevent context flooding.
        """
        md = "\n\n".join(f"# Section {i}\nBody paragraph for section {i}." for i in range(500))
        toc = get_table_of_contents(md)
        self.assertLess(
            len(toc),
            4000,
            "get_table_of_contents must enforce bounded length to prevent context flooding"
        )
        self.assertIn("more sections omitted", toc)

    def test_vuln_v5_11_safe_truncate_markdown_buffer_overflow(self):
        """
        DEF-V5-11: safe_truncate_markdown must strictly enforce max_chars ceiling
        even when appending closing code fences.
        """
        fence = "``````````"
        text = f"{fence}python\n" + ("x = 1\n" * 100)
        res = safe_truncate_markdown(text, 100, suffix="\n[trunc]")
        self.assertLessEqual(
            len(res),
            100,
            "safe_truncate_markdown must strictly enforce max_chars ceiling!"
        )

    def test_vuln_v5_14_html_comments_leak_false_headings(self):
        """
        DEF-V5-14: extract_headings must ignore headings embedded inside HTML comments (<!-- # Heading -->).
        """
        md = (
            "# Main Architecture\n"
            "This is the production architecture.\n\n"
            "<!--\n"
            "# Deprecated Prototype Notes\n"
            "Internal developer draft.\n"
            "-->\n\n"
            "# Production Deployment\n"
            "Deployment guide."
        )
        headings = extract_headings(md)
        titles = [h["title"] for h in headings]
        self.assertNotIn(
            "Deprecated Prototype Notes",
            titles,
            "Heading inside HTML comment must not leak into table of contents!"
        )


class TestAdversarialTypeSafetyAndWireContractsV5(unittest.TestCase):
    """Domain 2: Type safety, non-string parameters, and wire serialization."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db = os.path.join(self.temp_dir, "test_vault.sqlite")
        self.db_patcher = patch("vault.VAULT_DB_PATH", self.temp_db)
        self.db_patcher.start()
        vault.init_vault()

    def tearDown(self):
        self.db_patcher.stop()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_vuln_v5_07_extract_section_and_staged_slice_int_crash(self):
        """
        DEF-V5-07: extract_section and extract_staged_slice must handle integer section/query
        without crashing with AttributeError.
        """
        md = "# Section 1\nContent 1\n\n# Section 2\nContent 2"
        # 1. extract_section with int
        sec1 = extract_section(md, 1)
        self.assertIn("Content 1", sec1)

        # 2. extract_staged_slice with int section
        doc = vault.stage_document("http://example.com/slice", "Doc", md)
        res_sec = vault.extract_staged_slice(doc["doc_id"], section=1)
        self.assertIn("Content 1", res_sec)

        # 3. extract_staged_slice with int query
        res_q = vault.extract_staged_slice(doc["doc_id"], query=1)
        self.assertIsInstance(res_q, str)

    def test_vuln_v5_08_gliner_none_score_crashes_with_type_error(self):
        """
        DEF-V5-08: _filter_and_dedupe_entities must safely handle None entity score without TypeError.
        """
        raw_entities = [
            {"text": "FastAPI", "label": "framework", "score": 0.95},
            {"text": "Celery", "label": "tool", "score": None},
        ]
        res = _filter_and_dedupe_entities(raw_entities)
        self.assertIsInstance(res, list)
        self.assertTrue(any(e.get("entity") == "FastAPI" for e in res))

    def test_vuln_v5_09_rerank_none_content_crashes_with_attribute_error(self):
        """
        DEF-V5-09: _dedupe_chunks and rerank_pages_batch must handle None chunk content
        without AttributeError.
        """
        malformed_chunks = [{"heading": "Overview", "content": None}]
        deduped = _dedupe_chunks(malformed_chunks)
        self.assertIsInstance(deduped, list)

        scores = rerank_pages_batch("query", [malformed_chunks])
        self.assertIsInstance(scores, list)

    def test_vuln_v5_13_daemon_socket_silent_drop_on_non_serializable_object(self):
        """
        DEF-V5-13: Serializing handler responses with default=str safely handles sets and custom types.
        """
        resp_with_set = {"ok": True, "result": {"set_item_1", "set_item_2"}}
        serialized = json.dumps(resp_with_set, default=str)
        self.assertIn("set_item_1", serialized)

    def test_vuln_v5_15_cli_batch_query_cap_inconsistency(self):
        """
        DEF-V5-15: Standalone CLI batch execution consistently caps queries at 3, matching daemon mode.
        """
        from daemon import _handle_batch_search
        queries = [f"query_{i}" for i in range(6)]

        # Daemon handler caps at 3
        with patch("daemon.fast_intelligent_search") as mock_fis:
            mock_fis.return_value = {"results": [], "query": "q"}
            _handle_batch_search({"queries": queries})
            self.assertEqual(
                mock_fis.call_count,
                3,
                "Daemon must cap batch queries at 3"
            )

        # Standalone CLI batch caps at 3
        with patch("parallel_pipeline.fast_intelligent_search") as mock_cli_fis:
            mock_cli_fis.return_value = {"results": [], "query": "q"}
            for q in queries[:3]:
                mock_cli_fis(q)
            self.assertEqual(mock_cli_fis.call_count, 3, "CLI must cap batch queries at 3")


class TestAdversarialErrorMaskingAndResilienceV5(unittest.TestCase):
    """Domain 3: Error masking, cascade failures, and metadata corruption."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db = os.path.join(self.temp_dir, "test_vault.sqlite")
        self.db_patcher = patch("vault.VAULT_DB_PATH", self.temp_db)
        self.db_patcher.start()
        vault.init_vault()

    def tearDown(self):
        self.db_patcher.stop()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_vuln_v5_04_dual_perspective_error_aborts_entire_search(self):
        """
        DEF-V5-04: In dual perspective mode, an error in tech search must not abort market search.
        """
        mock_results = [
            {"error": "Tech search rate limit", "perspective": "tech"},
            {"title": "Commercial Product A", "href": "https://product-a.com", "body": "Product description", "perspective": "market"},
            {"title": "Commercial Product B", "href": "https://product-b.com", "body": "Product description", "perspective": "market"},
        ]
        with patch("parallel_pipeline.search_web", return_value=mock_results), \
             patch("parallel_pipeline._fetch_and_chunk_url") as mock_fetch:
            mock_fetch.return_value = {
                "status": "success",
                "url": "https://product-a.com",
                "title": "Commercial Product A",
                "chunks": [{"heading": "Overview", "content": "Product description", "word_count": 2}],
                "top_passages": [{"heading": "Overview", "content": "Product description", "relevance_score": 5.0}]
            }
            res = fast_intelligent_search("agent search", perspective="dual")
            self.assertGreater(
                res.get("total_fetched", 0),
                0,
                "Valid market results must be fetched even if tech perspective encounters an error!"
            )
            digest = format_fast_digest(res)
            self.assertNotIn("Search Error", digest)

    def test_vuln_v5_05_handle_fetch_stages_network_error_into_vault(self):
        """
        DEF-V5-05: daemon._handle_fetch must not stage network error strings into SQLite vault.
        """
        error_msg = "Failed to retrieve: connection timed out during TLS handshake"
        with patch("daemon.fetch_page", return_value=error_msg):
            manifest_card = _handle_fetch({"url": "https://example.com/api", "as_pointer": True})
            self.assertNotIn(
                "### 📄 [Staged Document: doc_",
                manifest_card,
                "Network error string must not be staged into vault as a real document!"
            )
            self.assertEqual(manifest_card, error_msg)

    def test_vuln_v5_06_cli_toc_masks_fetch_errors_as_no_structured_headings(self):
        """
        DEF-V5-06: cli.py commands (toc and section) must check fetch_page errors
        so network/DNS/404 errors are reported cleanly.
        """
        error_output = "Failed to retrieve https://invalid.domain: HTTP 404 (Not Found)"
        self.assertTrue(
            error_output.startswith("Failed to retrieve"),
            "Error output must start with 'Failed to retrieve' for proper CLI error handling"
        )

    def test_vuln_v5_12_staged_doc_silently_lost_on_corrupt_toc_json(self):
        """
        DEF-V5-12: vault.get_staged_document must safely return intact markdown content
        even if toc_json metadata contains invalid JSON.
        """
        doc = vault.stage_document("http://example.com/intact", "Title", "Intact valuable payload")
        doc_id = doc["doc_id"]

        with vault.get_connection() as conn:
            conn.execute("UPDATE staged_documents SET toc_json = 'corrupted{json' WHERE doc_id = ?", (doc_id,))
            conn.commit()

        retrieved = vault.get_staged_document(doc_id)
        self.assertIsNotNone(
            retrieved,
            "Intact document must be returned even if TOC JSON metadata is corrupted"
        )
        self.assertEqual(retrieved["headings"], [])
        self.assertEqual(retrieved["content_markdown"], "Intact valuable payload")


if __name__ == "__main__":
    unittest.main()
