"""CLI contract tests run as real subprocesses (stdout/stderr, exit codes, JSON, BHAP_HOME)."""
import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import DIJKSTRA, ERD, BhapTestCase, make_pdf

SRC = str(Path(__file__).resolve().parents[2] / "src")


class CliTests(BhapTestCase):
    def bhap(self, *args, stdin=None, home=None, check=None):
        env = dict(os.environ, PYTHONPATH=SRC, BHAP_HOME=str(home or os.environ["BHAP_HOME"]))
        env.pop("FORCE_COLOR", None)
        proc = subprocess.run([sys.executable, "-m", "bhap", *args], input=stdin, capture_output=True, text=True,
                              env=env, cwd=self.root, timeout=120)
        if check is not None:
            self.assertEqual(proc.returncode, check, f"{args}\nstdout:{proc.stdout}\nstderr:{proc.stderr}")
        return proc

    def test_help_and_version(self):
        self.assertIn("bhap", self.bhap("--help", check=0).stdout)
        from bhap import __version__
        self.assertIn(__version__, self.bhap("--version", check=0).stdout)
        self.assertIn("examples", self.bhap("--lang", "en", "--help", check=0).stdout)

    def test_bare_bhap_without_tty_is_a_usage_error(self):
        proc = self.bhap(stdin="", check=2)
        self.assertIn("TTY", proc.stderr)

    def test_full_scripted_flow_with_clean_json(self):
        self.bhap("workspace", "create", "kuliah", "--use", check=0)
        make_pdf(self.files / "Graph.pdf", ["Pengantar graf berarah dan tak berarah.", DIJKSTRA])
        self.write("erd.md", ERD)
        added = self.bhap("add", str(self.files), "--json", check=0)
        records = [json.loads(line) for line in added.stdout.splitlines()]
        self.assertEqual({r["status"] for r in records}, {"indexed"})
        self.assertEqual(self.bhap("add", str(self.files / "erd.md"), "--json", check=0).stdout.count('"unchanged"'), 1)

        ask = self.bhap("ask", "Apa syarat bobot Dijkstra?", "--json", check=0)
        self.assertNotIn("\x1b", ask.stdout)
        data = json.loads(ask.stdout)
        self.assertEqual(data["status"], "answered")
        self.assertEqual(data["citations"][0]["file"], "Graph.pdf")
        self.assertEqual(data["citations"][0]["page"], 2)
        self.assertEqual(data["workspace"], "kuliah")

        piped = json.loads(self.bhap("ask", "--stdin", "--json", stdin="Bagaimana fotosintesis bekerja?", check=0).stdout)
        self.assertEqual(piped["status"], "abstained")
        self.assertEqual(piped["citations"], [])

        plain = self.bhap("ask", "Apa syarat bobot Dijkstra?", "--evidence", check=0)
        self.assertIn("Graph.pdf — halaman 2", plain.stdout)
        self.assertNotIn("\x1b", plain.stdout + plain.stderr)

        search = json.loads(self.bhap("search", "foreign key", "--json", check=0).stdout)
        self.assertEqual(search["results"][0]["file"], "erd.md")
        status = json.loads(self.bhap("status", "--json", check=0).stdout)
        self.assertEqual((status["indexed"], status["offline_ready"]), (2, True))
        docs = json.loads(self.bhap("docs", "list", "--json", check=0).stdout)
        self.assertEqual(len(docs), 2)
        history = json.loads(self.bhap("history", "list", "--json", check=0).stdout)
        self.assertEqual(len(history), 3)
        chunk_id = data["citations"][0]["chunk_id"]
        self.assertIn("non-negatif", self.bhap("open", str(chunk_id), check=0).stdout)
        self.bhap("docs", "remove", "Graph.pdf", "--yes", check=0)
        after = json.loads(self.bhap("search", "Dijkstra bobot", "--json", check=0).stdout)
        self.assertTrue(all(r["file"] != "Graph.pdf" for r in after["results"]))

    def test_exit_codes(self):
        self.assertEqual(self.bhap("ask", "--json", stdin="").returncode, 2)
        self.assertEqual(self.bhap("workspace", "use", "nope").returncode, 2)
        bad = self.write("bad.pdf", "not a pdf")
        self.assertEqual(self.bhap("add", str(bad)).returncode, 4)
        err = json.loads(self.bhap("ask", "x", "-w", "nope", "--json").stdout)
        self.assertEqual(err["exit_code"], 2)
        self.assertEqual(self.bhap("doctor", "--offline", check=0).returncode, 0)
        dead = dict(os.environ, BHAP_OLLAMA_URL="http://127.0.0.1:9")
        proc = subprocess.run([sys.executable, "-m", "bhap", "doctor", "--json"], capture_output=True, text=True,
                              env=dict(dead, PYTHONPATH=SRC), timeout=60)
        self.assertEqual(proc.returncode, 3)
        self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_doctor_verify_offline_json(self):
        data = json.loads(self.bhap("doctor", "--offline", "--verify", "--json", check=0).stdout)
        names = {c["check"]: c["status"] for c in data["checks"]}
        self.assertEqual(names["network_guard"], "ok")
        self.assertEqual(names["embedding_test"], "ok")
        self.assertEqual(names["chat_test"], "ok")

    def test_bhap_home_isolation(self):
        self.write("a.md", DIJKSTRA)
        self.bhap("add", str(self.files / "a.md"), check=0)
        other = self.root / "other-home"
        self.bhap("config", "set", "language", "id", home=other, check=0)
        proc = subprocess.run([sys.executable, "-m", "bhap", "model", "use", "fake-chat"], capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=SRC, BHAP_HOME=str(other)), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        status = json.loads(self.bhap("status", "--json", home=other, check=0).stdout)
        self.assertEqual(status["documents"], 0)

    def test_config_and_backup_commands(self):
        self.bhap("config", "set", "top_k", "4", check=0)
        self.assertEqual(self.bhap("config", "get", "top_k", check=0).stdout.strip(), "4")
        self.assertEqual(self.bhap("config", "set", "top_k", "abc").returncode, 2)
        self.assertEqual(self.bhap("config", "set", "ollama_url", "http://1.2.3.4:11434").returncode, 2)
        self.write("a.md", DIJKSTRA)
        self.bhap("add", str(self.files / "a.md"), check=0)
        pack = self.root / "p.bhap"
        self.bhap("backup", "create", "-o", str(pack), check=0)
        self.assertTrue(json.loads(self.bhap("backup", "verify", str(pack), "--json", check=0).stdout)["ok"])
        self.bhap("backup", "restore", str(pack), "--as", "pulih", check=0)
        self.assertIn("pulih", self.bhap("workspace", "list", check=0).stdout)

    def test_stdin_document_and_study_json(self):
        self.bhap("add", "-", "--name", "catatan.md", stdin="# Dijkstra\n" + DIJKSTRA, check=0)
        quiz = json.loads(self.bhap("study", "quiz", "Dijkstra", "--json", check=0).stdout)
        self.assertTrue(quiz["items"] and all(i["sources"] for i in quiz["items"]))
        summary = json.loads(self.bhap("summarize", "catatan.md", "--level", "short", "--json", check=0).stdout)
        self.assertTrue(summary["citations"])
