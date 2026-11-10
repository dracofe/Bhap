"""DOCX and PPTX parsing straight from OOXML (no macros, links or embedded objects run)."""
import posixpath
import re

from ..errors import ParseError
from .common import block, local, open_zip, read_xml

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


# --------------------------------------------------------------------------- DOCX
def _docx_styles(archive):
    """styleId -> heading level (1-9) for heading/title styles."""
    levels = {}
    if "word/styles.xml" not in archive.namelist():
        return levels
    root = read_xml(archive, "word/styles.xml")
    for style in root.iter(W + "style"):
        style_id = style.get(W + "styleId")
        name_el = style.find(W + "name")
        name = (name_el.get(W + "val") if name_el is not None else "") or ""
        lowered = name.lower()
        level = None
        match = re.match(r"heading\s*(\d)", lowered)
        if match:
            level = int(match.group(1))
        elif lowered in {"title", "judul"}:
            level = 1
        outline = style.find(f"{W}pPr/{W}outlineLvl")
        if level is None and outline is not None:
            level = int(outline.get(W + "val", "9")) + 1
        if style_id and level and level <= 9:
            levels[style_id] = level
    return levels


def _paragraph(element, rendered=False):
    """Return (text, page_breaks, has_visual, is_list, style_id, outline_level).

    When Word stored rendered page breaks we count only those (they already include
    explicit breaks); otherwise explicit page breaks are the best approximation.
    """
    parts, breaks, visual = [], 0, False
    for node in element.iter():
        tag = local(node.tag)
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag == "br":
            if node.get(W + "type") == "page":
                breaks += 0 if rendered else 1
            else:
                parts.append("\n")
        elif tag == "lastRenderedPageBreak":
            breaks += 1 if rendered else 0
        elif tag in {"drawing", "pict", "object"}:
            visual = True
    ppr = element.find(W + "pPr")
    style_id = outline = None
    is_list = False
    if ppr is not None:
        style = ppr.find(W + "pStyle")
        style_id = style.get(W + "val") if style is not None else None
        is_list = ppr.find(W + "numPr") is not None
        level = ppr.find(W + "outlineLvl")
        if level is not None:
            outline = int(level.get(W + "val", "9")) + 1
        if ppr.find(W + "pageBreakBefore") is not None and not rendered:
            breaks += 1
    return "".join(parts).strip(), breaks, visual, is_list, style_id, outline


def _table_text(table):
    rows = []
    for row in table.iter(W + "tr"):
        cells = []
        for cell in row.findall(W + "tc"):
            texts = [_paragraph(p)[0] for p in cell.iter(W + "p")]
            cells.append(" ".join(t for t in texts if t).replace("|", "/"))
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def parse_docx(path):
    with open_zip(path) as archive:
        styles = _docx_styles(archive)
        root = read_xml(archive, "word/document.xml")
        body = root.find(W + "body")
        if body is None:
            raise ParseError(f"{path.name}: DOCX has no body")
        blocks, stack, buffer = [], [], []
        page = 1
        has_render_marks = body.find(f".//{W}lastRenderedPageBreak") is not None
        visual_any = False
        state = {"page": 1, "visual": False}

        def section():
            return " > ".join(title for _, title in stack)

        def flush():
            if buffer:
                blocks.append(block("\n".join(buffer), page=state["page"], section=section(), approx=True,
                                    visual=state["visual"]))
                buffer.clear()
                state["visual"] = False

        def walk(container):
            nonlocal page, visual_any
            for child in container:
                tag = local(child.tag)
                if tag == "sdt":
                    content = child.find(W + "sdtContent")
                    if content is not None:
                        walk(content)
                elif tag == "p":
                    text, breaks, visual, is_list, style_id, outline = _paragraph(child, has_render_marks)
                    if breaks:
                        flush()
                        page += breaks
                        state["page"] = page
                    if visual:
                        state["visual"] = visual_any = True
                    level = styles.get(style_id) or outline
                    if text and level and level <= 6 and len(text) < 200:
                        flush()
                        while stack and stack[-1][0] >= level:
                            stack.pop()
                        stack.append((level, text))
                        buffer.append(text)
                    elif text:
                        buffer.append(("• " if is_list else "") + text)
                elif tag == "tbl":
                    flush()
                    table = _table_text(child)
                    if table:
                        blocks.append(block(table, page=state["page"], section=section(), kind="table", approx=True))

        walk(body)
        flush()
        warnings = []
        for part, label in (("word/footnotes.xml", "Footnotes"), ("word/endnotes.xml", "Endnotes")):
            if part in archive.namelist():
                notes = []
                for note in read_xml(archive, part):
                    if note.get(W + "type") in ("separator", "continuationSeparator"):
                        continue
                    text = " ".join(_paragraph(p)[0] for p in note.iter(W + "p")).strip()
                    if text:
                        notes.append(f"[{note.get(W + 'id')}] {text}")
                if notes:
                    blocks.append(block("\n".join(notes), section=label, kind="notes"))
        if visual_any:
            warnings.append("document contains images/diagrams; visual content may not have been interpreted")
        if page == 1:
            for item in blocks:
                item["page"] = None  # no reliable page information at all
        return {"blocks": blocks, "units": page, "unit": "page~", "warnings": warnings, "parser": "bhap-docx/1"}


# --------------------------------------------------------------------------- PPTX
def _rels(archive, part):
    directory, name = posixpath.split(part)
    rel_path = posixpath.join(directory, "_rels", name + ".rels")
    if rel_path not in archive.namelist():
        return {}
    mapping = {}
    for rel in read_xml(archive, rel_path).iter(REL + "Relationship"):
        if rel.get("TargetMode") == "External":
            continue  # never follow external links
        target = posixpath.normpath(posixpath.join(directory, rel.get("Target", "")))
        mapping[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], target)
    return mapping


def _shape_paragraphs(tx_body):
    lines = []
    for para in tx_body.iter(A + "p"):
        text = "".join(node.text or "" for node in para.iter() if local(node.tag) == "t").strip()
        if text:
            ppr = para.find(A + "pPr")
            level = int(ppr.get("lvl", "0")) if ppr is not None else 0
            lines.append(("  " * level) + text)
    return lines


def _placeholder(shape):
    for node in shape.iter(P + "ph"):
        return node.get("type", "body")
    return None


def _slide_content(root):
    title, body, tables, visual = "", [], [], False
    for shape in root.iter():
        tag = local(shape.tag)
        if tag == "sp" and shape.tag.startswith(P):
            tx = shape.find(P + "txBody")
            if tx is None:
                continue
            lines = _shape_paragraphs(tx)
            if not lines:
                continue
            kind = _placeholder(shape)
            if kind in ("title", "ctrTitle") and not title:
                title = " ".join(line.strip() for line in lines)
            elif kind in ("sldNum", "dt", "ftr"):
                continue
            else:
                body.extend(lines)
        elif tag == "pic":
            visual = True
        elif tag == "graphicFrame":
            table = shape.find(f".//{A}tbl")
            if table is not None:
                rows = []
                for row in table.iter(A + "tr"):
                    cells = [" ".join(_shape_paragraphs(cell)).replace("|", "/") for cell in row.iter(A + "tc")]
                    if any(cells):
                        rows.append("| " + " | ".join(cells) + " |")
                if rows:
                    tables.append("\n".join(rows))
            else:
                visual = True  # chart, SmartArt, embedded object
    return title, body, tables, visual


def parse_pptx(path, include_notes=True):
    with open_zip(path) as archive:
        if "ppt/presentation.xml" not in archive.namelist():
            raise ParseError(f"{path.name}: PPTX has no presentation part")
        pres_rels = _rels(archive, "ppt/presentation.xml")
        presentation = read_xml(archive, "ppt/presentation.xml")
        order = []
        id_list = presentation.find(P + "sldIdLst")
        if id_list is not None:
            for slide in id_list.findall(P + "sldId"):
                rel = pres_rels.get(slide.get(R + "id"))
                if rel and rel[1] in archive.namelist():
                    order.append(rel[1])
        if not order:  # fall back to numeric file order
            order = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
        blocks, visual_only, hidden = [], [], []
        for number, part in enumerate(order, 1):
            root = read_xml(archive, part)
            if root.get("show") == "0":
                hidden.append(number)
            title, body, tables, visual = _slide_content(root)
            text = "\n".join(([title] if title else []) + ["• " + line if not line.startswith("  ") else line
                                                           for line in body])
            if text.strip():
                blocks.append(block(text, slide=number, section=title, visual=visual))
            for table in tables:
                blocks.append(block(table, slide=number, section=title, kind="table"))
            if visual and len(text) < 40 and not tables:
                visual_only.append(number)
            if include_notes:
                for rel_type, target in _rels(archive, part).values():
                    if rel_type == "notesSlide" and target in archive.namelist():
                        notes_root = read_xml(archive, target)
                        lines = []
                        for shape in notes_root.iter(P + "sp"):
                            if _placeholder(shape) in ("sldNum", "sldImg", "hdr", "ftr", "dt"):
                                continue
                            tx = shape.find(P + "txBody")
                            if tx is not None:
                                lines.extend(_shape_paragraphs(tx))
                        if lines:
                            blocks.append(block("\n".join(lines), slide=number, section=title, kind="notes"))
        warnings = []
        if visual_only:
            from .pdf import _ranges
            warnings.append(f"visual content may not have been interpreted on slide(s) {_ranges(visual_only)}")
        if hidden:
            from .pdf import _ranges
            warnings.append(f"hidden slide(s) {_ranges(hidden)} were indexed")
        return {"blocks": blocks, "units": len(order), "unit": "slide", "warnings": warnings,
                "parser": "bhap-pptx/1"}
