"""ask, search, summarize, compare, history, notes."""
import json
import sys
from pathlib import Path

from .. import config, ui
from ..core import citations, generation, history, library, retrieval, study
from ..errors import EXIT_OK, UsageError
from ..i18n import t, tr
from ..storage import sqlite
from .common import Context, TokenPrinter, answer_json, emit_json, print_answer_footer, print_sources


def build_scope(ctx, doc_refs=None, folders=None):
    ids = [library.find_document(ctx.conn, ctx.wid, ref)["id"] for ref in (doc_refs or [])]
    return retrieval.Scope(ids, folders or [])


def ensure_documents(ctx):
    stats = library.stats(ctx.conn, ctx.wid)
    if not stats["indexed"]:
        raise UsageError(t("ask.no_docs", name=ctx.workspace), hint="bhap add <file>")


def answer_question(ctx, question, *, mode=None, scope=None, conversation_id=None, stream=True, evidence=False,
                    save=True, json_mode=False):
    """Shared by `bhap ask` and the interactive session. Returns the result dict."""
    turns = history.turns(ctx.conn, conversation_id, 2 * ctx.settings["history_turns"]) if conversation_id else []
    spinner = ui.Spinner(tr("Mencari di materi lokal…", "Searching local material…"), enabled=not json_mode)
    printer = TokenPrinter(spinner) if stream and not json_mode else None
    with spinner:
        result = generation.ask(ctx.conn, ctx.wid, question, mode=mode, scope=scope, history=turns,
                                on_token=printer, settings=ctx.settings)
    if json_mode:
        emit_json(answer_json(result, ctx.workspace))
    else:
        if not printer:
            ui.out(result["answer"])
        print_answer_footer(result, evidence=evidence, streamed=bool(printer and printer.started))
    if save and ctx.settings["save_history"] and result["status"] != "cancelled":
        cid = conversation_id or history.start(ctx.conn, ctx.wid, result["mode"])
        history.record(ctx.conn, cid, result)
        result["conversation_id"] = cid
    return result


def cmd_ask(args):
    question = args.prompt
    if args.stdin or (question is None and not sys.stdin.isatty()):
        question = sys.stdin.read()
    question = (question or "").strip()
    if not question:
        raise UsageError(tr("Berikan pertanyaan atau gunakan --stdin", "Provide a question or use --stdin"),
                         hint='bhap ask "Apa itu normalisasi?"  ·  echo "..." | bhap ask --stdin --json')
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        ensure_documents(ctx)
        scope = build_scope(ctx, args.doc, args.folder)
        cid = history.latest(conn, ctx.wid) if args.continue_ else None
        stream = sys.stdout.isatty() and not args.no_stream
        result = answer_question(ctx, question, mode=args.mode, scope=scope, conversation_id=cid, stream=stream,
                                 evidence=args.evidence, save=not args.no_save, json_mode=args.json)
        if args.sources and not args.json and result["citations"]:
            for cite in result["citations"]:
                ui.out()
                from .library import preview_chunk
                preview_chunk(conn, ctx.wid, cite["chunk_id"], context=0, highlight=cite["excerpt"])
    return EXIT_OK


def cmd_search(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        ensure_documents(ctx)
        scope = build_scope(ctx, args.doc, args.folder)
        with ui.Spinner(tr("Mencari…", "Searching…"), enabled=not args.json):
            hits = retrieval.search(conn, ctx.wid, args.query, scope=scope, settings=ctx.settings, limit=args.limit,
                                    diversify=not args.no_diversify)
        if args.json:
            emit_json({"query": args.query, "workspace": ctx.workspace, "results": [
                {"chunk_id": h["id"], "document_id": h["document_id"], "file": h["filename"],
                 "locator": citations.locator(h), "page": h["page_no"], "slide": h["slide_no"],
                 "section": h["section_path"] or None, "score": h["score"], "similarity": h["similarity"],
                 "text": h["text"]} for h in hits], "ms": retrieval.search.last_ms})
            return EXIT_OK
        if not hits:
            ui.info(t("search.none"))
            missing = retrieval.missing_terms(conn, ctx.wid, args.query)
            if missing:
                ui.info(t("ask.gap", terms=", ".join(missing)))
            return EXIT_OK
        terms = set(retrieval.terms(args.query))
        for rank, hit in enumerate(hits, 1):
            ui.out(f"{ui.style(str(rank) + '.', 'bold')} {hit['filename']} — {citations.locator(hit)}"
                   + ui.style(f"  #{hit['id']} · score {hit['score']} · sim {hit['similarity']}", "gray"))
            snippet = ui.truncate(hit["text"], 3 * ui.width())
            if ui.color_enabled():
                words = []
                for word in snippet.split(" "):
                    bare = word.strip(".,;:!?()[]\"'").lower()
                    words.append(ui.style(word, "bold", "yellow") if bare in terms or any(
                        bare.startswith(term) for term in terms if len(term) >= 4) else word)
                snippet = " ".join(words)
            ui.out("   " + snippet)
        ui.out(ui.style(f"\n{len(hits)} · {retrieval.search.last_ms} ms · bhap open <chunk-id>", "gray"))
    return EXIT_OK


def _write_output(text, cites, output, title):
    lines = [f"# {title}", "", text.strip(), ""]
    if cites:
        lines += ["## Sumber / Sources", ""]
        lines += [f"- [{c['marker']}] {c['file']} — {c['locator']}: “{c['excerpt']}”" for c in cites]
    target = Path(output).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ui.ok(tr(f"Disimpan: {target}", f"Saved: {target}"))


def _run_task(ctx, args, title, runner):
    json_mode = getattr(args, "json", False)
    stream = sys.stdout.isatty() and not json_mode
    spinner = ui.Spinner(tr("Menyusun dari materi lokal…", "Composing from local material…"), enabled=not json_mode)
    printer = TokenPrinter(spinner) if stream else None
    with spinner:
        result = runner(printer)
    if json_mode:
        emit_json({"title": title, **result})
        return result
    if not printer:
        ui.out(result["text"])
    else:
        ui.out()
    if result["cancelled"]:
        ui.warn(t("ask.cancelled"))
        return result
    if result["invalid_markers"]:
        ui.warn(t("ask.invalid_markers", markers=", ".join(f"[{m}]" for m in result["invalid_markers"])))
    print_sources(result["citations"])
    if getattr(args, "output", None):
        _write_output(result["text"], result["citations"], args.output, title)
    return result


def cmd_summarize(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        ensure_documents(ctx)
        if args.topic:
            title = tr(f"Ringkasan: {args.topic}", f"Summary: {args.topic}")
            _run_task(ctx, args, title, lambda printer: study.summarize(
                conn, ctx.wid, topic=args.topic, level=args.level, on_token=printer, settings=ctx.settings,
                scope=build_scope(ctx, args.doc, args.folder)))
        else:
            if not args.ref:
                raise UsageError("bhap summarize <doc-id> | --topic <topic>", hint="bhap docs list")
            doc = library.find_document(conn, ctx.wid, args.ref)
            title = tr(f"Ringkasan: {doc['filename']}", f"Summary: {doc['filename']}")
            _run_task(ctx, args, title, lambda printer: study.summarize(
                conn, ctx.wid, doc=doc, level=args.level, on_token=printer, settings=ctx.settings))
    return EXIT_OK


def cmd_compare(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        doc_a = library.find_document(conn, ctx.wid, args.a)
        doc_b = library.find_document(conn, ctx.wid, args.b)
        title = f"{doc_a['filename']} ↔ {doc_b['filename']}" + (f" · {args.topic}" if args.topic else "")
        _run_task(ctx, args, title, lambda printer: study.compare(conn, ctx.wid, doc_a, doc_b, topic=args.topic,
                                                                  on_token=printer, settings=ctx.settings))
    return EXIT_OK


# ----------------------------------------------------------------------------- history
def cmd_history(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        if args.action == "list":
            rows = history.conversations(conn, ctx.wid, args.limit)
            if args.json:
                emit_json([dict(r) for r in rows])
                return EXIT_OK
            if not rows:
                ui.info(tr("Belum ada percakapan.", "No conversations yet."))
                return EXIT_OK
            ui.table([(str(r["id"]), r["updated_at"][:16], r["mode"], str(r["messages"]), r["title"]) for r in rows],
                     ["ID", tr("DIPERBARUI", "UPDATED"), "MODE", tr("PESAN", "MSGS"), tr("JUDUL", "TITLE")])
        elif args.action == "show":
            if args.id is None:
                raise UsageError("bhap history show <id>")
            conv, messages = history.conversation(conn, ctx.wid, args.id)
            if args.json:
                emit_json({"conversation": conv, "messages": messages})
                return EXIT_OK
            ui.out(history.to_markdown(conv, messages))
        elif args.action == "search":
            if not args.query:
                raise UsageError("bhap history search <query>")
            rows = history.search(conn, ctx.wid, args.query)
            for row in rows:
                ui.out(f"#{row['conversation_id']} {row['created_at'][:16]} {row['role']}: "
                       f"{ui.truncate(row['text'], ui.width() - 30)}")
            if not rows:
                ui.info(t("search.none"))
        elif args.action == "export":
            ids = [args.id] if args.id else [r["id"] for r in history.conversations(conn, ctx.wid, 100000)]
            chunks = []
            data = []
            for cid in ids:
                conv, messages = history.conversation(conn, ctx.wid, cid)
                chunks.append(history.to_markdown(conv, messages))
                data.append({"conversation": conv, "messages": messages})
            text = json.dumps(data, ensure_ascii=False, indent=2) if args.format == "json" else "\n\n---\n\n".join(chunks)
            if args.output:
                Path(args.output).expanduser().write_text(text + "\n", encoding="utf-8")
                ui.ok(tr(f"Diekspor: {args.output}", f"Exported: {args.output}"))
            else:
                ui.out(text)
        elif args.action in ("delete", "clear"):
            if args.action == "delete" and args.id is None:
                raise UsageError("bhap history delete <id>")
            target = args.id if args.action == "delete" else None
            question = tr(f"Hapus percakapan #{target}?", f"Delete conversation #{target}?") if target else \
                tr(f"Hapus SEMUA percakapan di workspace '{ctx.workspace}'?", f"Delete ALL conversations in '{ctx.workspace}'?")
            if not args.yes and not ui.confirm(question):
                ui.info(t("cancelled"))
                return EXIT_OK
            count = history.delete(conn, ctx.wid, target)
            ui.ok(tr(f"{count} percakapan dihapus.", f"{count} conversation(s) deleted."))
    return EXIT_OK


# ----------------------------------------------------------------------------- notes
def cmd_notes(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        if args.action == "list":
            rows = history.notes(conn, ctx.wid)
            if args.json:
                emit_json([dict(r, source_refs=json.loads(r["source_refs"])) for r in rows])
                return EXIT_OK
            if not rows:
                ui.info(tr("Belum ada catatan. Di sesi interaktif, /save menyimpan jawaban terakhir.",
                           "No notes yet. In the interactive session, /save stores the last answer."))
                return EXIT_OK
            ui.table([(str(r["id"]), r["created_at"][:16], str(len(json.loads(r["source_refs"]))), r["title"]) for r in rows],
                     ["ID", tr("DIBUAT", "CREATED"), tr("SUMBER", "SOURCES"), tr("JUDUL", "TITLE")])
        elif args.action == "add":
            body = args.body if args.body is not None else (sys.stdin.read() if not sys.stdin.isatty() else "")
            if not args.title or not body.strip():
                raise UsageError('bhap notes add "<title>" --body "<text>"  (or pipe the body on stdin)')
            note_id = history.add_note(conn, ctx.wid, args.title, body)
            ui.ok(tr(f"Catatan #{note_id} disimpan.", f"Note #{note_id} saved."))
        elif args.action in ("show", "remove"):
            if not args.title or not str(args.title).isdigit():
                raise UsageError(f"bhap notes {args.action} <id>")
            row = history.note(conn, ctx.wid, int(args.title))
            if args.action == "show":
                emit_json(dict(row)) if args.json else ui.out(history.note_markdown(row, conn))
            else:
                conn.execute("DELETE FROM notes WHERE id=?", (row["id"],))
                ui.ok(tr(f"Catatan #{row['id']} dihapus.", f"Note #{row['id']} deleted."))
        elif args.action == "export":
            rows = history.notes(conn, ctx.wid)
            directory = Path(args.output).expanduser() if args.output else config.path("workspaces", str(ctx.wid), "exports", "notes")
            directory.mkdir(parents=True, exist_ok=True)
            for row in rows:
                slug = "".join(ch if ch.isalnum() else "-" for ch in row["title"].lower())[:50].strip("-") or "note"
                (directory / f"{row['id']:04d}-{slug}.md").write_text(history.note_markdown(row, conn), encoding="utf-8")
            ui.ok(tr(f"{len(rows)} catatan diekspor ke {directory}", f"{len(rows)} notes exported to {directory}"))
    return EXIT_OK


# ----------------------------------------------------------------------------- graph
def cmd_graph(args):
    from ..core import graph
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        ensure_documents(ctx)
        text = graph.render(graph.build(conn, ctx.wid, args.concepts), args.format)
    if args.output:
        Path(args.output).expanduser().write_text(text, encoding="utf-8")
        hint = {"mermaid": "mermaid.live / Markdown ```mermaid", "dot": "dot -Tsvg file.dot > graph.svg"}.get(args.format, "")
        ui.ok(tr(f"Graf disimpan: {args.output}", f"Graph saved: {args.output}") + (f"  ({hint})" if hint else ""))
    else:
        ui.out(text.rstrip())
    return EXIT_OK
