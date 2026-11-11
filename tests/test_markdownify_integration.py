#!/usr/bin/env python3
"""
Test Suite for CommonMark Conversion Pipeline & markdownify Integration.
Verifies the two-stage HTML to Markdown pipeline in fetcher.py:
1. Code blocks are cleanly fenced with triple backticks and not fragmented.
2. Tables are converted to clean GitHub Flavored Markdown (GFM) pipe tables.
3. Document preamble before the first # Heading is preserved.
4. HTML comments are stripped and do not leak into headings.
5. Fallback resilience when trafilatura returns None, malformed HTML, or converter raises.
6. Verify fetch_page integration returns clean CommonMark markdown and respects the 6,000 char cap.
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure repository root is on sys.path
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

from fetcher import (
    CommonMarkConverter,
    _html_to_clean_markdown,
    fetch_page,
    fetch_fast_tier,
    clean_web_content
)


class TestCommonMarkConverterCodeBlocks(unittest.TestCase):
    """Test 1: Code blocks are cleanly fenced with triple backticks and not fragmented."""

    def setUp(self):
        self.converter = CommonMarkConverter(
            heading_style="ATX",
            strip=["script", "style", "head", "noscript", "iframe"],
            newline_style="backslash"
        )

    def test_standard_pre_code_block(self):
        html = "<pre><code>import sys\nprint(sys.version)</code></pre>"
        md = self.converter.convert(html).strip()
        self.assertTrue(md.startswith("```"))
        self.assertTrue(md.endswith("```"))
        self.assertIn("import sys\nprint(sys.version)", md)
        # Verify no fragmented or duplicate backticks
        self.assertEqual(md.count("```"), 2)

    def test_nested_pre_tags_unwrapped(self):
        # trafilatura frequently outputs <pre><pre>code</pre></pre>
        html = "<pre><pre>const x = 42;\nconsole.log(x);</pre></pre>"
        md = self.converter.convert(html).strip()
        self.assertTrue(md.startswith("```"))
        self.assertTrue(md.endswith("```"))
        self.assertIn("const x = 42;\nconsole.log(x);", md)
        self.assertEqual(md.count("```"), 2, "Nested <pre> tags must produce exactly one code fence")

    def test_deeply_nested_pre_tags(self):
        html = "<pre><pre><pre>deeply_nested_code()</pre></pre></pre>"
        md = self.converter.convert(html).strip()
        self.assertEqual(md.count("```"), 2)
        self.assertIn("deeply_nested_code()", md)

    def test_pre_inside_atx_heading(self):
        html = "<h2>Method <pre>process_data(batch)</pre> Overview</h2>"
        md = self.converter.convert(html).strip()
        self.assertIn("## Method `process_data(batch)` Overview", md)
        self.assertEqual(md.count("```"), 0, "No fenced code block inside heading")

    def test_h1_with_pre_converted_to_inline_code(self):
        html = "<h1>Class <pre>Pipeline</pre></h1>"
        md = self.converter.convert(html).strip()
        self.assertIn("# Class `Pipeline`", md)
        self.assertNotIn("```", md)


class TestCommonMarkConverterTables(unittest.TestCase):
    """Test 2: Tables are converted to clean GitHub Flavored Markdown (GFM) pipe tables."""

    def setUp(self):
        self.converter = CommonMarkConverter(
            heading_style="ATX",
            strip=["script", "style", "head", "noscript", "iframe"],
            newline_style="backslash"
        )

    def test_table_conversion_to_gfm_pipe_table(self):
        html = """
        <table>
            <thead>
                <tr><th>Parameter</th><th>Type</th><th>Description</th></tr>
            </thead>
            <tbody>
                <tr><td>url</td><td>str</td><td>Target URL to fetch</td></tr>
                <tr><td>timeout</td><td>int</td><td>Timeout in seconds</td></tr>
            </tbody>
        </table>
        """
        md = self.converter.convert(html).strip()
        lines = [line.strip() for line in md.split("\n") if line.strip()]
        self.assertTrue(len(lines) >= 3, "Table should produce at least header, separator, and data rows")
        # Check header
        self.assertIn("Parameter", lines[0])
        self.assertIn("Type", lines[0])
        self.assertIn("Description", lines[0])
        # Check separator row contains ---
        self.assertIn("---", lines[1])
        # Check data rows
        self.assertTrue(any("Target URL to fetch" in l for l in lines))
        self.assertTrue(any("Timeout in seconds" in l for l in lines))
        # Ensure pipes are used as delimiters
        for l in lines:
            self.assertTrue(l.startswith("|") and l.endswith("|"), f"Row should be pipe-delimited: {l}")


class TestPreamblePreservation(unittest.TestCase):
    """Test 3: Document preamble before the first # Heading is preserved."""

    def setUp(self):
        self.converter = CommonMarkConverter(
            heading_style="ATX",
            strip=["script", "style", "head", "noscript", "iframe"],
            newline_style="backslash"
        )

    def test_preamble_before_first_heading(self):
        html = (
            "<p>This document provides an overview of the core architecture and features.</p>"
            "<p>It is intended for integration engineers and system architects.</p>"
            "<h1>1. System Overview</h1>"
            "<p>Detailed system specs follow here.</p>"
        )
        md = self.converter.convert(html).strip()
        self.assertIn("This document provides an overview of the core architecture", md)
        self.assertIn("It is intended for integration engineers", md)
        self.assertIn("# 1. System Overview", md)
        # Ensure preamble appears before the first heading
        preamble_pos = md.find("This document provides an overview")
        heading_pos = md.find("# 1. System Overview")
        self.assertTrue(preamble_pos < heading_pos, "Preamble must precede the first ATX heading")


class TestCommentStripping(unittest.TestCase):
    """Test 4: HTML comments (<!-- ... -->) are stripped and do not leak into headings."""

    def setUp(self):
        self.converter = CommonMarkConverter(
            heading_style="ATX",
            strip=["script", "style", "head", "noscript", "iframe"],
            newline_style="backslash"
        )

    def test_html_comments_stripped_completely(self):
        html = (
            "<!-- wp:heading -->"
            "<h2>Getting Started</h2>"
            "<!-- /wp:heading -->"
            "<!-- wp:paragraph -->"
            "<p>Follow these instructions to run the application.</p>"
            "<!-- /wp:paragraph -->"
        )
        md = self.converter.convert(html).strip()
        self.assertNotIn("<!--", md)
        self.assertNotIn("-->", md)
        self.assertNotIn("wp:heading", md)
        self.assertNotIn("wp:paragraph", md)
        self.assertIn("## Getting Started", md)
        self.assertIn("Follow these instructions to run the application.", md)

    def test_html_comment_does_not_pollute_heading(self):
        html = "<div><!-- Temporary Announcement Banner --><h1>Release 2.0</h1></div>"
        md = self.converter.convert(html).strip()
        self.assertEqual(md, "# Release 2.0")


class TestFallbackResilience(unittest.TestCase):
    """Test 5: Fallback resilience when trafilatura returns None or malformed HTML."""

    def test_empty_or_none_input(self):
        self.assertEqual(_html_to_clean_markdown(""), "")
        self.assertEqual(_html_to_clean_markdown(None), "")
        self.assertEqual(_html_to_clean_markdown("   \n\t  "), "")

    @patch("fetcher.trafilatura.extract")
    def test_trafilatura_returns_none_falls_back_to_markdown_format(self, mock_extract):
        # Stage 1 (output_format="html") returns None
        # Stage 3 fallback (output_format="markdown") returns raw markdown
        def side_effect(content, output_format="txt", **kwargs):
            if output_format == "html":
                return None
            elif output_format == "markdown":
                return "# Fallback Content\n\nThis is recovered from markdown fallback."
            return None

        mock_extract.side_effect = side_effect
        res = _html_to_clean_markdown("<html><body>Some difficult to parse html</body></html>")
        self.assertIn("# Fallback Content", res)
        self.assertIn("This is recovered from markdown fallback.", res)

    @patch("fetcher.trafilatura.extract")
    def test_trafilatura_fails_completely_returns_empty_string(self, mock_extract):
        mock_extract.return_value = None
        res = _html_to_clean_markdown("<html><body>Unparseable content</body></html>")
        self.assertEqual(res, "")

    @patch("fetcher.trafilatura.extract")
    def test_trafilatura_raises_exception_handled_gracefully(self, mock_extract):
        mock_extract.side_effect = RuntimeError("Trafilatura native parser failure")
        res = _html_to_clean_markdown("<html><body>Bad content</body></html>")
        self.assertEqual(res, "")

    @patch("fetcher.CommonMarkConverter.convert")
    @patch("fetcher.trafilatura.extract")
    def test_markdownify_exception_shields_and_falls_back(self, mock_extract, mock_convert):
        # Stage 1 succeeds with HTML
        # Stage 2 markdownify raises unexpected conversion error
        # Stage 3 falls back to trafilatura output_format="markdown"
        def extract_side_effect(content, output_format="txt", **kwargs):
            if output_format == "html":
                return "<html><body><p>Valid extracted HTML</p></body></html>"
            elif output_format == "markdown":
                return "Resilient fallback markdown"
            return None

        mock_extract.side_effect = extract_side_effect
        mock_convert.side_effect = TypeError("Unexpected malformed entity crash in markdownify")

        res = _html_to_clean_markdown("<html><body>Test</body></html>")
        self.assertEqual(res, "Resilient fallback markdown")


class TestFetchPageIntegration(unittest.TestCase):
    """Test 6: Verify fetch_page integration returns clean CommonMark markdown and respects the 6,000 char cap."""

    @patch("fetcher.fetch_page_detailed")
    def test_fetch_page_respects_6000_char_cap(self, mock_detailed):
        # Generate a large markdown document exceeding 6,000 chars
        long_content = "# Section 1\n\n" + ("This is paragraph text with lots of detailed explanations. " * 150)
        self.assertTrue(len(long_content) > 6000)

        mock_detailed.return_value = {
            "success": True,
            "content": long_content,
            "error_reason": None,
            "cached": False
        }

        result = fetch_page("https://example.com/long-page", use_cache=False)
        self.assertIn("Webpage content capped at 6,000 characters", result)
        # Content portion should be safely truncated
        self.assertTrue(len(result) < len(long_content))

    @patch("fetcher.requests.Session")
    def test_fetch_fast_tier_end_to_end_conversion(self, mock_session_cls):
        # Simulate a real HTML response through fetch_fast_tier
        html_payload = """<!DOCTYPE html>
<html>
<head><title>Integration Test Page</title></head>
<body>
<article>
<h1>Integration Guide</h1>
<p>This is a complete integration guide for the CommonMark pipeline.</p>
<h2>Code Sample</h2>
<pre><pre>def run():
    print("running")
</pre></pre>
<h2>Configuration Table</h2>
<table>
<thead><tr><th>Option</th><th>Default</th></tr></thead>
<tbody><tr><td>timeout</td><td>12</td></tr></tbody>
</table>
<p>Additional details to ensure the content exceeds minimum length requirements for extraction.</p>
</article>
</body>
</html>"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_payload
        mock_resp.content = html_payload.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html"}

        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_resp
        mock_session_cls.return_value = mock_session_instance

        success, content, raw, err = fetch_fast_tier("https://test.local/doc", skip_tls_verify=True)
        self.assertTrue(success)
        self.assertIsNone(err)
        # Code fence should be clean ```
        self.assertIn("```", content)
        self.assertEqual(content.count("```"), 2, "Nested pre must be normalized to single fenced code block")
        # Table should be pipe formatted
        self.assertIn("| Option | Default |", content)
        # Heading should be ATX style
        self.assertIn("# Integration Guide", content)


if __name__ == "__main__":
    unittest.main()
