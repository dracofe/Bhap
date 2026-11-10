"""Full-screen-feeling terminal front end for the interactive session (prompt_toolkit).

Layout (top to bottom):
    mascot + title/version, model & readiness, cwd, workspace, notices
    … conversation scrolls here …
                                                   ◐ strict · /mode
    ─────────────────────────────────────────────────────────────────
    ❯ Tanya apa saja tentang materimu…
    ─────────────────────────────────────────────────────────────────
      ● offline siap · qwen2.5:7b · 3 dokumen · personal      /help

Falls back to the plain readline prompt when prompt_toolkit is unavailable,
when NO_COLOR/TERM=dumb is set, or when BHAP_PLAIN=1.
"""
import os
import sys
from pathlib import Path

from . import __version__, config
from .i18n import tr

try:
    from prompt_toolkit import PromptSession, print_formatted_text
    from prompt_toolkit.application import get_app
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    AVAILABLE = True
except ImportError:  # pragma: no cover - optional front end
    AVAILABLE = False

ACCENT = "#4fb3a9"   # teal
WARM = "#e0a458"     # amber
MUTED = "#8a8f98"
RULE = "#5c6370"

STYLE = {
    "accent": f"{ACCENT} bold",
    "warm": WARM,
    "muted": MUTED,
    "title": "bold",
    "rule": RULE,
    "ok": "#7ec699",
    "bad": "#e06c75",
    "warn": WARM,
    "prompt": f"{ACCENT} bold",
    "placeholder": f"{MUTED} italic",
    "bottom-toolbar": f"noreverse bg:default {MUTED}",
    "bottom-toolbar.text": "noreverse",
    "completion-menu.completion": "bg:#262a30 #c8ccd4",
    "completion-menu.completion.current": f"bg:{ACCENT} #101214 bold",
    "completion-menu.meta.completion": "bg:#262a30 #8a8f98",
    "completion-menu.meta.completion.current": f"bg:{ACCENT} #101214",
    "user": "#c8ccd4",
}

# 4-row pixel owl built from half blocks (Bhap's mascot): ears, eyes, beak, feet.
MASCOT = [
    "▄▀▄   ▄▀▄",
    "█ ◉ ▄ ◉ █",
    "█  ▀▀▀  █",
    " ▀▀▄ ▄▀▀ ",
]


def enabled():
    if not AVAILABLE or os.environ.get("BHAP_PLAIN") or os.environ.get("TERM") == "dumb":
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def short_path(path):
    path, home = str(path), str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


class SlashAndPathCompleter(Completer):
    def __init__(self, session):
        self.session = session

    def get_completions(self, document, complete_event):
        from .repl import COMMANDS
        text = document.text_before_cursor
        lang = 0 if config.load()["language"] == "id" else 1
        if text.startswith("/") and " " not in text:
            for name, desc in COMMANDS.items():
                if name.startswith(text) and name != "/quit":
                    yield Completion(name + " ", start_position=-len(text), display=name, display_meta=desc[lang])
            return
        word = document.get_word_before_cursor(WORD=True)
        if text.startswith(("/upload ", "/add ", "/open ")) or word.startswith(("/", "./", "../", "~", "@")):
            prefix = word[1:] if word.startswith("@") else word
            for candidate in self.session._complete_path(prefix):
                display = candidate.rstrip().rstrip("/").rsplit("/", 1)[-1] + ("/" if candidate.endswith("/") else "")
                yield Completion(candidate.rstrip() if not candidate.endswith("/") else candidate,
                                 start_position=-len(prefix), display=display)


class Tui:
    def __init__(self, session):
        self.session = session
        self.style = Style.from_dict(STYLE)
        history = InMemoryHistory()
        if session.ctx.settings["save_history"]:
            try:
                history = FileHistory(str(session.history_file))
            except OSError:
                pass
        bindings = KeyBindings()

        @bindings.add("escape", "enter")  # Alt+Enter: newline inside the message
        def _(event):
            event.current_buffer.insert_text("\n")

        @bindings.add("c-l")
        def _(event):
            event.app.renderer.clear()

        self.prompt_session = PromptSession(history=history, completer=SlashAndPathCompleter(session),
                                            complete_while_typing=True, key_bindings=bindings, style=self.style,
                                            reserve_space_for_menu=0, mouse_support=False, erase_when_done=True)
        self.state = None

    # ------------------------------------------------------------------ helpers
    def echo(self, fragments):
        print_formatted_text(FormattedText(fragments), style=self.style)

    def width(self):
        try:
            return get_app().output.get_size().columns
        except Exception:  # noqa: BLE001 - no running app yet
            import shutil
            return shutil.get_terminal_size((100, 30)).columns

    def refresh_state(self):
        from .commands.common import readiness
        from .core import library
        s = self.session
        self.state = {"ready": readiness(s.ctx.settings), "stats": library.stats(s.conn, s.ctx.wid),
                      "stale": library.stale_count(s.conn, s.ctx.wid, s.ctx.settings["embedding_model"])}

    def status_words(self):
        ready = self.state["ready"]
        if ready["ready"]:
            return "ok", tr("offline siap", "offline ready")
        if not ready["runtime_ok"]:
            return "bad", tr("model tidak tersedia", "model unavailable")
        return "warn", tr("setup belum lengkap", "setup incomplete")

    # ------------------------------------------------------------------ welcome screen
    def welcome(self):
        from .commands.common import setup_hint
        self.refresh_state()
        s = self.session
        sys.stdout.write("\033[2J\033[3J\033[H")
        sys.stdout.flush()
        settings = s.ctx.settings
        klass, words = self.status_words()
        stats = self.state["stats"]
        info = [
            [("class:title", "Bhap"), ("class:muted", f" v{__version__}")],
            [("", f"{settings['llm_model'] or tr('belum ada model', 'no model')}"),
             ("class:muted", f" · {settings['embedding_model'] or '-'} · "), (f"class:{klass}", f"● {words}")],
            [("class:muted", short_path(s.cwd))],
            [("class:muted", "Workspace: "), ("", s.ctx.workspace),
             ("class:muted", f" · {stats['indexed']} {tr('dokumen', 'documents')}")],
        ]
        self.echo([("", "\n")])
        for index, art in enumerate(MASCOT):
            line = [("", "  "), ("class:accent", art), ("", "   ")]
            line += info[index] if index < len(info) else []
            self.echo(line)
        self.echo([("", "")])
        notices = []
        if not self.state["ready"]["ready"]:
            notices.append(("class:warn", setup_hint(self.state["ready"])))
        if self.state["stale"]:
            notices.append(("class:warn", tr(f"{self.state['stale']} dokumen perlu reindex → /reindex",
                                             f"{self.state['stale']} documents need reindexing → /reindex")))
        if stats["indexed"] == 0:
            title = tr("Selamat datang di Bhap", "Welcome to Bhap")
            body = tr("Unggah materi dengan /upload <lokasi file>, lalu tanyakan apa saja tentang isinya.",
                      "Upload material with /upload <file path>, then ask anything about it.")
        else:
            title = tr("Siap menjawab dari materimu", "Ready to answer from your material")
            body = tr("Ketik pertanyaan langsung. /upload untuk menambah file, / untuk semua perintah.",
                      "Just type a question. /upload adds files, / shows every command.")
        used = 2 + len(MASCOT) + 1 + 2 + len(notices)  # leading gap, mascot, blank, title/body, notices
        self.echo([("", "  "), ("class:warm", title)])
        self.echo([("", "  "), ("", body)])
        for style, text in notices:
            self.echo([("", "  "), (style, text)])
        # push the input box to the bottom of the window, like a full-screen app
        import shutil
        rows = shutil.get_terminal_size((100, 30)).lines
        sys.stdout.write("\n" * max(1, rows - used - 6))  # box = badge, rule, input, rule, status (+1 spare)
        sys.stdout.flush()

    # ------------------------------------------------------------------ input box
    def _message(self):
        width = max(20, self.width())
        s = self.session
        badge = f"◐ {s.mode} · /mode"
        if s.scope:
            badge = f"⌖ {s.scope.describe()} · " + badge
        return FormattedText([
            ("", " " * max(0, width - len(badge) - 2)), ("class:muted", badge), ("", "\n"),
            ("class:rule", "─" * width), ("", "\n"),
            ("class:prompt", "❯ "),
        ])

    def _toolbar(self):
        width = max(20, self.width())
        klass, words = self.status_words()
        stats = self.state["stats"]
        model = self.session.ctx.settings["llm_model"] or "-"
        left = f"  ● {words} · {model} · {stats['indexed']} {tr('dok', 'docs')} · {self.session.ctx.workspace}"
        right = tr("/upload <file> · / perintah · Ctrl+D keluar", "/upload <file> · / commands · Ctrl+D quit")
        gap = max(2, width - len(left) - len(right) - 2)
        return FormattedText([
            ("class:rule", "─" * width), ("", "\n"),
            (f"class:{klass}", "  ●"), ("class:muted", left[3:]), ("", " " * gap), ("class:muted", right),
        ])

    def _placeholder(self):
        if self.state["stats"]["indexed"] == 0:
            return FormattedText([("class:placeholder", tr('Coba "/upload ~/Downloads/materi.pdf"',
                                                            'Try "/upload ~/Downloads/notes.pdf"'))])
        return FormattedText([("class:placeholder", tr("Tanya apa saja tentang materimu…",
                                                        "Ask anything about your material…"))])

    def read(self):
        self.refresh_state()
        text = self.prompt_session.prompt(self._message, bottom_toolbar=self._toolbar, placeholder=self._placeholder())
        if text.strip():
            lines = text.rstrip().split("\n")
            self.echo([("", "")])
            self.echo([("class:accent", "❯ "), ("class:user", lines[0])])
            for line in lines[1:]:
                self.echo([("", "  "), ("class:user", line)])
        return text
