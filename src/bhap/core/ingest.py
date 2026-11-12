"""Document ingestion: scan → hash → parse → chunk → embed → atomic index write.

Every file gets a persistent job row so progress, failures and interruptions survive a
restart. Nothing is written to the knowledge index until all expensive work succeeded,
so a crash or Ctrl+C never leaves a half-indexed document that looks searchable.
"""
import fnmatch
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .. import config
from ..errors import ParseError, UsageError
from ..i18n import t
from ..logs import log
from ..parsers import FORMATS_HELP, PARSER_VERSION, SUPPORTED, kind_of, parse_file
from ..parsers.common import looks_secret, sniff
from ..storage import transaction
from .chunking import chunk
from .embedder import Embedder
from .library import workspace_dir

IGNORED_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".cache", ".bhap",
                ".tox", ".mypy_cache", ".pytest_cache", "site-packages", ".idea", ".vscode", "dist", "build",
                "$RECYCLE.BIN", "System Volume Information", ".Trash", ".local", ".npm", ".cargo"}
RUNNING = ("queued", "extracting", "chunking", "embedding")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------ scanning
def scan(raw_paths, cwd=None, recursive=False, exclude=(), max_mb=None):
    """Expand files/directories safely.

    Returns (files, skipped) where skipped maps a reason to a list of paths. Directory
    walks never follow symlinks, skip hidden/tooling folders and Bhap's own data dir.
    """
    cwd = Path(cwd or Path.cwd())
    data_root = config.home().resolve()
    files, skipped, seen = [], {}, set()
    limit = (max_mb or config.load()["max_file_mb"]) * 1024 * 1024

    def skip(reason, item):
        skipped.setdefault(reason, []).append(str(item))

    def consider(item, explicit=False):
        try:
            resolved = item.resolve()
        except OSError:
            return skip("unreadable", item)
        if resolved in seen:
            return None
        if any(fnmatch.fnmatch(item.name, pattern) or fnmatch.fnmatch(str(item), pattern) for pattern in exclude):
            return skip("excluded", item)
        kind = kind_of(item.name)
        if not explicit and looks_secret(item.name):
            return skip("secret-like", item)  # .env, private keys … only when named explicitly
        if kind not in SUPPORTED:
            if explicit:
                try:
                    textual = sniff(item) == "text"
                except OSError:
                    textual = False
                if not textual:
                    raise UsageError(t("repl.unsupported_file", file=item.name, formats=FORMATS_HELP))
            else:
                return skip("unsupported", item)
        if not explicit and item.is_symlink():
            return skip("symlink", item)
        if data_root in resolved.parents:
            return skip("bhap-data", item)
        try:
            size = resolved.stat().st_size
        except OSError:
            return skip("unreadable", item)
        if size > limit:
            if explicit:
                raise UsageError(t("ingest.too_large", file=item.name, limit=limit // 2**20))
            return skip("too-large", item)
        if size == 0:
            return skip("empty", item)
        seen.add(resolved)
        files.append(resolved)
        return None

    for raw in raw_paths:
        target = Path(os.path.expandvars(str(raw))).expanduser()
        if not target.is_absolute():
            target = cwd / target
        if target.is_file():
            consider(target, explicit=True)
        elif target.is_dir():
            if target.resolve() == data_root or data_root in target.resolve().parents:
                skip("bhap-data", target)
                continue
            if recursive:
                for root, dirs, names in os.walk(target, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
                    for name in sorted(names):
                        if not name.startswith((".", "~$")):
                            consider(Path(root) / name)
            else:
                for item in sorted(target.iterdir()):
                    if item.is_file() and not item.name.startswith((".", "~$")):
                        consider(item)
        else:
            raise UsageError(t("repl.path_missing", path=raw))
    return files, skipped


def preview(files):
    by_ext, total = {}, 0
    for item in files:
        by_ext[item.suffix.lower()] = by_ext.get(item.suffix.lower(), 0) + 1
        try:
            total += item.stat().st_size
        except OSError:
            pass
    return by_ext, total


# ------------------------------------------------------------------ jobs
def _pid_alive(pid):
    if not pid:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return False  # conservative: treat as dead; job can be retried
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_jobs(conn):
    """Mark jobs whose process died mid-way as interrupted (restart safety)."""
    count = 0
    for row in conn.execute(f"SELECT id, pid FROM jobs WHERE status IN ({','.join('?' * len(RUNNING))})", RUNNING).fetchall():
        if not _pid_alive(row["pid"]):
            conn.execute("UPDATE jobs SET status='interrupted', updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            count += 1
    return count


def _job(conn, job_id, **fields):
    if job_id is None:
        return
    sets = ", ".join(f"{key}=?" for key in fields)
    conn.execute(f"UPDATE jobs SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields.values(), job_id))


def purge_cache_for(conn, doc_id):
    """Remove cached vectors that only this document used (hard-delete hygiene)."""
    conn.execute("""DELETE FROM embed_cache WHERE text_hash IN (SELECT text_hash FROM chunks WHERE document_id=?)
                    AND text_hash NOT IN (SELECT text_hash FROM chunks WHERE document_id != ?)""", (doc_id, doc_id))


# ------------------------------------------------------------------ ingest
class Ingestor:
    def __init__(self, conn, wid, settings=None, progress=None):
        self.conn = conn
        self.wid = wid
        self.settings = settings or config.load()
        self.progress = progress or (lambda stage, info: None)
        self._embedder = None

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = Embedder(self.settings, self.conn)
        return self._embedder

    def import_path(self, path, folder="", force=False, confirm_change=None, original_path=None, job_id=None):
        """Index one file. Returns a result dict with status indexed/unchanged/duplicate/updated."""
        path = Path(path)
        original = str(original_path or path)
        if job_id is None:
            job_id = self.conn.execute("INSERT INTO jobs(workspace_id, type, target, options, status, pid) "
                                       "VALUES(?, 'ingest', ?, ?, 'queued', ?)",
                                       (self.wid, original, json.dumps({"folder": folder, "force": force}),
                                        os.getpid())).lastrowid
        else:
            _job(self.conn, job_id, status="queued", error="", pid=os.getpid())
        started = time.monotonic()
        try:
            result = self._import(path, original, folder, force, confirm_change, job_id)
        except KeyboardInterrupt:
            _job(self.conn, job_id, status="cancelled", error="cancelled by user")
            raise
        except Exception as exc:
            _job(self.conn, job_id, status="failed", error=str(exc)[:500])
            log("ingest.failed", level="error", file=original, error=str(exc))
            raise
        status = "done" if result["status"] in ("indexed", "updated") else "skipped"
        _job(self.conn, job_id, status=status, progress=1.0, document_id=result.get("id"))
        result["seconds"] = round(time.monotonic() - started, 2)
        log("ingest." + result["status"], file=original, chunks=result.get("chunks"), seconds=result["seconds"])
        return result

    def _import(self, path, original, folder, force, confirm_change, job_id):
        if not path.is_file():
            raise UsageError(t("repl.path_missing", path=path))
        size = path.stat().st_size
        limit = self.settings["max_file_mb"] * 1024 * 1024
        if size > limit:
            raise UsageError(t("ingest.too_large", file=path.name, limit=self.settings["max_file_mb"]))
        free = shutil.disk_usage(config.home()).free if config.home().exists() else None
        needed = size * (2 if self.settings["store_originals"] else 1) + 200 * 1024 * 1024
        if free is not None and free < needed:
            raise UsageError(f"Not enough free disk space in {config.home()} ({free // 2**20} MB free); "
                             "free some space or set BHAP_HOME to a larger disk")
        digest = sha256_file(path)
        existing = self.conn.execute("SELECT * FROM documents WHERE workspace_id=? AND original_path=?",
                                     (self.wid, original)).fetchone()
        if existing and existing["sha256"] == digest and existing["status"] == "indexed" and not force:
            return {"status": "unchanged", "id": existing["id"], "file": path.name}
        duplicate = self.conn.execute("SELECT id, filename FROM documents WHERE workspace_id=? AND sha256=? "
                                      "AND status='indexed' AND id != ?",
                                      (self.wid, digest, existing["id"] if existing else -1)).fetchone()
        if duplicate and not force:
            return {"status": "duplicate", "id": duplicate["id"], "file": path.name, "other": duplicate["filename"]}
        if existing and existing["sha256"] and existing["sha256"] != digest and existing["status"] == "indexed":
            if confirm_change and not confirm_change(existing):
                return {"status": "unchanged", "id": existing["id"], "file": path.name, "declined": True}

        _job(self.conn, job_id, status="extracting", progress=0.1)
        self.progress("extracting", {"file": path.name})
        parsed = parse_file(path, self.settings)

        _job(self.conn, job_id, status="chunking", progress=0.3)
        self.progress("chunking", {"file": path.name})
        title = path.stem
        items = list(chunk(parsed["blocks"], self.settings["chunk_words"], self.settings["chunk_overlap"]))
        if not items:
            raise ParseError(t("ingest.no_text"))
        for item in items:
            header = f"{title}" + (f" — {item['section_path']}" if item["section_path"] else "")
            item["embed_text"] = f"{header}\n{item['text']}"
            item["embed_hash"] = hashlib.sha256(
                (self.embedder.model + "\x00" + item["embed_text"]).encode("utf-8")).hexdigest()

        _job(self.conn, job_id, status="embedding", progress=0.4)

        def on_progress(done, total):
            _job(self.conn, job_id, progress=round(0.4 + 0.55 * done / max(total, 1), 3))
            self.progress("embedding", {"file": path.name, "done": done, "total": total})

        self.progress("embedding", {"file": path.name, "done": 0, "total": len(items)})
        vectors, cached = self.embedder.documents(items, on_progress)

        kind = kind_of(path.name).lstrip(".") or "txt"
        warnings = json.dumps(parsed.get("warnings", []), ensure_ascii=False)
        with transaction(self.conn):
            if existing:
                doc_id = existing["id"]
                purge_cache_for(self.conn, doc_id)
                self.conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
                self.conn.execute("""UPDATE documents SET sha256=?, size=?, status='indexed', warnings=?, unit=?, units=?,
                                     kind=?, folder=COALESCE(NULLIF(?, ''), folder), updated_at=CURRENT_TIMESTAMP
                                     WHERE id=?""",
                                  (digest, size, warnings, parsed["unit"], parsed["units"], kind, folder, doc_id))
            else:
                doc_id = self.conn.execute(
                    """INSERT INTO documents(workspace_id, title, filename, original_path, folder, sha256, size, kind,
                       status, unit, units, warnings) VALUES(?,?,?,?,?,?,?,?,'indexed',?,?,?)""",
                    (self.wid, title, path.name, original, folder, digest, size, kind, parsed["unit"],
                     parsed["units"], warnings)).lastrowid
            version_id = self.conn.execute(
                """INSERT INTO document_versions(document_id, sha256, parser_version, embedding_model, chunk_count, size)
                   VALUES(?,?,?,?,?,?)""",
                (doc_id, digest, f"{PARSER_VERSION}:{parsed.get('parser', '')}", self.embedder.model,
                 len(items), size)).lastrowid
            for item, (dims, blob) in zip(items, vectors):
                chunk_id = self.conn.execute(
                    """INSERT INTO chunks(document_id, version_id, ordinal, page_no, slide_no, line_no, section_path,
                       kind, approx, visual, ocr_conf, text, text_hash, token_count)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (doc_id, version_id, item["ordinal"], item["page_no"], item["slide_no"], item["line_no"],
                     item["section_path"], item["kind"], int(item["approx"]), int(item["visual"]), item["ocr_conf"],
                     item["text"], item["embed_hash"], item["token_count"])).lastrowid
                self.conn.execute("INSERT INTO embeddings(chunk_id, model, dims, vector) VALUES(?,?,?,?)",
                                  (chunk_id, self.embedder.model, dims, blob))
                self.conn.execute("INSERT OR IGNORE INTO embed_cache(text_hash, model, dims, vector) VALUES(?,?,?,?)",
                                  (item["embed_hash"], self.embedder.model, dims, blob))
            stored = ""
            if self.settings["store_originals"]:
                target = workspace_dir(self.wid, "originals") / f"{doc_id}{path.suffix.lower()}"
                if path.resolve() != target.resolve():
                    tmp = target.with_suffix(target.suffix + ".part")
                    shutil.copyfile(path, tmp)
                    os.replace(tmp, target)
                stored = str(target)
            elif existing and existing["stored_path"]:
                Path(existing["stored_path"]).unlink(missing_ok=True)
            self.conn.execute("UPDATE documents SET current_version_id=?, stored_path=? WHERE id=?",
                              (version_id, stored, doc_id))
        self.progress("indexed", {"file": path.name})
        unit_label = {"page": "hal.", "page~": "hal.", "slide": "slide", "line": "baris"}.get(parsed["unit"], "")
        return {"status": "updated" if existing else "indexed", "id": doc_id, "file": path.name,
                "chunks": len(items), "cached_embeddings": cached, "units": parsed["units"], "unit": parsed["unit"],
                "unit_label": unit_label, "warnings": parsed.get("warnings", []), "version_id": version_id}

    def import_text(self, name, text, folder=""):
        """Index text from stdin as a document (stored under the workspace inbox)."""
        safe = "".join(ch for ch in Path(name).name if ch.isalnum() or ch in "-_. ") or "stdin.txt"
        if Path(safe).suffix.lower() not in {".md", ".txt", ".markdown"}:
            safe += ".txt"
        target = workspace_dir(self.wid, "inbox") / safe
        target.write_text(text, encoding="utf-8")
        return self.import_path(target, folder=folder, force=False)

    def reindex(self, row, force=True):
        original = Path(row["original_path"])
        source = original
        note = None
        if not original.is_file():
            if row["stored_path"] and Path(row["stored_path"]).is_file():
                source, note = Path(row["stored_path"]), t("docs.moved")
            else:
                raise UsageError(f"#{row['id']} {row['filename']}: {t('docs.moved')}; "
                                 "no stored copy (reference mode) — re-add the file from its new location")
        result = self.import_path(source, folder=row["folder"], force=force, original_path=row["original_path"])
        if note:
            result.setdefault("warnings", []).append(note)
        return result


def remove(conn, wid, doc_id):
    row = conn.execute("SELECT * FROM documents WHERE id=? AND workspace_id=?", (doc_id, wid)).fetchone()
    if not row:
        raise UsageError(t("docs.missing", id=doc_id))
    with transaction(conn):
        purge_cache_for(conn, doc_id)
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))  # cascades to versions/chunks/vectors/citations
        conn.execute("UPDATE jobs SET document_id=NULL WHERE document_id=?", (doc_id,))
    if row["stored_path"]:
        Path(row["stored_path"]).unlink(missing_ok=True)
    owned = {config.home() / "workspaces" / str(wid) / name for name in ("inbox", "uploads")}
    if Path(row["original_path"]).parent in owned:  # files Bhap itself received (stdin / app uploads)
        Path(row["original_path"]).unlink(missing_ok=True)
    log("docs.removed", id=doc_id)
    return row


def check(conn, wid):
    """Compare each document with its original on disk."""
    states = []
    for row in conn.execute("SELECT * FROM documents WHERE workspace_id=? ORDER BY id", (wid,)).fetchall():
        original = Path(row["original_path"])
        if not original.is_file():
            state = "missing"
        elif row["status"] == "stale":
            state = "stale"
        elif original.stat().st_size != row["size"] or sha256_file(original) != row["sha256"]:
            state = "modified"
        else:
            state = "ok"
        states.append((row, state))
    return states
