"""`bhap app` — open the desktop app window backed by the local API server.

Window strategies, best first:
  1. native GTK/WebKit window (Linux, via any Python that has PyGObject + WebKit2GTK)
  2. pywebview, if installed (macOS/Windows: `pip install "bhap-ai[app]"`)
  3. a Chromium-family browser in --app mode (Chrome, Chromium, Edge, Brave, Vivaldi)
  4. the default browser (a tab); the server stops a minute after the tab is closed
The server and any Ollama that Bhap started stop when the window closes.
"""
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from .. import config, ui
from ..i18n import tr
from .server import STATIC, AppServer

TITLE = "Bhap"
ICON = STATIC / "bhap.svg"
CHROMIUMS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "microsoft-edge",
             "microsoft-edge-stable", "brave", "brave-browser", "vivaldi", "vivaldi-stable")
WIN_BROWSERS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe")
MAC_BROWSERS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Chromium.app/Contents/MacOS/Chromium")
GTK_PROBE = ("import gi; gi.require_version('Gtk','3.0');\n"
             "try:\n gi.require_version('WebKit2','4.1')\nexcept ValueError:\n gi.require_version('WebKit2','4.0')\n"
             "from gi.repository import Gtk, WebKit2")


def gtk_python():
    """A Python interpreter that can import GTK 3 + WebKit2 (often the system one)."""
    if not sys.platform.startswith("linux") or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    for candidate in dict.fromkeys([sys.executable, "/usr/bin/python3", shutil.which("python3") or ""]):
        if candidate and Path(candidate).exists():
            try:
                probe = subprocess.run([candidate, "-c", GTK_PROBE], capture_output=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                continue
            if probe.returncode == 0:
                return candidate
    return None


def chromium():
    for name in CHROMIUMS:
        found = shutil.which(name)
        if found:
            return found
    for path in (WIN_BROWSERS if os.name == "nt" else MAC_BROWSERS if sys.platform == "darwin" else ()):
        if Path(path).exists():
            return path
    return None


def open_window(url, mode="auto"):
    """Return (strategy, process-or-None). The process exits when the window closes."""
    if mode in ("auto", "native"):
        python = gtk_python()
        if python:
            script = Path(__file__).resolve().parent / "window_gtk.py"
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            # WebKitGTK's DMA-BUF renderer shows blank windows on many NVIDIA setups; the fallback is safe.
            env.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
            proc = subprocess.Popen([python, "-I", str(script), url, TITLE, str(ICON)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "native", proc
        try:
            import webview  # optional extra: pywebview
            return "pywebview", webview
        except ImportError:
            pass
    if mode in ("auto", "native", "chromium"):
        browser = chromium()
        if browser:
            profile = config.path("cache") / "app-profile"
            proc = subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                                     "--no-default-browser-check", "--disable-sync", "--window-size=1180,780"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "chromium", proc
    webbrowser.open(url)
    return "browser", None


def run(args):
    server = AppServer(port=getattr(args, "port", 0) or 0).start()
    url = server.launch_url
    if getattr(args, "print_url", False) or getattr(args, "no_window", False):
        ui.out(url)
    strategy, handle = ("none", None) if getattr(args, "no_window", False) else open_window(url, args.window)
    ui.info(tr(f"Bhap app berjalan ({strategy}) di http://127.0.0.1:{server.port} — tutup jendela atau Ctrl+C untuk keluar.",
               f"Bhap app running ({strategy}) on http://127.0.0.1:{server.port} — close the window or Ctrl+C to quit."))
    try:
        if strategy == "pywebview":
            handle.create_window(TITLE, url, width=1180, height=780)
            handle.start()
        elif handle is not None:
            handle.wait()
        else:
            # Browser tab / headless: stay alive while the page keeps sending heartbeats.
            grace = 90 if strategy == "browser" else None
            while True:
                time.sleep(2)
                if grace and time.monotonic() - server.state.last_seen > grace:
                    break
    except KeyboardInterrupt:
        ui.err()
    finally:
        server.stop()
    return 0


def install_shortcut():
    """Add Bhap to the desktop's application menu (Linux, per user)."""
    if not sys.platform.startswith("linux"):
        ui.warn(tr("Pintasan otomatis baru tersedia untuk Linux.", "Automatic shortcuts are Linux-only for now."))
        return 1
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    icon_dir = data / "icons/hicolor/scalable/apps"
    apps = data / "applications"
    icon_dir.mkdir(parents=True, exist_ok=True)
    apps.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON, icon_dir / "bhap.svg")
    executable = shutil.which("bhap") or f"{sys.executable} -m bhap"
    desktop = apps / "bhap.desktop"
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=Bhap\nGenericName=Offline AI knowledge assistant\n"
        "Comment=Tanya materi Anda secara offline\n"
        f"Exec={executable} app\nIcon=bhap\nTerminal=false\nCategories=Education;Office;Utility;\n"
        "Keywords=AI;RAG;offline;PDF;study;\nStartupWMClass=bhap\n", encoding="utf-8")
    ui.ok(tr(f"Bhap ditambahkan ke menu aplikasi ({desktop}).", f"Bhap added to the application menu ({desktop})."))
    return 0
