"""Interactive-by-default terminal session (`bhap` with no subcommand).

Input routing priority (PRD §26.4):
  1. exact registered slash command      /help, /add, /exit, …
  2. built-in navigation                 cd, pwd, ls, clear
  3. filesystem path(s)                  ./a.pdf, ~/x.docx, /home/u/y.pptx, C:\\z.pdf, "with space.pdf"
  4. @file references inside a question  Jelaskan @./materi/graph.pdf
  5. natural-language question           everything else
The session cwd is virtual: `cd` never changes the parent shell, and navigating never
reads or indexes files on its own.
"""
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import __version__, config, ui
from .commands.common import Context, print_sources, readiness, setup_hint, status_badge, run_ingest
from .commands.library import confirm_directory, preview_chunk
from .commands.qa import answer_question, build_scope
from .core import history, ingest, library, retrieval
from .errors import BhapError, ModelError, NetworkBlockedError
from .i18n import set_language, t, tr
from .parsers import SUPPORTED

try:
    import readline
except ImportError:  # Windows without pyreadline3
    readline = None

COMMANDS = {
    "/upload": ("unggah & indeks file/folder: /upload <lokasi file>", "upload & index a file/folder: /upload <file path>"),
    "/help": ("tampilkan bantuan", "show help"),
    "/add": ("impor file/folder: /add <path...> [--recursive]", "import files/folders: /add <path...> [--recursive]"),
    "/sources": ("sumber dari jawaban terakhir", "sources of the last answer"),
    "/open": ("preview sumber: /open <n> [--app]", "preview a source: /open <n> [--app]"),
    "/docs": ("daftar dokumen di workspace", "list documents in the workspace"),
    "/remove": ("hapus dokumen: /remove <id|nama>", "delete a document: /remove <id|name>"),
    "/reindex": ("indeks ulang dokumen yang perlu (model/format berubah)", "reindex stale documents"),
    "/search": ("cari potongan sumber: /search <kata kunci>", "search source passages: /search <query>"),
    "/mode": ("mode jawaban: strict | general | brainstorm", "answer mode: strict | general | brainstorm"),
    "/scope": ("batasi sumber: /scope <id|nama|folder/>… | clear", "restrict sources: /scope <id|name|folder/>… | clear"),
    "/workspace": ("workspace: list | use <nama> | create <nama>", "workspace: list | use <name> | create <name>"),
    "/model": ("model: list | use <nama> | embedding <nama>", "models: list | use <name> | embedding <name>"),
    "/summarize": ("ringkas dokumen/topik: /summarize <id|topik> [short|medium|full]", "summarize: /summarize <id|topic> [short|medium|full]"),
    "/compare": ("bandingkan dua dokumen: /compare <a> <b> [topik]", "compare two documents: /compare <a> <b> [topic]"),
    "/teach": ("ajari dari nol: /teach <topik>", "teach from zero: /teach <topic>"),
    "/tutor": ("tutor Socratic: /tutor <topik>", "Socratic tutor: /tutor <topic>"),
    "/quiz": ("kuis dari materi: /quiz <topik>", "quiz from your material: /quiz <topic>"),
    "/flashcards": ("buat flashcards: /flashcards <topik>", "make flashcards: /flashcards <topic>"),
    "/review": ("ulang kartu/soal jatuh tempo", "review due cards/questions"),
    "/evidence": ("tampilkan/sembunyikan peta bukti", "toggle the evidence map"),
    "/save": ("simpan jawaban terakhir sebagai catatan: /save [judul]", "save the last answer as a note: /save [title]"),
    "/notes": ("daftar catatan", "list notes"),
    "/history": ("percakapan tersimpan", "saved conversations"),
    "/new": ("mulai percakapan baru", "start a new conversation"),
    "/clear": ("bersihkan konteks sesi (riwayat tetap)", "clear session context (history kept)"),
    "/status": ("status engine, model, workspace", "engine, model and workspace status"),
    "/doctor": ("pemeriksaan kesiapan offline", "offline readiness check"),
    "/lang": ("bahasa antarmuka: id | en", "UI language: id | en"),
    "/exit": ("keluar", "quit"),
    "/quit": ("keluar", "quit"),
}
BUILTINS = {"cd", "pwd", "ls", "clear", "dir"}
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _rl_wrap(text):
    """Mark ANSI sequences as zero-width for readline so line editing stays aligned."""
    if readline is None or not ui.color_enabled():
        return text
    return re.sub(r"(\033\[[0-9;]*m)", "\001\\1\002", text)


def _safe(check, path):
    """Path checks that never raise: overlong names, NUL bytes, permission errors mean "no"."""
    try:
        return check(path)
    except (OSError, ValueError):
        return False


def exists(path):
    return _safe(Path.exists, path)


def is_file(path):
    return _safe(Path.is_file, path)


def is_dir(path):
    return _safe(Path.is_dir, path)


MAX_PATH_INPUT = 4096


def short_path(path):
    path = str(path)
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


class Session:
    def __init__(self, conn, args):
        self.conn = conn
        self.cwd = Path.cwd()
        self.ctx = Context(conn, args, cwd=self.cwd)
        self.explicit_workspace = bool(getattr(args, "workspace", None))
        self.mode = self.ctx.settings["mode"]
        self.scope = retrieval.Scope()
        self.conversation_id = None
        self.last = None
        self.evidence = False
        self.history_file = config.path("cache") / "repl_history"
        self.tui = None

    # ----------------------------------------------------------------- lifecycle
    def header(self):
        state = readiness(self.ctx.settings)
        stats = library.stats(self.conn, self.ctx.wid)
        model = self.ctx.settings["llm_model"] or "-"
        left = ui.style("  Bhap · Offline AI", "bold") + ui.style(f"  v{__version__}", "gray")
        ui.out(left)
        ui.out(f"  {ui.style(short_path(self.cwd), 'cyan')}    Workspace: {ui.style(self.ctx.workspace, 'bold')}")
        ui.out(f"  Model: {model}    {tr('Pengetahuan', 'Knowledge')}: {stats['indexed']} "
               f"{tr('dokumen', 'documents')}    {status_badge(state)}")
        if not state["ready"]:
            ui.out(ui.style("  → " + setup_hint(state), "yellow"))
        stale = library.stale_count(self.conn, self.ctx.wid, self.ctx.settings["embedding_model"])
        if stale:
            ui.out(ui.style("  " + t("docs.stale", count=stale), "yellow"))
        ui.out(ui.style("  " + t("repl.help_hint"), "gray"))
        ui.out()

    def setup_readline(self):
        if readline is None:
            return
        try:
            if self.ctx.settings["save_history"] and self.history_file.exists():
                readline.read_history_file(str(self.history_file))
            readline.set_history_length(2000)
        except OSError:
            pass
        readline.set_completer_delims(" \t\n\"'@=")
        readline.set_completer(self.complete)
        if "libedit" in (getattr(readline, "__doc__", "") or ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")

    def save_readline(self):
        if readline is None or not self.ctx.settings["save_history"]:
            return
        try:
            readline.write_history_file(str(self.history_file))
            if os.name != "nt":
                self.history_file.chmod(0o600)
        except OSError:
            pass

    def complete(self, text, state):
        try:
            buffer = readline.get_line_buffer()
            if buffer.startswith("/") and " " not in buffer:
                matches = [c + " " for c in COMMANDS if c.startswith(text)]
            else:
                matches = self._complete_path(text)
            return matches[state] if state < len(matches) else None
        except Exception:  # noqa: BLE001 - completion must never crash the session
            return None

    def _complete_path(self, text):
        expanded = os.path.expanduser(text)
        base = Path(expanded) if os.path.isabs(expanded) else self.cwd / expanded
        directory = base if text.endswith(("/", os.sep)) else base.parent
        prefix = "" if text.endswith(("/", os.sep)) else base.name
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return []
        results = []
        for entry in entries:
            if not entry.name.startswith(prefix) or (entry.name.startswith(".") and not prefix.startswith(".")):
                continue
            head = text[: len(text) - len(prefix)]
            results.append(head + entry.name + ("/" if entry.is_dir() else " "))
        return results[:200]

    def prompt(self):
        scope = ui.style(f"[{self.scope.describe()}] ", "magenta") if self.scope else ""
        mode = "" if self.mode == "strict" else ui.style(f"({self.mode}) ", "yellow")
        return _rl_wrap(scope + mode + ui.style("bhap ", "bold") + ui.style(ui.symbol("arrow", sys.stdout) + " ", "cyan"))

    def read(self):
        if self.tui is not None:
            return self.tui.read()
        line = input(self.prompt())
        if line.strip() == '"""':
            lines = []
            while True:
                part = input(_rl_wrap(ui.style("  … ", "gray")))
                if part.strip() == '"""':
                    break
                lines.append(part)
            return "\n".join(lines)
        while line.endswith("\\"):
            line = line[:-1] + "\n" + input(_rl_wrap(ui.style("  … ", "gray")))
        return line

    def run(self):
        from . import tui
        if tui.enabled() and ui.color_enabled():
            self.tui = tui.Tui(self)
            ui.set_chat_style(True)
            self.tui.welcome()
        else:
            self.setup_readline()
            self.header()
        try:
            while True:
                try:
                    line = self.read().strip()
                except KeyboardInterrupt:
                    ui.err("\n" + ui.style(t("repl.ctrl_c"), "gray"))
                    continue
                except EOFError:
                    ui.err()
                    break
                if not line:
                    continue
                try:
                    if self.route(line) == "exit":
                        break
                except KeyboardInterrupt:
                    ui.err()
                    ui.warn(t("ask.cancelled"))
                except NetworkBlockedError as exc:
                    ui.error(str(exc))
                except BhapError as exc:
                    ui.error(str(exc), exc.hint)
                except (OSError, ValueError) as exc:
                    ui.error(str(exc))
                except Exception as exc:  # noqa: BLE001 - a bug must never kill the user's session
                    from .logs import log
                    log("repl.crash", level="error", error=f"{type(exc).__name__}: {exc}")
                    ui.error(f"{type(exc).__name__}: {exc}", "bhap logs")
        finally:
            self.save_readline()
        ui.out(ui.style(t("repl.bye"), "gray"))

    # ----------------------------------------------------------------- routing
    def route(self, line):
        head = line.split(None, 1)[0]
        rest = line[len(head):].strip()
        if head in COMMANDS:
            return self.command(head, rest)
        if head == "/":
            return self.command("/help", "")
        if head in BUILTINS:
            return self.builtin(head, rest)
        if line.startswith("!"):
            return self.shell(line[1:].strip())
        handled = self.try_paths(line)
        if handled:
            return None
        if head.startswith("/") and "/" not in head[1:] and not exists(Path(head)):
            ui.error(t("repl.unknown_cmd", cmd=head))
            close = [c for c in COMMANDS if c.startswith(head[:3])]
            if close:
                ui.info(" ".join(close))
            return None
        return self.ask(line)

    def resolve(self, raw):
        raw = os.path.expandvars(raw.strip().strip('"').strip("'"))
        path = Path(raw).expanduser()
        return path if path.is_absolute() else (self.cwd / path)

    @staticmethod
    def looks_like_path(token):
        return (token.startswith(("./", "../", "~/", "~\\", ".\\", "..\\", "/")) or WINDOWS_PATH.match(token) is not None
                or (Path(token).suffix.lower() in SUPPORTED | {".doc", ".ppt"} and " " not in token.strip()))

    def try_paths(self, line):
        """Steps 3: input that is a path (or several pasted paths). Returns True if handled."""
        if "\n" in line.strip() or len(line) > MAX_PATH_INPUT:
            return False  # pasted text/code is a message, never a path
        try:
            tokens = shlex.split(line, posix=os.name != "nt")
        except ValueError:
            tokens = [line]
        if not tokens:
            return False
        whole = line.strip().strip('"').strip("'")
        candidates = [whole] if len(tokens) > 1 and exists(self.resolve(whole)) else tokens
        paths = [self.resolve(token) for token in candidates]
        if all(exists(path) for path in paths):
            explicit = all(self.looks_like_path(token) or os.sep in token
                           or Path(token).suffix.lower() in SUPPORTED for token in candidates)
            if not explicit:
                if len(candidates) > 1 or not ui.confirm(t("repl.ambiguous", text=whole)):
                    return False
            self.import_paths(paths)
            return True
        bare_command = tokens[0].startswith("/") and "/" not in tokens[0][1:] and not Path(tokens[0]).suffix
        if len(tokens) == 1 and self.looks_like_path(tokens[0]) and not bare_command:
            ui.error(t("repl.path_missing", path=tokens[0]))
            return True
        return False

    def import_paths(self, paths, recursive=None):
        files, skipped, dirs = [], {}, [p for p in paths if p.is_dir()]
        for path in paths:
            found, skip = ingest.scan([path], cwd=self.cwd, recursive=path.is_dir() if recursive is None else recursive,
                                      max_mb=self.ctx.settings["max_file_mb"])
            files += found
            for reason, items in skip.items():
                skipped.setdefault(reason, []).extend(items)
        if dirs:
            files = confirm_directory(files, skipped, ", ".join(short_path(d) for d in dirs))
        elif skipped:
            ui.warn(t("ingest.dir_skipped", details=", ".join(f"{len(v)} {k}" for k, v in skipped.items())))
        if not files:
            return []
        run_ingest(self.ctx, files, confirm_change=lambda row: ui.confirm(
            t("ingest.changed", file=row["filename"]), default=True))
        return files

    # ----------------------------------------------------------------- builtins
    def builtin(self, name, rest):
        if name == "pwd":
            ui.out(str(self.cwd))
        elif name == "clear":
            ui.out("\033[2J\033[H" if sys.stdout.isatty() else "", end="")
        elif name in ("ls", "dir"):
            target = self.resolve(rest) if rest else self.cwd
            if not is_dir(target):
                raise BhapError(t("repl.path_missing", path=rest))
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for entry in entries:
                if entry.name.startswith(".") and "-a" not in rest:
                    continue
                if entry.is_dir():
                    ui.out(ui.style(entry.name + "/", "blue", "bold"))
                elif entry.suffix.lower() in SUPPORTED:
                    ui.out(ui.style(entry.name, "green"))
                else:
                    ui.out(ui.style(entry.name, "gray"))
        elif name == "cd":
            parts = shlex.split(rest, posix=os.name != "nt") if rest else ["~"]
            if len(parts) != 1:
                raise BhapError(tr("Gunakan: cd <direktori>", "Usage: cd <directory>"))
            target = Path.home() if parts[0] == "~" else self.resolve(parts[0])
            if parts[0] == "-":
                target = getattr(self, "_previous_cwd", self.cwd)
            target = target.resolve()
            if not is_dir(target):
                raise BhapError(t("repl.path_missing", path=parts[0]))  # cwd unchanged
            self._previous_cwd, self.cwd = self.cwd, target
            ui.ok(t("repl.cwd", path=short_path(self.cwd)))
            self.maybe_switch_workspace()
        return None

    def maybe_switch_workspace(self):
        if self.explicit_workspace or not config.load()["auto_switch"]:
            return
        _, project = config.find_project(self.cwd)
        name = project.get("workspace")
        if name and name != self.ctx.workspace:
            self.ctx.workspace, self.ctx.wid = name, library.ensure_workspace(self.conn, name)
            self.conversation_id, self.scope = None, retrieval.Scope()
            ui.info(t("repl.auto_switch", name=name))

    def shell(self, command):
        if not command:
            return None
        if not config.load()["allow_shell"]:
            ui.warn(t("repl.shell_disabled"))
            return None
        if ui.confirm(t("repl.shell_confirm", cwd=short_path(self.cwd), cmd=command)):
            subprocess.run(command, shell=True, cwd=self.cwd)  # noqa: S602 - explicit, confirmed user command
        return None

    # ----------------------------------------------------------------- chat
    def ask(self, line, scope=None):
        question, at_scope = self.extract_at_refs(line)
        if at_scope is False:
            return None
        if not question.strip():
            return None
        scope = scope or at_scope or self.scope
        stats = library.stats(self.conn, self.ctx.wid)
        if not stats["indexed"] and self.mode == "strict":
            ui.info(t("ask.no_docs", name=self.ctx.workspace))
            return None
        if self.conversation_id is None and self.ctx.settings["save_history"]:
            self.conversation_id = history.start(self.conn, self.ctx.wid, self.mode)
        result = answer_question(self.ctx, question, mode=self.mode, scope=scope,
                                 conversation_id=self.conversation_id, stream=True, evidence=self.evidence)
        if result["status"] != "cancelled":
            self.last = result
        ui.out()
        return None

    def extract_at_refs(self, line):
        """@path references: ensure each file is indexed (with consent) and scope the question to it."""
        refs = re.findall(r'(?:^|(?<=\s))@("[^"]+"|\'[^\']+\'|\S+)', line)
        if not refs:
            return line, None
        doc_ids = []
        for ref in refs:
            bare = ref.strip("\"'")
            path = self.resolve(ref)
            if not is_file(path):
                if self.looks_like_path(bare) or ref[:1] in "\"'":
                    ui.error(t("repl.path_missing", path=bare))
                    return line, False
                continue  # @Override, @Test, @username … are text, not file references
            row = self.conn.execute("SELECT id, sha256 FROM documents WHERE workspace_id=? AND original_path=?",
                                    (self.ctx.wid, str(path.resolve()))).fetchone()
            if not row or row["sha256"] != ingest.sha256_file(path):
                if not ui.confirm(t("repl.at_import", file=path.name), default=True):
                    return line, False
                run_ingest(self.ctx, [path.resolve()])
                row = self.conn.execute("SELECT id FROM documents WHERE workspace_id=? AND original_path=?",
                                        (self.ctx.wid, str(path.resolve()))).fetchone()
                if not row:
                    return line, False
            doc_ids.append(row["id"])
            line = line.replace("@" + ref, path.name)
        return line, retrieval.Scope(doc_ids)

    # ----------------------------------------------------------------- slash commands
    def command(self, name, rest):
        handler = getattr(self, "cmd_" + name[1:], None)
        if handler is None:
            ui.error(t("repl.unknown_cmd", cmd=name))
            return None
        return handler(rest)

    def cmd_exit(self, rest):
        return "exit"

    cmd_quit = cmd_exit

    def cmd_help(self, rest):
        ui.out(ui.style(tr("Perintah", "Commands"), "bold"))
        lang = 0 if config.load()["language"] == "id" else 1
        for name, desc in COMMANDS.items():
            if name == "/quit":
                continue
            ui.out(f"  {ui.style(name.ljust(12), 'cyan')} {desc[lang]}")
        ui.out(ui.style(tr("Navigasi", "Navigation"), "bold"))
        ui.out(tr("  cd <dir> · pwd · ls · clear — hanya navigasi, tidak membaca/mengindeks file",
                  "  cd <dir> · pwd · ls · clear — navigation only, never reads or indexes files"))
        ui.out(tr("  Tempel path file (./a.pdf, ~/b.docx, \"c d.pptx\") untuk mengimpor; folder minta konfirmasi.",
                  "  Paste a file path (./a.pdf, ~/b.docx, \"c d.pptx\") to import; folders ask first."))
        ui.out(tr("  @./file.pdf di dalam pertanyaan membatasi jawaban ke file itu.",
                  "  @./file.pdf inside a question restricts the answer to that file."))
        ui.out(tr("  Akhiri baris dengan \\ atau gunakan \"\"\" untuk input multi-baris. Ctrl+C membatalkan jawaban, Ctrl+D keluar.",
                  "  End a line with \\ or use \"\"\" for multi-line input. Ctrl+C cancels an answer, Ctrl+D quits."))
        ui.out(tr("  !<perintah> menjalankan shell (nonaktif default, selalu dikonfirmasi).",
                  "  !<command> runs a shell command (off by default, always confirmed)."))
        return None

    def cmd_add(self, rest):
        if not rest:
            raise BhapError("/add <path...> [--recursive]")
        tokens = shlex.split(rest, posix=os.name != "nt")
        recursive = "--recursive" in tokens or "-r" in tokens
        paths = [self.resolve(tok) for tok in tokens if tok not in ("--recursive", "-r")]
        missing = [str(p) for p in paths if not exists(p)]
        if missing:
            raise BhapError(t("repl.path_missing", path=", ".join(missing)))
        self.import_paths(paths, recursive=recursive or None)
        return None

    def cmd_upload(self, rest):
        """/upload <path> — the whole rest of the line may be one path, spaces included (no quotes needed)."""
        raw = rest.strip()
        if not raw:
            raise BhapError(tr("Gunakan: /upload <lokasi file atau folder>", "Usage: /upload <file or folder path>"),
                            hint="/upload ~/Downloads/materi.pdf")
        whole = self.resolve(raw)
        if exists(whole):
            self.import_paths([whole])
            return None
        return self.cmd_add(raw)

    def cmd_remove(self, rest):
        if not rest.strip():
            raise BhapError("/remove <id|name>")
        row = library.find_document(self.conn, self.ctx.wid, rest.strip())
        if ui.confirm(t("docs.confirm_remove", count=1) + f" ({row['filename']})"):
            ingest.remove(self.conn, self.ctx.wid, row["id"])
            ui.ok(t("docs.removed", id=row["id"], file=row["filename"]))
        return None

    def cmd_reindex(self, rest):
        from .commands.library import cmd_docs
        cmd_docs(_Args(action="reindex", stale=not rest.strip(), refs=rest.split(), json=False,
                       workspace=self.ctx.workspace))
        return None

    def cmd_sources(self, rest):
        if not self.last or not self.last.get("citations"):
            ui.info(t("repl.no_last"))
            return None
        print_sources(self.last["citations"])
        ui.out(ui.style("  /open <n> · /open <n> --app", "gray"))
        return None

    def cmd_open(self, rest):
        tokens = rest.split()
        if not tokens:
            raise BhapError("/open <n> [--app]")
        ref = tokens[0].strip("[]#")
        cite = None
        if self.last and ref.isdigit():
            cite = next((c for c in self.last.get("citations", []) if c["marker"] == int(ref)), None)
        chunk_id = cite["chunk_id"] if cite else int(ref) if ref.isdigit() else None
        if chunk_id is None:
            raise BhapError("/open <n> [--app]")
        row = preview_chunk(self.conn, self.ctx.wid, chunk_id, context=1, highlight=cite["excerpt"] if cite else None)
        if "--app" in tokens:
            from .commands.library import open_original
            open_original(library.document(self.conn, self.ctx.wid, row["document_id"]),
                          page=row["page_no"] or row["slide_no"], settings=self.ctx.settings)
        return None

    def cmd_docs(self, rest):
        from .commands.library import cmd_docs
        cmd_docs(_Args(action="list", folder=rest or None, json=False, workspace=self.ctx.workspace, refs=[]))
        return None

    def cmd_search(self, rest):
        if not rest:
            raise BhapError("/search <query>")
        from .commands.qa import cmd_search
        cmd_search(_Args(query=rest, json=False, workspace=self.ctx.workspace, doc=self._scope_refs(),
                         folder=self.scope.folders, limit=self.ctx.settings["top_k"], no_diversify=False))
        return None

    def _scope_refs(self):
        return [str(i) for i in self.scope.doc_ids]

    def cmd_mode(self, rest):
        if rest:
            if rest not in ("strict", "general", "brainstorm"):
                raise BhapError("/mode strict | general | brainstorm")
            self.mode = rest
        ui.ok(t("repl.mode", mode=f"{self.mode} — {t('mode.' + self.mode)}"))
        return None

    def cmd_scope(self, rest):
        if rest in ("", "show"):
            ui.info(t("repl.scope", scope=self.scope.describe() or t("repl.scope_all")))
            return None
        if rest in ("clear", "all", "reset"):
            self.scope = retrieval.Scope()
            ui.ok(t("repl.scope", scope=t("repl.scope_all")))
            return None
        doc_refs, folders = [], []
        for token in shlex.split(rest):
            (folders if token.endswith("/") or token.startswith("folder:") else doc_refs).append(
                token[7:] if token.startswith("folder:") else token)
        self.scope = build_scope(self.ctx, doc_refs, folders)
        names = [library.document(self.conn, self.ctx.wid, i)["filename"] for i in self.scope.doc_ids]
        ui.ok(t("repl.scope", scope=", ".join(names + [f + "/" for f in self.scope.folders])))
        return None

    def cmd_workspace(self, rest):
        tokens = rest.split(None, 1)
        action = tokens[0] if tokens else "current"
        name = tokens[1].strip() if len(tokens) > 1 else ""
        if action == "list":
            for row in library.workspaces(self.conn):
                marker = ui.style("●", "green") if row["name"] == self.ctx.workspace else " "
                ui.out(f" {marker} {row['name']}  {ui.style(str(row['docs']) + ' docs', 'gray')}")
        elif action in ("use", "create"):
            if not name:
                raise BhapError(f"/workspace {action} <name>")
            if action == "create":
                library.create_workspace(self.conn, name)
                ui.ok(t("ws.created", name=name))
            row = library.get_workspace(self.conn, name)
            self.ctx.workspace, self.ctx.wid = row["name"], row["id"]
            self.explicit_workspace = True
            self.conversation_id, self.scope, self.last = None, retrieval.Scope(), None
            ui.ok(t("ws.active", name=name))
        else:
            ui.out(self.ctx.workspace)
        return None

    cmd_ws = cmd_workspace

    def cmd_model(self, rest):
        from .adapters import runtime
        tokens = rest.split()
        rt = runtime(self.ctx.settings)
        if not tokens:
            ui.out(f"chat: {self.ctx.settings['llm_model'] or '-'}  ·  embedding: {self.ctx.settings['embedding_model'] or '-'}"
                   f"  ·  runtime: {self.ctx.settings['runtime']}")
        elif tokens[0] == "list":
            for item in rt.models():
                ui.out(f"  {item['name']}  {ui.style(ui.human_size(item['size']) if item.get('size') else '', 'gray')}")
        elif tokens[0] in ("use", "embedding") and len(tokens) > 1:
            if not rt.has_model(tokens[1]):
                raise ModelError(tr(f"Model '{tokens[1]}' tidak tersedia lokal", f"Model '{tokens[1]}' is not available locally"),
                                 hint="/model list")
            key = "embedding_model" if tokens[0] == "embedding" else "llm_model"
            self.ctx.settings = config.update(**{key: tokens[1]})
            ui.ok(f"{key}: {tokens[1]}")
            if key == "embedding_model":
                stale = library.stale_count(self.conn, self.ctx.wid, tokens[1])
                if stale:
                    ui.warn(t("setup.embedding_changed", count=stale))
        else:
            raise BhapError("/model [list | use <name> | embedding <name>]")
        return None

    def cmd_summarize(self, rest):
        from .commands.qa import cmd_summarize
        tokens = rest.rsplit(None, 1)
        level = "medium"
        if len(tokens) == 2 and tokens[1] in ("short", "medium", "full"):
            rest, level = tokens[0], tokens[1]
        if not rest:
            raise BhapError("/summarize <doc-id|topic> [short|medium|full]")
        is_doc = rest.isdigit() or self.conn.execute(
            "SELECT 1 FROM documents WHERE workspace_id=? AND filename=?", (self.ctx.wid, rest)).fetchone()
        cmd_summarize(_Args(ref=rest if is_doc else None, topic=None if is_doc else rest, level=level, output=None,
                            workspace=self.ctx.workspace, doc=self._scope_refs(), folder=self.scope.folders, json=False))
        return None

    def cmd_compare(self, rest):
        from .commands.qa import cmd_compare
        tokens = shlex.split(rest)
        if len(tokens) < 2:
            raise BhapError("/compare <doc-a> <doc-b> [topic]")
        cmd_compare(_Args(a=tokens[0], b=tokens[1], topic=" ".join(tokens[2:]) or None, output=None,
                          workspace=self.ctx.workspace, json=False))
        return None

    def _study(self, action, rest, **extra):
        from .commands.study_cmd import cmd_study
        defaults = {"count": 5, "kind": "mixed", "difficulty": "medium", "doc": self._scope_refs(),
                    "folder": self.scope.folders, "json": False, "text": None, "format": "md", "output": None,
                    "yes": False}
        defaults.update(extra)
        cmd_study(_Args(action=action, topic=rest or None, workspace=self.ctx.workspace, **defaults))
        return None

    def cmd_teach(self, rest):
        return self._study("teach", rest)

    def cmd_tutor(self, rest):
        return self._study("tutor", rest)

    def cmd_quiz(self, rest):
        return self._study("quiz", rest)

    def cmd_flashcards(self, rest):
        return self._study("flashcards", rest, count=10)

    def cmd_review(self, rest):
        return self._study("review", rest, count=20)

    def cmd_evidence(self, rest):
        self.evidence = not self.evidence
        ui.ok(f"{t('ask.evidence_map')}: {'on' if self.evidence else 'off'}")
        return None

    def cmd_save(self, rest):
        if not self.last or self.last["status"] not in ("answered", "uncited"):
            ui.info(t("repl.no_last"))
            return None
        title = rest or self.last["question"][:80]
        body = f"**{tr('Pertanyaan', 'Question')}:** {self.last['question']}\n\n{self.last['answer']}"
        note_id = history.add_note(self.conn, self.ctx.wid, title, body, self.last["citations"])
        ui.ok(t("repl.note_saved", id=note_id, n=len(self.last["citations"])))
        return None

    def cmd_notes(self, rest):
        from .commands.qa import cmd_notes
        cmd_notes(_Args(action="show" if rest.isdigit() else "list", title=rest or None, body=None,
                        output=None, json=False, workspace=self.ctx.workspace))
        return None

    def cmd_history(self, rest):
        from .commands.qa import cmd_history
        cmd_history(_Args(action="show" if rest.isdigit() else "list", id=int(rest) if rest.isdigit() else None,
                          limit=15, json=False, workspace=self.ctx.workspace, query=None, yes=False))
        return None

    def cmd_new(self, rest):
        self.conversation_id, self.last = None, None
        ui.ok(t("repl.new_conversation"))
        return None

    def cmd_clear(self, rest):
        self.conversation_id, self.last, self.scope = None, None, retrieval.Scope()
        ui.ok(t("repl.cleared"))
        return None

    def cmd_status(self, rest):
        from .commands.system import cmd_status
        cmd_status(_Args(json=False, workspace=self.ctx.workspace))
        ui.out(f"  {ui.style('cwd'.ljust(14), 'gray')} {short_path(self.cwd)}")
        ui.out(f"  {ui.style('scope'.ljust(14), 'gray')} {self.scope.describe() or t('repl.scope_all')}")
        return None

    def cmd_doctor(self, rest):
        from .commands.system import cmd_doctor
        cmd_doctor(_Args(offline=True, verify="--verify" in rest, json=False))
        return None

    def cmd_lang(self, rest):
        if rest not in ("id", "en"):
            raise BhapError("/lang id | en")
        self.ctx.settings = config.update(language=rest)
        set_language(rest)
        ui.ok(f"language: {rest}")
        return None


class _Args:
    """Minimal argparse.Namespace stand-in for reusing CLI handlers."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        return None


def run(conn, args):
    Session(conn, args).run()
