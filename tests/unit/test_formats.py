import io
import json
import shutil
import subprocess
import tarfile
import zipfile

from helpers import BhapTestCase, make_pdf

from bhap.core import citations, generation, library, retrieval
from bhap.core.chunking import chunk
from bhap.core.ingest import Ingestor, scan
from bhap.errors import ParseError, UsageError
from bhap.parsers import parse_direct
from bhap.storage import sqlite

JAVA = """package kampus;

import java.util.*;

public class Course {
    private List<String> names = new ArrayList<>();
    private Map<String, Integer> scores = new HashMap<>();

    public boolean register(String name) {
        if (name == null || names.contains(name)) {
            return false;
        }
        names.add(name);
        scores.put(name, 0);
        return true;
    }

    public double average() {
        if (names.isEmpty()) return 0.0;
        int total = 0;
        for (int s : scores.values()) total += s;
        return (double) total / names.size();
    }
}
"""
C_CODE = """#include <stdio.h>

/* hitung faktorial secara rekursif */
int faktorial(int n) {
    if (n <= 1) return 1;
    return n * faktorial(n - 1);
}

int main(void) {
    printf("%d\\n", faktorial(5));
    return 0;
}
"""


class FormatTests(BhapTestCase):
    def test_java_keeps_lines_and_names_methods(self):
        path = self.write("Course.java", JAVA)
        result = parse_direct(path, {})
        self.assertEqual(result["unit"], "line")
        sections = [b["section"] for b in result["blocks"]]
        self.assertTrue(any("Course" in s for s in sections), sections)
        joined = "\n".join(b["text"] for b in result["blocks"])
        self.assertIn("        if (name == null || names.contains(name)) {", joined)  # indentation preserved
        items = list(chunk(result["blocks"]))
        self.assertTrue(all(i["kind"] == "code" for i in items))
        reg = next(i for i in items if "names.add(name)" in i["text"])
        first_line = JAVA.splitlines()[reg["line_no"] - 1]
        self.assertEqual(first_line.strip(), reg["text"].splitlines()[0].strip())  # line number is exact

    def test_c_and_unknown_text_and_binary(self):
        blocks = parse_direct(self.write("faktorial.c", C_CODE), {})["blocks"]
        self.assertTrue(any("faktorial" in b["section"] for b in blocks))
        notes = self.files / "CATATAN"  # no extension, plain text
        notes.write_text("Catatan rapat: rilis minggu depan.")
        self.assertIn("rilis", parse_direct(notes, {})["blocks"][0]["text"])
        binary = self.files / "program.exe"
        binary.write_bytes(b"MZ\x00\x00\x90" * 50)
        with self.assertRaises(UsageError):
            parse_direct(binary, {})
        fake_java = self.files / "Fake.java"
        fake_java.write_bytes(b"\x00\x01\x02binary" * 20)
        with self.assertRaises(ParseError):
            parse_direct(fake_java, {})

    def test_zip_project_with_traps(self):
        archive = self.files / "tugas.zip"
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as inner:
            inner.writestr("x.txt", "nested")
        pdf_bytes = make_pdf(self.files / "tmp.pdf", ["Laporan: kelas Course menyimpan nilai mahasiswa."]).read_bytes()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("tugas/src/kampus/Course.java", JAVA)
            zf.writestr("tugas/src/faktorial.c", C_CODE)
            zf.writestr("tugas/docs/laporan.pdf", pdf_bytes)
            zf.writestr("tugas/README.md", "# Tugas\nImplementasi kelas Course.")
            zf.writestr("tugas/build/Course.class", b"\xca\xfe\xba\xbe\x00\x00")
            zf.writestr("tugas/out/Main.class", b"\xca\xfe\xba\xbe")
            zf.writestr("tugas/.env", "DB_PASSWORD=rahasia")
            zf.writestr("tugas/id_rsa", "-----BEGIN PRIVATE KEY-----")
            zf.writestr("tugas/lib/dep.zip", nested.getvalue())
            zf.writestr("tugas/node_modules/x/index.js", "module.exports = 1")
            zf.writestr("../../escape.txt", "evil")
            zf.writestr("tugas/data.bin", b"\x00\x01\x02\x03" * 100)
            link = zipfile.ZipInfo("tugas/link")
            link.external_attr = (0o120777 << 16)
            zf.writestr(link, "/etc/passwd")
        result = parse_direct(archive, {"ocr": "off"})
        text = "\n".join(b["text"] for b in result["blocks"])
        sections = {b["section"].split(" > ")[0] for b in result["blocks"]}
        self.assertEqual(sections, {"tugas/src/kampus/Course.java", "tugas/src/faktorial.c", "tugas/docs/laporan.pdf",
                                    "tugas/README.md"})
        self.assertNotIn("rahasia", text)
        self.assertNotIn("PRIVATE KEY", text)
        self.assertNotIn("evil", text)
        self.assertNotIn("/etc/passwd", text)
        self.assertNotIn("module.exports", text)
        self.assertEqual(result["units"], 4)
        pdf_block = next(b for b in result["blocks"] if b["section"].startswith("tugas/docs/laporan.pdf"))
        self.assertEqual(pdf_block["page"], 1)
        self.assertTrue(any("skipped" in w for w in result["warnings"]))

    def test_tar_gz_and_secrets_in_folder_scan(self):
        tgz = self.files / "proyek.tar.gz"
        with tarfile.open(tgz, "w:gz") as tf:
            data = C_CODE.encode()
            info = tarfile.TarInfo("proyek/main.c")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            sym = tarfile.TarInfo("proyek/passwd")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "/etc/passwd"
            tf.addfile(sym)
        result = parse_direct(tgz, {})
        self.assertEqual({b["section"].split(" > ")[0] for b in result["blocks"]}, {"proyek/main.c"})
        folder = self.files / "repo"
        folder.mkdir()
        (folder / "App.java").write_text(JAVA)
        (folder / "secret.pem").write_text("key")
        (folder / "photo.raw").write_bytes(b"\x00" * 10)
        files, skipped = scan([folder], recursive=True)
        self.assertEqual([f.name for f in files], ["App.java"])
        self.assertIn("secret-like", skipped)

    def test_data_formats(self):
        csv_blocks = parse_direct(self.write("nilai.csv", "nama,nilai\nAni,75\nBudi,50\n"), {})["blocks"]
        self.assertEqual(csv_blocks[0]["kind"], "table")
        self.assertIn("| Ani | 75 |", csv_blocks[0]["text"])
        js = parse_direct(self.write("config.json", '{"port": 8080, "debug": true}'), {})["blocks"]
        self.assertIn('"port": 8080', js[0]["text"])
        html = parse_direct(self.write("page.html", "<html><head><title>T</title><script>evil()</script></head>"
                                                     "<body><h1>Graf</h1><p>Simpul dan sisi.</p></body></html>"), {})
        self.assertEqual(html["blocks"][0]["section"], "Graf")
        self.assertNotIn("evil", html["blocks"][0]["text"])
        nb = {"cells": [{"cell_type": "markdown", "source": ["# Analisis\n", "Data nilai."]},
                        {"cell_type": "code", "source": ["def rata(x):\n", "    return sum(x)/len(x)\n"],
                         "outputs": [{"text": ["62.5\n"]}]}], "metadata": {"kernelspec": {"language": "python"}}}
        nbr = parse_direct(self.write("analisis.ipynb", json.dumps(nb)), {})["blocks"]
        self.assertTrue(any(b["section"].startswith("Cell 2 (python)") and "def rata" in b["text"] for b in nbr))
        self.assertTrue(any("62.5" in b["text"] for b in nbr))
        rtf = parse_direct(self.write("surat.rtf", r"{\rtf1\ansi{\fonttbl\f0 Arial;}\f0 Halo \b dunia\b0.\par Baris dua.}"), {})
        self.assertIn("Halo dunia.", rtf["blocks"][0]["text"])

    def test_office_extras(self):
        xlsx = self.files / "nilai.xlsx"
        with zipfile.ZipFile(xlsx, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                        '<sheet name="Kelas A" sheetId="1" r:id="rId1"/></sheets></workbook>')
            zf.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
            zf.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        '<si><t>Nama</t></si><si><t>Nilai</t></si><si><t>Ani</t></si></sst>')
            zf.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>75</v></c></row></sheetData></worksheet>')
        result = parse_direct(xlsx, {})
        self.assertEqual(result["blocks"][0]["section"], "Kelas A")
        self.assertIn("| Ani | 75 |", result["blocks"][0]["text"])
        odt = self.files / "catatan.odt"
        with zipfile.ZipFile(odt, "w") as zf:
            zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            zf.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
                        '<text:h text:outline-level="1">Bab 1</text:h><text:p>Isi bab satu.</text:p></office:text></office:body>'
                        '</office:document-content>')
        odt_blocks = parse_direct(odt, {})["blocks"]
        self.assertEqual(odt_blocks[0]["section"], "Bab 1")
        epub = self.files / "buku.epub"
        with zipfile.ZipFile(epub, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                        '<rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
            zf.writestr("OEBPS/book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                        '<item id="c1" href="c1.xhtml"/><item id="c2" href="c2.xhtml"/></manifest>'
                        '<spine><itemref idref="c2"/><itemref idref="c1"/></spine></package>')
            zf.writestr("OEBPS/c1.xhtml", "<html><body><h1>Dua</h1><p>Bab kedua.</p></body></html>")
            zf.writestr("OEBPS/c2.xhtml", "<html><body><h1>Satu</h1><p>Bab pertama.</p></body></html>")
        epub_blocks = parse_direct(epub, {})["blocks"]
        self.assertEqual([b["section"] for b in epub_blocks][:2], ["Satu", "Dua"])  # spine order, not file order

    def test_image_ocr_when_available(self):
        if not (shutil.which("tesseract") and shutil.which("magick")):
            self.skipTest("tesseract/imagemagick not installed")
        image = self.files / "papan.png"
        subprocess.run(["magick", "-size", "900x200", "xc:white", "-pointsize", "48", "-fill", "black",
                        "-annotate", "+30+120", "Dijkstra shortest path", str(image)], check=True)
        result = parse_direct(image, {"ocr_languages": "eng"})
        self.assertIn("Dijkstra", result["blocks"][0]["text"])
        self.assertEqual(result["blocks"][0]["kind"], "ocr")

    def test_legacy_doc_via_libreoffice_when_available(self):
        if not shutil.which("soffice"):
            self.skipTest("LibreOffice not installed")
        source = self.write("lama.txt", "Normalisasi mengurangi redundansi data.")
        subprocess.run(["soffice", f"-env:UserInstallation=file://{self.root}/lo", "--headless", "--convert-to", "doc",
                        "--outdir", str(self.files / "out"), str(source)], capture_output=True, timeout=180)
        if not (self.files / "out" / "lama.doc").exists():
            self.skipTest("LibreOffice could not create a .doc fixture here")
        result = parse_direct(self.files / "out" / "lama.doc", {})
        self.assertIn("redundansi", "\n".join(b["text"] for b in result["blocks"]))
        self.assertIn("libreoffice", result["parser"])

    def test_end_to_end_ask_about_code_in_zip(self):
        archive = self.files / "proyek.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("src/Course.java", JAVA)
        conn = sqlite.connect()
        wid = library.ensure_workspace(conn, "kode")
        doc = Ingestor(conn, wid, self.settings()).import_path(archive.resolve())
        self.assertEqual(doc["status"], "indexed")
        hits = retrieval.search(conn, wid, "register names contains", settings=self.settings())
        self.assertTrue(hits and "src/Course.java" in hits[0]["section_path"])
        self.assertRegex(citations.locator(hits[0]), r"^baris \d+–\d+")
        result = generation.ask(conn, wid, "register names contains", mode="general", settings=self.settings())
        self.assertTrue(result["citations"])
        conn.close()
