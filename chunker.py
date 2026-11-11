"""
Markdown Chunker, Code-Block Preserver, and Table of Contents (TOC) Generator.
- Protects code blocks (``` ... ```) so code syntax is never fragmented.
- Guarantees strict chunk size limits (never allows huge chunks to overflow context).
- Extracts clean Table of Contents outlines for progressive disclosure.
- Deduplicates chunks to eliminate redundant boilerplate.
- Allows targeted extraction of single sections by heading or index.
"""
import re
import hashlib
import uuid
from typing import List, Dict, Any, Optional, Tuple

HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
CODE_BLOCK_PATTERN = re.compile(r'(`{3,}|~{3,})[\s\S]*?(?<![`~])\1(?![`~])', re.MULTILINE)
FENCE_LINE_PATTERN = re.compile(r'^([\s>]*)(`{3,}|~{3,})(.*)$')
HTML_COMMENT_PATTERN = re.compile(r'<!--[\s\S]*?(?:-->|\Z)')

def _find_code_block_ranges(text: str) -> List[Tuple[int, int]]:
    """
    Find all code block ranges (start_char, end_char) in text,
    including unclosed code blocks extending to EOF.
    Follows CommonMark fence rules.
    """
    ranges = []
    active_char = None
    active_len = 0
    fence_start = 0

    offset = 0
    for line in text.splitlines(keepends=True):
        line_start = offset
        offset += len(line)
        line_end = offset
        line_stripped = line.rstrip('\r\n')

        prefix_match = re.match(r'^([\s>]*)', line_stripped)
        indent = prefix_match.group(1) if prefix_match else ""
        stripped_start = line_stripped[len(indent):]

        if active_len > 0:
            # Check if closing fence
            close_match = re.match(r'^(`{3,}|~{3,})\s*$', stripped_start)
            if close_match:
                fence_str = close_match.group(1)
                if fence_str[0] == active_char and len(fence_str) >= active_len:
                    ranges.append((fence_start, line_end))
                    active_len = 0
                    active_char = None
        else:
            # Check if closed fence on the same line (single-line inline fence)
            if re.match(r'^([\s>]*)(`{3,}|~{3,})(?![`~]).*\2\s*$', line_stripped):
                ranges.append((line_start, line_end))
                continue
            # Check for opening fence
            open_match = re.match(r'^(`{3,}|~{3,})', stripped_start)
            if open_match:
                fence_str = open_match.group(1)
                active_char = fence_str[0]
                active_len = len(fence_str)
                fence_start = line_start

    if active_len > 0:
        ranges.append((fence_start, len(text)))

    return ranges

def _get_unclosed_fences(text: str) -> List[Tuple[str, str]]:
    """
    Track unclosed code fences using a strict CommonMark state machine.
    Under CommonMark (§4.5):
    - Code fences do NOT nest.
    - An opening fence of length N is only closed by a fence of >= N of the same character.
    - Any backticks/tildes inside an active fence are literal code text.
    """
    active_char = None
    active_len = 0
    active_indent = ""

    for line in text.split("\n"):
        prefix_match = re.match(r'^([\s>]*)', line)
        indent = prefix_match.group(1) if prefix_match else ""
        stripped_start = line[len(indent):]

        if active_len > 0:
            # Check if closing fence
            close_match = re.match(r'^(`{3,}|~{3,})\s*$', stripped_start)
            if close_match:
                fence_str = close_match.group(1)
                if fence_str[0] == active_char and len(fence_str) >= active_len:
                    active_len = 0
                    active_char = None
                    active_indent = ""
        else:
            # Check if closed fence on the same line (single-line inline fence)
            if re.match(r'^([\s>]*)(`{3,}|~{3,})(?![`~]).*\2\s*$', line):
                continue
            # Check for opening fence
            open_match = re.match(r'^(`{3,}|~{3,})', stripped_start)
            if open_match:
                fence_str = open_match.group(1)
                active_char = fence_str[0]
                active_len = len(fence_str)
                active_indent = indent

    if active_len > 0:
        return [(active_char * active_len, active_indent)]
    return []

def safe_truncate_markdown(text: str, max_chars: int, suffix: str = "") -> str:
    """
    Truncate markdown text cleanly to max_chars without breaking markdown syntax:
    1. Returns "" if max_chars <= 0 or not text.
    2. If len(text) <= max_chars, still ensures any unclosed fences are properly closed.
    3. Respects line boundaries and table row boundaries (prevents mid-row table truncation).
    4. Supports arbitrary fence lengths (3+, 4+, indented, blockquoted).
    5. Appends matching backticks before suffix so no code block is left unclosed.
    """
    if max_chars <= 0 or not text:
        return ""

    if len(text) <= max_chars:
        unclosed = _get_unclosed_fences(text)
        if not unclosed:
            return text
        closing = "".join(f"\n{indent}{fence}" for fence, indent in unclosed)
        if len(text.rstrip()) + len(closing) <= max_chars:
            return text.rstrip() + closing

    target_len = max(0, max_chars - len(suffix))
    if target_len <= 0:
        return suffix[:max(0, max_chars)] if max_chars > 0 else ""

    slice_candidate = text[:target_len]
    last_newline = slice_candidate.rfind("\n")

    # Detect if slicing in the middle of a table row or line
    partial_line = slice_candidate[last_newline + 1:] if last_newline != -1 else slice_candidate
    is_table_row = ("|" in partial_line) or (last_newline != -1 and "|" in slice_candidate[max(0, last_newline - 50):last_newline])

    if last_newline > 0:
        if is_table_row and last_newline >= int(target_len * 0.50):
            truncated = slice_candidate[:last_newline]
        elif last_newline >= int(target_len * 0.75):
            truncated = slice_candidate[:last_newline]
        else:
            truncated = slice_candidate
    else:
        truncated = slice_candidate

    unclosed = _get_unclosed_fences(truncated)
    if unclosed:
        closing = "".join(f"\n{indent if '>' in indent else ''}{fence}" for fence, indent in unclosed)
        if len(truncated) + len(closing) + len(suffix) > max_chars:
            overflow = (len(truncated) + len(closing) + len(suffix)) - max_chars
            shrunk = truncated[:max(0, len(truncated) - overflow)]
            if _get_unclosed_fences(shrunk):
                truncated = shrunk.rstrip() + closing
            else:
                truncated = shrunk
        else:
            truncated = truncated.rstrip() + closing

    res = truncated + suffix
    if len(res) > max_chars:
        res = res[:max_chars]
    return res

def _clean_heading_title(raw: str) -> str:
    """Clean and sanitize heading text; prevent tables, lists, or paragraphs from masquerading as headings."""
    # Remove markdown link syntax [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', raw).strip()
    # Remove markdown formatting bold/italics/backticks
    text = re.sub(r'[\*_`]+', ' ', text).strip()
    # Remove leading list markers, table pipes, blockquotes, or colons
    text = re.sub(r'^[\s\-\*\|\>#\:]+', '', text).strip()
    # Remove trailing pipes or colons
    text = re.sub(r'[\s\|\:]+$', '', text).strip()
    # Normalize internal whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def _clean_section_content(content: str, heading_title: str) -> str:
    """Removes redundant leading heading line from chunk content if it repeats the section title."""
    lines = content.strip().split("\n")
    if lines:
        first = lines[0].strip()
        if re.match(r'^#{1,6}\s+', first):
            first_clean = re.sub(r'^#{1,6}\s+', '', first).strip()
            first_clean = re.sub(r'[\*_`]+', '', first_clean).strip()
            ht_clean = heading_title.strip() if heading_title else ""
            if ht_clean:
                if first_clean.lower() == ht_clean.lower():
                    lines = lines[1:]
                elif len(ht_clean) >= 8 and len(first_clean) >= 8:
                    if first_clean.lower().startswith(ht_clean[:18].lower()) or ht_clean.lower().startswith(first_clean[:18].lower()):
                        lines = lines[1:]
    res = "\n".join(lines).strip()
    # Collapse 3+ consecutive newlines to 2
    res = re.sub(r'\n{3,}', '\n\n', res)
    return res

def extract_headings(markdown: str) -> List[Dict[str, Any]]:
    """Extract all headings with their level, title, raw_title, and character offsets."""
    headings = []
    # Identify code block ranges (including unclosed fences) and HTML comments
    code_ranges = _find_code_block_ranges(markdown)
    comment_ranges = [(m.start(), m.end()) for m in HTML_COMMENT_PATTERN.finditer(markdown)]
    excluded_ranges = code_ranges + comment_ranges

    def is_inside_excluded(pos: int) -> bool:
        return any(start <= pos < end for start, end in excluded_ranges)

    for m in HEADING_PATTERN.finditer(markdown):
        if is_inside_excluded(m.start()):
            continue
        level = len(m.group(1))
        raw_text = m.group(2).strip()
        title = _clean_heading_title(raw_text)
        if not title or title in ("-", "*", "|", ":", "..."):
            continue
        headings.append({
            "level": level,
            "title": title,
            "raw_title": raw_text,
            "start": m.start(),
            "end": m.end()
        })
    return headings

def get_table_of_contents(markdown: str, max_headings: int = 100, max_chars: int = 3500) -> str:
    """Generate a clean, token-efficient Table of Contents outline."""
    headings = extract_headings(markdown)
    if not headings:
        words = len(markdown.split())
        return f"Document has no structured headings. Total word count: ~{words} words."

    lines = ["# Document Table of Contents", ""]
    visible_headings = headings[:max_headings]
    for idx, h in enumerate(visible_headings, 1):
        indent = "  " * (h["level"] - 1)
        display_title = h["title"]
        if len(display_title) > 70:
            words = display_title.split()
            if len(words) > 8:
                display_title = " ".join(words[:7]) + "..."
        lines.append(f"{indent}- **[{idx}]** {display_title}")

    if len(headings) > max_headings:
        lines.append(f"- ... *({len(headings) - max_headings} more sections omitted for brevity)*")

    total_words = len(markdown.split())
    footer = (
        f"\n*Total document size: ~{total_words} words ({len(headings)} sections)*\n"
        "Use `read_section(url, section_index_or_name)` to view a specific section."
    )
    full_toc = "\n".join(lines) + "\n" + footer

    if len(full_toc) > max_chars:
        omission_line = "- ... *(additional sections omitted for brevity)*"
        while len(lines) > 2:
            if lines[-1].startswith("- ... *("):
                lines.pop()
                if len(lines) > 2:
                    lines.pop()
                lines.append(omission_line)
            else:
                lines.pop()
                lines.append(omission_line)
            candidate = "\n".join(lines) + "\n" + footer
            if len(candidate) <= max_chars:
                return candidate

        return safe_truncate_markdown(full_toc, max_chars)

    return full_toc

def extract_section(markdown: str, target: Any) -> str:
    """
    Extract a specific section by heading title (case-insensitive substring)
    or by numeric index from the Table of Contents.
    """
    headings = extract_headings(markdown)
    if not headings:
        return f"Section '{target}' not found: document contains no structured markdown headings."

    target_clean = str(target or "").strip().lower()
    if not target_clean:
        available = ", ".join([f"'{h['title']}'" for h in headings[:10]])
        return f"Section '{target}' not found: empty query. Available headings: {available}..."

    target_idx = None

    # Check if target is a numeric index like "1", "3", etc.
    if target_clean.isdigit():
        idx = int(target_clean) - 1
        if 0 <= idx < len(headings):
            target_idx = idx

    # Pass 1: Exact match on title or raw_title
    if target_idx is None:
        for idx, h in enumerate(headings):
            h_title = h["title"].lower()
            h_raw = h.get("raw_title", "").lower()
            if target_clean == h_title or target_clean == h_raw or target_clean.rstrip(".") == h_title.rstrip("."):
                target_idx = idx
                break

    # Pass 2: Substring match (target_clean inside heading title or raw_title)
    if target_idx is None:
        for idx, h in enumerate(headings):
            h_title = h["title"].lower()
            h_raw = h.get("raw_title", "").lower()
            if target_clean in h_title or target_clean in h_raw:
                target_idx = idx
                break

    # Pass 3: Word boundary match if heading title is in target_clean
    if target_idx is None:
        for idx, h in enumerate(headings):
            h_title = h["title"].lower()
            if h_title and re.search(r'\b' + re.escape(h_title) + r'\b', target_clean):
                target_idx = idx
                break

    if target_idx is None:
        available = ", ".join([f"'{h['title']}'" for h in headings[:10]])
        return f"Section '{target}' not found. Available headings: {available}..."

    start_pos = headings[target_idx]["start"]
    target_level = headings[target_idx]["level"]

    # Find where the section ends: next heading with level <= target_level
    end_pos = len(markdown)
    for h in headings[target_idx + 1:]:
        if h["level"] <= target_level:
            end_pos = h["start"]
            break

    section_text = markdown[start_pos:end_pos].strip()
    return section_text

def extract_section_snippet(markdown: str, target: Any, max_chars: int = 1500) -> str:
    """
    Extract a specific section by heading title or index, safely truncated to max_chars.
    """
    sec_text = extract_section(markdown, target)
    if sec_text.startswith(f"Section '{target}' not found"):
        return sec_text
    return safe_truncate_markdown(
        sec_text,
        max_chars,
        suffix=f"\n\n... [Content truncated at {max_chars} chars. Use `inspect_vault_doc` with a specific section or query for more.]"
    )

def _synthesize_label(text: str, fallback_title: str = "Details") -> str:
    """Extract a meaningful 3-6 word label from text if no heading is available."""
    clean = re.sub(r'[#*`_\[\]()\|\>\-\:\;\=]+', ' ', text).strip()
    first_line = clean.split('\n')[0].strip()
    words = first_line.split()
    if len(words) >= 3:
        label = " ".join(words[:6])
        if len(label) > 45:
            label = label[:42] + "..."
        return label
    elif len(words) >= 1:
        return " ".join(words[:4])
    return fallback_title

def _pack_and_recurse(
    parts: List[str],
    separator: str,
    max_words: int,
    max_chars: int
) -> List[str]:
    """
    Greedily pack parts into chunks up to max_words and max_chars.
    If an individual part is oversized, recursively subdivide it.
    Zero text is lost.
    """
    result = []
    current_parts: List[str] = []
    current_w = 0
    current_c = 0

    for part in parts:
        p_str = part.strip()
        if not p_str:
            continue
        p_w = len(p_str.split())
        p_c = len(p_str)

        # If a single part exceeds limits, flush current, then recursively subdivide it
        if p_w > max_words or p_c > max_chars:
            if current_parts:
                result.append(separator.join(current_parts))
                current_parts = []
                current_w = 0
                current_c = 0
            sub_parts = _subdivide_text_boundaries(p_str, max_words, max_chars)
            result.extend(sub_parts)
            continue

        sep_len = len(separator) if current_parts else 0
        if current_parts and (current_w + p_w > max_words or current_c + p_c + sep_len > max_chars):
            result.append(separator.join(current_parts))
            current_parts = [p_str]
            current_w = p_w
            current_c = p_c
        else:
            current_parts.append(p_str)
            current_w += p_w
            current_c += p_c + sep_len

    if current_parts:
        result.append(separator.join(current_parts))

    return result

def _subdivide_text_boundaries(
    text: str,
    max_words: int = 350,
    max_chars: int = 2200
) -> List[str]:
    """
    Subdivides text recursively using markdown and semantic boundaries:
    1. Double newlines (\\n\\n)
    2. Single newlines (\\n)
    3. Sentence boundaries (. ! ?)
    4. Clause boundaries (, ;)
    5. Word boundaries (spaces)
    6. Character slicing for tokens > max_chars.
    Zero text is lost.
    """
    text = text.strip()
    if not text:
        return []

    words = text.split()
    if len(words) <= max_words and len(text) <= max_chars:
        return [text]

    # Level 1: Double newlines
    if "\n\n" in text:
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(parts) > 1:
            return _pack_and_recurse(parts, "\n\n", max_words, max_chars)

    # Level 2: Single newlines
    if "\n" in text:
        parts = [p.strip() for p in text.split("\n") if p.strip()]
        if len(parts) > 1:
            return _pack_and_recurse(parts, "\n", max_words, max_chars)

    # Level 3: Sentence boundaries (. ! ?)
    sentence_parts = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    if len(sentence_parts) > 1:
        return _pack_and_recurse(sentence_parts, " ", max_words, max_chars)

    # Level 4: Clause boundaries (, ;)
    clause_parts = [c.strip() for c in re.split(r'(?<=[,;])\s+', text) if c.strip()]
    if len(clause_parts) > 1:
        return _pack_and_recurse(clause_parts, " ", max_words, max_chars)

    # Level 5: Word boundaries
    if len(words) > 1:
        return _pack_and_recurse(words, " ", max_words, max_chars)

    # Level 6: An indivisible token longer than max_chars
    chunks = []
    for i in range(0, len(text), max_chars):
        chunks.append(text[i:i + max_chars])
    return chunks

def _split_oversized_code_block(cb_text: str, max_words: int, max_chars: int = 2200) -> List[str]:
    """Split an oversized code block into multiple valid fenced code blocks."""
    cb_stripped = cb_text.strip()
    m = re.match(r'^([\s>]*)(`{3,}|~{3,})([^\n]*)\n([\s\S]*?)\n?\1\2$', cb_stripped)
    if not m:
        m = re.match(r'^([\s>]*)(`{3,}|~{3,})([^\n]*)\n([\s\S]*)$', cb_stripped)

    if not m:
        return _subdivide_text_boundaries(cb_text, max_words=max_words, max_chars=max_chars)

    prefix = m.group(1) or ""
    fence = m.group(2)
    lang = m.group(3).strip()
    inner = m.group(4)

    closing_suffix = prefix + fence
    if inner.rstrip().endswith(closing_suffix):
        inner = inner.rstrip()[:-len(closing_suffix)].rstrip()
    elif inner.rstrip().endswith(fence):
        inner = inner.rstrip()[:-len(fence)].rstrip()

    overhead = len(prefix) * 2 + len(fence) * 2 + len(lang) + 4
    avail_chars = max(200, max_chars - overhead)
    sub_pieces = _subdivide_text_boundaries(inner, max_words=max_words, max_chars=avail_chars)

    result = []
    for sub in sub_pieces:
        sub_clean = sub.strip()
        if not sub_clean:
            continue
        wrapped = f"{prefix}{fence}{lang}\n{sub_clean}\n{prefix}{fence}"
        result.append(wrapped)
    return result if result else [cb_text]

def _split_oversized_text(
    text: str,
    title: str,
    max_chunk_words: int,
    min_chunk_words: int
) -> List[Dict[str, Any]]:
    """Split oversized markdown into chunks, strictly preserving code blocks and with zero text dropped."""
    slot_nonce = uuid.uuid4().hex[:8]
    code_blocks = []
    def save_code(m):
        full_cb = m.group(0)
        if len(full_cb) > 2200:
            sub_cbs = _split_oversized_code_block(full_cb, max_words=max_chunk_words, max_chars=2200)
            slot_tags = []
            for scb in sub_cbs:
                code_blocks.append(scb)
                slot_tags.append(f"__CB_SLOT_{slot_nonce}_{len(code_blocks)-1}__")
            return "\n\n".join(slot_tags)
        else:
            code_blocks.append(full_cb)
            return f"__CB_SLOT_{slot_nonce}_{len(code_blocks)-1}__"

    masked = CODE_BLOCK_PATTERN.sub(save_code, text)

    def restore_code(t: str) -> str:
        return re.sub(
            rf'__CB_SLOT_{slot_nonce}_(\d+)__',
            lambda m: code_blocks[int(m.group(1))] if int(m.group(1)) < len(code_blocks) else m.group(0),
            t
        )

    # Subdivide masked text into pieces that respect max_chunk_words and 2200 chars
    pieces = _subdivide_text_boundaries(masked, max_words=max_chunk_words, max_chars=2200)

    chunks = []
    for piece in pieces:
        chunk_body = restore_code(piece)

        # In case restoring code blocks expands chunk_body over 2200 chars:
        if len(chunk_body) > 2200:
            cb_matches = list(CODE_BLOCK_PATTERN.finditer(chunk_body))
            is_single_isolated_cb = (
                len(cb_matches) == 1
                and chunk_body[:cb_matches[0].start()].strip() == ""
                and chunk_body[cb_matches[0].end():].strip() == ""
            )

            if is_single_isolated_cb:
                cb_sub_pieces = _split_oversized_code_block(chunk_body, max_words=max_chunk_words, max_chars=2200)
                for sub_p in cb_sub_pieces:
                    sub_p_clean = sub_p.strip()
                    if not sub_p_clean:
                        continue
                    w_sub = len(sub_p_clean.split())
                    chunks.append({
                        "heading": title,
                        "content": sub_p_clean,
                        "word_count": w_sub
                    })
            elif not cb_matches:
                # No code blocks: pure oversized prose
                for sub_p in _subdivide_text_boundaries(chunk_body, max_words=max_chunk_words, max_chars=2200):
                    sub_p_clean = sub_p.strip()
                    if not sub_p_clean:
                        continue
                    w_sub = len(sub_p_clean.split())
                    chunks.append({
                        "heading": title,
                        "content": sub_p_clean,
                        "word_count": w_sub
                    })
            else:
                # Multiple code blocks or mixed prose and code blocks:
                # Partition at fence boundaries so prose is never wrapped in code fences
                segments: List[Tuple[bool, str]] = []
                last_pos = 0
                for m in cb_matches:
                    prose_before = chunk_body[last_pos:m.start()].strip()
                    if prose_before:
                        segments.append((False, prose_before))
                    segments.append((True, m.group(0).strip()))
                    last_pos = m.end()
                prose_after = chunk_body[last_pos:].strip()
                if prose_after:
                    segments.append((False, prose_after))

                for is_code, seg in segments:
                    if is_code:
                        if len(seg) > 2200:
                            sub_cbs = _split_oversized_code_block(seg, max_words=max_chunk_words, max_chars=2200)
                            for scb in sub_cbs:
                                scb_clean = scb.strip()
                                if scb_clean:
                                    chunks.append({
                                        "heading": title,
                                        "content": scb_clean,
                                        "word_count": len(scb_clean.split())
                                    })
                        else:
                            chunks.append({
                                "heading": title,
                                "content": seg,
                                "word_count": len(seg.split())
                            })
                    else:
                        if len(seg) > 2200 or len(seg.split()) > max_chunk_words:
                            sub_prose = _subdivide_text_boundaries(seg, max_words=max_chunk_words, max_chars=2200)
                            for sp in sub_prose:
                                sp_clean = sp.strip()
                                if sp_clean:
                                    chunks.append({
                                        "heading": title,
                                        "content": sp_clean,
                                        "word_count": len(sp_clean.split())
                                    })
                        else:
                            chunks.append({
                                "heading": title,
                                "content": seg,
                                "word_count": len(seg.split())
                            })
        else:
            p_clean = chunk_body.strip()
            if not p_clean:
                continue
            w = len(p_clean.split())
            chunks.append({
                "heading": title,
                "content": p_clean,
                "word_count": w
            })

    # If the last chunk is smaller than min_chunk_words and we have multiple chunks:
    if len(chunks) > 1 and chunks[-1]["word_count"] < min_chunk_words:
        last = chunks.pop()
        combined = chunks[-1]["content"] + "\n\n" + last["content"]
        # Merge if it doesn't exceed 2200 chars, otherwise keep as separate chunk
        if len(combined) <= 2200:
            chunks[-1]["content"] = combined
            chunks[-1]["word_count"] = len(combined.split())
        else:
            chunks.append(last)

    return chunks

def chunk_markdown_smart(
    markdown: str,
    max_chunk_words: int = 350,
    min_chunk_words: int = 25
) -> List[Dict[str, Any]]:
    """
    Smart semantic chunker with strict size enforcement:
    1. Preserves code blocks (never splits inside ```).
    2. Respects heading hierarchy.
    3. RECURSIVELY divides oversized sections into sub-chunks.
    4. Deduplicates duplicate/boilerplate passages.
    5. Hard limit: chunks are subdivided so none exceeds 2,200 characters without data loss.
    """
    if not markdown or len(markdown.strip()) < 50:
        return []

    # Strip recommendation widgets, citation footers, and blank academic metadata fields
    cleaned_md = re.sub(
        r'(?i)^#{1,6}\s*(Similar content|Related research|People also read|Recommended articles|More like this|Citations \(\d+\)|References \(\d+\))[\s\S]*?(?=^#{1,6}\s|\Z)',
        '',
        markdown,
        flags=re.MULTILINE
    )
    cleaned_md = re.sub(r'(?i)\b(Received|Accepted|Published|Revised):\s*(\n|$)', '', cleaned_md)
    cleaned_md = re.sub(r'(?i)This site requires JavaScript to run correctly\..*?(?:unblock scripts|\Z)', '', cleaned_md, flags=re.DOTALL)

    headings = extract_headings(cleaned_md)
    raw_chunks = []

    if not headings:
        # Document without headings: subdivide text cleanly into chunks
        pieces = _split_oversized_text(cleaned_md, "Overview", max_chunk_words, min_chunk_words)
        for c in pieces:
            label = _synthesize_label(c["content"], "Overview")
            c["heading"] = label
            raw_chunks.append(c)
    else:
        # Preamble before first heading (DEF-V5-01)
        if headings[0]["start"] > 0:
            preamble = cleaned_md[:headings[0]["start"]].strip()
            if preamble and (len(preamble.split()) >= 5 or len(preamble) >= 30):
                preamble_chunks = _split_oversized_text(preamble, "Overview", max_chunk_words, min_chunk_words)
                raw_chunks.extend(preamble_chunks)

        # Walk sections defined by headings
        for i, h in enumerate(headings):
            start = h["start"]
            end = headings[i + 1]["start"] if i + 1 < len(headings) else len(cleaned_md)
            sec_text = cleaned_md[start:end].strip()
            cleaned_sec = _clean_section_content(sec_text, h["title"])
            w = len(cleaned_sec.split())

            if w <= max_chunk_words and len(cleaned_sec) <= 2200:
                if w >= min_chunk_words or not raw_chunks:
                    raw_chunks.append({
                        "heading": h["title"],
                        "content": cleaned_sec,
                        "word_count": w
                    })
                elif cleaned_sec:
                    # Merge small section into preceding chunk if budget allows, else keep separate
                    combined = raw_chunks[-1]["content"] + "\n\n" + cleaned_sec
                    if len(combined) <= 2200:
                        raw_chunks[-1]["content"] = combined
                        raw_chunks[-1]["word_count"] = len(combined.split())
                    else:
                        raw_chunks.append({
                            "heading": h["title"],
                            "content": cleaned_sec,
                            "word_count": w
                        })
            else:
                # Subdivide oversized section cleanly with zero text loss
                sub_chunks = _split_oversized_text(cleaned_sec, h["title"], max_chunk_words, min_chunk_words)
                raw_chunks.extend(sub_chunks)

    # Deduplicate chunks based on text hash
    seen_hashes = set()
    deduped_chunks = []
    for c in raw_chunks:
        h_norm = c.get("heading", "").lower()
        if any(k in h_norm for k in ("similar content", "people also read", "related research", "more like this")):
            continue
        norm = re.sub(r'\s+', ' ', c["content"]).strip().lower()
        if len(norm) < 40 and len(raw_chunks) > 1:
            continue
        h = hashlib.md5(norm[:300].encode('utf-8')).hexdigest()
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        deduped_chunks.append(c)

    return deduped_chunks

def find_query_passages(markdown: str, query: str, max_chars: int = 1500) -> str:
    """
    Search markdown for the most relevant paragraph(s) containing query keywords.
    Preserves code blocks and Markdown structures without breaking syntax.
    Enforces max_chars limit with clean truncation if necessary.
    """
    if not markdown:
        return ""
    if not query or not query.strip():
        return ""

    query_clean = query.strip()
    # Retain symbols (+, #, $) and short keywords
    q_terms = [w.lower() for w in re.findall(r'[\w\-\+\#\$]+', query_clean) if len(w) > 1 or any(c in w for c in '+#$') or w.isalnum()]

    # Protect code blocks
    slot_nonce = uuid.uuid4().hex[:8]
    code_blocks = []
    def save_code(m):
        code_blocks.append(m.group(0))
        return f"__CB_SLOT_{slot_nonce}_{len(code_blocks)-1}__"

    masked = CODE_BLOCK_PATTERN.sub(save_code, markdown)
    raw_paras = [p.strip() for p in re.split(r'\n{2,}', masked) if p.strip()]

    def restore_code(text: str) -> str:
        return re.sub(
            rf'__CB_SLOT_{slot_nonce}_(\d+)__',
            lambda m: code_blocks[int(m.group(1))] if int(m.group(1)) < len(code_blocks) else m.group(0),
            text
        )

    scored = []
    current_heading = ""
    for idx, raw_p in enumerate(raw_paras):
        first_line = raw_p.split("\n")[0].strip()
        if re.match(r'^#{1,6}\s+', first_line):
            current_heading = first_line

        p_restored = restore_code(raw_p)
        p_lower = p_restored.lower()

        score = 0
        if query_clean.lower() in p_lower:
            score += 25

        if q_terms:
            term_matches = sum(1 for term in q_terms if term in p_lower)
            if term_matches > 0:
                score += term_matches * 5
                freq = sum(p_lower.count(term) for term in q_terms)
                score += min(freq, 10)
                # Boost technical symbol terms (e.g. C++, C#, $100)
                symbol_matches = sum(1 for term in q_terms if any(c in term for c in '+#$') and term in p_lower)
                score += symbol_matches * 15

        if score > 0:
            lines = [l.strip() for l in raw_p.split("\n") if l.strip()]
            is_bare_heading = all(re.match(r'^#{1,6}\s+', l) for l in lines)
            assoc = [idx]

            # If a heading matched the query, associate it with the following body paragraph
            # so we never return a bare heading with 0 body lines!
            if is_bare_heading:
                next_idx = idx + 1
                has_substantive_body = False
                accumulated_parts = [p_restored]
                orig_level_m = re.match(r'^(#{1,6})\s+', lines[0])
                orig_level = len(orig_level_m.group(1)) if orig_level_m else 1

                while next_idx < len(raw_paras) and not has_substantive_body:
                    next_p = raw_paras[next_idx]
                    next_lines = [l.strip() for l in next_p.split("\n") if l.strip()]
                    non_heading_lines = [l for l in next_lines if not re.match(r'^#{1,6}\s+', l)]

                    first_l = next_lines[0] if next_lines else ""
                    h_m = re.match(r'^(#{1,6})\s+', first_l)
                    if h_m and len(h_m.group(1)) <= orig_level and not non_heading_lines:
                        # Reached sibling or higher-level heading without body text
                        break

                    body_text = restore_code(next_p)
                    accumulated_parts.append(body_text)
                    assoc.append(next_idx)

                    if non_heading_lines:
                        has_substantive_body = True
                        break

                    next_idx += 1

                p_restored = "\n\n".join(accumulated_parts)

            if 40 <= len(p_restored) <= 1500:
                score += 2
            scored.append((score, idx, p_restored, current_heading, assoc))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    top_score = scored[0][0]

    selected_passages = []
    total_len = 0
    seen_indices = set()

    for score, idx, p_text, heading, assoc in scored:
        if idx in seen_indices or any(a in seen_indices for a in assoc):
            continue

        # Filter out low-relevance matches that are less than 50% of the top score
        if len(selected_passages) > 0 and score < top_score * 0.5:
            break

        entry = p_text
        if heading and not p_text.startswith("#") and heading not in p_text:
            entry = f"{heading}\n{p_text}"

        entry_len = len(entry) + 4
        if total_len + entry_len > max_chars and selected_passages:
            break

        selected_passages.append(entry)
        seen_indices.add(idx)
        seen_indices.update(assoc)
        total_len += entry_len

        if total_len >= max_chars * 0.8:
            break

    result = "\n\n---\n\n".join(selected_passages)
    if len(result) > max_chars:
        trunc_msg = f"\n\n... [Content truncated at {max_chars} chars. Use `inspect_vault_doc` with a specific section or query for more.]"
        result = safe_truncate_markdown(result, max_chars, suffix=trunc_msg)

    return result
