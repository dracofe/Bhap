"""Test fixtures: isolated BHAP_HOME, fake Ollama, and real PDF/DOCX/PPTX builders."""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Safety net: no test may ever touch the developer's real Bhap data directory.
_SESSION_HOME = tempfile.mkdtemp(prefix="bhap-test-session-")
os.environ["BHAP_HOME"] = _SESSION_HOME
os.environ.setdefault("BHAP_LANG", "id")
os.environ["BHAP_NO_AUTOSTART"] = "1"  # tests must never launch the developer's real Ollama

from fake_ollama import FakeOllama  # noqa: E402

from bhap import config, i18n  # noqa: E402
from bhap.core import retrieval  # noqa: E402


# --------------------------------------------------------------------------- builders
def _pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path, pages, outline=None):
    """Write a real text PDF. pages: list of page texts (lines split on \\n)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None]
    kids = []
    font_id = 3 + 2 * len(pages)
    for index, text in enumerate(pages):
        page_id, content_id = 3 + 2 * index, 4 + 2 * index
        kids.append(f"{page_id} 0 R")
        lines = text.split("\n")
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"]
        for line in lines:
            ops.append(f"({_pdf_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                       f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>")
        objects.append(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    Path(path).write_bytes(bytes(out))
    if outline:
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter(clone_from=PdfReader(str(path)))
        for title, page_index in outline:
            writer.add_outline_item(title, page_index)
        with open(path, "wb") as handle:
            writer.write(handle)
    return Path(path)


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(path, body_xml, footnotes=None):
    styles = (f'<w:styles {W}><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
              f'<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style></w:styles>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f"<w:document {W}><w:body>{body_xml}</w:body></w:document>")
        archive.writestr("word/styles.xml", styles)
        if footnotes:
            notes = "".join(f'<w:footnote w:id="{i}"><w:p><w:r><w:t>{t}</w:t></w:r></w:p></w:footnote>'
                            for i, t in enumerate(footnotes, 1))
            archive.writestr("word/footnotes.xml", f"<w:footnotes {W}>{notes}</w:footnotes>")
    return Path(path)


def p(text, style=None, page_break=False):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    br = '<w:r><w:br w:type="page"/></w:r>' if page_break else ""
    return f"<w:p>{ppr}{br}<w:r><w:t>{text}</w:t></w:r></w:p>"


def table(rows):
    cells = "".join("<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows)
    return f"<w:tbl>{cells}</w:tbl>"


PNS = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _shape(text, ph=None):
    ph_xml = f'<p:nvPr><p:ph type="{ph}"/></p:nvPr>' if ph else "<p:nvPr/>"
    paras = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in text.split("\n"))
    return f"<p:sp><p:nvSpPr><p:cNvPr id='1' name='x'/><p:cNvSpPr/>{ph_xml}</p:nvSpPr><p:txBody>{paras}</p:txBody></p:sp>"


def make_pptx(path, slides, order=None):
    """slides: list of dicts {title, body, notes?, table?, hidden?, picture?}; order: file indexes in show order."""
    order = order or list(range(1, len(slides) + 1))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{n}"/>' for i, n in enumerate(order, 1))
        archive.writestr("ppt/presentation.xml", f"<p:presentation {PNS}><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>")
        rels = "".join(f'<Relationship Id="rId{n}" Type="{REL}/slide" Target="slides/slide{n}.xml"/>'
                       for n in range(1, len(slides) + 1))
        archive.writestr("ppt/_rels/presentation.xml.rels",
                         f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for n, slide in enumerate(slides, 1):
            shapes = _shape(slide["title"], "title") + _shape(slide.get("body", ""))
            if slide.get("table"):
                rows = "".join("<a:tr>" + "".join(f"<a:tc><a:txBody><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:txBody></a:tc>"
                                                  for c in row) + "</a:tr>" for row in slide["table"])
                shapes += f"<p:graphicFrame><a:graphic><a:graphicData><a:tbl>{rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
            if slide.get("picture"):
                shapes += "<p:pic/>"
            hidden = ' show="0"' if slide.get("hidden") else ""
            archive.writestr(f"ppt/slides/slide{n}.xml", f"<p:sld {PNS}{hidden}><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>")
            if slide.get("notes"):
                archive.writestr(f"ppt/slides/_rels/slide{n}.xml.rels",
                                 f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                 f'<Relationship Id="rId1" Type="{REL}/notesSlide" Target="../notesSlides/notesSlide{n}.xml"/>'
                                 f'</Relationships>')
                archive.writestr(f"ppt/notesSlides/notesSlide{n}.xml",
                                 f"<p:notes {PNS}><p:cSld><p:spTree>{_shape('1', 'sldNum')}{_shape(slide['notes'], 'body')}"
                                 f"</p:spTree></p:cSld></p:notes>")
    return Path(path)


# --------------------------------------------------------------------------- base test case
DIJKSTRA = ("Algoritma Dijkstra mencari jalur terpendek dari satu simpul sumber. Dijkstra mensyaratkan bobot sisi "
            "non-negatif. Pada setiap langkah algoritma memilih simpul dengan jarak sementara terkecil.")
ERD = ("Entity Relationship Diagram (ERD) konseptual menggambarkan entitas, atribut, dan relasi. Model relasional "
       "menyimpan data dalam tabel dengan primary key dan foreign key.")


class BhapTestCase(unittest.TestCase):
    isolate = False  # parse in-process by default; dedicated tests cover the worker

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bhap-test-")
        self.root = Path(self.tmp.name)
        self.fake = FakeOllama().__enter__()
        self.env = patch.dict(os.environ, {"BHAP_HOME": str(self.root / "home"), "BHAP_OLLAMA_URL": self.fake.url,
                                           "BHAP_LANG": "id", "NO_COLOR": "1"})
        self.env.start()
        i18n.set_language("id")
        retrieval._matrix_cache.clear()
        config.update(llm_model="fake-chat", embedding_model="fake-embed", isolate_parsing=self.isolate,
                      max_file_mb=5)
        self.files = self.root / "files"
        self.files.mkdir()

    def tearDown(self):
        self.env.stop()
        self.fake.__exit__()
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.files / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def settings(self):
        return config.load()
