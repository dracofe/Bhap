"""Offline guarantee: the full core flow works with egress blocked, and nothing tries to leave."""
import socket
from unittest.mock import patch

from helpers import DIJKSTRA, BhapTestCase, make_pdf

from bhap import netguard
from bhap.core import generation, history, library, retrieval, study
from bhap.core.ingest import Ingestor
from bhap.errors import NetworkBlockedError
from bhap.storage import sqlite


class OfflineTests(BhapTestCase):
    isolate = True  # include the parse worker process

    def test_core_flow_with_egress_blocked(self):
        attempts = []
        real_connect = socket.socket.connect

        def spy(sock, address):
            attempts.append(address)
            return real_connect(sock, address)

        netguard.install()
        try:
            with patch.object(socket.socket, "connect", spy):
                netguard.uninstall()
                netguard.install()  # guard wraps the spy: every connect is recorded *and* checked
                conn = sqlite.connect()
                wid = library.ensure_workspace(conn, "offline")
                ingestor = Ingestor(conn, wid, self.settings())
                ingestor.import_path(make_pdf(self.files / "g.pdf", [DIJKSTRA]).resolve())
                ingestor.import_path(self.write("n.md", "# Catatan\nGraf berbobot.").resolve())
                result = generation.ask(conn, wid, "Apa syarat bobot Dijkstra?", settings=self.settings())
                history.record(conn, history.start(conn, wid, "strict"), result)
                study.generate_quiz(conn, wid, "Dijkstra", settings=self.settings())
                self.assertTrue(retrieval.search(conn, wid, "Dijkstra", settings=self.settings()))
                conn.close()
                with self.assertRaises(NetworkBlockedError):
                    socket.create_connection(("1.1.1.1", 443), timeout=1)
        finally:
            netguard.uninstall()
        self.assertEqual(result["status"], "answered")
        hosts = {a[0] for a in attempts if isinstance(a, tuple)}
        self.assertTrue(hosts <= {"127.0.0.1", "::1", "localhost"}, hosts)

    def test_runtime_url_must_be_loopback(self):
        from bhap.adapters.base import Runtime
        from bhap.errors import ModelError
        with self.assertRaises(ModelError):
            Runtime("http://192.168.1.10:11434")
