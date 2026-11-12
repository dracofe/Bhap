"""Source code: keep lines verbatim, cut at definition boundaries, remember class > function."""
import re

from .common import block

CONTROL = {"if", "for", "while", "switch", "return", "else", "catch", "do", "try", "case", "elif", "with", "match",
           "foreach", "using", "lock", "sizeof", "new", "throw", "await", "yield"}
TYPE_DEF = re.compile(r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|sealed|export|default|"
                      r"partial|data|open|pub(?:\([^)]*\))?)\s+)*(class|interface|enum|struct|record|trait|impl|object|"
                      r"module|namespace|union)\s+([A-Za-z_][\w:]*)")
FUNC_DEF = re.compile(r"^\s*(?:def|fn|func|function|fun|sub|proc)\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w.]*)")
C_LIKE = re.compile(r"^\s*(?:[\w<>\[\],.*&:?]+\s+)+\**([A-Za-z_]\w*)\s*\([^;{}]*\)?\s*(?:const\s*)?"
                    r"(?:throws\s+[\w.,\s]+)?\s*(?:\{|=>|:)?\s*$")
ARROW = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[^=]*\)?\s*=>")
MAX_LINES = 60
MIN_LINES = 12


def definition(line):
    """Return ('type'|'func', name) when a line starts a class/function definition."""
    match = TYPE_DEF.match(line)
    if match:
        return "type", match.group(2)
    match = FUNC_DEF.match(line) or ARROW.match(line)
    if match:
        return "func", match.group(1)
    match = C_LIKE.match(line)
    if match and match.group(1) not in CONTROL and not line.strip().startswith(("#", "//", "*", "/*", "--")):
        first = line.strip().split()[0].split("(")[0]
        if first not in CONTROL and "=" not in line.split("(")[0]:
            return "func", match.group(1)
    return None


def parse_code(text, name=""):
    lines = text.replace("\r\n", "\n").replace("\t", "    ").split("\n")
    blocks, start, current_type, current_func = [], 0, "", ""
    section_at_start = ""

    def label():
        return " > ".join(part for part in (current_type, current_func) if part)

    def emit(end):
        first = start
        while first < end and not lines[first].strip():
            first += 1  # keep the reported line number on the first real line
        chunk = "\n".join(lines[first:end]).rstrip()
        if chunk.strip():
            blocks.append(block(chunk, line=first + 1, section=section_at_start or label(), kind="code"))

    for index, line in enumerate(lines):
        found = definition(line) if line.strip() else None
        size = index - start
        boundary = found is not None or (not line.strip() and size >= MIN_LINES)
        if size >= MAX_LINES or (boundary and size >= MIN_LINES):
            emit(index)
            start = index
            section_at_start = ""
        if found:
            kind, ident = found
            if kind == "type":
                current_type, current_func = ident, ""
            else:
                current_func = ident
            if index == start or not section_at_start:
                section_at_start = label()
    emit(len(lines))
    return blocks
