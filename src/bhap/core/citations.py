"""Locators and citation integrity.

The model never produces page/slide numbers. It may only emit bracket markers like
[2] that index into the evidence list Bhap retrieved; every marker is validated and
translated back to a stored chunk with its parser-derived locator.
"""
import re

from ..i18n import t

# A citation marker is a standalone [n] / [n, m]; `names[1]` or `f(x)[0]` are code, not citations.
MARKER = re.compile(r"(?<![\w)])\[(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\]")
CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.S)
REMOVED = "\x00"


def _segments(text):
    """Split text into (is_code, segment) so citation handling never rewrites code."""
    parts, last = [], 0
    for match in CODE.finditer(text):
        if match.start() > last:
            parts.append((False, text[last:match.start()]))
        parts.append((True, match.group(0)))
        last = match.end()
    if last < len(text):
        parts.append((False, text[last:]))
    return parts


def prose(text):
    """Text with code blocks and inline code removed (for claims/markers)."""
    return "".join(segment for is_code, segment in _segments(text) if not is_code)
SENTENCE = re.compile(r"(?<=[.!?。])\s+(?=[A-Z0-9\"'“(\-•*])|\n+")


def _move_marker(match):
    """'claim. [1] Next' -> 'claim [1]. Next' (keeps the whitespace that separated sentences)."""
    markers = match.group(2)
    tail = markers[len(markers.rstrip()):]
    return " " + markers.strip() + match.group(1) + (tail or "")


def locator(row):
    """Human-readable location from chunk metadata (never from generated text)."""
    keys = row.keys() if hasattr(row, "keys") else row

    def get(key):
        return row[key] if key in keys else None

    parts = []
    if get("slide_no"):
        parts.append(t("loc.slide", n=get("slide_no")))
    elif get("page_no"):
        parts.append(t("loc.page_approx" if get("approx") else "loc.page", n=get("page_no")))
    elif get("line_no"):
        parts.append(t("loc.line", n=get("line_no")))
    kind = get("kind")
    if kind == "notes":
        parts.append(t("loc.footnotes") if get("section_path") in ("Footnotes", "Endnotes") else t("loc.notes"))
    elif kind == "table":
        parts.append(t("loc.table"))
    elif kind == "ocr" and get("ocr_conf") is not None:
        parts.append(t("loc.ocr", conf=int(get("ocr_conf"))))
    section = get("section_path") or ""
    if section and section not in ("Footnotes", "Endnotes") and not get("slide_no"):
        parts.append("§ " + section)
    elif section and get("slide_no") and kind != "notes":
        parts.append("“" + section[:60] + "”")
    return " · ".join(parts) or t("loc.document")


def label(row):
    return f"{row['filename']} — {locator(row)}"


def markers_in(text):
    found = []
    for match in MARKER.finditer(prose(text)):
        for number in re.split(r"\s*[,;]\s*", match.group(1)):
            found.append(int(number))
    return found


def validate(text, evidence_count):
    """Return (clean_text, used_markers_in_order, invalid_markers)."""
    used, invalid = [], []

    def replace(match):
        numbers = [int(n) for n in re.split(r"\s*[,;]\s*", match.group(1))]
        good = [n for n in numbers if 1 <= n <= evidence_count]
        invalid.extend(n for n in numbers if n not in good)
        for number in good:
            if number not in used:
                used.append(number)
        return "[" + ", ".join(str(n) for n in good) + "]" if good else REMOVED

    pieces = []
    for is_code, segment in _segments(text):
        if is_code:
            pieces.append(segment)  # code is returned byte-for-byte
            continue
        segment = MARKER.sub(replace, segment)
        segment = re.sub(r"[ \t]*" + REMOVED, "", segment)  # tidy only where a bad marker was removed
        # "claim. [1]" → "claim [1]." so each marker stays attached to the sentence it supports
        segment = re.sub(r"([.!?])[ \t]*((?:\[\d+(?:, \d+)*\][ \t]*)+)", _move_marker, segment)
        pieces.append(segment)
    return "".join(pieces).strip(), used, sorted(set(invalid))


def evidence_map(text, evidence_count):
    """Split an answer into claims and attach the supporting markers of each."""
    claims = []
    text = CODE.sub("\n", text)  # code blocks are not claims
    text = re.sub(r"([.!?])[ \t]*((?:\[\d+(?:\s*,\s*\d+)*\][ \t]*)+)", _move_marker, text)
    for piece in SENTENCE.split(text):
        piece = (piece or "").strip()
        if len(piece) < 3 or piece.startswith("#") or (piece.endswith(":") and not markers_in(piece)):
            continue  # headings and list intros are structure, not claims
        refs = [n for n in markers_in(piece) if 1 <= n <= evidence_count]
        claim = re.sub(r"\s+([.,;:!?])", r"\1", MARKER.sub("", piece)).strip()
        if claim:
            claims.append({"claim": claim, "sources": sorted(set(refs)), "supported": bool(refs)})
    return claims
