"""
Adversarial Red-Team Regression Battery (Remediated Plan V4).
Verifies 15 remediations across Markdown Chunking, Persistence/Vault,
Wire Contracts, and Search/Retrieval Pipelines.

STRICT CONCURRENCY RULE: Zero background daemon usage; all tests run in-process.
"""
import unittest
import os
import sys
import json
import sqlite3
import datetime
from unittest.mock import patch

# Ensure repo root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from chunker import (
    chunk_markdown_smart,
    _split_oversized_text,
    _clean_section_content,
    extract_headings,
    extract_section,
    safe_truncate_markdown,
    _get_unclosed_fences,
    CODE_BLOCK_PATTERN,
)
from vault import (
    stage_document,
    extract_staged_slice,
    record_fact,
    get_ledger_markdown,
    _hash_url,
    get_connection,
)
from searcher import (
    _canonical_url,
    clean_agent_query,
    search_web,
)
from parallel_pipeline import (
    format_fast_digest,
    format_deep_digest,
)


class TestAdversarialMarkdownAndChunkerV4(unittest.TestCase):
    """Domain 1: Hostile inputs, code fence corruption, and chunker data loss."""

    def test_vuln_01_multiple_code_blocks_expansion_corrupts_markdown_syntax(self):
        """
        DEF-01: In _split_oversized_text, multiple adjacent code blocks and intervening text
        must not cause prose documentation to be wrapped into a code fence.
        """
        cb1 = "```python\n# BLOCK 1\n" + ("x = 1\n" * 200) + "```"
        prose = "This is critical architectural documentation explaining the design."
        cb2 = "```python\n# BLOCK 2\n" + ("y = 2\n" * 200) + "```"
        text = f"{cb1}\n\n{prose}\n\n{cb2}"

        chunks = _split_oversized_text(text, "Architecture", 350, 25)
        # Verify plain prose was NOT swallowed and wrapped in ```python
        prose_swallowed_in_code = False
        for c in chunks:
            content = c["content"]
            if prose in content and content.startswith("```python"):
                prose_swallowed_in_code = True
                break

        self.assertFalse(
            prose_swallowed_in_code,
            "Plain prose documentation was improperly wrapped into a python code fence!"
        )
        self.assertTrue(
            any(prose in c["content"] for c in chunks),
            "Prose content must be preserved across chunks"
        )

    def test_vuln_02_silent_data_loss_on_sections_under_min_words(self):
        """
        DEF-02: chunk_markdown_smart must retain small sections (< min_chunk_words)
        by merging them into preceding chunk or keeping as standalone.
        """
        md = """# Introduction
This is the primary introduction to the framework with more than twenty five words so it qualifies as a valid chunk under min_chunk_words rule.

# Security Notice
The master encryption key is SECRET_KEY_12345. Never commit this to git.

# Deployment
This is the final section of our deployment guide which contains sufficient words to exceed the minimum threshold and remain intact.
"""
        chunks = chunk_markdown_smart(md, min_chunk_words=25)
        all_content = " ".join(c["content"] for c in chunks)
        self.assertIn(
            "SECRET_KEY_12345",
            all_content,
            "Small section content must be preserved and not dropped!"
        )

    def test_vuln_03_nested_code_fences_slice_and_premature_termination(self):
        """
        DEF-03: CODE_BLOCK_PATTERN must not slice nested fences (e.g. 4 backticks inside 3 backticks),
        preventing code comments from being treated as real headings.
        """
        text = """```markdown
Outer block contains 4 backticks:
````python
# False Heading Inside Code
def foo():
    pass
````
```"""
        headings = extract_headings(text)
        heading_titles = [h["title"] for h in headings]
        self.assertNotIn(
            "False Heading Inside Code",
            heading_titles,
            "Code comment inside nested fence must not be extracted as top-level heading!"
        )

    def test_vuln_04_safe_truncate_markdown_catastrophic_text_loss(self):
        """
        DEF-04: safe_truncate_markdown must retain substantive text rather than
        collapsing to heading when target_len <= 200.
        """
        text = "# Title\n" + ("This is substantive text explaining key concepts in detail. " * 5)
        res = safe_truncate_markdown(text, 205)
        self.assertTrue(
            len(res) > 50,
            "safe_truncate_markdown must retain substantive text rather than collapsing to title"
        )
        self.assertIn("substantive text", res)

    def test_vuln_13_clean_section_content_false_positive_deletion(self):
        """
        DEF-13: _clean_section_content must not falsely delete subheadings that share
        short prefix characters with heading.
        """
        content = "## Applications in Robotics\nRobotics details and architecture."
        cleaned = _clean_section_content(content, "App")
        self.assertIn(
            "Applications in Robotics",
            cleaned,
            "Subheading 'Applications in Robotics' was falsely deleted by 'App' heading!"
        )

    def test_vuln_14_single_line_inline_fence_flagged_as_unclosed(self):
        """
        DEF-14: _get_unclosed_fences must not flag single-line closed code blocks
        as unclosed.
        """
        unclosed = _get_unclosed_fences("```python print('hello') ```")
        self.assertEqual(
            len(unclosed),
            0,
            "Single-line closed fence was falsely flagged as unclosed!"
        )


class TestAdversarialPersistenceAndVaultV4(unittest.TestCase):
    """Domain 2: SQLite persistence, type safety, and URL canonicalization."""

    def test_vuln_05_unhandled_type_error_on_string_headings_in_extract_staged_slice(self):
        """
        DEF-05: extract_staged_slice must safely handle string headings without raising TypeError.
        """
        doc = stage_document("http://example.com/test", "Title", "Body text", headings=["Section A", "Section B"])
        try:
            res = extract_staged_slice(doc["doc_id"], section="NonexistentSection")
            self.assertIsInstance(res, str)
        except TypeError:
            self.fail("extract_staged_slice raised TypeError on string headings!")

    def test_vuln_06_url_fragment_breaks_cache_sharing(self):
        """
        DEF-06: _hash_url strips anchor fragments so identical pages share cache.
        """
        h1 = _hash_url("https://example.com/docs#overview")
        h2 = _hash_url("https://example.com/docs#installation")
        self.assertEqual(
            h1,
            h2,
            "Anchor fragments must be stripped so identical pages share cache!"
        )

    def test_vuln_07_record_fact_crashes_on_non_serializable_tags(self):
        """
        DEF-07: record_fact uses default=str in json.dumps so non-serializable objects do not crash.
        """
        fact = record_fact("security", 1, "Fact text", tags=[datetime.datetime.now()])
        self.assertIsNotNone(fact)

    def test_vuln_08_get_ledger_markdown_crashes_on_corrupt_timestamp(self):
        """
        DEF-08: get_ledger_markdown safely handles extreme or corrupt timestamps.
        """
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO research_ledger (
                    category, tier, pass_num, fact_text, source_url, source_title, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("test_overflow", 1, 1, "Overflow test fact", "", "", "[]", 1e16))
            conn.commit()

        try:
            md = get_ledger_markdown(category="test_overflow")
            self.assertIn("Overflow test fact", md)
        finally:
            with get_connection() as conn:
                conn.execute("DELETE FROM research_ledger WHERE category = 'test_overflow'")
                conn.commit()

    def test_vuln_15_duplicate_redundant_cache_purges_in_stage_document(self):
        """
        DEF-15: stage_document must not perform duplicate redundant clean_expired_cache calls.
        """
        with patch("vault.clean_expired_staged_docs") as mock_staged, \
             patch("vault.clean_expired_cache") as mock_cache:
            import vault
            vault._last_cleanup = 0.0  # Force cleanup trigger
            stage_document("http://example.com/purge", "Title", "Content")
            self.assertTrue(mock_staged.called, "clean_expired_staged_docs should be called")
            self.assertFalse(mock_cache.called, "clean_expired_cache should not be redundantly called")


class TestAdversarialSearchAndPipelineV4(unittest.TestCase):
    """Domain 3 & 4: Searcher, query cleaning, error masking, and pipeline formatting."""

    def test_vuln_09_canonical_url_attribute_error_on_non_string(self):
        """
        DEF-09: _canonical_url safely handles non-string inputs.
        """
        result = _canonical_url(12345)
        self.assertEqual(result, "12345")

    def test_vuln_10_clean_agent_query_fails_on_short_tech_keywords(self):
        """
        DEF-10: clean_agent_query cleans fluff from short technical keywords ('AI', 'C#', 'Go', 'R').
        """
        cleaned_ai = clean_agent_query("What is AI?")
        cleaned_cs = clean_agent_query("Please search for C#")
        cleaned_go = clean_agent_query("Can you find Go?")

        self.assertEqual(cleaned_ai, "AI", "AI query was not cleaned!")
        self.assertEqual(cleaned_cs, "C#", "C# query was not cleaned!")
        self.assertEqual(cleaned_go, "Go", "Go query was not cleaned!")

    def test_vuln_11_zero_results_falsely_reported_as_blocked_paywall(self):
        """
        DEF-11: Zero results reports 'No search results found' instead of paywall message.
        """
        data = {"query": "nonexistent_query_12345", "results": [], "elapsed_seconds": 0.1, "device": "cpu"}
        digest_fast = format_fast_digest(data)
        digest_deep = format_deep_digest(data)

        self.assertNotIn("All 0 sources were inaccessible or blocked", digest_fast)
        self.assertNotIn("All 0 sources were inaccessible or blocked", digest_deep)
        self.assertIn("No search results found", digest_fast)
        self.assertIn("No search results found", digest_deep)

    def test_vuln_12_corrupted_none_links_emitted_in_digests(self):
        """
        DEF-12: format_fast_digest sanitizes None title/url into 'Untitled Source' / '#'.
        """
        data = {
            "query": "none_link_test",
            "results": [
                {
                    "title": None,
                    "url": None,
                    "status": "success",
                    "top_passages": [{"heading": "Details", "content": "Text here", "relevance_score": 10.0}]
                }
            ],
            "elapsed_seconds": 0.1
        }
        digest = format_fast_digest(data)
        self.assertNotIn("[None](None)", digest)
        self.assertIn("[Untitled Source](#)", digest)


if __name__ == "__main__":
    unittest.main()
