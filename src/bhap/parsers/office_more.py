"""XLSX, OpenDocument (ODT/ODS/ODP), EPUB, legacy Office via LibreOffice, and images via OCR."""
import posixpath
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..errors import ParseError
from . import ocr
from .common import block, local, open_zip, read_xml

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_ROWS = 20000


def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref or "")
    if not letters:
        return 0
    value = 0
    for ch in letters.group(0):
        value = value * 26 + ord(ch) - 64
    return value - 1


def parse_xlsx(path):
    with open_zip(path) as archive:
        names = archive.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in read_xml(archive, "xl/sharedStrings.xml").iter(S + "si"):
                shared.append("".join(node.text or "" for node in item.iter(S + "t")))
        workbook = read_xml(archive, "xl/workbook.xml")
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in read_xml(archive, "xl/_rels/workbook.xml.rels").iter(REL + "Relationship"):
                rels[rel.get("Id")] = posixpath.normpath(posixpath.join("xl", rel.get("Target", "").lstrip("/").replace("xl/", "")))
        blocks, warnings, sheets = [], [], 0
        for sheet in workbook.iter(S + "sheet"):
            target = rels.get(sheet.get(R + "id"))
            if not target or target not in names:
                continue
            sheets += 1
            rows = []
            for row in read_xml(archive, target).iter(S + "row"):
                cells = {}
                for cell in row.iter(S + "c"):
                    value = cell.find(S + "v")
                    if cell.get("t") == "inlineStr":
                        text = "".join(node.text or "" for node in cell.iter(S + "t"))
                    elif value is None:
                        continue
                    elif cell.get("t") == "s":
                        index = int(value.text or 0)
                        text = shared[index] if index < len(shared) else ""
                    else:
                        text = value.text or ""
                    cells[_col_index(cell.get("r"))] = text.replace("|", "/").replace("\n", " ").strip()
                if any(cells.values()):
                    width = max(cells) + 1
                    rows.append("| " + " | ".join(cells.get(i, "") for i in range(width)) + " |")
                if len(rows) >= MAX_ROWS:
                    warnings.append(f"sheet {sheet.get('name')}: only the first {MAX_ROWS} rows were indexed")
                    break
            name = sheet.get("name") or f"Sheet{sheets}"
            for start in range(0, len(rows), 40):
                part = rows[:1] + rows[max(1, start):start + 40] if start else rows[:40]
                blocks.append(block("\n".join(part), section=name, kind="table"))
        return {"blocks": blocks, "units": sheets, "unit": "sheet", "warnings": warnings, "parser": "bhap-xlsx/1"}


ODF_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
ODF_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
ODF_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"


def _odf_text(node):
    parts = []
    for child in node.iter():
        tag = local(child.tag)
        if child.text and tag in ("p", "h", "span", "a", "s"):
            parts.append(child.text)
        if tag == "tab":
            parts.append("\t")
        if tag == "line-break":
            parts.append("\n")
        if child.tail and child is not node and tag in ("span", "a", "s", "tab", "line-break"):
            parts.append(child.tail)
    return "".join(parts).strip()


def parse_odf(path, kind):
    with open_zip(path) as archive:
        root = read_xml(archive, "content.xml")
        blocks, stack, units = [], [], 0
        if kind == ".odp":
            for number, page in enumerate(root.iter(ODF_DRAW + "page"), 1):
                units = number
                lines = [_odf_text(p) for p in page.iter() if local(p.tag) in ("p", "h")]
                text = "\n".join(line for line in lines if line)
                if text:
                    blocks.append(block(text, slide=number, section=page.get(ODF_DRAW + "name", "")))
            return {"blocks": blocks, "units": units, "unit": "slide", "warnings": [], "parser": "bhap-odf/1"}
        if kind == ".ods":
            for table in root.iter(ODF_TABLE + "table"):
                units += 1
                rows = []
                for row in table.iter(ODF_TABLE + "table-row"):
                    cells = [_odf_text(c).replace("|", "/") for c in row.iter(ODF_TABLE + "table-cell")]
                    while cells and not cells[-1]:
                        cells.pop()
                    if cells:
                        rows.append("| " + " | ".join(cells) + " |")
                    if len(rows) >= MAX_ROWS:
                        break
                for start in range(0, len(rows), 40):
                    part = rows[:1] + rows[max(1, start):start + 40] if start else rows[:40]
                    blocks.append(block("\n".join(part), section=table.get(ODF_TABLE + "name", ""), kind="table"))
            return {"blocks": blocks, "units": units, "unit": "sheet", "warnings": [], "parser": "bhap-odf/1"}
        buffer = []

        def flush():
            if buffer:
                blocks.append(block("\n".join(buffer), section=" > ".join(t for _, t in stack)))
                buffer.clear()

        body = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}text")
        for node in (body if body is not None else root).iter():
            tag = local(node.tag)
            if node.tag == ODF_TEXT + "h":
                flush()
                level = int(node.get(ODF_TEXT + "outline-level", "1"))
                title = _odf_text(node)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                if title:
                    stack.append((level, title))
                    buffer.append(title)
            elif node.tag == ODF_TEXT + "p":
                text = _odf_text(node)
                if text:
                    buffer.append(text)
            elif tag == "table-row":
                cells = [_odf_text(c) for c in node if local(c.tag) == "table-cell"]
                if any(cells):
                    buffer.append("| " + " | ".join(cells) + " |")
        flush()
        return {"blocks": blocks, "units": 1, "unit": "document", "warnings": [], "parser": "bhap-odf/1"}


def parse_epub(path):
    from .data import parse_html
    with open_zip(path) as archive:
        container = read_xml(archive, "META-INF/container.xml")
        rootfile = next((node.get("full-path") for node in container.iter() if local(node.tag) == "rootfile"), None)
        if not rootfile:
            raise ParseError(f"{path.name}: EPUB has no package document")
        opf = read_xml(archive, rootfile)
        base = posixpath.dirname(rootfile)
        manifest = {item.get("id"): item.get("href") for item in opf.iter() if local(item.tag) == "item"}
        spine = [ref.get("idref") for ref in opf.iter() if local(ref.tag) == "itemref"]
        blocks, chapter = [], 0
        for idref in spine:
            href = manifest.get(idref)
            if not href:
                continue
            member = posixpath.normpath(posixpath.join(base, href))
            if member not in archive.namelist():
                continue
            chapter += 1
            data = archive.read(member).decode("utf-8", "replace")
            for item in parse_html(data):
                item["page"] = chapter
                item["section"] = item["section"] or f"Chapter {chapter}"
                blocks.append(item)
        return {"blocks": blocks, "units": chapter, "unit": "chapter", "warnings": [], "parser": "bhap-epub/1"}


def convert_legacy(path, target_ext, timeout=180):
    """Convert .doc/.ppt/.xls with a local LibreOffice (no network) and return the converted path."""
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        raise ParseError(f"{path.name}: legacy {path.suffix} needs LibreOffice to convert locally "
                         f"(install libreoffice, or save the file as {target_ext})")
    workdir = Path(tempfile.mkdtemp(prefix="bhap-convert-"))
    profile = workdir / "profile"
    try:
        subprocess.run([office, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
                        "--convert-to", target_ext.lstrip("."), "--outdir", str(workdir), str(path)],
                       capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        shutil.rmtree(workdir, ignore_errors=True)
        raise ParseError(f"{path.name}: LibreOffice conversion timed out") from None
    converted = next(iter(workdir.glob(f"*{target_ext}")), None)
    if not converted:
        shutil.rmtree(workdir, ignore_errors=True)
        raise ParseError(f"{path.name}: LibreOffice could not convert the file")
    return converted, workdir


def parse_image(path, settings):
    if not ocr.available_image():
        raise ParseError(f"{path.name}: images need local OCR — install tesseract "
                         "(Arch: pacman -S tesseract tesseract-data-ind tesseract-data-eng)")
    text, conf = ocr.ocr_image(path, settings.get("ocr_languages", "ind+eng"))
    if len(text.strip()) < 3:
        raise ParseError(f"{path.name}: no readable text found in the image (photos/diagrams without text "
                         "cannot be interpreted yet)")
    warnings = ["text recognised with local OCR; verify against the image"]
    if conf is not None and conf < 70:
        warnings.append(f"low OCR confidence ({conf:.0f}%)")
    return {"blocks": [block(text, kind="ocr", ocr_conf=conf, visual=True, page=1)], "units": 1, "unit": "image",
            "warnings": warnings, "parser": "bhap-image/1+tesseract"}
