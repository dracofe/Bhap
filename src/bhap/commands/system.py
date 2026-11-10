"""setup, doctor, status, model, config, logs."""
import json
import os
import platform
import sys
from pathlib import Path

from .. import __version__, config, hardware, logs, netguard, ui
from ..adapters import runtime
from ..core import library
from ..core.ingest import recover_jobs
from ..errors import EXIT_MODEL, EXIT_OK, EXIT_STORAGE, ModelError, UsageError
from ..i18n import t, tr
from ..parsers import ocr
from ..storage import sqlite
from .common import emit_json, readiness, setup_hint, status_badge


# ----------------------------------------------------------------------------- setup
def _choose(kind, candidates, current, recommended):
    """Interactive picker; returns a model name or None."""
    if not candidates:
        return None
    ui.out(ui.style(f"\n{kind}:", "bold"))
    for index, item in enumerate(candidates, 1):
        size = ui.human_size(item["size"]) if item.get("size") else ""
        tags = []
        if item["name"] == current:
            tags.append(tr("saat ini", "current"))
        if any(item["name"].split(":")[0] == r.split(":")[0] for r in recommended):
            tags.append(tr("disarankan", "recommended"))
        extra = ui.style(f"  {size} {item.get('parameters', '')} {' · '.join(tags)}".rstrip(), "gray")
        ui.out(f"  {index}) {item['name']}{extra}")
    default = current if current in [c["name"] for c in candidates] else candidates[0]["name"]
    try:
        answer = input(tr(f"Pilih nomor/nama [{default}]: ", f"Choose number/name [{default}]: ")).strip()
    except EOFError:
        answer = ""
    if not answer:
        return default
    if answer.isdigit() and 1 <= int(answer) <= len(candidates):
        return candidates[int(answer) - 1]["name"]
    return answer


def _pull(rt, model, assume_yes=False):
    if not rt.supports_pull:
        raise ModelError(f"{rt.name} cannot download models; download the GGUF manually")
    if not assume_yes and not ui.confirm(t("setup.pull_confirm", model=model)):
        return False
    last = ""
    for event in rt.pull(model):
        status = event.get("status", "")
        if event.get("total"):
            pct = int(100 * event.get("completed", 0) / event["total"])
            line = f"{model}: {status} {pct}% ({ui.human_size(event.get('completed', 0))}/{ui.human_size(event['total'])})"
        else:
            line = f"{model}: {status}"
        if line != last:
            if sys.stderr.isatty():
                ui.err("\r\033[K" + line, end="")
            elif status != last.split(": ", 1)[-1].split(" ")[0]:
                ui.err(line)
            last = line
    if sys.stderr.isatty():
        ui.err()
    ui.ok(tr(f"{model} terpasang lokal.", f"{model} installed locally."))
    return True


def cmd_setup(args):
    settings = config.load()
    hw = hardware.summary(config.home())
    rec = hardware.RECOMMENDATIONS[hw["profile"]]
    ui.out(ui.style(t("setup.title"), "bold"))
    ui.out(tr(f"Perangkat: {hw['os']}, RAM {hw['ram_total_gb']} GB, profil '{hw['profile']}'",
              f"Device: {hw['os']}, RAM {hw['ram_total_gb']} GB, profile '{hw['profile']}'"))
    if args.runtime:
        settings = config.update(runtime=args.runtime)
    rt = runtime(settings)
    try:
        rt.version()
    except ModelError:
        ui.error(t("setup.no_runtime", url=rt.base), t("setup.install_hint"))
        return EXIT_MODEL
    for wanted in filter(None, [args.llm, args.embedding]):
        if not rt.has_model(wanted) and args.download:
            _pull(rt, wanted, assume_yes=args.yes)
    models = rt.models()
    interactive = sys.stdin.isatty() and not (args.llm and args.embedding)
    if not models:
        ui.warn(tr("Belum ada model di runtime lokal.", "No models in the local runtime yet."))
        if not interactive and not args.download:
            ui.err(tr(f"Saran (profil {hw['profile']}): chat {rec['llm'][0]}, embedding {rec['embedding'][0]}.\n"
                      f"Unduh saat online: bhap setup --download --llm {rec['llm'][0]} --embedding {rec['embedding'][0]}",
                      f"Suggested (profile {hw['profile']}): chat {rec['llm'][0]}, embedding {rec['embedding'][0]}.\n"
                      f"Download while online: bhap setup --download --llm {rec['llm'][0]} --embedding {rec['embedding'][0]}"))
            return EXIT_MODEL
        if interactive:
            for model in (rec["llm"][0], rec["embedding"][0]):
                _pull(rt, model, assume_yes=args.yes)
            models = rt.models()
    chat_candidates = [m for m in models if m["embedding"] is not True] or models
    embed_candidates = [m for m in models if m["embedding"] is not False] or models
    llm = args.llm or (interactive and _choose(tr("Model chat", "Chat model"), chat_candidates,
                                               settings["llm_model"], rec["llm"])) or settings["llm_model"]
    emb = args.embedding or (interactive and _choose(tr("Model embedding", "Embedding model"), embed_candidates,
                                                     settings["embedding_model"], rec["embedding"])) \
        or settings["embedding_model"]
    for name in (llm, emb):
        if not rt.has_model(name):
            if interactive and _pull(rt, name, assume_yes=args.yes):
                continue
            raise ModelError(tr(f"Model '{name}' tidak tersedia lokal.", f"Model '{name}' is not available locally."),
                             hint=f"ollama pull {name}  (online)  ·  bhap setup --download --llm {llm} --embedding {emb}")
    old_embedding = settings["embedding_model"]
    settings = config.update(llm_model=llm, embedding_model=emb)
    ui.ok(t("setup.saved", llm=llm, emb=emb))
    with sqlite.db() as conn:
        library.ensure_workspace(conn, "personal")
        if old_embedding and old_embedding != emb:
            stale = sum(library.stale_count(conn, row["id"], emb) for row in library.workspaces(conn))
            if stale:
                ui.warn(t("setup.embedding_changed", count=stale))
    multilingual = ("bge-m3", "paraphrase-multilingual", "granite-embedding", "multilingual", "e5")
    if not any(name in emb.lower() for name in multilingual):
        ui.info(tr("Tip: untuk materi berbahasa Indonesia, model embedding multilingual (mis. bge-m3) biasanya lebih akurat.",
                   "Tip: for Indonesian material a multilingual embedding model (e.g. bge-m3) is usually more accurate."))
    ui.info(tr("Langkah berikut: bhap doctor --offline · bhap add <file> · bhap",
               "Next: bhap doctor --offline · bhap add <file> · bhap"))
    return EXIT_OK


# ----------------------------------------------------------------------------- doctor
class Checks:
    def __init__(self):
        self.items = []

    def add(self, name, status, detail="", fix="", required=True, group="core"):
        self.items.append({"check": name, "status": status, "detail": detail, "fix": fix,
                           "required": required, "group": group})

    def failed(self, group=None):
        return [i for i in self.items if i["status"] == "fail" and i["required"] and (group is None or i["group"] == group)]


def run_checks(offline=False, verify=False):
    checks = Checks()
    settings = config.load()
    py_ok = sys.version_info >= (3, 10)
    checks.add("python", "ok" if py_ok else "fail", platform.python_version(), "Python 3.10+")
    root = config.home()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        checks.add("data_dir", "ok", str(root))
    except OSError as exc:
        checks.add("data_dir", "fail", f"{root}: {exc}", "set BHAP_HOME to a writable directory", group="storage")
    free = hardware.disk_free(root)
    if free is not None:
        checks.add("disk_free", "ok" if free > 2 * 1024 ** 3 else "warn", ui.human_size(free),
                   tr("kosongkan ruang disk; indeks dan salinan asli butuh ruang", "free disk space"), required=False)
    try:
        with sqlite.db() as conn:
            result = sqlite.integrity(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            checks.add("database", "ok" if result == "ok" else "fail", f"schema v{version}, {result}",
                       "bhap backup restore <file>", group="storage")
            interrupted = recover_jobs(conn)
            failed_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('failed','interrupted')").fetchone()[0]
            if failed_jobs:
                checks.add("jobs", "warn", tr(f"{failed_jobs} job gagal/terputus", f"{failed_jobs} failed/interrupted jobs"),
                           "bhap jobs list · bhap jobs retry", required=False)
            stale = 0
            for row in library.workspaces(conn):
                stale += library.stale_count(conn, row["id"], settings["embedding_model"])
            if stale:
                checks.add("index", "warn", t("docs.stale", count=stale), "bhap docs reindex --stale", required=False)
            del interrupted
    except Exception as exc:  # noqa: BLE001 - report any storage failure
        checks.add("database", "fail", str(exc), "bhap backup restore <file>", group="storage")
    try:
        import pypdf
        checks.add("parser_pdf", "ok", f"pypdf {pypdf.__version__}")
    except ImportError:
        checks.add("parser_pdf", "fail", "pypdf missing", "pip install -e .")
    checks.add("parser_office", "ok", "DOCX/PPTX/MD/TXT (built-in)")
    if ocr.available():
        langs = ocr.usable_languages(settings["ocr_languages"])
        checks.add("ocr", "ok" if langs else "warn", f"tesseract + pdftoppm ({langs or 'no language data'})",
                   "install tesseract-data-ind / tesseract-ocr-ind", required=False)
    else:
        checks.add("ocr", "warn", tr("tidak terpasang (opsional, untuk PDF scan)", "not installed (optional, for scanned PDFs)"),
                   "Arch: pacman -S tesseract tesseract-data-ind tesseract-data-eng poppler · "
                   "Ubuntu: apt install tesseract-ocr tesseract-ocr-ind poppler-utils", required=False)
    try:
        import numpy  # noqa: F401
        checks.add("vector_accel", "ok", "numpy", required=False)
    except ImportError:
        checks.add("vector_accel", "warn", tr("pure Python (lebih lambat untuk koleksi besar)", "pure Python (slower on big collections)"),
                   'pip install "bhap-ai[fast]"', required=False)
    checks.add("network_guard", "ok" if netguard.active() and netguard.self_test() else "fail",
               tr("koneksi non-loopback diblokir di proses bhap", "non-loopback connections are blocked in the bhap process"),
               group="offline")
    url = settings["ollama_url"] if settings["runtime"] == "ollama" else settings["llamacpp_url"]
    checks.add("runtime_url", "ok" if config.LOOPBACK_URL.match(url) else "fail", url, group="offline")
    state = readiness(settings)
    if state["runtime_ok"]:
        checks.add("runtime", "ok", f"{settings['runtime']} {state['runtime_version']}", group="model")
    else:
        checks.add("runtime", "fail", state["error"] or "", t("setup.install_hint"), group="model")
    total, available = hardware.memory()
    sizes = {m["name"]: m.get("size", 0) for m in state["models"]}
    for key, ready in (("llm_model", state["llm_ready"]), ("embedding_model", state["embedding_ready"])):
        name = settings[key]
        if not name:
            checks.add(key, "fail", tr("belum dipilih", "not selected"), "bhap setup", group="model")
        elif not state["runtime_ok"]:
            checks.add(key, "fail", name, "ollama serve", group="model")
        elif not ready:
            checks.add(key, "fail", tr(f"{name} tidak terpasang", f"{name} not installed"),
                       f"ollama pull {name} (online) · bhap setup", group="model")
        else:
            size = sizes.get(name) or sizes.get(f"{name}:latest") or 0
            detail = f"{name}" + (f" ({ui.human_size(size)})" if size else "")
            status = "ok"
            if size and total and size > total * 0.75:
                status, detail = "warn", detail + tr(" — mungkin melebihi RAM; pilih model lebih kecil",
                                                     " — may exceed RAM; choose a smaller model")
            elif size and available and size > available:
                status, detail = "warn", detail + tr(" — RAM tersedia saat ini rendah", " — little free RAM right now")
            checks.add(key, status, detail, "bhap model use <smaller-model>", group="model")
    if (offline or verify) and state["ready"]:
        try:
            from ..core.embedder import Embedder
            vector = Embedder(settings).query("uji kesiapan offline / offline readiness test")
            checks.add("embedding_test", "ok", tr(f"{len(vector)} dimensi", f"{len(vector)} dimensions"), group="model")
        except Exception as exc:  # noqa: BLE001
            checks.add("embedding_test", "fail", str(exc), "bhap setup", group="model")
    if verify and state["ready"]:
        try:
            rt = runtime(settings)
            reply = rt.complete(settings["llm_model"], [{"role": "user", "content": "Reply with the single word: OK"}],
                                options={"temperature": 0, "num_ctx": 1024, "num_predict": 8})
            checks.add("chat_test", "ok" if reply.strip() else "fail", ui.truncate(reply.strip(), 40), group="model")
        except Exception as exc:  # noqa: BLE001
            checks.add("chat_test", "fail", str(exc), "bhap setup", group="model")
    if netguard.blocked_attempts():
        checks.add("egress_attempts", "warn", ", ".join(netguard.blocked_attempts()[:5]), required=False, group="offline")
    hw = hardware.summary(root)
    rec = hardware.RECOMMENDATIONS[hw["profile"]]
    checks.add("hardware", "ok", f"{hw['cpu_count']} CPU · RAM {hw['ram_total_gb']} GB · "
               f"{(hw['gpu'] or {}).get('name', 'no dedicated GPU detected')} · profile {hw['profile']}",
               tr(f"saran: {rec['llm'][0]} + {rec['embedding'][0]}", f"suggested: {rec['llm'][0]} + {rec['embedding'][0]}"),
               required=False)
    return checks, state


def cmd_doctor(args):
    checks, state = run_checks(offline=args.offline, verify=args.verify)
    failed = checks.failed()
    code = EXIT_OK
    if any(i["group"] == "storage" for i in failed):
        code = EXIT_STORAGE
    elif failed:
        code = EXIT_MODEL
    if args.json:
        emit_json({"ok": not failed, "offline_ready": not failed, "checks": checks.items, "exit_code": code})
        return code
    ui.out(ui.style(t("doctor.offline_title") if args.offline else t("doctor.title"), "bold"))
    icons = {"ok": ui.style(ui.symbol("ok", sys.stdout), "green"), "warn": ui.style(ui.symbol("warn", sys.stdout), "yellow"),
             "fail": ui.style(ui.symbol("err", sys.stdout), "red")}
    width = max(len(i["check"]) for i in checks.items)
    for item in checks.items:
        ui.out(f"  {icons[item['status']]} {item['check'].ljust(width)}  {item['detail']}")
        if item["status"] != "ok" and item["fix"]:
            ui.out(ui.style(f"    {' ' * width}  → {item['fix']}", "gray"))
    ui.out()
    if failed:
        ui.out(ui.style(t("doctor.not_ok"), "red"))
    else:
        ui.out(ui.style(t("doctor.all_ok"), "green"))
        if args.offline and not args.verify:
            ui.out(ui.style(tr("Matikan Wi-Fi lalu jalankan `bhap doctor --offline --verify` untuk uji end-to-end.",
                               "Turn Wi-Fi off and run `bhap doctor --offline --verify` for an end-to-end test."), "gray"))
    return code


# ----------------------------------------------------------------------------- status
def cmd_status(args):
    settings = config.load()
    state = readiness(settings)
    with sqlite.db() as conn:
        from .common import Context
        ctx = Context(conn, args)
        stats = library.stats(conn, ctx.wid)
        running = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','extracting','chunking','embedding')"
                               ).fetchone()[0]
        stale = library.stale_count(conn, ctx.wid, settings["embedding_model"])
        workspaces = len(library.workspaces(conn))
        warn_docs = conn.execute("SELECT COUNT(*) FROM documents WHERE workspace_id=? AND warnings != '[]'",
                                 (ctx.wid,)).fetchone()[0]
    data = {"version": __version__, "data_dir": str(config.home()), "workspace": ctx.workspace,
            "workspace_source": ctx.ws_source, "workspaces": workspaces, **stats, "stale_documents": stale,
            "documents_with_warnings": warn_docs, "indexing_jobs": running, "runtime": settings["runtime"],
            "runtime_ok": state["runtime_ok"], "llm_model": settings["llm_model"], "llm_ready": state["llm_ready"],
            "embedding_model": settings["embedding_model"], "embedding_ready": state["embedding_ready"],
            "mode": settings["mode"], "offline_ready": state["ready"], "network_guard": netguard.active(),
            "language": settings["language"]}
    if args.json:
        emit_json(data)
        return EXIT_OK
    ui.out(ui.style(f"Bhap {__version__}", "bold") + "  " + status_badge(state))
    rows = [
        (tr("Workspace", "Workspace"), f"{ctx.workspace}  ({ctx.ws_source})"),
        (tr("Dokumen", "Documents"), f"{stats['indexed']}/{stats['documents']} {tr('terindeks', 'indexed')} · "
                                     f"{stats['chunks']} chunk · {ui.human_size(stats['bytes'])}"),
        (tr("Model chat", "Chat model"), f"{settings['llm_model'] or '-'} {'✓' if state['llm_ready'] else '✗'}"),
        (tr("Model embedding", "Embedding"), f"{settings['embedding_model'] or '-'} {'✓' if state['embedding_ready'] else '✗'}"),
        (tr("Mode jawaban", "Answer mode"), t("mode." + settings["mode"])),
        (tr("Data", "Data"), str(config.home())),
        (tr("Jaringan", "Network"), tr("hanya loopback (guard aktif)", "loopback only (guard active)")),
    ]
    if stale:
        rows.append((tr("Perlu reindex", "Needs reindex"), str(stale)))
    if running:
        rows.append(("Indexing", tr(f"{running} job berjalan", f"{running} jobs running")))
    if warn_docs:
        rows.append((tr("Peringatan parsing", "Parsing warnings"), tr(f"{warn_docs} dokumen (bhap docs list)",
                                                                       f"{warn_docs} documents (bhap docs list)")))
    width = max(len(r[0]) for r in rows)
    for key, value in rows:
        ui.out(f"  {ui.style(key.ljust(width), 'gray')}  {value}")
    if not state["ready"]:
        ui.out(ui.style("  → " + setup_hint(state), "yellow"))
    return EXIT_OK


# ----------------------------------------------------------------------------- model
def cmd_model(args):
    settings = config.load()
    rt = runtime(settings)
    if args.action == "list":
        models = rt.models()
        if args.json:
            emit_json({"runtime": settings["runtime"], "models": models, "llm_model": settings["llm_model"],
                       "embedding_model": settings["embedding_model"]})
            return EXIT_OK
        rows = []
        for item in models:
            role = []
            if item["name"] in (settings["llm_model"], settings["llm_model"] + ":latest"):
                role.append("chat ✓")
            if item["name"] in (settings["embedding_model"], settings["embedding_model"] + ":latest"):
                role.append("embedding ✓")
            kind = "embedding" if item["embedding"] else ("chat" if item["embedding"] is False else "?")
            rows.append((item["name"], kind, item.get("parameters", ""), item.get("quantization", ""),
                         ui.human_size(item["size"]) if item.get("size") else "", " ".join(role)))
        if not rows:
            ui.info(tr("Belum ada model lokal. Saat online: bhap model pull <nama>", "No local models. While online: bhap model pull <name>"))
            return EXIT_MODEL
        ui.table(rows, [tr("MODEL", "MODEL"), tr("JENIS", "KIND"), "PARAM", "QUANT", tr("UKURAN", "SIZE"), tr("DIPAKAI", "IN USE")])
        return EXIT_OK
    if not args.name:
        raise UsageError(tr("Nama model diperlukan", "Model name required"), hint="bhap model list")
    if args.action == "pull":
        _pull(rt, args.name, assume_yes=args.yes)
        return EXIT_OK
    if args.action == "info":
        ctx = rt.context_length(args.name)
        info = {"name": args.name, "installed": rt.has_model(args.name), "context_length": ctx}
        emit_json(info) if args.json else ui.out("\n".join(f"{k}: {v}" for k, v in info.items()))
        return EXIT_OK
    if not rt.has_model(args.name):
        raise ModelError(tr(f"Model '{args.name}' tidak tersedia lokal", f"Model '{args.name}' is not available locally"),
                         hint=f"bhap model pull {args.name}  (online)")
    key = "embedding_model" if args.embedding else "llm_model"
    old = settings[key]
    config.update(**{key: args.name})
    ui.ok(f"{'embedding' if args.embedding else 'chat'}: {args.name}")
    if args.embedding and old and old != args.name:
        with sqlite.db() as conn:
            stale = sum(library.stale_count(conn, row["id"], args.name) for row in library.workspaces(conn))
        if stale:
            ui.warn(t("setup.embedding_changed", count=stale))
    return EXIT_OK


# ----------------------------------------------------------------------------- config
def cmd_config(args):
    if args.action == "path":
        ui.out(str(config.config_file()))
        return EXIT_OK
    if args.action == "reset":
        if args.key:
            if args.key not in config.SCHEMA:
                raise UsageError(f"Unknown setting: {args.key}", hint="bhap config show")
            config.update(**{args.key: config.SCHEMA[args.key][0]})
        else:
            keep = {k: config.stored()[k] for k in ("llm_model", "embedding_model", "workspace")}
            config.save({**config.defaults(), **keep})
        ui.ok(tr("Konfigurasi direset.", "Settings reset."))
        return EXIT_OK
    settings = config.load()
    if args.action == "get":
        if args.key not in config.SCHEMA:
            raise UsageError(f"Unknown setting: {args.key}", hint="bhap config show")
        value = settings[args.key]
        ui.out(json.dumps(value) if not isinstance(value, str) else value)
        return EXIT_OK
    if args.action == "set":
        if args.key is None or args.value is None:
            raise UsageError("bhap config set <key> <value>", hint="bhap config show")
        if args.key in ("llm_model", "embedding_model"):
            raise UsageError(tr("Gunakan `bhap model use` agar model diverifikasi.", "Use `bhap model use` so the model is verified."))
        settings = config.update(**{args.key: args.value})
        ui.ok(f"{args.key} = {json.dumps(settings[args.key], ensure_ascii=False)}")
        return EXIT_OK
    if args.json:
        emit_json(settings)
        return EXIT_OK
    rows = [(key, json.dumps(settings[key], ensure_ascii=False), spec[3]) for key, spec in config.SCHEMA.items()]
    ui.table(rows, [tr("KUNCI", "KEY"), tr("NILAI", "VALUE"), tr("KETERANGAN", "DESCRIPTION")])
    ui.out(ui.style(f"\n{config.config_file()}", "gray"))
    return EXIT_OK


# ----------------------------------------------------------------------------- logs
def cmd_logs(args):
    records = logs.read(args.limit)
    if args.action == "export":
        records = [logs.redact(r) for r in logs.read(100000)] if args.redact else logs.read(100000)
        target = Path(args.output).expanduser() if args.output else config.path("exports") / "bhap-logs.jsonl"
        target.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
        ui.ok(tr(f"Log diekspor: {target}", f"Logs exported: {target}") + (" (redacted)" if args.redact else ""))
        return EXIT_OK
    for record in records:
        level = record.get("level", "info")
        color = {"error": "red", "warning": "yellow"}.get(level)
        extra = {k: v for k, v in record.items() if k not in ("ts", "level", "event")}
        line = f"{record.get('ts')} {level:5} {record.get('event')} {json.dumps(extra, ensure_ascii=False)}"
        ui.out(ui.style(line, color) if color else line)
    if not records:
        ui.info(tr("Belum ada log.", "No logs yet."))
    return EXIT_OK


def cmd_version(args):
    info = {"bhap": __version__, "python": platform.python_version(), "platform": platform.platform(),
            "data_dir": str(config.home()), "pid": os.getpid()}
    emit_json(info) if getattr(args, "json", False) else ui.out(f"bhap {__version__} (Python {info['python']}, {info['platform']})")
    return EXIT_OK
