"""Structure-aware chunking: never crosses a page, slide, table or section boundary."""
import hashlib
import re

SENT_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


def _windows(text, words, overlap):
    """Split text into word windows, preferring sentence boundaries."""
    sentences = [s for s in SENT_BREAK.split(text) if s and s.strip()]
    pieces, current, count = [], [], 0
    for sentence in sentences:
        tokens = sentence.split()
        if len(tokens) > words:  # one huge sentence: hard split
            if current:
                pieces.append(current)
                current, count = [], 0
            step = max(1, words - overlap)
            for start in range(0, len(tokens), step):
                pieces.append([" ".join(tokens[start:start + words])])
                if start + words >= len(tokens):
                    break
            continue
        if count + len(tokens) > words and current:
            pieces.append(current)
            tail, tail_count = [], 0
            for previous in reversed(current):  # carry overlapping sentences
                size = len(previous.split())
                if tail_count + size > overlap:
                    break
                tail.insert(0, previous)
                tail_count += size
            current, count = tail, tail_count
        current.append(sentence.strip())
        count += len(tokens)
    if current:
        pieces.append(current)
    return ["\n".join(piece) if "\n" in text else " ".join(piece) for piece in pieces]


def _table_windows(text, words):
    rows = text.splitlines()
    header = rows[0] if rows else ""
    pieces, current, count = [], [], 0
    for row in rows:
        size = len(row.split())
        if current and count + size > words:
            pieces.append("\n".join(current))
            current, count = [header], len(header.split())
        current.append(row)
        count += size
    if current:
        pieces.append("\n".join(current))
    return pieces


def _code_windows(text, budget, overlap_lines=3):
    """Split code by whole lines (formatting preserved); yields (line_offset, text)."""
    lines = text.split("\n")
    if len(text.split()) <= budget * 1.4:
        yield 0, text
        return
    start = 0
    while start < len(lines):
        count, end = 0, start
        while end < len(lines) and (count + len(lines[end].split()) <= budget or end == start):
            count += len(lines[end].split())
            end += 1
        piece = "\n".join(lines[start:end])
        if piece.strip():
            yield start, piece
        if end >= len(lines):
            break
        start = max(start + 1, end - overlap_lines)


def chunk(blocks, words=220, overlap=40):
    """Yield chunk dicts carrying the block's locator metadata."""
    ordinal = 0
    small = None  # merge consecutive tiny text blocks of the same section/page

    def emit(block, text, line=None):
        nonlocal ordinal
        ordinal += 1
        clean = text.strip("\n") if block.get("kind") == "code" else text.strip()
        return {"ordinal": ordinal, "text": clean, "page_no": block.get("page"), "slide_no": block.get("slide"),
                "line_no": line or block.get("line"), "section_path": block.get("section") or "",
                "kind": block.get("kind", "text"), "approx": bool(block.get("approx")),
                "visual": bool(block.get("visual")), "ocr_conf": block.get("ocr_conf"),
                "text_hash": hashlib.sha256(clean.encode("utf-8")).hexdigest(),
                "token_count": int(len(clean.split()) * 1.3) + 1}

    def same_place(a, b):
        return (a.get("page"), a.get("slide"), a.get("section"), a.get("kind")) == \
               (b.get("page"), b.get("slide"), b.get("section"), b.get("kind"))

    for block in blocks:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        if block.get("kind") == "text" and not block.get("slide") and len(text.split()) < words // 4:
            if small and same_place(small, block) and len((small["text"] + " " + text).split()) <= words:
                small["text"] += "\n" + text
                continue
            if small:
                yield emit(small, small["text"])
            small = dict(block, text=text)
            continue
        if small:
            yield emit(small, small["text"])
            small = None
        if block.get("kind") == "table":
            for piece in _table_windows(text, words):
                yield emit(block, piece)
        elif block.get("kind") == "code":
            for offset, piece in _code_windows(block.get("text") or "", int(words * 1.5)):
                yield emit(block, piece, (block.get("line") or 1) + offset)
        else:
            for piece in _windows(text, words, overlap):
                if piece.strip():
                    yield emit(block, piece)
    if small:
        yield emit(small, small["text"])
