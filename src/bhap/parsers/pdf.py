"""PDF parsing with page numbers, outline sections, visual detection and optional OCR."""
import re

from ..errors import ParseError
from . import ocr
from .common import block

MIN_TEXT_CHARS = 25


def _outline(reader):
    """Map 0-based page index -> section path using the PDF outline (bookmarks)."""
    entries = []

    def walk(items, level):
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            try:
                page = reader.get_destination_page_number(item)
                title = str(item.title).strip()
            except Exception:  # noqa: BLE001 - malformed outlines are common
                continue
            if title and page is not None and page >= 0:
                entries.append((page, level, title))

    try:
        walk(reader.outline, 0)
    except Exception:  # noqa: BLE001
        return {}
    entries.sort(key=lambda item: item[0])
    mapping, stack, cursor = {}, [], 0
    for index in range(len(reader.pages)):
        while cursor < len(entries) and entries[cursor][0] <= index:
            _, level, title = entries[cursor]
            del stack[level:]
            stack.append(title)
            cursor += 1
        mapping[index] = " > ".join(stack)
    return mapping


GAP = re.compile(r" {3,}")


def clean_layout(text):
    """Turn pypdf layout-mode text into compact lines; keep table rows as `a | b | c`.

    Layout mode preserves table rows (plain mode emits one cell per line, which makes models
    swap columns). Justified prose also has wide gaps, so a line where *every* word is separated
    by a wide gap is treated as prose, not as a table row.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        found = GAP.findall(line)
        tokens = line.split()
        numeric = sum(1 for tok in tokens if re.fullmatch(r"[\d.,%/-]+", tok))
        justified_prose = len(found) == len(tokens) - 1 and len(tokens) >= 5 and numeric * 2 < len(tokens)
        tabular = found and not justified_prose and (
                             len(found) < len(tokens) - 1          # some cells hold several words
                             or max(map(len, found)) >= 5           # column-sized gaps
                             or numeric * 2 >= len(tokens))         # mostly numbers
        if tabular:
            cells = [c.strip() for c in GAP.split(line) if c.strip()]
            plain_words = sum(1 for c in cells if re.fullmatch(r"[a-z\u00C0-\u024F,.;:()-]+", c))
            if len(cells) >= 4 and plain_words / len(cells) >= 0.6:
                tabular = False  # justified prose: cells are ordinary lowercase words
        if tabular:
            line = GAP.sub(" | ", line)
        else:
            line = " ".join(line.split())
        lines.append(line)
    return "\n".join(lines)


def page_text(page):
    """Layout-preserving extraction with a plain-mode fallback."""
    try:
        text = clean_layout(page.extract_text(extraction_mode="layout") or "")
    except Exception:  # noqa: BLE001 - layout mode can fail on odd PDFs
        text = ""
    if len(text.strip()) < MIN_TEXT_CHARS:
        plain = page.extract_text() or ""
        if len(plain.strip()) > len(text.strip()):
            return plain
    return text


def _has_images(page):
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        for ref in xobjects.values():
            obj = ref.get_object()
            # Ignore small logos/bullets; flag real figures, diagrams and scans.
            if obj.get("/Subtype") == "/Image" and int(obj.get("/Width", 0)) * int(obj.get("/Height", 0)) >= 200 * 200:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def parse(path, settings):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ParseError(f"{path.name} is password-protected; remove the password locally first")
            except ParseError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ParseError(f"{path.name} is encrypted and cannot be opened: {exc}") from exc
        pages = list(reader.pages)
    except PdfReadError as exc:
        raise ParseError(f"{path.name} is not a readable PDF: {exc}") from exc

    sections = _outline(reader)
    use_ocr = settings.get("ocr") == "auto" and ocr.available()
    blocks, warnings = [], []
    scanned, ocred, visual_pages, low_conf = [], [], [], []
    for index, page in enumerate(pages):
        number = index + 1
        try:
            text = page_text(page)
        except Exception:  # noqa: BLE001 - one broken page must not kill the document
            text = ""
            warnings.append(f"page {number}: text extraction failed")
        visual = _has_images(page)
        conf = None
        kind = "text"
        if len(text.strip()) < MIN_TEXT_CHARS:
            if use_ocr:
                ocr_text, conf = ocr.ocr_pdf_page(path, number, settings.get("ocr_languages", "ind+eng"))
                if len(ocr_text.strip()) >= MIN_TEXT_CHARS:
                    text, kind = ocr_text, "ocr"
                    ocred.append(number)
                    if conf is not None and conf < 70:
                        low_conf.append(number)
                else:
                    scanned.append(number)
            elif visual or not text.strip():
                scanned.append(number)
        elif visual:
            visual_pages.append(number)
        if text.strip():
            blocks.append(block(text, page=number, section=sections.get(index, ""), kind=kind,
                                visual=visual, ocr_conf=conf))
    if scanned:
        hint = "" if use_ocr else (" — install tesseract + poppler for local OCR" if not ocr.available()
                                   else " — OCR is disabled (bhap config set ocr auto)")
        warnings.append(f"no readable text on page(s) {_ranges(scanned)} (scan/image?){hint}")
    if ocred:
        warnings.append(f"OCR used on page(s) {_ranges(ocred)}; verify against the original")
    if low_conf:
        warnings.append(f"low OCR confidence on page(s) {_ranges(low_conf)}")
    if visual_pages:
        warnings.append(f"visual content may not have been interpreted on page(s) {_ranges(visual_pages)}")
    return {"blocks": blocks, "units": len(pages), "unit": "page", "warnings": warnings,
            "parser": "bhap-pdf/2+pypdf-layout" + ("+ocr" if ocred else "")}


def _ranges(numbers, limit=12):
    numbers = sorted(set(numbers))
    spans, start, prev = [], None, None
    for value in numbers:
        if start is None:
            start = prev = value
        elif value == prev + 1:
            prev = value
        else:
            spans.append((start, prev))
            start = prev = value
    if start is not None:
        spans.append((start, prev))
    text = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in spans[:limit])
    return text + (" …" if len(spans) > limit else "")
