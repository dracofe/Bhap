import os
import sys
from unittest.mock import patch

from helpers import BhapTestCase

from bhap.web import app


class ShortcutTests(BhapTestCase):
    def test_install_shortcut_writes_desktop_entry_and_icon(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux-only")
        data = self.root / "xdg"
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(data)}):
            self.assertEqual(app.install_shortcut(), 0)
        entry = (data / "applications" / "bhap.desktop").read_text()
        self.assertIn("Exec=", entry)
        self.assertIn(" app", entry)
        self.assertTrue((data / "icons/hicolor/scalable/apps/bhap.svg").exists())

    def test_static_assets_are_packaged_and_offline(self):
        static = app.STATIC
        for name in ("index.html", "app.css", "app.js", "bhap.svg"):
            self.assertTrue((static / name).is_file(), name)
        for name in ("index.html", "app.css", "app.js"):
            text = (static / name).read_text()
            self.assertNotIn("http://", text.replace("http://www.w3.org", ""))
            self.assertNotIn("https://", text)
