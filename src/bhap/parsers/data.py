"""CSV/TSV, JSON(L), HTML/XML, Jupyter notebooks and RTF — all parsed locally from text."""
import csv
import io
import json
import re
from html.parser import HTMLParser

from ..errors import ParseError
from .code import parse_code
from .common import block
from .text import parse_markdown

MAX_ROWS = 20000


def parse_table(text, delimiter):
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [row for row in rows if any(cell.strip() for cell in row)][:MAX_ROWS]
    if not rows:
        return []
    clean = ["| " + " | ".join(cell.strip().replace("|", "/").replace("\n", " ") for cell in row) + " |" for row in rows]
    header, body = clean[0], clean[1:]
    blocks = []
    for start in range(0, max(len(body), 1), 40):  # header repeated in every slice
        part = [header] + body[start:start + 40]
        blocks.append(block("\n".join(part), kind="table", line=start + 2 if body else 1))
    return blocks


def parse_json(text, name):
    stripped = text.strip()
    if name.endswith((".jsonl", ".ndjson")) or ("\n{" in stripped and not stripped.startswith("[")):
        lines = [line for line in stripped.splitlines() if line.strip()]
        try:
            pretty = "\n".join(json.dumps(json.loads(line), ensure_ascii=False) for line in lines[:MAX_ROWS])
        except ValueError:
            pretty = stripped
    else:
        try:
            pretty = json.dumps(json.loads(stripped), ensure_ascii=False, indent=2)
        except ValueError:
            pretty = stripped  # keep invalid JSON as text rather than failing
    return parse_code(pretty, name)


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCKS = {"p", "div", "li", "tr", "br", "section", "article", "header", "footer", "pre", "blockquote", "dd", "dt",
              "figcaption", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.buffer, self.stack, self.skip, self.title = [], [], [], 0, ""
        self.heading = None

    def flush(self):
        text = re.sub(r"[ \t]+", " ", "".join(self.buffer))
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if text:
            self.blocks.append(block(text, section=" > ".join(title for _, title in self.stack)))
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.heading = (int(tag[1]), [])
        elif tag in ("td", "th"):
            self.buffer.append(" | ")
        elif tag in self.BLOCKS:
            self.buffer.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif re.fullmatch(r"h[1-6]", tag) and self.heading:
            level, parts = self.heading
            title = " ".join("".join(parts).split())
            self.heading = None
            if title:
                while self.stack and self.stack[-1][0] >= level:
                    self.stack.pop()
                self.stack.append((level, title))
                self.buffer.append(title + "\n")
        elif tag in self.BLOCKS:
            self.buffer.append("\n")

    def handle_data(self, data):
        if self.skip:
            if self.lasttag == "title":
                self.title += data
            return
        if self.heading:
            self.heading[1].append(data)
        else:
            self.buffer.append(data)


def parse_html(text):
    parser = _HTMLText()
    parser.feed(text)
    parser.close()
    parser.flush()
    return parser.blocks


def parse_xml(text):
    if "<!DOCTYPE" in text[:4096] or "<!ENTITY" in text[:4096]:
        if re.search(r"<!DOCTYPE\s+html", text[:4096], re.I):
            return parse_html(text)
        raise ParseError("XML declares a DTD/entities; refusing (XXE defense)")
    if re.search(r"<html[\s>]", text[:4096], re.I):
        return parse_html(text)
    plain = re.sub(r"<[^>]+>", lambda m: "\n" if m.group(0).startswith("</") else " ", text)
    plain = "\n".join(" ".join(line.split()) for line in plain.splitlines() if line.strip())
    return [block(plain, kind="text")] if plain else []


def parse_notebook(text):
    try:
        notebook = json.loads(text)
    except ValueError as exc:
        raise ParseError(f"invalid notebook JSON: {exc}") from exc
    language = (notebook.get("metadata", {}).get("kernelspec", {}) or {}).get("language", "python")
    blocks = []
    for index, cell in enumerate(notebook.get("cells", []), 1):
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        if not source.strip():
            continue
        if cell.get("cell_type") == "markdown":
            for item in parse_markdown(source):
                item["section"] = f"Cell {index}" + (f" > {item['section']}" if item["section"] else "")
                blocks.append(item)
        else:
            for item in parse_code(source):
                item["section"] = f"Cell {index} ({language})" + (f" > {item['section']}" if item["section"] else "")
                blocks.append(item)
            outputs = []
            for out in cell.get("outputs", []):
                data = out.get("text") or (out.get("data") or {}).get("text/plain") or ""
                outputs.append("".join(data) if isinstance(data, list) else str(data))
            if "".join(outputs).strip():
                blocks.append(block("\n".join(outputs).strip()[:4000], section=f"Cell {index} output", kind="text"))
    return blocks


def parse_rtf(text):
    text = re.sub(r"\{\\\*[^{}]*\}", "", text)                       # destination groups
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", "ignore"), text)
    text = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), text)
    text = re.sub(r"\\(par|line|row)\b ?", "\n", text)
    text = re.sub(r"\\cell\b ?", " | ", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = re.sub(r"\\([{}\\])", r"\1", text).replace("{", "").replace("}", "")
    paragraphs = [" ".join(p.split()) for p in text.split("\n")]
    body = "\n".join(p for p in paragraphs if p)
    return [block(body)] if body else []
