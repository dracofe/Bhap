import os
import shlex
import socket
import stat
import sys
from pathlib import Path
from unittest.mock import patch

from helpers import BhapTestCase

from bhap import config
from bhap.adapters import autostart

FAKE = Path(__file__).resolve().parents[1] / "fake_ollama.py"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AutostartTests(BhapTestCase):
    def setUp(self):
        super().setUp()
        bindir = self.root / "bin"
        bindir.mkdir()
        script = bindir / "ollama"
        script.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.port = free_port()
        self.env2 = patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                                            "BHAP_OLLAMA_URL": f"http://127.0.0.1:{self.port}"})
        self.env2.start()
        os.environ.pop("BHAP_NO_AUTOSTART", None)

    def tearDown(self):
        autostart.stop()
        os.environ["BHAP_NO_AUTOSTART"] = "1"
        self.env2.stop()
        super().tearDown()

    def test_starts_when_down_and_stops_on_exit(self):
        if os.name == "nt":
            self.skipTest("POSIX shell script fake")
        notified = []
        result = autostart.ensure_running(config.load(), wait=120, notify=notified.append)  # slow CI runners
        log = config.home() / "logs" / "ollama.log"
        self.assertEqual(result, "started", log.read_text(errors="replace")[-2000:] if log.exists() else "no log")
        self.assertEqual(notified, [f"127.0.0.1:{self.port}"])
        self.assertTrue(autostart.started_by_bhap())
        self.assertEqual(autostart.ensure_running(config.load()), "running")
        autostart.stop()
        self.assertFalse(autostart._reachable(config.load()))
        self.assertTrue((config.home() / "logs" / "ollama.log").exists())

    def test_skips_when_disabled_or_missing(self):
        config.update(auto_start_runtime=False)
        self.assertEqual(autostart.ensure_running(config.load()), "skipped")
        config.update(auto_start_runtime=True)
        with patch("bhap.adapters.autostart.find_binary", return_value=None):
            self.assertEqual(autostart.ensure_running(config.load()), "unavailable")
        with patch.dict(os.environ, {"BHAP_NO_AUTOSTART": "1"}):
            self.assertEqual(autostart.ensure_running(config.load()), "skipped")
