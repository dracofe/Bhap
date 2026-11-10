"""Shared rendering and context helpers for CLI commands and the interactive session."""
import json
import sys
import time

from .. import ui
from ..adapters import runtime
from ..config import load
from ..core import library
from ..errors import BhapError, ModelError
from ..i18n import t, tr


def emit_json(data):
    """Stable machine output: UTF-8 JSON, no ANSI, one document per call."""
    sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


class Context:
    """Per-invocation state: settings, DB connection and the active workspace."""

    def __init__(self, conn, args=None, cwd=None):
        self.conn = conn
        self.settings = load()
        explicit = getattr(args, "workspace", None) if args else None
        self.workspace, self.wid, self.ws_source = library.resolve(conn, explicit, cwd)

    def refresh(self):
        self.settings = load()


def readiness(settings=None):
    """Return dict describing runtime/model readiness without raising."""
    settings = settings or load()
    state = {"runtime": settings["runtime"], "runtime_ok": False, "runtime_version": None,
             "llm_model": settings["llm_model"], "embedding_model": settings["embedding_model"],
             "llm_ready": False, "embedding_ready": False, "error": None, "models": []}
    try:
        rt = runtime(settings)
        state["runtime_version"] = rt.version()
        state["runtime_ok"] = True
        state["models"] = rt.models()
        state["llm_ready"] = rt.has_model(settings["llm_model"])
        state["embedding_ready"] = rt.has_model(settings["embedding_model"])
    except (ModelError, OSError) as exc:
        state["error"] = str(exc)
    state["ready"] = state["runtime_ok"] and state["llm_ready"] and state["embedding_ready"]
    return state


def status_badge(state):
    if state["ready"]:
        return ui.style(ui.symbol("dot", sys.stdout) + " " + t("ready"), "green")
    if not state["runtime_ok"]:
        return ui.style(ui.symbol("dot", sys.stdout) + " " + t("model_unavailable"), "red")
    return ui.style(ui.symbol("dot", sys.stdout) + " " + t("setup_incomplete"), "yellow")


def setup_hint(state):
    if not state["runtime_ok"]:
        return tr("Jalankan runtime lokal (`ollama serve`), lalu `bhap doctor`.",
                  "Start the local runtime (`ollama serve`), then run `bhap doctor`.")
    missing = [m for m, ok in ((state["llm_model"] or "chat", state["llm_ready"]),
                               (state["embedding_model"] or "embedding", state["embedding_ready"])) if not ok]
    return tr(f"Model belum siap: {', '.join(missing)} → `bhap setup`",
              f"Models not ready: {', '.join(missing)} → `bhap setup`")


# --------------------------------------------------------------------- answers
def print_sources(cites, stream=None, excerpts=True):
    stream = stream or sys.stdout
    if not cites:
        return
    stream.write("\n" + ui.style("  " + t("ask.sources") + ":", "bold") + "\n")
    for cite in cites:
        tag = f"  #{cite['chunk_id']}"
        label = ui.truncate(f"{cite['file']} — {cite['locator']}", ui.width() - len(tag) - 8)
        stream.write(f"  {ui.style('[' + str(cite['marker']) + ']', 'cyan')} {label}{ui.style(tag, 'gray')}\n")
        if excerpts and cite.get("excerpt"):
            stream.write(ui.style(f"      “{ui.truncate(cite['excerpt'], ui.width() - 10)}”", "dim") + "\n")
    stream.flush()


def print_answer_footer(result, evidence=False, streamed=True):
    """Everything after the streamed answer text: warnings, sources, evidence map, gaps."""
    if streamed:
        ui.out()
    status = result["status"]
    if status == "cancelled":
        ui.warn(t("ask.cancelled"))
        return
    if result.get("invalid_markers"):
        ui.warn(t("ask.invalid_markers", markers=", ".join(f"[{m}]" for m in result["invalid_markers"])))
    if status == "unsupported":
        ui.warn(t("ask.unsupported"))
        if streamed:
            ui.out(result["answer"])
    if status in ("abstained", "unsupported"):
        if status == "unsupported" and result.get("mode") == "strict":
            ui.info(tr("Model punya jawaban, tapi tidak bersumber dari materi Anda. Untuk tugas umum "
                       "(mis. menulis/melengkapi kode) izinkan pengetahuan umum: /mode general "
                       "(atau bhap ask --mode general).",
                       "The model has an answer, but not from your material. For general tasks "
                       "(e.g. writing/completing code) allow general knowledge: /mode general "
                       "(or bhap ask --mode general)."))
            return
        if result.get("missing_terms"):
            ui.info(t("ask.gap", terms=", ".join(result["missing_terms"][:8])))
        else:
            ui.info(t("ask.gap_generic"))
        return
    print_sources(result.get("citations", []))
    if evidence and result.get("evidence_map"):
        ui.out("\n" + ui.style("  " + t("ask.evidence_map") + ":", "bold"))
        for claim in result["evidence_map"]:
            if claim["supported"]:
                mark = ui.style(ui.symbol("ok", sys.stdout), "green")
                refs = ui.style(" " + "".join(f"[{n}]" for n in claim["sources"]), "cyan")
            else:
                mark = ui.style("?", "yellow")
                refs = ui.style(" (" + t("ask.inference") + ")", "yellow")
            ui.out(f"  {mark} {ui.truncate(claim['claim'], ui.width() - 20)}{refs}")
    if result.get("documents_cited", 0) > 1:
        ui.out(ui.style("  " + t("ask.conflict_note"), "gray"))
    meta = t("ask.generated", model=result["model"], mode=t("mode." + result["mode"]), n=len(result["citations"]))
    timing = f" · {result.get('seconds', 0)}s"
    ui.out(ui.style("  " + meta + timing, "gray"))


def answer_json(result, workspace):
    keys = ("question", "answer", "status", "mode", "language", "model", "citations", "invalid_markers",
            "evidence_map", "missing_terms", "seconds", "retrieval_ms")
    data = {key: result.get(key) for key in keys}
    data["workspace"] = workspace
    if result.get("raw_answer"):
        data["raw_answer"] = result["raw_answer"]
    return data


class TokenPrinter:
    """Writes streamed tokens to stdout, hiding the spinner on the first token."""

    def __init__(self, spinner=None, prefix=""):
        self.spinner = spinner
        self.started = False
        self.chat = ui.chat_style()  # full-screen UI: "● answer" with an indented body
        self.prefix = prefix or (ui.style("● ", "cyan") if self.chat else "")

    def __call__(self, delta):
        if not self.started:
            if self.spinner:
                self.spinner.stop()
            sys.stdout.write(self.prefix)
            self.started = True
            delta = delta.lstrip("\n")
        if self.chat:
            delta = delta.replace("\n", "\n  ")
        sys.stdout.write(delta)
        sys.stdout.flush()


# --------------------------------------------------------------------- ingest progress
class IngestReporter:
    def __init__(self, total=1, json_mode=False):
        self.total = total
        self.index = 0
        self.json_mode = json_mode
        self.spinner = ui.Spinner("", enabled=not json_mode)
        self.started = None

    def begin(self, path):
        self.index += 1
        self.started = time.monotonic()
        self.name = path.name
        self.spinner.update(self._prefix() + t("ingest.stage.queued"))
        if not self.spinner._thread:
            self.spinner.__enter__()

    def _prefix(self):
        return f"[{self.index}/{self.total}] {self.name} · " if self.total > 1 else f"{self.name} · "

    def stage(self, stage, info):
        label = t("ingest.stage." + stage) if stage != "embedding" else "embedding"
        if stage == "embedding" and info.get("total"):
            label += f" {info.get('done', 0)}/{info['total']}"
        self.spinner.update(self._prefix() + label)

    def done(self, result):
        self.spinner.stop()
        status = result["status"]
        if self.json_mode:
            emit_json({"file": self.name, **{k: v for k, v in result.items() if k != "unit_label"}})
            return
        if status in ("indexed", "updated"):
            units = f"{result['units']} {unit_name(result['unit'])}"
            ui.ok(t("ingest.indexed", file=result["file"], units=units, chunks=result["chunks"])
                  + ui.style(f"  #{result['id']} · {ui.elapsed(self.started)}"
                             + (f" · cache {result['cached_embeddings']}" if result.get("cached_embeddings") else ""),
                             "gray"))
            for warning in result.get("warnings", []):
                ui.warn("  " + t("ingest.warning", text=warning))
        elif status == "unchanged":
            ui.info(t("ingest.unchanged", file=result["file"], id=result["id"]))
        elif status == "duplicate":
            ui.warn(t("ingest.duplicate", file=result["file"], id=result["id"], other=result["other"]))

    def failed(self, error):
        self.spinner.stop()
        if self.json_mode:
            emit_json({"file": self.name, "status": "failed", "error": str(error)})
        else:
            ui.error(t("ingest.failed", file=self.name, error=error), getattr(error, "hint", None))

    def close(self):
        self.spinner.stop()


def unit_name(unit):
    return {"page": tr("halaman", "pages"), "page~": tr("halaman (perkiraan)", "pages (approx.)"),
            "slide": "slide", "line": tr("baris", "lines")}.get(unit, unit)


def run_ingest(ctx, files, folder="", force=False, json_mode=False, confirm_change=None):
    """Index files with per-file progress; returns (ok, skipped, failed). Ctrl+C stops cleanly."""
    from ..core.ingest import Ingestor
    reporter = IngestReporter(len(files), json_mode)
    ingestor = Ingestor(ctx.conn, ctx.wid, ctx.settings, progress=reporter.stage)
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    try:
        for path in files:
            reporter.begin(path)
            try:
                result = ingestor.import_path(path, folder=folder, force=force, confirm_change=confirm_change)
            except ModelError:
                reporter.close()
                raise
            except BhapError as exc:
                reporter.failed(exc)
                counts["failed"] += 1
                continue
            except OSError as exc:
                reporter.failed(exc)
                counts["failed"] += 1
                continue
            reporter.done(result)
            counts["ok" if result["status"] in ("indexed", "updated") else "skipped"] += 1
    except KeyboardInterrupt:
        reporter.close()
        ui.warn(t("ingest.cancelled"))
        raise
    finally:
        reporter.close()
    if len(files) > 1 and not json_mode:
        ui.info(t("ingest.summary", **counts))
    return counts
