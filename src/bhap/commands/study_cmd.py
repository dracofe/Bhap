"""bhap study quiz | flashcards | review | sprint | teach | tutor | progress | note | export | reset."""
import json
import sys
from pathlib import Path

from .. import ui
from ..core import citations, generation, study
from ..errors import EXIT_OK, UsageError
from ..i18n import t, tr
from ..storage import sqlite
from .common import Context, TokenPrinter, emit_json, print_sources
from .qa import build_scope, ensure_documents


def _refs_line(item):
    refs = json.loads(item["source_refs"] or "[]")
    return "; ".join(f"{r['file']} — {r['locator']}" for r in refs)


def _read_multiline(prompt):
    ui.out(ui.style(prompt + tr(" (baris kosong untuk selesai)", " (empty line to finish)"), "gray"))
    lines = []
    while True:
        try:
            line = input("  ")
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines).strip()


def run_items(ctx, items, interactive=True):
    """Ask study items one by one, grade, give feedback with references, update schedule."""
    if not items:
        ui.info(t("study.none_due"))
        return 0, 0
    if not interactive:
        for item in items:
            _print_item(item, reveal=True)
        return 0, 0
    ui.out(ui.style(t("study.generated_label"), "gray"))
    score_total, count = 0.0, 0
    for index, item in enumerate(items, 1):
        ui.out()
        ui.out(ui.style(f"[{index}/{len(items)}] ", "bold") + item["prompt"])
        try:
            if item["type"] == "mcq":
                choices = json.loads(item["choices"] or "[]")
                for i, choice in enumerate(choices):
                    ui.out(f"   {chr(65 + i)}. {choice}")
                answer = input(tr("  Jawaban (A-D, Enter=lewati): ", "  Answer (A-D, Enter=skip): ")).strip().upper()
                correct = int(item["solution"])
                chosen = ord(answer[0]) - 65 if answer else -1
                score = 1.0 if chosen == correct else 0.0
                if score:
                    ui.ok(t("study.correct"))
                else:
                    ui.warn(t("study.wrong", answer=f"{chr(65 + correct)}. {choices[correct]}"))
                if item["explanation"]:
                    ui.out(ui.style("  " + item["explanation"], "dim"))
                study.record_attempt(ctx.conn, item, answer or "(skip)", score)
            elif item["type"] == "essay":
                response = _read_multiline(t("study.your_answer"))
                if not response:
                    ui.info(tr("Dilewati. Jawaban acuan:", "Skipped. Reference answer:"))
                    ui.out("  " + item["solution"])
                    study.record_attempt(ctx.conn, item, "(skip)", 0.0)
                    score = 0.0
                else:
                    with ui.Spinner(tr("Menilai…", "Grading…")):
                        score, feedback, _ = study.grade_essay(ctx.conn, item, response, ctx.settings)
                    color = "green" if score >= 0.75 else ("yellow" if score >= 0.5 else "red")
                    ui.out(ui.style(f"  {int(score * 100)}/100", "bold", color) + "  " + feedback)
                    ui.out(ui.style(tr("  Jawaban acuan: ", "  Reference answer: ") + item["solution"], "dim"))
                    study.record_attempt(ctx.conn, item, response, score, feedback)
            else:  # flashcard
                input(ui.style("  " + t("study.reveal"), "gray"))
                ui.out("  " + ui.style(item["solution"], "cyan"))
                rating = input(t("study.rate")).strip()
                rating = int(rating) if rating in {"1", "2", "3", "4"} else 3
                score = (rating - 1) / 3
                ctx.conn.execute("INSERT INTO study_attempts(item_id, response, score) VALUES(?,?,?)",
                                 (item["id"], f"rating {rating}", score))
                study.schedule(ctx.conn, item["id"], rating)
            ui.out(ui.style("  ↳ " + _refs_line(item), "gray"))
            score_total += score
            count += 1
        except KeyboardInterrupt:
            ui.err()
            break
        except EOFError:
            break
    if count:
        ui.out()
        ui.out(ui.style(t("study.score", score=round(score_total, 1), total=count), "bold"))
    return score_total, count


def _print_item(item, reveal=False):
    ui.out(ui.style(f"#{item['id']} [{item['type']}] ", "bold") + item["prompt"])
    for i, choice in enumerate(json.loads(item["choices"] or "[]")):
        mark = " ✔" if reveal and str(i) == item["solution"] else ""
        ui.out(f"   {chr(65 + i)}. {choice}{mark}")
    if reveal and item["type"] != "mcq":
        ui.out(ui.style("   → " + item["solution"], "cyan"))
    if reveal and item["explanation"]:
        ui.out(ui.style("   " + item["explanation"], "dim"))
    ui.out(ui.style("   ↳ " + _refs_line(item), "gray"))


def _items_json(rows):
    return [{"id": r["id"], "topic": r["topic"], "type": r["type"], "prompt": r["prompt"],
             "choices": json.loads(r["choices"] or "[]"), "solution": r["solution"], "explanation": r["explanation"],
             "sources": json.loads(r["source_refs"] or "[]")} for r in rows]


def cmd_study(args):
    with sqlite.db() as conn:
        ctx = Context(conn, args)
        interactive = sys.stdin.isatty() and sys.stdout.isatty() and not getattr(args, "json", False)
        action = args.action
        if action in ("quiz", "flashcards", "teach", "tutor") and not args.topic:
            raise UsageError(f"bhap study {action} <topic>")
        if action == "quiz":
            ensure_documents(ctx)
            with ui.Spinner(tr("Membuat soal dari materi…", "Generating questions from your material…"),
                            enabled=not args.json):
                ids = study.generate_quiz(conn, ctx.wid, args.topic, count=args.count, kind=args.kind,
                                          difficulty=args.difficulty, settings=ctx.settings,
                                          scope=build_scope(ctx, args.doc, args.folder))
            rows = study.items(conn, ctx.wid, ids=ids, limit=len(ids))
            if args.json:
                emit_json({"topic": args.topic, "items": _items_json(rows)})
            else:
                run_items(ctx, rows, interactive)
        elif action == "flashcards":
            ensure_documents(ctx)
            with ui.Spinner(tr("Membuat flashcards…", "Generating flashcards…"), enabled=not args.json):
                ids = study.generate_flashcards(conn, ctx.wid, args.topic, count=args.count, settings=ctx.settings,
                                                scope=build_scope(ctx, args.doc, args.folder))
            rows = study.items(conn, ctx.wid, ids=ids, limit=len(ids))
            if args.json:
                emit_json({"topic": args.topic, "items": _items_json(rows)})
            else:
                ui.ok(tr(f"{len(rows)} flashcard dibuat untuk '{args.topic}'.", f"{len(rows)} flashcards created for '{args.topic}'."))
                if interactive and ui.confirm(tr("Latih sekarang?", "Practice now?"), default=True):
                    run_items(ctx, rows, interactive)
                else:
                    for row in rows:
                        _print_item(row, reveal=True)
        elif action == "review":
            rows = study.items(conn, ctx.wid, topic=args.topic, due=True, limit=args.count)
            if args.json:
                emit_json({"due": _items_json(rows)})
            else:
                run_items(ctx, rows, interactive)
        elif action == "sprint":
            rows = study.items(conn, ctx.wid, topic=args.topic, weakest=True, limit=args.count,
                               kinds=("mcq", "essay", "flashcard"))
            if len(rows) < args.count and args.topic:
                ensure_documents(ctx)
                with ui.Spinner(tr("Menambah soal latihan baru…", "Adding fresh practice questions…")):
                    ids = study.generate_quiz(conn, ctx.wid, args.topic, count=args.count - len(rows),
                                              difficulty=args.difficulty, settings=ctx.settings)
                rows = list(rows) + list(study.items(conn, ctx.wid, ids=ids, limit=len(ids)))
            ui.out(ui.style(tr(f"Exam Sprint · {len(rows)} soal · fokus pada yang sering salah",
                               f"Exam Sprint · {len(rows)} items · focusing on your weak spots"), "bold"))
            run_items(ctx, rows, interactive)
        elif action == "teach":
            ensure_documents(ctx)
            hits, lang = study.topic_material(conn, ctx.wid, args.topic, ctx.settings,
                                              scope=build_scope(ctx, args.doc, args.folder))
            if not hits:
                ui.info(t("ask.gap_generic"))
                return EXIT_OK
            spinner = ui.Spinner(tr("Menyiapkan pelajaran…", "Preparing the lesson…"))
            printer = TokenPrinter(spinner)
            with spinner:
                text, cancelled = generation.stream(ctx.settings, study.lesson_messages(args.topic, hits, lang), printer)
            ui.out()
            result = study.finish_text(text, hits, cancelled)
            print_sources(result["citations"])
            with sqlite.transaction(conn):
                study.touch_concept(conn, ctx.wid, args.topic)
            if not cancelled and interactive and ui.confirm(tr("Kerjakan 3 soal latihan sekarang?",
                                                               "Do 3 practice questions now?"), default=True):
                with ui.Spinner(tr("Membuat soal…", "Generating questions…")):
                    ids = study.generate_quiz(conn, ctx.wid, args.topic, count=3, settings=ctx.settings)
                run_items(ctx, study.items(conn, ctx.wid, ids=ids, limit=3), interactive)
        elif action == "tutor":
            ensure_documents(ctx)
            if not interactive:
                raise UsageError(t("not_tty"))
            tutor_session(ctx, args.topic, scope=build_scope(ctx, args.doc, args.folder))
        elif action == "progress":
            rows = study.progress(conn, ctx.wid)
            if args.json:
                emit_json([dict(r) for r in rows])
                return EXIT_OK
            if not rows:
                ui.info(t("study.progress_empty"))
                return EXIT_OK
            ui.table([(r["topic"], str(r["items"]), str(r["attempts"]),
                       f"{int(r['accuracy'])}%" if r["accuracy"] is not None else "-", str(r["lapses"] or 0),
                       str(r["due"]), (r["last"] or "")[:16], ui.truncate(r["difficulty"] or r["note"] or "", 40))
                      for r in rows],
                     [tr("TOPIK", "TOPIC"), "ITEM", tr("PERCOBAAN", "ATTEMPTS"), tr("AKURASI", "ACCURACY"),
                      tr("LUPA", "LAPSES"), tr("JATUH TEMPO", "DUE"), tr("TERAKHIR", "LAST"), tr("CATATAN", "NOTE")])
            ui.out(ui.style(tr("Bhap mencatat latihan Anda, bukan mengklaim Anda sudah menguasai materi.",
                               "Bhap tracks your practice; it never claims you have mastered a topic."), "gray"))
        elif action == "note":
            if not args.topic or not args.text:
                raise UsageError('bhap study note <topic> "<difficulty or note>"')
            with sqlite.transaction(conn):
                study.touch_concept(conn, ctx.wid, args.topic, note=args.text, difficulty=args.text)
            ui.ok(tr(f"Catatan konsep '{args.topic}' disimpan.", f"Concept note for '{args.topic}' saved."))
        elif action == "export":
            text = study.export(conn, ctx.wid, fmt=args.format, topic=args.topic)
            if args.output:
                Path(args.output).expanduser().write_text(text + "\n", encoding="utf-8")
                ui.ok(tr(f"Diekspor: {args.output}", f"Exported: {args.output}"))
            else:
                ui.out(text)
        elif action == "reset":
            if not args.yes and not ui.confirm(tr(f"Hapus semua soal, kartu, dan progres belajar di '{ctx.workspace}'?",
                                                  f"Delete all study items and progress in '{ctx.workspace}'?")):
                ui.info(t("cancelled"))
                return EXIT_OK
            with sqlite.transaction(conn):
                conn.execute("DELETE FROM study_items WHERE workspace_id=?", (ctx.wid,))
                conn.execute("DELETE FROM concepts WHERE workspace_id=?", (ctx.wid,))
            ui.ok(t("study.reset", name=ctx.workspace))
    return EXIT_OK


def tutor_session(ctx, topic, scope=None):
    hits, lang = study.topic_material(ctx.conn, ctx.wid, topic, ctx.settings, scope=scope)
    if not hits:
        ui.info(t("ask.gap_generic"))
        return
    ui.out(ui.style(t("study.tutor_intro", topic=topic), "bold"))
    messages = study.lesson_messages(topic, hits, lang, socratic=True)
    all_cites = {}
    while True:
        spinner = ui.Spinner(tr("Tutor berpikir…", "Tutor is thinking…"))
        printer = TokenPrinter(spinner, prefix=ui.style("tutor ❯ ", "magenta"))
        with spinner:
            text, cancelled = generation.stream(ctx.settings, messages, printer)
        ui.out()
        result = study.finish_text(text, hits, cancelled)
        for cite in result["citations"]:
            all_cites[cite["marker"]] = cite
        if result["citations"]:
            ui.out(ui.style("  ↳ " + "; ".join(f"[{c['marker']}] {c['file']} — {c['locator']}"
                                                for c in result["citations"]), "gray"))
        messages.append({"role": "assistant", "content": text})
        try:
            reply = input(ui.style("anda ❯ " if lang == "id" else "you ❯ ", "cyan")).strip()
        except (EOFError, KeyboardInterrupt):
            ui.err()
            break
        if reply in ("/exit", "/quit", "/q"):
            break
        if reply.startswith("/hard"):
            with sqlite.transaction(ctx.conn):
                study.touch_concept(ctx.conn, ctx.wid, topic, difficulty=reply[5:].strip() or "sulit")
            ui.ok(tr("Kesulitan dicatat untuk sesi berikutnya.", "Difficulty noted for next time."))
            continue
        messages.append({"role": "user", "content": reply or "/hint"})
    with sqlite.transaction(ctx.conn):
        study.touch_concept(ctx.conn, ctx.wid, topic)
    if all_cites:
        print_sources(sorted(all_cites.values(), key=lambda c: c["marker"]), excerpts=False)


__all__ = ["cmd_study", "tutor_session", "run_items", "citations"]
