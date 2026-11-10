import zipfile

from helpers import BhapTestCase, make_docx, make_pdf, make_pptx, p, table

from bhap.errors import ParseError, UsageError
from bhap.parsers import parse_direct, parse_file


class ParserTests(BhapTestCase):
    def test_markdown_heading_hierarchy_and_lines(self):
        path = self.write("notes.md", "# Graf\nPengantar graf.\n\n## Dijkstra\nBobot non-negatif.\n\n```\n# bukan heading\n```\n"
                                      "### Langkah\nPilih jarak terkecil.\n\n# Pohon\nPohon adalah graf.\n")
        blocks = parse_direct(path, {})["blocks"]
        sections = [b["section"] for b in blocks]
        self.assertEqual(sections, ["Graf", "Graf > Dijkstra", "Graf > Dijkstra > Langkah", "Pohon"])
        self.assertIn("# bukan heading", blocks[1]["text"])
        self.assertEqual(blocks[1]["line"], 4)

    def test_plain_text_paragraph_lines_and_encodings(self):
        path = self.files / "latin.txt"
        path.write_bytes("Baris satu café.\n\nBaris tiga.".encode("cp1252"))
        blocks = parse_direct(path, {})["blocks"]
        self.assertEqual([b["line"] for b in blocks], [1, 3])
        self.assertIn("café", blocks[0]["text"])

    def test_pdf_pages_and_outline_sections(self):
        path = make_pdf(self.files / "graf.pdf", ["Bab 1 Graf adalah kumpulan simpul dan sisi yang saling terhubung.",
                                                  "Dijkstra mensyaratkan bobot non-negatif pada setiap sisi graf.",
                                                  "Penutup materi graf dan algoritma jalur terpendek."],
                        outline=[("Pendahuluan", 0), ("Dijkstra", 1)])
        result = parse_direct(path, {"ocr": "off"})
        self.assertEqual(result["units"], 3)
        self.assertEqual([b["page"] for b in result["blocks"]], [1, 2, 3])
        self.assertEqual(result["blocks"][1]["section"], "Dijkstra")
        self.assertEqual(result["blocks"][2]["section"], "Dijkstra")

    def test_layout_cleanup_keeps_table_rows_and_prose(self):
        from bhap.parsers.pdf import clean_layout
        text = ("      Perangkat / model      Jumlah     Harga\n"
                "      PC                     98         1000\n"
                "   Masing-masing        gedung        juga        punya        switch\n"
                "   Kabel dari PC ke switch jadi paling pendek.")
        lines = clean_layout(text).splitlines()
        self.assertEqual(lines[1], "PC | 98 | 1000")
        self.assertEqual(lines[2], "Masing-masing gedung juga punya switch")
        self.assertEqual(lines[3], "Kabel dari PC ke switch jadi paling pendek.")
        mixed = clean_layout("dipakai     untuk     kerja     internal, Wi-Fi     tamu,     server     privat, dan tiga")
        self.assertNotIn(" | ", mixed)

    def test_pdf_textless_page_is_reported_not_hidden(self):
        path = make_pdf(self.files / "scan.pdf", ["Halaman ini memiliki cukup banyak teks untuk diekstrak dengan baik.", ""])
        result = parse_direct(path, {"ocr": "off"})
        self.assertEqual(len(result["blocks"]), 1)
        self.assertTrue(any("2" in w and "scan" in w for w in result["warnings"]), result["warnings"])

    def test_docx_headings_tables_pages_footnotes(self):
        body = (p("Normalisasi", "Heading1") + p("Normalisasi mengurangi redundansi data.")
                + p("Bentuk Normal 1", "Heading2") + p("Atribut bernilai atomik.")
                + table([["Bentuk", "Syarat"], ["1NF", "atomik"]])
                + p("Bentuk Normal 2", "Heading2", page_break=True) + p("Tidak ada ketergantungan parsial."))
        path = make_docx(self.files / "basis.docx", body, footnotes=["Lihat Codd 1970."])
        result = parse_direct(path, {})
        blocks = result["blocks"]
        self.assertEqual(blocks[0]["section"], "Normalisasi")
        table_block = next(b for b in blocks if b["kind"] == "table")
        self.assertIn("| 1NF | atomik |", table_block["text"])
        self.assertEqual(table_block["section"], "Normalisasi > Bentuk Normal 1")
        last = [b for b in blocks if b["kind"] == "text"][-1]
        self.assertEqual(last["section"], "Normalisasi > Bentuk Normal 2")
        self.assertEqual(last["page"], 2)
        self.assertTrue(last["approx"])
        self.assertTrue(any(b["kind"] == "notes" and "Codd" in b["text"] for b in blocks))

    def test_pptx_order_titles_tables_notes_hidden(self):
        slides = [{"title": "Penutup", "body": "Terima kasih"},
                  {"title": "Pembuka", "body": "Graf berarah", "notes": "Jelaskan contoh peta jalan",
                   "table": [["Simpul", "Jarak"], ["A", "0"]]},
                  {"title": "Rahasia", "body": "Slide tersembunyi", "hidden": True}]
        path = make_pptx(self.files / "graf.pptx", slides, order=[2, 1, 3])
        result = parse_direct(path, {"include_speaker_notes": True})
        self.assertEqual(result["units"], 3)
        first = result["blocks"][0]
        self.assertEqual((first["slide"], first["section"]), (1, "Pembuka"))
        self.assertTrue(any(b["kind"] == "notes" and b["slide"] == 1 and "peta" in b["text"] for b in result["blocks"]))
        self.assertTrue(any(b["kind"] == "table" and "| A | 0 |" in b["text"] for b in result["blocks"]))
        self.assertFalse(any("1" == b["text"].strip() for b in result["blocks"]))  # slide-number placeholder skipped
        self.assertTrue(any("hidden" in w for w in result["warnings"]))
        no_notes = parse_direct(path, {"include_speaker_notes": False})
        self.assertFalse(any(b["kind"] == "notes" for b in no_notes["blocks"]))

    def test_extension_spoofing_is_refused(self):
        fake_pdf = self.write("evil.pdf", "this is not a pdf")
        with self.assertRaises(ParseError):
            parse_direct(fake_pdf, {})
        binary_txt = self.files / "bin.txt"
        binary_txt.write_bytes(b"\x7fELF\x00\x00\x00binary")
        with self.assertRaises(ParseError):
            parse_direct(binary_txt, {})

    def test_legacy_office_and_unsupported(self):
        legacy = self.files / "old.doc"
        legacy.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
        with self.assertRaises(ParseError):
            parse_direct(legacy, {})
        with self.assertRaises(UsageError):
            parse_direct(self.write("x.exe", "MZ"), {})

    def test_zip_bomb_and_dtd_are_refused(self):
        bomb = self.files / "bomb.docx"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<a>" + "A" * (40 * 1024 * 1024) + "</a>")
        with self.assertRaises(ParseError) as ctx:
            parse_direct(bomb, {})
        self.assertIn("zip bomb", str(ctx.exception))
        laughs = self.files / "laughs.docx"
        with zipfile.ZipFile(laughs, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
        with self.assertRaises(ParseError):
            parse_direct(laughs, {})

    def test_isolated_worker_matches_direct_and_reports_errors(self):
        path = self.write("a.md", "# Judul\nIsi dokumen untuk worker.")
        settings = dict(self.settings(), isolate_parsing=True)
        self.assertEqual(parse_file(path, settings)["blocks"], parse_direct(path, settings)["blocks"])
        with self.assertRaises(ParseError):
            parse_file(self.write("bad.pdf", "nope"), settings)
