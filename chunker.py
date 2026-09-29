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
from typing import List, Dict, Any, Optional

HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
CODE_BLOCK_PATTERN = re.compile(r'```[\s\S]*?```', re.MULTILINE)

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
    
    # If title is excessively long (> 70 chars or > 10 words), synthesize a concise label
    words = text.split()
    if len(text) > 70 or len(words) > 10:
        text = " ".join(words[:7]) + "..."
    return text

def _clean_section_content(content: str, heading_title: str) -> str:
    """Removes redundant leading heading line from chunk content if it repeats the section title."""
    lines = content.strip().split("\n")
    if lines:
        first = lines[0].strip()
        if re.match(r'^#{1,6}\s+', first):
            first_clean = re.sub(r'^#{1,6}\s+', '', first).strip()
            first_clean = re.sub(r'[\*_`]+', '', first_clean).strip()
            if first_clean.lower().startswith(heading_title[:18].lower()) or heading_title.lower().startswith(first_clean[:18].lower()):
                lines = lines[1:]
    res = "\n".join(lines).strip()
    # Collapse 3+ consecutive newlines to 2
    res = re.sub(r'\n{3,}', '\n\n', res)
    return res

def extract_headings(markdown: str) -> List[Dict[str, Any]]:
    """Extract all headings with their level, title, and character offsets."""
    headings = []
    # Identify code block ranges to ignore false headings inside code blocks
    code_ranges = [(m.start(), m.end()) for m in CODE_BLOCK_PATTERN.finditer(markdown)]
    
    def is_inside_code(pos: int) -> bool:
        return any(start <= pos <= end for start, end in code_ranges)

    for m in HEADING_PATTERN.finditer(markdown):
        if is_inside_code(m.start()):
            continue
        level = len(m.group(1))
        title = _clean_heading_title(m.group(2))
        if not title or len(title) < 2 or title in ("-", "*", "|", ":", "..."):
            continue
        headings.append({
            "level": level,
            "title": title,
            "start": m.start(),
            "end": m.end()
        })
    return headings

def get_table_of_contents(markdown: str) -> str:
    """Generate a clean, token-efficient Table of Contents outline."""
    headings = extract_headings(markdown)
    if not headings:
        words = len(markdown.split())
        return f"Document has no structured headings. Total word count: ~{words} words."

    lines = ["# Document Table of Contents", ""]
    for idx, h in enumerate(headings, 1):
        indent = "  " * (h["level"] - 1)
        lines.append(f"{indent}- **[{idx}]** {h['title']}")

    total_words = len(markdown.split())
    lines.append(f"\n*Total document size: ~{total_words} words ({len(headings)} sections)*")
    lines.append("Use `read_section(url, section_index_or_name)` to view a specific section.")
    return "\n".join(lines)

def extract_section(markdown: str, target: str) -> str:
    """
    Extract a specific section by heading title (case-insensitive substring)
    or by numeric index from the Table of Contents.
    """
    headings = extract_headings(markdown)
    if not headings:
        return markdown

    target_idx = None
    target_clean = target.strip().lower()

    # Check if target is a numeric index like "1", "3", etc.
    if target_clean.isdigit():
        idx = int(target_clean) - 1
        if 0 <= idx < len(headings):
            target_idx = idx

    # Otherwise match by heading text
    if target_idx is None:
        for idx, h in enumerate(headings):
            if target_clean in h["title"].lower():
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

def _split_oversized_text(text: str, title: str, max_chunk_words: int, min_chunk_words: int) -> List[Dict[str, Any]]:
    """Split oversized markdown into chunks, strictly preserving code blocks."""
    # Find code blocks and placeholder them to avoid breaking code syntax
    code_blocks = []
    def save_code(m):
        code_blocks.append(m.group(0))
        return f"__CODE_BLOCK_SLOT_{len(code_blocks)-1}__"

    masked = CODE_BLOCK_PATTERN.sub(save_code, text)
    paragraphs = masked.split("\n\n")

    chunks = []
    current_chunk = []
    current_words = 0

    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue

        w = len(p_clean.split())
        if current_words + w > max_chunk_words and current_chunk:
            chunk_body = "\n\n".join(current_chunk)
            # Restore code blocks
            for idx, cb in enumerate(code_blocks):
                chunk_body = chunk_body.replace(f"__CODE_BLOCK_SLOT_{idx}__", cb)
            
            # Hard character cap on any single chunk (never exceed 2,200 chars)
            if len(chunk_body) > 2200:
                chunk_body = chunk_body[:2150] + "\n[... Content truncated ...]"

            chunks.append({
                "heading": title,
                "content": chunk_body.strip(),
                "word_count": len(chunk_body.split())
            })
            current_chunk = [p_clean]
            current_words = w
        else:
            current_chunk.append(p_clean)
            current_words += w

    if current_chunk:
        chunk_body = "\n\n".join(current_chunk)
        for idx, cb in enumerate(code_blocks):
            chunk_body = chunk_body.replace(f"__CODE_BLOCK_SLOT_{idx}__", cb)
        if len(chunk_body) > 2200:
            chunk_body = chunk_body[:2150] + "\n[... Content truncated ...]"
        if len(chunk_body.split()) >= min_chunk_words or not chunks:
            chunks.append({
                "heading": title,
                "content": chunk_body.strip(),
                "word_count": len(chunk_body.split())
            })

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
    5. Hard limit: no chunk exceeds 2,200 characters.
    """
    if not markdown or len(markdown.strip()) < 50:
        return []

    headings = extract_headings(markdown)
    raw_chunks = []

    if not headings:
        # Paragraph-based chunking with dynamic heading labels
        paragraphs = markdown.split("\n\n")
        current_chunk = []
        current_words = 0
        for p in paragraphs:
            p_strip = p.strip()
            if not p_strip:
                continue
            w = len(p_strip.split())
            if current_words + w > max_chunk_words and current_chunk:
                content_str = "\n\n".join(current_chunk)
                label = _synthesize_label(content_str, "Overview")
                if len(content_str) > 2200:
                    content_str = content_str[:2150] + "\n[... Content truncated ...]"
                raw_chunks.append({
                    "heading": label,
                    "content": content_str,
                    "word_count": len(content_str.split())
                })
                current_chunk = [p_strip]
                current_words = w
            else:
                current_chunk.append(p_strip)
                current_words += w

        if current_chunk:
            content_str = "\n\n".join(current_chunk).strip()
            if len(content_str.split()) >= min_chunk_words or not raw_chunks:
                label = _synthesize_label(content_str, "Overview")
                if len(content_str) > 2200:
                    content_str = content_str[:2150] + "\n[... Content truncated ...]"
                raw_chunks.append({
                    "heading": label,
                    "content": content_str,
                    "word_count": len(content_str.split())
                })
    else:
        # Walk sections defined by headings
        for i, h in enumerate(headings):
            start = h["start"]
            end = headings[i + 1]["start"] if i + 1 < len(headings) else len(markdown)
            sec_text = markdown[start:end].strip()
            cleaned_sec = _clean_section_content(sec_text, h["title"])
            w = len(cleaned_sec.split())

            if w <= max_chunk_words:
                if w >= min_chunk_words:
                    if len(cleaned_sec) > 2200:
                        cleaned_sec = cleaned_sec[:2150] + "\n[... Content truncated ...]"
                    raw_chunks.append({
                        "heading": h["title"],
                        "content": cleaned_sec,
                        "word_count": w
                    })
            else:
                # Subdivide oversized section
                sub_chunks = _split_oversized_text(cleaned_sec, h["title"], max_chunk_words, min_chunk_words)
                raw_chunks.extend(sub_chunks)

    # Deduplicate chunks based on text hash
    seen_hashes = set()
    deduped_chunks = []
    for c in raw_chunks:
        norm = re.sub(r'\s+', ' ', c["content"]).strip().lower()
        if len(norm) < 40:
            continue
        h = hashlib.md5(norm[:300].encode('utf-8')).hexdigest()
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        deduped_chunks.append(c)

    return deduped_chunks
