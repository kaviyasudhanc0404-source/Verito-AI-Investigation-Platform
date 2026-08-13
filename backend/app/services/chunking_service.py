"""
services/chunking_service.py
------------------------------
Structure-aware semantic chunker for evidence processing.

Strategy (Rule-based, deterministic — recommended for VERITO):
  Use the structural markers already present in Docling's Markdown output
  (headings, blank-line-separated paragraphs) to split text into logical
  chunks that respect semantic boundaries.

  Priority order:
    1. Section boundaries  — Markdown headings (#, ##, ###) or ALL-CAPS lines
                             or [Label] markers (e.g. [Page 1], [Sheet: X])
    2. Paragraph boundaries — blank lines between blocks of text
    3. Sentence boundaries  — fallback when a paragraph exceeds max_chunk_chars

  This directly solves the problems seen in the previous chunking output:
    ✅ No more mid-word / mid-sentence cuts
    ✅ No more cross-section bleeding (Education mixed with Projects etc.)
    ✅ Semantically coherent chunks → better embeddings → better RAG retrieval
    ✅ No duplicate chunks (each section is processed once)

Chunk size settings:
  MAX_CHUNK_CHARS  = 1200  characters  (≈ 250-300 tokens — fits BGE context window)
  MIN_CHUNK_CHARS  =   80  characters  (discard micro-fragments)
  OVERLAP_SENTENCES =   1  sentence    (carried across paragraph splits only)
"""

from __future__ import annotations

import re

# ── Configuration ────────────────────────────────────────────────────────────
MAX_CHUNK_CHARS = 1200    # Maximum characters per chunk
MIN_CHUNK_CHARS = 80      # Minimum characters — shorter chunks are discarded
OVERLAP_SENTENCES = 1     # Sentences to overlap at paragraph split boundaries


# ── Heading / section-boundary detection ────────────────────────────────────

# Markdown headings: # Title, ## Title, ### Title
_HEADING = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)

# Docling sometimes emits headings as bullet items, e.g. "- # Project objective".
_BOUNDARY_PREFIX = re.compile(r"^(?:[-*]\s+|\d+[.)]\s+)+")

# [Label] markers produced by parsers: [Page 1], [Sheet: X], [Image Evidence: ...]
_LABEL_MARKER = re.compile(r"^\[.+\]$", re.MULTILINE)

# ALL-CAPS section headers (≥ 3 uppercase words, no lowercase)
# e.g.  CAREER OBJECTIVE  |  WORK EXPERIENCE  |  CERTIFICATIONS
_ALLCAPS_HEADER = re.compile(r"^[A-Z][A-Z\s\-/&]{4,}$", re.MULTILINE)

# Sentence-ending punctuation (used in overlap logic)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


# ── Public API ────────────────────────────────────────────────────────────────

def chunk_text(
    text: str,
    max_chars: int = MAX_CHUNK_CHARS,
    min_chars: int = MIN_CHUNK_CHARS,
) -> list[str]:
    """
    Split text into semantically coherent chunks using structure-aware rules.

    Algorithm:
      1. Split on section headings / label markers → logical sections
      2. Within each section, split on paragraph boundaries (blank lines)
      3. If a paragraph exceeds max_chars, split on sentence boundaries
      4. Accumulate paragraphs into a chunk until max_chars is reached
      5. Discard chunks shorter than min_chars (header-only fragments)

    Args:
        text:      Full cleaned text (Markdown from Docling or any parser).
        max_chars: Maximum characters per chunk. Default: 1200.
        min_chars: Minimum characters — shorter chunks are dropped. Default: 80.

    Returns:
        List of non-empty, semantically coherent text chunks.
    """
    if not text or not text.strip():
        return []

    # Step 1: Split into top-level sections on heading / marker boundaries
    sections = _split_into_sections(text)

    chunks: list[str] = []

    for section_header, section_body in sections:
        # Each section is processed independently — no cross-section bleeding
        section_chunks = _chunk_section(section_header, section_body, max_chars)
        chunks.extend(section_chunks)

    # Step 5: Filter out micro-chunks (header-only or near-empty)
    chunks = [c for c in chunks if len(c.strip()) >= min_chars]

    return chunks


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_section_boundary(line: str) -> bool:
    """
    Return True if `line` starts a new logical section.

    Recognises:
      - Markdown headings:   # Title, ## Title
      - Parser label markers: [Page 1], [Sheet: X], [Document: ...]
      - ALL-CAPS section headers: EDUCATION, WORK EXPERIENCE
    """
    stripped = line.strip()
    if not stripped:
        return False

    boundary_text = _BOUNDARY_PREFIX.sub("", stripped).strip()

    if _HEADING.match(boundary_text):
        return True
    if _LABEL_MARKER.match(boundary_text):
        return True
    if _ALLCAPS_HEADER.match(boundary_text) and len(boundary_text) >= 5:
        return True
    if _looks_like_ppt_title(boundary_text):
        return True
    return False


def _section_header_text(line: str) -> str:
    """Normalize section boundary text before storing it as the chunk header."""
    stripped = line.strip()
    boundary_text = _BOUNDARY_PREFIX.sub("", stripped).strip()
    return boundary_text or stripped


def _looks_like_ppt_title(line: str) -> bool:
    """Detect short standalone slide titles missed by Docling markdown."""
    if not line or len(line) > 60:
        return False
    if any(ch in line for ch in "@/:\\"):
        return False
    if re.search(r"\d", line):
        return False
    if line.endswith((".", ",", ";", ":")):
        return False

    words = re.findall(r"[A-Za-z][A-Za-z\-]*", line)
    if not 1 <= len(words) <= 5:
        return False

    capitalized = sum(1 for word in words if word[0].isupper())
    return capitalized / len(words) >= 0.5


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """
    Split text into (header, body) tuples on section boundaries.

    Returns a list of (section_header, section_body) pairs.
    The header is the boundary line itself; body is everything until the next
    boundary. If the text has no headings, the entire text is one section.

    Args:
        text: Full cleaned text.

    Returns:
        List of (header, body) string tuples.
    """
    lines = text.split("\n")
    sections: list[tuple[str, str]] = []

    current_header = ""
    current_body_lines: list[str] = []

    for line in lines:
        if _is_section_boundary(line):
            # Flush accumulated body as a section
            if current_body_lines or current_header:
                body = "\n".join(current_body_lines).strip()
                sections.append((current_header, body))
            # Start a new section
            current_header = _section_header_text(line)
            current_body_lines = []
        else:
            current_body_lines.append(line)

    # Flush the final section
    if current_body_lines or current_header:
        body = "\n".join(current_body_lines).strip()
        sections.append((current_header, body))

    # Edge case: no sections found — treat entire text as one section
    if not sections:
        sections = [("", text.strip())]

    return sections


def _chunk_section(
    header: str,
    body: str,
    max_chars: int,
) -> list[str]:
    """
    Chunk a single section (header + body) into max_chars-sized pieces.

    Strategy:
      - Split body on paragraph boundaries (blank lines)
      - Accumulate paragraphs into a chunk until max_chars is exceeded
      - Prepend the section header to the first chunk of each section
      - If a single paragraph exceeds max_chars, split it on sentences

    Args:
        header:    Section heading line (may be empty string).
        body:      Section body text.
        max_chars: Maximum characters per chunk.

    Returns:
        List of chunk strings for this section.
    """
    if not body and not header:
        return []

    # If body is empty but header exists (e.g. standalone heading), keep it
    if not body:
        return [header] if header else []

    # Split on paragraph boundaries (one or more blank lines)
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]

    if not paragraphs:
        return [header] if header else []

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len: int = 0
    is_first_chunk = True

    def _flush() -> None:
        nonlocal current_parts, current_len, is_first_chunk
        if not current_parts:
            return
        chunk_body = "\n\n".join(current_parts)
        if is_first_chunk and header:
            chunk_text_val = f"{header}\n\n{chunk_body}"
            is_first_chunk = False
        else:
            chunk_text_val = chunk_body
        chunks.append(chunk_text_val.strip())
        current_parts = []
        current_len = 0

    for para in paragraphs:
        para_len = len(para)

        if para_len > max_chars:
            # Paragraph is too large — split it on sentence boundaries
            _flush()
            sentence_chunks = _split_paragraph_by_sentences(
                para, max_chars, header if is_first_chunk else ""
            )
            if sentence_chunks and is_first_chunk:
                is_first_chunk = False
            chunks.extend(sentence_chunks)
            continue

        # Will adding this paragraph exceed the limit?
        projected = current_len + para_len + (2 if current_parts else 0)

        if projected > max_chars and current_parts:
            # Flush current chunk, then start fresh with this paragraph
            _flush()

        current_parts.append(para)
        current_len += para_len + (2 if len(current_parts) > 1 else 0)

    # Flush any remaining content
    _flush()

    return chunks


def _split_paragraph_by_sentences(
    para: str,
    max_chars: int,
    header: str = "",
) -> list[str]:
    """
    Split a single oversized paragraph into sentence-boundary chunks.

    Adds OVERLAP_SENTENCES sentences from the end of each chunk to the
    start of the next, so boundary context is not lost.

    Args:
        para:      The paragraph text to split.
        max_chars: Maximum characters per chunk.
        header:    Section header to prepend to the first chunk.

    Returns:
        List of sentence-level chunk strings.
    """
    # Split on sentence-ending punctuation
    sentences = [s.strip() for s in _SENTENCE_END.split(para) if s.strip()]

    if not sentences:
        return [para] if para.strip() else []

    chunks: list[str] = []
    current_sentences: list[str] = []
    current_len = 0
    is_first = True

    for sentence in sentences:
        sent_len = len(sentence)

        if current_len + sent_len > max_chars and current_sentences:
            # Flush current chunk
            chunk_body = " ".join(current_sentences)
            if is_first and header:
                chunks.append(f"{header}\n\n{chunk_body}".strip())
                is_first = False
            else:
                chunks.append(chunk_body.strip())

            # Overlap: carry last OVERLAP_SENTENCES into next chunk
            overlap = current_sentences[-OVERLAP_SENTENCES:]
            current_sentences = overlap
            current_len = sum(len(s) for s in current_sentences)

        current_sentences.append(sentence)
        current_len += sent_len + 1  # +1 for space

    # Flush remainder
    if current_sentences:
        chunk_body = " ".join(current_sentences)
        if is_first and header:
            chunks.append(f"{header}\n\n{chunk_body}".strip())
        else:
            chunks.append(chunk_body.strip())

    return [c for c in chunks if c.strip()]


# ── Legacy fallback (kept for compatibility, not used in main pipeline) ───────

def chunk_text_by_sentence(
    text: str,
    max_chunk_size: int = 512,
    overlap_sentences: int = 1,
) -> list[str]:
    """
    Legacy sentence-boundary chunker.
    Kept for backward compatibility — use chunk_text() for new code.

    Args:
        text:             The full cleaned text.
        max_chunk_size:   Maximum characters per chunk.
        overlap_sentences: Sentences to carry between chunks.

    Returns:
        List of non-empty text chunks.
    """
    if not text or not text.strip():
        return []

    sentences = [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]
    if not sentences:
        return []

    chunks: list[str] = []
    current_sentences: list[str] = []
    current_len = 0

    for sentence in sentences:
        if current_len + len(sentence) > max_chunk_size and current_sentences:
            chunks.append(" ".join(current_sentences))
            current_sentences = current_sentences[-overlap_sentences:]
            current_len = sum(len(s) for s in current_sentences)

        current_sentences.append(sentence)
        current_len += len(sentence)

    if current_sentences:
        chunks.append(" ".join(current_sentences))

    return [c for c in chunks if c.strip()]
