"""Workspaces and document metadata queries."""
import json
import re
import shutil
from pathlib import Path

from .. import config
from ..errors import NotFoundError, UsageError
from ..i18n import t

NAME = re.compile(r"^[\w][\w .\-]{0,63}$", re.UNICODE)


def validate_name(name):
    name = (name or "").strip()
    if not NAME.match(name) or name in {".", ".."}:
        raise UsageError(t("ws.invalid"))
    return name


def get_workspace(conn, name):
    row = conn.execute("SELECT * FROM workspaces WHERE name=?", (name,)).fetchone()
    if not row:
        raise NotFoundError(t("ws.missing", name=name), hint=t("ws.missing_hint", name=name))
    return row


def create_workspace(conn, name, description=""):
    name = validate_name(name)
    if conn.execute("SELECT 1 FROM workspaces WHERE name=?", (name,)).fetchone():
        raise UsageError(t("ws.exists", name=name))
    return conn.execute("INSERT INTO workspaces(name, description) VALUES(?, ?)", (name, description)).lastrowid


def ensure_workspace(conn, name):
    row = conn.execute("SELECT id FROM workspaces WHERE name=?", (name,)).fetchone()
    return row["id"] if row else create_workspace(conn, name)


def resolve(conn, explicit=None, cwd=None):
    """Pick the active workspace: -w flag > linked project folder > config > 'personal'.

    Returns (name, id, source). The default personal workspace is created on demand;
    named workspaces must exist.
    """
    if explicit:
        row = get_workspace(conn, explicit)
        return row["name"], row["id"], "flag"
    folder, project = config.find_project(cwd or Path.cwd())
    if project.get("workspace"):
        name = project["workspace"]
        row = conn.execute("SELECT id FROM workspaces WHERE name=?", (name,)).fetchone()
        wid = row["id"] if row else create_workspace(conn, name)
        return name, wid, f"project:{folder}"
    name = config.load()["workspace"] or "personal"
    row = conn.execute("SELECT id FROM workspaces WHERE name=?", (name,)).fetchone()
    if row:
        return name, row["id"], "config"
    if name != "personal":
        name = "personal"
    return name, ensure_workspace(conn, name), "default"


def workspaces(conn):
    return conn.execute("""SELECT w.*, (SELECT COUNT(*) FROM documents d WHERE d.workspace_id=w.id) docs,
                           (SELECT COUNT(*) FROM documents d WHERE d.workspace_id=w.id AND d.status='indexed') n_indexed,
                           (SELECT MAX(updated_at) FROM documents d WHERE d.workspace_id=w.id) last_doc
                           FROM workspaces w ORDER BY w.name""").fetchall()


def workspace_dir(wid, *parts):
    return config.path("workspaces", str(wid), *parts)


def delete_workspace(conn, name):
    row = get_workspace(conn, name)
    from .ingest import purge_cache_for
    doc_ids = [r["id"] for r in conn.execute("SELECT id FROM documents WHERE workspace_id=?", (row["id"],))]
    from ..storage import transaction
    with transaction(conn):
        for doc_id in doc_ids:
            purge_cache_for(conn, doc_id)
        conn.execute("DELETE FROM workspaces WHERE id=?", (row["id"],))
    shutil.rmtree(config.home() / "workspaces" / str(row["id"]), ignore_errors=True)
    settings = config.stored()
    if settings.get("workspace") == name:
        config.update(workspace="personal")


def rename_workspace(conn, old, new):
    row = get_workspace(conn, old)
    new = validate_name(new)
    if conn.execute("SELECT 1 FROM workspaces WHERE name=?", (new,)).fetchone():
        raise UsageError(t("ws.exists", name=new))
    conn.execute("UPDATE workspaces SET name=? WHERE id=?", (new, row["id"]))
    if config.stored().get("workspace") == old:
        config.update(workspace=new)


# ---------------------------------------------------------------- documents
def documents(conn, wid, folder=None):
    query = """SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) chunk_count
               FROM documents d WHERE d.workspace_id=?"""
    params = [wid]
    if folder:
        query += " AND (d.folder=? OR d.folder LIKE ?)"
        params += [folder, folder.rstrip("/") + "/%"]
    return conn.execute(query + " ORDER BY d.folder, d.id", params).fetchall()


def document(conn, wid, doc_id):
    row = conn.execute("""SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) chunk_count
                          FROM documents d WHERE d.id=? AND d.workspace_id=?""", (doc_id, wid)).fetchone()
    if not row:
        raise NotFoundError(t("docs.missing", id=doc_id), hint="bhap docs list")
    return row


def find_document(conn, wid, ref):
    """Resolve a document by numeric id, exact filename, or unique filename prefix."""
    ref = str(ref).strip()
    if ref.isdigit():
        return document(conn, wid, int(ref))
    rows = conn.execute("SELECT id FROM documents WHERE workspace_id=? AND (filename=? OR title=?)",
                        (wid, ref, ref)).fetchall()
    if not rows:
        rows = conn.execute("SELECT id FROM documents WHERE workspace_id=? AND filename LIKE ?",
                            (wid, ref.replace("%", "") + "%")).fetchall()
    if len(rows) == 1:
        return document(conn, wid, rows[0]["id"])
    if not rows:
        raise NotFoundError(t("docs.missing", id=ref), hint="bhap docs list")
    raise UsageError(f"'{ref}' matches {len(rows)} documents; use the numeric id (bhap docs list)")


def warnings(row):
    try:
        return json.loads(row["warnings"] or "[]")
    except ValueError:
        return []


def stale_count(conn, wid, model):
    return conn.execute("""SELECT COUNT(*) FROM documents d WHERE d.workspace_id=? AND (d.status='stale' OR
                           (d.status='indexed' AND EXISTS (SELECT 1 FROM chunks c JOIN embeddings e ON e.chunk_id=c.id
                            WHERE c.document_id=d.id AND e.model != ?)))""", (wid, model)).fetchone()[0]


def stats(conn, wid):
    row = conn.execute("""SELECT COUNT(*) docs, SUM(status='indexed') n_indexed, SUM(size) bytes,
                          (SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.workspace_id=?) chunks
                          FROM documents WHERE workspace_id=?""", (wid, wid)).fetchone()
    return {"documents": row["docs"] or 0, "indexed": row["n_indexed"] or 0, "bytes": row["bytes"] or 0,
            "chunks": row["chunks"] or 0}
