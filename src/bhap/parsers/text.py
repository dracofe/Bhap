"""Markdown and plain-text parsing with heading hierarchy and line locators."""
import re

from .common import block

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SETEXT = re.compile(r"^(=+|-+)\s*$")


def decode(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def parse_markdown(text):
    blocks, stack, buffer = [], [], []  # stack holds (level, title)
    start_line = 1
    in_fence = False
    lines = text.splitlines()

    def flush(next_line):
        nonlocal buffer, start_line
        body = "\n".join(buffer).strip()
        if body:
            blocks.append(block(body, section=" > ".join(title for _, title in stack), line=start_line))
        buffer = []
        start_line = next_line

    for number, raw in enumerate(lines, 1):
        if raw.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            buffer.append(raw)
            continue
        match = None if in_fence else HEADING.match(raw)
        setext = (not in_fence and len(buffer) > 0 and SETEXT.match(raw) is not None
                  and buffer[-1].strip() != "" and not buffer[-1].lstrip().startswith(("-", "*", "|", "#")))
        if match or setext:
            if setext:
                title, level = buffer.pop().strip(), 1 if raw.strip().startswith("=") else 2
                heading_line = number - 1
            else:
                title, level = match.group(2).strip(), len(match.group(1))
                heading_line = number
            flush(heading_line)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            buffer.append("#" * level + " " + title)
            start_line = heading_line
            continue
        buffer.append(raw)
    flush(len(lines) + 1)
    return blocks


def parse_plain(text):
    """Group paragraphs into blocks while remembering the first line of each."""
    blocks, buffer, start = [], [], 1
    lines = text.splitlines()
    for number, raw in enumerate(lines, 1):
        if raw.strip():
            if not buffer:
                start = number
            buffer.append(raw)
        elif buffer:
            blocks.append(block("\n".join(buffer).strip(), line=start))
            buffer = []
    if buffer:
        blocks.append(block("\n".join(buffer).strip(), line=start))
    return blocks


def parse(path):
    text = decode(path.read_bytes()).replace("\r\n", "\n")
    if path.suffix.lower() in {".md", ".markdown"}:
        blocks = parse_markdown(text)
    else:
        blocks = parse_plain(text)
    return {"blocks": blocks, "units": len(text.splitlines()), "unit": "line", "warnings": [],
            "parser": "bhap-text/1"}
