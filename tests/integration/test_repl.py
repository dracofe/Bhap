"""Interactive session routing (PRD §26.4 / §26.7), driven without a real terminal."""
import os
from pathlib import Path
from unittest.mock import patch

from helpers import DIJKSTRA, BhapTestCase

from bhap.errors import BhapError
from bhap.repl import Session, _Args
from bhap.storage import sqlite


class ReplTests(BhapTestCase):
    def setUp(self):
        super().setUp()
        self.conn = sqlite.connect()
        self.start_cwd = os.getcwd()
        os.chdir(self.files)
        self.session = Session(self.conn, _Args(workspace=None))

    def tearDown(self):
        os.chdir(self.start_cwd)
        self.conn.close()
        super().tearDown()

    def docs(self):
        return [r["filename"] for r in self.conn.execute("SELECT filename FROM documents ORDER BY id")]

    def test_cd_pwd_virtual_cwd(self):
        (self.files / "sem3").mkdir()
        self.session.route("cd sem3")
        self.assertEqual(self.session.cwd, (self.files / "sem3").resolve())
        self.assertEqual(Path(os.getcwd()).resolve(), self.files.resolve())  # parent process cwd untouched
        with self.assertRaises(BhapError):
            self.session.route("cd does-not-exist")
        self.assertEqual(self.session.cwd, (self.files / "sem3").resolve())  # failed cd keeps cwd
        self.session.route("cd ..")
        self.assertEqual(self.session.cwd, self.files.resolve())

    def test_absolute_relative_and_quoted_paths_are_imported(self):
        absolute = self.write("abs.md", DIJKSTRA)
        self.session.route(str(absolute))
        self.write("Materi Basis/ERD final.md", "Entity Relationship Diagram konseptual.")
        self.session.route('"./Materi Basis/ERD final.md"')
        self.write("rel.txt", "Teks relatif tentang graf berbobot.")
        self.session.route("./rel.txt")
        self.write("dijkstra notes.md", "Catatan Dijkstra di folder kerja.")
        with patch("bhap.ui.confirm", side_effect=AssertionError("must not ask")):
            self.session.route('"dijkstra notes.md"')
        self.assertEqual(self.docs(), ["abs.md", "ERD final.md", "rel.txt", "dijkstra notes.md"])

    def test_missing_path_and_unknown_command_never_reach_the_model(self):
        before = self.fake.state.calls["chat"]
        self.session.route("./tidak-ada.pdf")
        self.session.route("/home/nobody/x.pdf")
        self.session.route("/unknowncmd")
        self.assertEqual(self.fake.state.calls["chat"], before)

    def test_directory_requires_confirmation(self):
        self.write("materi/a.md", DIJKSTRA)
        self.write("materi/b.md", "Graf berarah memiliki arah pada sisi.")
        with patch("bhap.ui.confirm", return_value=False), patch("sys.stdin.isatty", return_value=True):
            self.session.route("./materi")
        self.assertEqual(self.docs(), [])
        with patch("bhap.ui.confirm", return_value=True), patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", return_value=""):
            self.session.route("./materi/")
        self.assertEqual(sorted(self.docs()), ["a.md", "b.md"])

    def test_ambiguous_bare_word_asks_before_importing(self):
        self.write("graf", "Graf adalah kumpulan simpul.")
        with patch("bhap.ui.confirm", return_value=False):
            self.session.route("graf")  # declined -> treated as a question, not imported
        self.assertEqual(self.docs(), [])

    def test_question_sources_save_and_at_reference(self):
        self.session.route(str(self.write("a.md", DIJKSTRA)))
        self.session.route("Apa syarat bobot Dijkstra?")
        self.assertEqual(self.session.last["status"], "answered")
        self.session.route("/save Dijkstra")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
        other = self.write("b.md", "Dijkstra juga dipakai untuk routing jaringan OSPF.")
        with patch("bhap.ui.confirm", return_value=True):
            self.session.route(f"Jelaskan Dijkstra di @{other}")
        self.assertEqual({c["file"] for c in self.session.last["citations"]}, {"b.md"})
        self.assertEqual(self.session.route("/exit"), "exit")

    def test_mode_scope_and_workspace_switch(self):
        self.session.route("/mode general")
        self.assertEqual(self.session.mode, "general")
        self.session.route("/workspace create proyek")
        self.assertEqual(self.session.ctx.workspace, "proyek")
        self.session.route(str(self.write("a.md", DIJKSTRA)))
        self.session.route("/scope a.md")
        self.assertEqual(len(self.session.scope.doc_ids), 1)
        self.session.route("/scope clear")
        self.assertFalse(self.session.scope)

    def test_shell_is_disabled_by_default(self):
        with patch("subprocess.run") as run:
            self.session.route("!echo hi")
        run.assert_not_called()

    def test_upload_command_accepts_unquoted_paths_with_spaces(self):
        target = self.write("Materi Kuliah/II2120 M1.md", DIJKSTRA)
        self.session.route(f"/upload {target}")
        self.session.route("/upload ./Materi Kuliah/II2120 M1.md")  # unchanged on second upload
        self.assertEqual(self.docs(), ["II2120 M1.md"])
        with self.assertRaises(BhapError):
            self.session.route("/upload")
        with self.assertRaises(BhapError):
            self.session.route("/upload ./tidak/ada.pdf")

    def test_tui_toolbar_and_placeholder_render(self):
        from bhap import tui
        if not tui.AVAILABLE:
            self.skipTest("prompt_toolkit not installed")
        view = tui.Tui(self.session)
        view.refresh_state()
        toolbar = "".join(text for _, text in view._toolbar())
        self.assertIn("/upload", toolbar)
        self.assertIn("upload", "".join(text for _, text in view._placeholder()))
        self.session.route(str(self.write("a.md", DIJKSTRA)))
        view.refresh_state()
        self.assertIn("1", "".join(text for _, text in view._toolbar()))

    def test_pasted_long_code_is_a_question_not_a_path(self):
        self.session.route(str(self.write("a.md", DIJKSTRA)))
        self.session.mode = "general"
        code = ("tolong lengkapi codingan berikut import java.util.ArrayList;\nimport java.util.HashMap;\n"
                "public class Course {\n    private List<String> names = new ArrayList<>();\n"
                "    @Override\n    public String toString() { return names.toString(); }\n"
                + "    // TODO: Daftarkan peserta baru dengan nilai awal 0. Dijkstra bobot\n" * 40 + "}")
        before = self.fake.state.calls["chat"]
        self.session.route(code)  # must not raise "File name too long" nor treat @Override as a file
        self.assertEqual(self.fake.state.calls["chat"], before + 1)
        self.assertIn("@Override", self.session.last["question"])
        long_line = "jelaskan " + "x" * 600 + " Dijkstra"
        self.session.route(long_line)
        self.assertEqual(self.fake.state.calls["chat"], before + 2)
        with patch("bhap.ui.confirm", side_effect=AssertionError("must not ask")):
            self.session.route("kirim ke email budi@example.com tentang Dijkstra")
        self.assertEqual(self.fake.state.calls["chat"], before + 3)
