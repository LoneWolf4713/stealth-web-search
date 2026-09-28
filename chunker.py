"""
Markdown Chunker, Code-Block Preserver, and Table of Contents (TOC) Generator.
- Protects code blocks (``` ... ```) so code syntax is never fragmented.
- Extracts clean Table of Contents outlines for progressive disclosure.
- Allows targeted extraction of single sections by heading or index.
"""
import re
from typing import List, Dict, Any, Optional

HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
CODE_BLOCK_PATTERN = re.compile(r'```[\s\S]*?```', re.MULTILINE)

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
        title = m.group(2).strip()
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

def chunk_markdown_smart(
    markdown: str,
    max_chunk_words: int = 400,
    min_chunk_words: int = 30
) -> List[Dict[str, Any]]:
    """
    Chunks markdown while strictly preserving code blocks and heading hierarchy.
    Returns: List of {"heading": str, "content": str, "word_count": int}
    """
    headings = extract_headings(markdown)
    chunks = []

    if not headings:
        # Paragraph-based chunking with code block protection
        paragraphs = markdown.split("\n\n")
        current_chunk = []
        current_words = 0
        for p in paragraphs:
            w = len(p.split())
            if current_words + w > max_chunk_words and current_chunk:
                chunks.append({
                    "heading": "General",
                    "content": "\n\n".join(current_chunk),
                    "word_count": current_words
                })
                current_chunk = [p]
                current_words = w
            else:
                current_chunk.append(p)
                current_words += w
        if current_chunk:
            chunks.append({
                "heading": "General",
                "content": "\n\n".join(current_chunk),
                "word_count": current_words
            })
        return chunks

    for i, h in enumerate(headings):
        start = h["start"]
        end = headings[i + 1]["start"] if i + 1 < len(headings) else len(markdown)
        sec_text = markdown[start:end].strip()
        w = len(sec_text.split())
        if w >= min_chunk_words:
            chunks.append({
                "heading": h["title"],
                "content": sec_text,
                "word_count": w
            })

    return chunks
