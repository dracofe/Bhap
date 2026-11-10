"""Terminal output helpers.

Rules: results go to stdout, progress/warnings to stderr; colors only on a TTY
(respecting NO_COLOR, TERM=dumb, --no-color); spinners only on an interactive stderr.
"""
import os
import shutil
import sys
import threading
import time
import unicodedata

_state = {"color": None, "quiet": False, "chat": False}


def set_chat_style(value):
    _state["chat"] = bool(value)


def chat_style():
    return _state["chat"]

CODES = {"bold": "1", "dim": "2", "italic": "3", "underline": "4", "red": "31", "green": "32",
         "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "gray": "90", "reverse": "7"}


def configure(color="auto", quiet=False):
    if color == "never" or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        enabled = False
    elif color == "always" or os.environ.get("FORCE_COLOR"):
        enabled = True
    else:
        enabled = sys.stdout.isatty() and sys.stderr.isatty()
    if enabled and os.name == "nt":
        os.system("")  # enable VT processing on Windows 10+
    _state["color"] = enabled
    _state["quiet"] = quiet


def color_enabled():
    if _state["color"] is None:
        configure()
    return _state["color"]


def style(text, *names):
    if not names or not color_enabled():
        return text
    return "\033[" + ";".join(CODES[n] for n in names) + "m" + text + "\033[0m"


def _unicode_ok(stream):
    encoding = (getattr(stream, "encoding", None) or "").lower()
    return "utf" in encoding


def symbol(name, stream=None):
    stream = stream or sys.stderr
    fancy = {"ok": "✓", "warn": "⚠", "err": "✗", "dot": "●", "arrow": "❯", "bullet": "•", "info": "ℹ"}
    plain = {"ok": "+", "warn": "!", "err": "x", "dot": "*", "arrow": ">", "bullet": "-", "info": "i"}
    return (fancy if _unicode_ok(stream) else plain)[name]


def out(text="", end="\n"):
    sys.stdout.write(text + end)
    sys.stdout.flush()


def err(text="", end="\n"):
    sys.stderr.write(text + end)
    sys.stderr.flush()


def info(text):
    if not _state["quiet"]:
        err(style(symbol("info"), "cyan") + " " + text)


def ok(text):
    if not _state["quiet"]:
        err(style(symbol("ok"), "green") + " " + text)


def warn(text):
    err(style(symbol("warn") + " " + text, "yellow"))


def error(text, hint=None):
    err(style(symbol("err") + " " + text, "red"))
    if hint:
        err(style("  → " + hint, "dim"))


def width():
    return max(40, min(shutil.get_terminal_size((100, 24)).columns, 140))


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def truncate(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def table(rows, headers, stream=None):
    """Render a simple aligned table; rows are sequences of str."""
    stream = stream or sys.stdout
    rows = [[str(cell) for cell in row] for row in rows]
    widths = [display_width(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(cell))
    total = width()
    if sum(widths) + 2 * len(widths) > total and widths:
        last = len(widths) - 1
        widths[last] = max(12, total - sum(widths[:last]) - 2 * len(widths))
        rows = [row[:last] + [truncate(row[last], widths[last])] for row in rows]

    def line(cells, *styles):
        parts = [cell + " " * (widths[i] - display_width(cell)) for i, cell in enumerate(cells)]
        return style("  ".join(parts).rstrip(), *styles)

    stream.write(line(headers, "bold") + "\n")
    for row in rows:
        stream.write(line(row) + "\n")
    stream.flush()


def confirm(question, default=False):
    """Ask yes/no on the terminal. Non-interactive sessions return the default."""
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        err()
        return False
    if not answer:
        return default
    return answer in {"y", "yes", "ya", "iya"}


class Spinner:
    """Context manager drawing a spinner on stderr only when it is a TTY."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, text, enabled=True):
        self.text = text
        self.enabled = enabled and sys.stderr.isatty() and not _state["quiet"]
        self._stop = threading.Event()
        self._thread = None

    def update(self, text):
        self.text = text

    def _run(self):
        frames = self.FRAMES if _unicode_ok(sys.stderr) else "|/-\\"
        index = 0
        while not self._stop.wait(0.08):
            frame = frames[index % len(frames)]
            sys.stderr.write("\r\033[K" + style(frame, "cyan") + " " + truncate(self.text, width() - 4))
            sys.stderr.flush()
            index += 1

    def __enter__(self):
        if self.enabled and not self._thread:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self):
        if self._thread:
            self._stop.set()
            self._thread.join()
            self._thread = None
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    def __exit__(self, *exc):
        self.stop()
        return False


def elapsed(start):
    seconds = time.monotonic() - start
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def human_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"
