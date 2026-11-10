"""Command-line entry point: `bhap` (interactive) and scriptable subcommands."""
import argparse
import sqlite3
import sys

from . import __version__, netguard, ui
from .errors import EXIT_INTERRUPTED, EXIT_NETWORK, EXIT_STORAGE, BhapError, NetworkBlockedError, UsageError
from .i18n import set_language, t, tr

EPILOG_ID = """contoh:
  bhap                                   buka sesi interaktif (cd, tempel path file, bertanya)
  bhap doctor --offline                  cek kesiapan offline
  bhap workspace create kuliah --use     buat & aktifkan workspace
  bhap add ./materi --recursive          impor folder (pratinjau + konfirmasi)
  bhap ask "Apa itu normalisasi?" --sources
  echo "Apa itu ERD?" | bhap ask --stdin --json
  bhap study quiz "Dijkstra" --count 5
Exit code: 0 sukses · 2 argumen salah · 3 model/runtime tidak siap · 4 parsing gagal · 5 database · 130 dibatalkan"""

EPILOG_EN = """examples:
  bhap                                   open the interactive session (cd, paste file paths, ask)
  bhap doctor --offline                  offline readiness check
  bhap workspace create uni --use        create & activate a workspace
  bhap add ./notes --recursive           import a folder (preview + confirmation)
  bhap ask "What is normalization?" --sources
  echo "What is an ERD?" | bhap ask --stdin --json
  bhap study quiz "Dijkstra" --count 5
Exit codes: 0 ok · 2 invalid usage · 3 model/runtime not ready · 4 parse failure · 5 database · 130 cancelled"""


def _ws(parser):
    parser.add_argument("-w", "--workspace", help=tr("workspace yang dipakai", "workspace to use"))


def _json(parser):
    parser.add_argument("--json", action="store_true", help=tr("output JSON stabil (tanpa warna/spinner)",
                                                               "stable JSON output (no colors/spinners)"))


def _scope(parser):
    parser.add_argument("--doc", action="append", metavar="ID|NAME",
                        help=tr("batasi ke dokumen (bisa diulang)", "restrict to a document (repeatable)"))
    parser.add_argument("--folder", action="append", help=tr("batasi ke folder virtual", "restrict to a virtual folder"))


def build_parser():
    p = argparse.ArgumentParser(
        prog="bhap", formatter_class=argparse.RawDescriptionHelpFormatter,
        description=tr("Bhap — asisten pengetahuan pribadi yang berjalan offline di terminal.",
                       "Bhap — a private knowledge assistant that runs offline in your terminal."),
        epilog=tr(EPILOG_ID, EPILOG_EN))
    p.add_argument("-V", "--version", action="version", version=f"bhap {__version__}")
    _ws(p)
    p.add_argument("--no-color", action="store_true", help=tr("tanpa warna (juga NO_COLOR=1)", "disable colors (also NO_COLOR=1)"))
    p.add_argument("--lang", choices=["id", "en"], help=tr("bahasa antarmuka", "interface language"))
    p.add_argument("-q", "--quiet", action="store_true", help=tr("kurangi pesan progres", "fewer progress messages"))
    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("chat", help=tr("sesi interaktif (sama dengan `bhap`)", "interactive session (same as `bhap`)"))
    _ws(s)

    s = sub.add_parser("setup", help=tr("pilih/unduh model lokal (unduhan hanya dengan persetujuan)",
                                        "choose/download local models (downloads only with consent)"))
    s.add_argument("--llm", help=tr("model chat", "chat model"))
    s.add_argument("--embedding", help=tr("model embedding", "embedding model"))
    s.add_argument("--runtime", choices=["ollama", "llamacpp"])
    s.add_argument("--download", action="store_true", help=tr("izinkan unduh model yang belum ada (online)",
                                                              "allow downloading missing models (online)"))
    s.add_argument("-y", "--yes", action="store_true")

    s = sub.add_parser("doctor", help=tr("diagnostik & kesiapan offline", "diagnostics & offline readiness"))
    s.add_argument("--offline", action="store_true", help="Offline Readiness Check")
    s.add_argument("--verify", action="store_true", help=tr("uji embedding + chat end-to-end", "end-to-end embedding + chat test"))
    _json(s)

    s = sub.add_parser("status", help=tr("status engine, penyimpanan, model, workspace", "engine, storage, model, workspace status"))
    _ws(s)
    _json(s)

    s = sub.add_parser("workspace", aliases=["ws"], help=tr("kelola workspace", "manage workspaces"))
    s.add_argument("action", choices=["create", "list", "use", "delete", "rename", "link", "info", "current"])
    s.add_argument("name", nargs="?")
    s.add_argument("new_name", nargs="?")
    s.add_argument("--description", "-d")
    s.add_argument("--use", action="store_true", help=tr("langsung aktifkan setelah dibuat", "activate after creating"))
    s.add_argument("-y", "--yes", action="store_true")
    _json(s)

    s = sub.add_parser("add", help=tr("impor PDF/DOCX/PPTX/MD/TXT", "import PDF/DOCX/PPTX/MD/TXT"))
    s.add_argument("paths", nargs="+", help=tr("file, folder, atau - untuk stdin", "files, folders, or - for stdin"))
    _ws(s)
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--folder", help=tr("folder virtual di workspace", "virtual folder inside the workspace"))
    s.add_argument("--exclude", action="append", metavar="GLOB")
    s.add_argument("--force", action="store_true", help=tr("indeks ulang walau identik/duplikat", "reindex even if identical/duplicate"))
    s.add_argument("--name", help=tr("nama dokumen untuk input stdin", "document name for stdin input"))
    s.add_argument("-y", "--yes", action="store_true", help=tr("lewati konfirmasi folder", "skip folder confirmation"))
    _json(s)

    s = sub.add_parser("docs", help=tr("kelola dokumen", "manage documents"))
    s.add_argument("action", choices=["list", "show", "remove", "reindex", "check", "open", "move", "versions"])
    s.add_argument("refs", nargs="*", metavar="ID|NAME")
    _ws(s)
    s.add_argument("--folder")
    s.add_argument("--all", action="store_true")
    s.add_argument("--stale", action="store_true")
    s.add_argument("--changed", action="store_true")
    s.add_argument("--chunks", action="store_true", help=tr("tampilkan semua chunk", "show all chunks"))
    s.add_argument("--page", type=int)
    s.add_argument("-y", "--yes", action="store_true")
    _json(s)

    s = sub.add_parser("open", help=tr("preview sumber berdasarkan chunk id", "preview a source by chunk id"))
    s.add_argument("chunk_id", type=int)
    s.add_argument("--app", action="store_true", help=tr("buka file asli di aplikasi", "open the original in an app"))
    s.add_argument("--context", type=int, default=1)
    _ws(s)

    s = sub.add_parser("ask", help=tr("tanya sekali (cocok untuk skrip)", "one-shot question (script friendly)"))
    s.add_argument("prompt", nargs="?")
    _ws(s)
    s.add_argument("--stdin", action="store_true")
    s.add_argument("--mode", choices=["strict", "general", "brainstorm"])
    _scope(s)
    s.add_argument("--sources", action="store_true", help=tr("tampilkan konteks sumber lengkap", "show full source context"))
    s.add_argument("--evidence", action="store_true", help=tr("peta klaim → bukti", "claim → evidence map"))
    s.add_argument("-c", "--continue", dest="continue_", action="store_true",
                   help=tr("lanjutkan percakapan terakhir", "continue the last conversation"))
    s.add_argument("--no-stream", action="store_true")
    s.add_argument("--no-save", action="store_true", help=tr("jangan simpan ke riwayat", "do not save to history"))
    _json(s)

    s = sub.add_parser("search", help=tr("cari potongan sumber (hybrid)", "search source passages (hybrid)"))
    s.add_argument("query")
    _ws(s)
    _scope(s)
    s.add_argument("-k", "--limit", type=int, default=8)
    s.add_argument("--no-diversify", action="store_true")
    _json(s)

    s = sub.add_parser("summarize", help=tr("ringkasan berbasis sumber", "source-grounded summary"))
    s.add_argument("ref", nargs="?", metavar="DOC")
    s.add_argument("--topic")
    s.add_argument("--level", choices=["short", "medium", "full"], default="medium",
                   help=tr("short=30 detik, medium=5 menit, full=lengkap", "short=30s, medium=5 min, full=complete"))
    s.add_argument("-o", "--output", help=tr("simpan sebagai Markdown", "save as Markdown"))
    _ws(s)
    _scope(s)
    _json(s)

    s = sub.add_parser("compare", help=tr("bandingkan dua dokumen (perbedaan & kontradiksi)",
                                          "compare two documents (differences & contradictions)"))
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--topic")
    s.add_argument("-o", "--output")
    _ws(s)
    _json(s)

    s = sub.add_parser("study", help=tr("belajar: kuis, flashcards, tutor, progres", "study: quizzes, flashcards, tutor, progress"))
    s.add_argument("action", choices=["quiz", "flashcards", "review", "sprint", "teach", "tutor", "progress", "note",
                                      "export", "reset"])
    s.add_argument("topic", nargs="?")
    s.add_argument("text", nargs="?")
    _ws(s)
    _scope(s)
    s.add_argument("-n", "--count", type=int, default=5)
    s.add_argument("--kind", choices=["mcq", "essay", "mixed"], default="mixed")
    s.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.add_argument("-o", "--output")
    s.add_argument("-y", "--yes", action="store_true")
    _json(s)

    s = sub.add_parser("notes", help=tr("catatan bertaut sumber", "source-linked notes"))
    s.add_argument("action", choices=["list", "show", "add", "remove", "export"])
    s.add_argument("title", nargs="?", metavar="ID|TITLE")
    s.add_argument("--body")
    s.add_argument("-o", "--output")
    _ws(s)
    _json(s)

    s = sub.add_parser("history", help=tr("riwayat percakapan lokal", "local conversation history"))
    s.add_argument("action", choices=["list", "show", "search", "export", "delete", "clear"])
    s.add_argument("id", nargs="?", type=int)
    s.add_argument("--query", "-s")
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.add_argument("-o", "--output")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("-y", "--yes", action="store_true")
    _ws(s)
    _json(s)

    s = sub.add_parser("model", help=tr("daftar/pilih/unduh model lokal", "list/choose/download local models"))
    s.add_argument("action", choices=["list", "use", "pull", "info"])
    s.add_argument("name", nargs="?")
    s.add_argument("--embedding", action="store_true", help=tr("atur model embedding", "set the embedding model"))
    s.add_argument("-y", "--yes", action="store_true")
    _json(s)

    s = sub.add_parser("backup", help=tr("paket portabel: backup/restore", "portable packs: backup/restore"))
    s.add_argument("action", choices=["create", "restore", "verify", "list"])
    s.add_argument("file", nargs="?")
    _ws(s)
    s.add_argument("--all", action="store_true", help=tr("semua workspace", "all workspaces"))
    s.add_argument("-o", "--output")
    s.add_argument("--no-originals", action="store_true")
    s.add_argument("--encrypt", action="store_true")
    s.add_argument("--as", dest="as_name", help=tr("pulihkan dengan nama workspace baru", "restore under a new workspace name"))
    _json(s)

    s = sub.add_parser("graph", help=tr("peta pengetahuan: dokumen → bagian → konsep bersama",
                                        "knowledge map: documents → sections → shared concepts"))
    s.add_argument("--format", choices=["text", "mermaid", "dot", "json"], default="text")
    s.add_argument("--concepts", type=int, default=25)
    s.add_argument("-o", "--output")
    _ws(s)

    s = sub.add_parser("jobs", help=tr("status job indexing (tahan restart)", "indexing jobs (restart safe)"))
    s.add_argument("action", nargs="?", choices=["list", "retry", "clear"], default="list")
    s.add_argument("ids", nargs="*", type=int)
    s.add_argument("--all", action="store_true")
    _json(s)

    s = sub.add_parser("watch", help=tr("pantau folder & indeks perubahan", "watch folders & index changes"))
    s.add_argument("paths", nargs="+")
    _ws(s)
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--interval", type=int, default=10)
    s.add_argument("--folder")
    s.add_argument("--exclude", action="append")
    s.add_argument("--skip-existing", action="store_true")

    s = sub.add_parser("config", help=tr("pengaturan lokal", "local settings"))
    s.add_argument("action", choices=["show", "get", "set", "reset", "path"])
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    _json(s)

    s = sub.add_parser("logs", help=tr("log lokal terstruktur", "structured local logs"))
    s.add_argument("action", nargs="?", choices=["show", "export"], default="show")
    s.add_argument("-n", "--limit", type=int, default=40)
    s.add_argument("--redact", action="store_true")
    s.add_argument("-o", "--output")

    s = sub.add_parser("version", help=tr("versi dan platform", "version and platform"))
    _json(s)
    return p


NEEDS_RUNTIME = {None, "chat", "ask", "search", "add", "docs", "summarize", "compare", "study", "setup", "doctor",
                 "watch", "jobs", "model"}


def _autostart(args):
    """Bring up the local model runtime for commands that need it (no-op if already running)."""
    if args.command not in NEEDS_RUNTIME:
        return
    from .adapters import autostart
    json_mode = getattr(args, "json", False)

    def notify(host):
        if not json_mode:
            ui.info(tr("Menyalakan Bhap…", "Starting Bhap…"))

    result = autostart.ensure_running(notify=notify)
    if result == "failed" and not json_mode:
        ui.warn(tr("Ollama gagal dinyalakan; lihat log: bhap logs (atau BHAP_HOME/logs/ollama.log)",
                   "Ollama failed to start; see BHAP_HOME/logs/ollama.log"))


def dispatch(args):
    command = args.command
    _autostart(args)
    if command in (None, "chat"):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise UsageError(t("not_tty"), hint="bhap --help")
        from .repl import run
        from .storage import sqlite
        with sqlite.db() as conn:
            from .core.ingest import recover_jobs
            recover_jobs(conn)
            run(conn, args)
        return 0
    from .commands import backup_cmd, library, qa, study_cmd, system
    table = {
        "setup": system.cmd_setup, "doctor": system.cmd_doctor, "status": system.cmd_status,
        "model": system.cmd_model, "config": system.cmd_config, "logs": system.cmd_logs,
        "version": system.cmd_version, "workspace": library.cmd_workspace, "ws": library.cmd_workspace,
        "add": library.cmd_add, "docs": library.cmd_docs, "open": library.cmd_open, "jobs": library.cmd_jobs,
        "watch": library.cmd_watch, "ask": qa.cmd_ask, "search": qa.cmd_search, "summarize": qa.cmd_summarize,
        "compare": qa.cmd_compare, "history": qa.cmd_history, "notes": qa.cmd_notes, "study": study_cmd.cmd_study,
        "backup": backup_cmd.cmd_backup, "graph": qa.cmd_graph,
    }
    return table[command](args)


def _strip_globals(argv):
    cleaned, skip = [], False
    for index, token in enumerate(argv):
        if skip:
            skip = False
            continue
        if token == "--":
            cleaned += argv[index:]
            break
        if token in ("--no-color", "-q", "--quiet") or token.startswith("--lang="):
            continue
        if token == "--lang":
            skip = True
            continue
        cleaned.append(token)
    return cleaned


def _utf8_streams():
    """Windows pipes/consoles may default to cp1252 and crash on ●, →, —; always emit UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv=None):
    _utf8_streams()
    netguard.install()  # offline by construction: only loopback connections are possible
    argv = sys.argv[1:] if argv is None else argv
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=["id", "en"])
    pre.add_argument("--no-color", action="store_true")
    pre.add_argument("-q", "--quiet", action="store_true")
    known, _ = pre.parse_known_args(argv)
    argv = _strip_globals(argv)  # global flags may appear anywhere, e.g. `bhap ask "..." --lang en`
    if known.lang:
        set_language(known.lang)
    try:
        from .config import load
        settings = load()
        color = "never" if known.no_color else settings["color"]
    except BhapError:
        color = "never" if known.no_color else "auto"
    json_mode = "--json" in argv
    ui.configure("never" if json_mode else color)
    try:
        args = build_parser().parse_args(argv)
        args.quiet = known.quiet
        ui.configure("never" if json_mode else color, quiet=known.quiet)
        return dispatch(args) or 0
    except KeyboardInterrupt:
        ui.err()
        ui.warn(t("interrupted"))
        return EXIT_INTERRUPTED
    except NetworkBlockedError as exc:
        ui.error(str(exc))
        return EXIT_NETWORK
    except BhapError as exc:
        if json_mode:
            from .commands.common import emit_json
            emit_json({"error": str(exc), "hint": exc.hint, "exit_code": exc.exit_code})
        else:
            ui.error(str(exc), exc.hint)
        return exc.exit_code
    except BrokenPipeError:
        return 0
    except sqlite3.Error as exc:
        ui.error(f"Database error: {exc}", "bhap doctor")
        return EXIT_STORAGE
    except OSError as exc:
        ui.error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
