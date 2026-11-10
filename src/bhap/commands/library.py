"""workspace, add, docs, open, jobs, watch."""
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .. import config, ui
from ..core import citations, ingest, library, retrieval
from ..errors import EXIT_OK, EXIT_PARSE, NotFoundError, UsageError
from ..i18n import t, tr
from ..storage import sqlite
from .common import Context, emit_json, run_ingest, unit_name


# ----------------------------------------------------------------------------- workspace
def cmd_workspace(args):
    with sqlite.db() as conn:
        if args.action == "list":
            current = Context(conn, None).workspace
            rows = library.workspaces(conn)
            if args.json:
                emit_json([{"name": r["name"], "id": r["id"], "documents": r["docs"], "indexed": r["n_indexed"],
                            "active": r["name"] == current} for r in rows])
                return EXIT_OK
            ui.table([("●" if r["name"] == current else " ", r["name"], str(r["docs"]), r["created_at"][:10],
                       r["description"]) for r in rows],
                     [" ", "WORKSPACE", tr("DOKUMEN", "DOCS"), tr("DIBUAT", "CREATED"), tr("DESKRIPSI", "DESCRIPTION")])
            return EXIT_OK
        if args.action == "current":
            ctx = Context(conn, args)
            emit_json({"workspace": ctx.workspace, "source": ctx.ws_source}) if args.json else ui.out(ctx.workspace)
            return EXIT_OK
        if not args.name and args.action != "link":
            raise UsageError(tr("Nama workspace diperlukan", "Workspace name required"),
                             hint=f"bhap workspace {args.action} <name>")
        if args.action == "create":
            library.create_workspace(conn, args.name, args.description or "")
            ui.ok(t("ws.created", name=args.name))
            if args.use:
                config.update(workspace=args.name)
                ui.ok(t("ws.active", name=args.name))
        elif args.action == "use":
            library.get_workspace(conn, args.name)
            config.update(workspace=args.name)
            ui.ok(t("ws.active", name=args.name))
            folder, project = config.find_project(Path.cwd())
            if project.get("workspace") and project["workspace"] != args.name:
                ui.warn(tr(f"Folder ini tertaut ke '{project['workspace']}' ({folder}); tautan folder diprioritaskan.",
                           f"This folder is linked to '{project['workspace']}' ({folder}); the link takes precedence."))
        elif args.action == "delete":
            row = library.get_workspace(conn, args.name)
            docs = conn.execute("SELECT COUNT(*) FROM documents WHERE workspace_id=?", (row["id"],)).fetchone()[0]
            if not args.yes and not ui.confirm(t("ws.confirm_delete", name=args.name, docs=docs)):
                ui.info(t("cancelled"))
                return EXIT_OK
            library.delete_workspace(conn, args.name)
            ui.ok(t("ws.deleted", name=args.name))
        elif args.action == "rename":
            if not args.new_name:
                raise UsageError("bhap workspace rename <old> <new>")
            library.rename_workspace(conn, args.name, args.new_name)
            ui.ok(t("ws.renamed", old=args.name, new=args.new_name))
        elif args.action == "link":
            name = args.name or Context(conn, args).workspace
            library.ensure_workspace(conn, name)
            target = config.link_project(Path.cwd(), name)
            ui.ok(t("ws.linked", folder=Path.cwd(), name=name, file=target))
        elif args.action == "info":
            row = library.get_workspace(conn, args.name)
            stats = library.stats(conn, row["id"])
            info = {"name": row["name"], "id": row["id"], "description": row["description"],
                    "created_at": row["created_at"], **stats,
                    "conversations": conn.execute("SELECT COUNT(*) FROM conversations WHERE workspace_id=?",
                                                  (row["id"],)).fetchone()[0],
                    "notes": conn.execute("SELECT COUNT(*) FROM notes WHERE workspace_id=?", (row["id"],)).fetchone()[0],
                    "study_items": conn.execute("SELECT COUNT(*) FROM study_items WHERE workspace_id=?",
                                                (row["id"],)).fetchone()[0]}
            emit_json(info) if args.json else ui.out("\n".join(f"{k}: {v}" for k, v in info.items()))
    return EXIT_OK


# ----------------------------------------------------------------------------- add
def confirm_directory(files, skipped, path_label, assume_yes=False):
    """Preview a recursive import and ask for approval (with optional excludes)."""
    by_ext, size = ingest.preview(files)
    breakdown = ", ".join(f"{count} {ext}" for ext, count in sorted(by_ext.items())) or "-"
    ui.err(t("ingest.dir_preview", path=path_label, count=len(files), size=ui.human_size(size), breakdown=breakdown))
    if skipped:
        ui.err(ui.style("  " + t("ingest.dir_skipped", details=", ".join(f"{len(v)} {k}" for k, v in skipped.items())),
                        "gray"))
    if not files or assume_yes or not sys.stdin.isatty():
        return files
    if not ui.confirm(t("ingest.dir_confirm", count=len(files)), default=True):
        return []
    try:
        patterns = input(t("ingest.dir_exclude")).strip()
    except EOFError:
        patterns = ""
    if patterns:
        globs = [p.strip() for p in patterns.split(",") if p.strip()]
        files = [f for f in files if not any(Path(f).match(g) or f.name == g for g in globs)]
        ui.err(tr(f"{len(files)} file akan diimpor.", f"{len(files)} files will be imported."))
    return files


def cmd_add(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        ingest.recover_jobs(conn)
        if args.paths == ["-"]:
            text = sys.stdin.read()
            if not text.strip():
                raise UsageError(tr("stdin kosong", "stdin is empty"))
            from ..core.ingest import Ingestor
            result = Ingestor(conn, ctx.wid, ctx.settings).import_text(args.name or "stdin.txt", text, args.folder or "")
            emit_json({"file": result["file"], **result}) if args.json else ui.ok(
                t("ingest.indexed", file=result["file"], units=f"{result.get('units', 0)} {unit_name(result.get('unit', 'line'))}",
                  chunks=result.get("chunks", 0)))
            return EXIT_OK
        files, skipped = ingest.scan(args.paths, recursive=args.recursive, exclude=args.exclude or [],
                                     max_mb=ctx.settings["max_file_mb"])
        has_dir = any(Path(p).expanduser().is_dir() for p in args.paths)
        if has_dir and not args.json:
            files = confirm_directory(files, skipped, ", ".join(args.paths), assume_yes=args.yes)
        if not files:
            ui.warn(tr("Tidak ada file yang didukung untuk diimpor.", "No supported files to import."))
            return EXIT_OK
        if not args.json:
            ui.info(tr(f"Workspace: {ctx.workspace}", f"Workspace: {ctx.workspace}"))
        folder = args.folder or ""
        counts = run_ingest(ctx, files, folder=folder, force=args.force, json_mode=args.json)
        return EXIT_PARSE if counts["failed"] else EXIT_OK


# ----------------------------------------------------------------------------- docs
def _doc_rows(conn, wid, refs):
    return [library.find_document(conn, wid, ref) for ref in refs]


def cmd_docs(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        model = ctx.settings["embedding_model"]
        if args.action == "list":
            rows = library.documents(conn, ctx.wid, folder=args.folder)
            if args.json:
                emit_json([{"id": r["id"], "file": r["filename"], "folder": r["folder"], "status": r["status"],
                            "unit": r["unit"], "units": r["units"], "chunks": r["chunk_count"], "size": r["size"],
                            "sha256": r["sha256"], "original_path": r["original_path"],
                            "warnings": library.warnings(r), "updated_at": r["updated_at"]} for r in rows])
                return EXIT_OK
            if not rows:
                ui.info(t("docs.empty", name=ctx.workspace))
                return EXIT_OK
            table = []
            for row in rows:
                warns = library.warnings(row)
                status = row["status"]
                status = ui.style(status, "green" if status == "indexed" else "yellow")
                table.append((str(row["id"]), row["filename"], row["folder"] or "-", status,
                              f"{row['units']} {unit_name(row['unit'])}", str(row["chunk_count"]),
                              ui.human_size(row["size"]), ui.style(f"⚠ {len(warns)}", "yellow") if warns else ""))
            ui.table(table, ["ID", tr("FILE", "FILE"), "FOLDER", "STATUS", tr("ISI", "UNITS"), "CHUNK",
                             tr("UKURAN", "SIZE"), ""])
            stale = library.stale_count(conn, ctx.wid, model)
            if stale:
                ui.warn(t("docs.stale", count=stale))
            if any(library.warnings(r) for r in rows):
                ui.info(tr("Lihat detail peringatan: bhap docs show <id>", "See warning details: bhap docs show <id>"))
            return EXIT_OK
        if args.action == "check":
            states = ingest.check(conn, ctx.wid)
            if args.json:
                emit_json([{"id": r["id"], "file": r["filename"], "state": s, "path": r["original_path"]} for r, s in states])
                return EXIT_OK
            labels = {"ok": ui.style(t("docs.ok"), "green"), "modified": ui.style(t("docs.modified"), "yellow"),
                      "missing": ui.style(t("docs.moved"), "red"), "stale": ui.style("stale", "yellow")}
            ui.table([(str(r["id"]), r["filename"], labels[s], ui.truncate(r["original_path"], 60)) for r, s in states],
                     ["ID", "FILE", "STATUS", "PATH"])
            if any(s == "modified" for _, s in states):
                ui.info("bhap docs reindex --changed")
            return EXIT_OK
        if args.action == "reindex":
            return _reindex(ctx, args)
        if not args.refs:
            raise UsageError(tr("ID dokumen diperlukan", "Document id required"), hint="bhap docs list")
        rows = _doc_rows(conn, ctx.wid, args.refs)
        if args.action == "show":
            for row in rows:
                _show_doc(conn, row, args)
        elif args.action == "remove":
            if not args.yes and not ui.confirm(t("docs.confirm_remove", count=len(rows))):
                ui.info(t("cancelled"))
                return EXIT_OK
            for row in rows:
                ingest.remove(conn, ctx.wid, row["id"])
                ui.ok(t("docs.removed", id=row["id"], file=row["filename"]))
        elif args.action == "move":
            if args.folder is None:
                raise UsageError("bhap docs move <id...> --folder <folder>")
            for row in rows:
                conn.execute("UPDATE documents SET folder=? WHERE id=?", (args.folder.strip("/"), row["id"]))
                ui.ok(f"#{row['id']} {row['filename']} → {args.folder.strip('/') or '/'}")
        elif args.action == "open":
            for row in rows:
                open_original(row, page=args.page, settings=ctx.settings)
        elif args.action == "versions":
            for row in rows:
                versions = conn.execute("SELECT * FROM document_versions WHERE document_id=? ORDER BY id DESC",
                                        (row["id"],)).fetchall()
                ui.out(ui.style(f"#{row['id']} {row['filename']}", "bold"))
                ui.table([(str(v["id"]), v["indexed_at"], v["sha256"][:12], v["embedding_model"], str(v["chunk_count"]),
                           v["parser_version"], "●" if v["id"] == row["current_version_id"] else "") for v in versions],
                         [tr("VERSI", "VERSION"), tr("DIINDEKS", "INDEXED"), "SHA256", "EMBEDDING", "CHUNK", "PARSER", ""])
    return EXIT_OK


def _show_doc(conn, row, args):
    versions = conn.execute("SELECT COUNT(*) FROM document_versions WHERE document_id=?", (row["id"],)).fetchone()[0]
    info = {"id": row["id"], "file": row["filename"], "status": row["status"], "folder": row["folder"],
            "original_path": row["original_path"], "stored_copy": row["stored_path"] or tr("(mode referensi)", "(reference mode)"),
            "sha256": row["sha256"], "size": row["size"], "content": f"{row['units']} {unit_name(row['unit'])}",
            "chunks": row["chunk_count"], "versions": versions, "indexed_at": row["updated_at"],
            "warnings": library.warnings(row)}
    chunks = conn.execute("SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal", (row["id"],)).fetchall()
    if args.json:
        info["chunk_list"] = [{"id": c["id"], "locator": citations.locator(c), "text": c["text"] if args.chunks else
                               c["text"][:200]} for c in chunks]
        emit_json(info)
        return
    ui.out(ui.style(f"#{row['id']} {row['filename']}", "bold"))
    for key, value in info.items():
        if key in ("id", "file", "warnings"):
            continue
        ui.out(f"  {ui.style(key.ljust(14), 'gray')} {value}")
    for warning in info["warnings"]:
        ui.warn("  " + warning)
    ui.out()
    limit = None if args.chunks else 12
    for chunk in chunks[:limit]:
        flags = (" 🖼" if chunk["visual"] else "")
        ui.out(f"  {ui.style('#' + str(chunk['id']), 'cyan')} {citations.locator(chunk)}{flags}")
        preview = chunk["text"] if args.chunks else ui.truncate(chunk["text"], ui.width() - 8)
        ui.out(ui.style("     " + preview.replace("\n", "\n     "), "dim"))
    if limit and len(chunks) > limit:
        ui.out(ui.style(tr(f"  … {len(chunks) - limit} chunk lagi (--chunks untuk semua)",
                           f"  … {len(chunks) - limit} more chunks (--chunks for all)"), "gray"))


def _reindex(ctx, args):
    conn = ctx.conn
    if args.stale:
        rows = [r for r in library.documents(conn, ctx.wid) if r["status"] == "stale" or conn.execute(
            "SELECT 1 FROM chunks c JOIN embeddings e ON e.chunk_id=c.id WHERE c.document_id=? AND e.model != ? LIMIT 1",
            (r["id"], ctx.settings["embedding_model"])).fetchone()]
    elif args.changed:
        rows = [r for r, state in ingest.check(conn, ctx.wid) if state == "modified"]
    elif args.all:
        rows = list(library.documents(conn, ctx.wid))
    elif args.refs:
        rows = _doc_rows(conn, ctx.wid, args.refs)
    else:
        raise UsageError("bhap docs reindex <id...> | --all | --stale | --changed")
    if not rows:
        ui.info(tr("Tidak ada dokumen yang perlu di-reindex.", "Nothing to reindex."))
        return EXIT_OK
    from ..core.ingest import Ingestor
    from .common import IngestReporter
    reporter = IngestReporter(len(rows), args.json)
    ingestor = Ingestor(conn, ctx.wid, ctx.settings, progress=reporter.stage)
    failed = 0
    try:
        for row in rows:
            reporter.begin(Path(row["filename"]))
            try:
                reporter.done(ingestor.reindex(row, force=True))
            except (UsageError, OSError) as exc:
                reporter.failed(exc)
                failed += 1
            except Exception as exc:  # noqa: BLE001 - parse/model errors per file
                from ..errors import ModelError
                if isinstance(exc, ModelError):
                    raise
                reporter.failed(exc)
                failed += 1
    finally:
        reporter.close()
    return EXIT_PARSE if failed else EXIT_OK


# ----------------------------------------------------------------------------- open / preview
def open_original(row, page=None, settings=None):
    settings = settings or config.load()
    path = Path(row["original_path"]) if Path(row["original_path"]).is_file() else Path(row["stored_path"] or "")
    if not path or not path.is_file():
        raise NotFoundError(tr(f"File asli #{row['id']} tidak ditemukan; gunakan preview teks: bhap open <chunk-id>",
                               f"Original of #{row['id']} not found; use the text preview: bhap open <chunk-id>"))
    template = settings.get("pdf_viewer") if path.suffix.lower() == ".pdf" and page else ""
    try:
        if template:
            command = [part.format(page=page, file=str(path)) for part in shlex.split(template)]
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            os.startfile(str(path))  # noqa: S606 - opening a local document chosen by the user
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                raise UsageError(tr("Tidak ada desktop (SSH/headless); gunakan preview teks: bhap open <chunk-id>",
                                    "No desktop session (SSH/headless); use the text preview: bhap open <chunk-id>"))
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise UsageError(tr(f"Tidak ada aplikasi pembuka: {exc}", f"No opener available: {exc}")) from exc
    where = f" @ {tr('halaman', 'page')} {page}" if page else ""
    ui.ok(tr(f"Membuka {path.name}{where}", f"Opening {path.name}{where}")
          + (ui.style(tr("  (atur `pdf_viewer` agar langsung ke halaman)", "  (set `pdf_viewer` to jump to the page)"), "gray")
             if page and not template else ""))


def preview_chunk(conn, wid, chunk_id, context=1, highlight=None, stream=None):
    row = conn.execute("""SELECT c.*, d.filename, d.original_path, d.stored_path, d.workspace_id FROM chunks c
                          JOIN documents d ON d.id=c.document_id WHERE c.id=?""", (chunk_id,)).fetchone()
    if not row or row["workspace_id"] != wid:
        raise NotFoundError(tr(f"Chunk #{chunk_id} tidak ada di workspace ini", f"Chunk #{chunk_id} is not in this workspace"))
    ui.out(ui.style(f"{row['filename']} — {citations.locator(row)}", "bold") + ui.style(f"  #{row['id']}", "gray"))
    if row["visual"]:
        ui.out(ui.style("  ⚠ " + tr("Konten visual di lokasi ini mungkin tidak terbaca.",
                                    "Visual content at this location may not have been interpreted."), "yellow"))
    if row["kind"] == "ocr":
        ui.out(ui.style(f"  ⚠ OCR ({int(row['ocr_conf'] or 0)}%) — " + tr("verifikasi dengan file asli.",
                                                                       "verify against the original."), "yellow"))
    ui.out(ui.style("  " + tr("Teks hasil ekstraksi (bukan render visual dokumen):",
                              "Extracted text (not a visual rendering of the document):"), "gray"))
    for item in retrieval.neighbors(conn, row, context):
        text = item["text"]
        if item["id"] == row["id"]:
            body = text
            if highlight:
                body = _highlight(body, highlight)
            ui.out(ui.style("  ▌ ", "cyan") + body.replace("\n", "\n" + ui.style("  ▌ ", "cyan")))
        else:
            ui.out(ui.style("    " + ui.truncate(text, 400).replace("\n", "\n    "), "dim"))
    return row


def _highlight(text, excerpt):
    probe = excerpt.rstrip("…").strip()[:80]
    index = text.find(probe) if probe else -1
    if index < 0:
        return text
    end = text.find(excerpt.rstrip("…").strip()[-40:], index)
    end = end + len(excerpt.rstrip("…").strip()[-40:]) if end >= 0 else index + len(probe)
    return text[:index] + ui.style(text[index:end], "reverse") + text[end:]


def cmd_open(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        row = preview_chunk(conn, ctx.wid, args.chunk_id, context=args.context)
        if args.app:
            doc = library.document(conn, ctx.wid, row["document_id"])
            open_original(doc, page=row["page_no"] or row["slide_no"], settings=ctx.settings)
    return EXIT_OK


# ----------------------------------------------------------------------------- jobs
def cmd_jobs(args):
    with sqlite.db() as conn:
        recovered = ingest.recover_jobs(conn)
        if args.action == "clear":
            count = conn.execute("DELETE FROM jobs WHERE status IN ('done','skipped','failed','cancelled','interrupted')").rowcount
            ui.ok(tr(f"{count} job dihapus dari daftar", f"{count} jobs cleared"))
            return EXIT_OK
        if args.action == "retry":
            query = "SELECT * FROM jobs WHERE status IN ('failed','interrupted','cancelled')"
            params = []
            if args.ids:
                query += f" AND id IN ({','.join('?' * len(args.ids))})"
                params = args.ids
            jobs = conn.execute(query, params).fetchall()
            if not jobs:
                ui.info(tr("Tidak ada job untuk diulang.", "No jobs to retry."))
                return EXIT_OK
            failed = 0
            for job in jobs:
                ws = conn.execute("SELECT name FROM workspaces WHERE id=?", (job["workspace_id"],)).fetchone()
                if not ws:
                    continue
                args.workspace = ws["name"]
                ctx = Context(conn, args)
                options = json.loads(job["options"] or "{}")
                path = Path(job["target"])
                if not path.is_file():
                    ui.error(t("repl.path_missing", path=path))
                    failed += 1
                    continue
                from ..core.ingest import Ingestor
                from .common import IngestReporter
                reporter = IngestReporter(1)
                reporter.begin(path)
                try:
                    reporter.done(Ingestor(conn, ctx.wid, ctx.settings, progress=reporter.stage).import_path(
                        path, folder=options.get("folder", ""), force=options.get("force", False), job_id=job["id"]))
                except Exception as exc:  # noqa: BLE001
                    reporter.failed(exc)
                    failed += 1
            return EXIT_PARSE if failed else EXIT_OK
        query = "SELECT j.*, w.name ws FROM jobs j LEFT JOIN workspaces w ON w.id=j.workspace_id"
        if not args.all:
            query += " WHERE j.status NOT IN ('done','skipped')"
        rows = conn.execute(query + " ORDER BY j.id DESC LIMIT 200").fetchall()
        if args.json:
            emit_json([dict(r) for r in rows])
            return EXIT_OK
        if recovered:
            ui.warn(tr(f"{recovered} job terputus (proses berhenti) ditandai 'interrupted'.",
                       f"{recovered} jobs whose process died were marked 'interrupted'."))
        if not rows:
            ui.info(tr("Tidak ada job aktif/gagal. (--all untuk semua)", "No active/failed jobs. (--all for all)"))
            return EXIT_OK
        colors = {"done": "green", "failed": "red", "interrupted": "yellow", "cancelled": "yellow"}
        ui.table([(str(r["id"]), r["ws"] or "-", ui.style(r["status"], colors.get(r["status"], "cyan")),
                   f"{int(r['progress'] * 100)}%", ui.truncate(Path(r["target"]).name, 40), r["updated_at"],
                   ui.truncate(r["error"], 60)) for r in rows],
                 ["ID", "WORKSPACE", "STATUS", "%", "FILE", tr("DIPERBARUI", "UPDATED"), "ERROR"])
        if any(r["status"] in ("failed", "interrupted") for r in rows):
            ui.info("bhap jobs retry [id...]")
    return EXIT_OK


# ----------------------------------------------------------------------------- watch
def cmd_watch(args):
    """Poll folders and (re)index new or modified supported files. Never deletes."""
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        ui.info(tr(f"Memantau {', '.join(args.paths)} → workspace '{ctx.workspace}' setiap {args.interval}s (Ctrl+C untuk berhenti)",
                   f"Watching {', '.join(args.paths)} → workspace '{ctx.workspace}' every {args.interval}s (Ctrl+C to stop)"))
        snapshot = {}
        first = True
        try:
            while True:
                files, _ = ingest.scan(args.paths, recursive=args.recursive, exclude=args.exclude or [],
                                       max_mb=ctx.settings["max_file_mb"])
                changed = []
                for path in files:
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    key = (stat.st_mtime_ns, stat.st_size)
                    if snapshot.get(path) != key:
                        snapshot[path] = key
                        changed.append(path)
                if changed and not (first and args.skip_existing):
                    known = {r["original_path"]: r for r in library.documents(conn, ctx.wid)}
                    todo = [p for p in changed if str(p) not in known or known[str(p)]["size"] != p.stat().st_size
                            or ingest.sha256_file(p) != known[str(p)]["sha256"]]
                    if todo:
                        run_ingest(ctx, todo, folder=args.folder or "")
                for gone in [p for p in snapshot if not p.exists()]:
                    snapshot.pop(gone)
                    ui.warn(tr(f"{gone.name} dihapus dari disk; indeks dipertahankan (bhap docs remove untuk menghapus).",
                               f"{gone.name} was deleted from disk; index kept (use bhap docs remove)."))
                first = False
                time.sleep(args.interval)
        except KeyboardInterrupt:
            ui.err()
            ui.info(tr("Pemantauan dihentikan.", "Watching stopped."))
    return EXIT_OK
