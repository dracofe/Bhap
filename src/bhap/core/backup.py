"""Portable knowledge packs (.bhap): manifest + data + optional originals, optional encryption.

Layout (ZIP): manifest.json, data.jsonl, originals/<doc-id><ext>
Encrypted packs: b"BHAPENC1" + 16-byte salt + Fernet token of the ZIP, key = scrypt(passphrase).
"""
import base64
import hashlib
import io
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

from .. import __version__, config
from ..errors import BhapError, UsageError
from ..storage import transaction
from ..storage.sqlite import SCHEMA_VERSION
from .library import create_workspace, get_workspace, workspace_dir

FORMAT = "bhap-pack"
FORMAT_VERSION = 1
MAGIC = b"BHAPENC1"

TABLES = [  # (table, filter SQL by workspace id)
    ("documents", "SELECT * FROM documents WHERE workspace_id=?"),
    ("document_versions", "SELECT v.* FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE d.workspace_id=?"),
    ("chunks", "SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.workspace_id=?"),
    ("embeddings", "SELECT e.* FROM embeddings e JOIN chunks c ON c.id=e.chunk_id JOIN documents d ON d.id=c.document_id WHERE d.workspace_id=?"),
    ("conversations", "SELECT * FROM conversations WHERE workspace_id=?"),
    ("messages", "SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.workspace_id=?"),
    ("message_citations", "SELECT mc.* FROM message_citations mc JOIN messages m ON m.id=mc.message_id JOIN conversations c ON c.id=m.conversation_id WHERE c.workspace_id=?"),
    ("notes", "SELECT * FROM notes WHERE workspace_id=?"),
    ("study_items", "SELECT * FROM study_items WHERE workspace_id=?"),
    ("study_attempts", "SELECT a.* FROM study_attempts a JOIN study_items i ON i.id=a.item_id WHERE i.workspace_id=?"),
    ("concepts", "SELECT * FROM concepts WHERE workspace_id=?"),
]


def _crypto():
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        raise UsageError("Encryption needs the optional 'cryptography' package",
                         hint='pip install "bhap-ai[crypto]" (while online, before going offline)') from None
    return Fernet, InvalidToken


def _key(passphrase, salt):
    raw = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return base64.urlsafe_b64encode(raw)


def _encode(row):
    out = {}
    for key, value in dict(row).items():
        out[key] = {"__b64__": base64.b64encode(value).decode()} if isinstance(value, bytes) else value
    return out


def _decode(row):
    return {k: base64.b64decode(v["__b64__"]) if isinstance(v, dict) and "__b64__" in v else v for k, v in row.items()}


def create(conn, workspace_names, output=None, include_originals=True, passphrase=None):
    buffer = io.BytesIO()
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "app_version": __version__,
                "schema_version": SCHEMA_VERSION, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "includes_originals": include_originals, "workspaces": [], "embedding_models": {}, "files": {}}
    lines = []
    total_docs = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in workspace_names:
            ws = get_workspace(conn, name)
            counts = {}
            lines.append(json.dumps({"t": "workspaces", "row": _encode(ws)}))
            for table, query in TABLES:
                rows = conn.execute(query, (ws["id"],)).fetchall()
                counts[table] = len(rows)
                for row in rows:
                    lines.append(json.dumps({"t": table, "row": _encode(row)}, ensure_ascii=False))
            for row in conn.execute("SELECT model, dims, COUNT(*) n FROM embeddings e JOIN chunks c ON c.id=e.chunk_id "
                                    "JOIN documents d ON d.id=c.document_id WHERE d.workspace_id=? GROUP BY model, dims",
                                    (ws["id"],)):
                manifest["embedding_models"][row["model"]] = row["dims"]
            if include_originals:
                for doc in conn.execute("SELECT id, stored_path, original_path FROM documents WHERE workspace_id=?",
                                        (ws["id"],)):
                    source = Path(doc["stored_path"]) if doc["stored_path"] else Path(doc["original_path"])
                    if source.is_file():
                        member = f"originals/{doc['id']}{source.suffix.lower()}"
                        archive.write(source, member)
                        manifest["files"][member] = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest["workspaces"].append({"name": ws["name"], "id": ws["id"], "documents": counts["documents"],
                                           "chunks": counts["chunks"]})
            total_docs += counts["documents"]
        data = ("\n".join(lines) + "\n").encode("utf-8")
        manifest["files"]["data.jsonl"] = hashlib.sha256(data).hexdigest()
        archive.writestr("data.jsonl", data)
        manifest["encrypted"] = bool(passphrase)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    payload = buffer.getvalue()
    if passphrase:
        Fernet, _ = _crypto()
        salt = os.urandom(16)
        payload = MAGIC + salt + Fernet(_key(passphrase, salt)).encrypt(payload)
    if output is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        label = workspace_names[0] if len(workspace_names) == 1 else "all"
        output = config.path("backups") / f"bhap-{label}-{stamp}.bhap"
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".part")
    with open(tmp, "wb") as handle:
        handle.write(payload)
    if os.name != "nt":
        tmp.chmod(0o600)
    os.replace(tmp, output)
    return output, manifest, total_docs


def is_encrypted(path):
    with open(path, "rb") as handle:
        return handle.read(len(MAGIC)) == MAGIC


def _open(path, passphrase=None):
    raw = Path(path).expanduser().read_bytes()
    if raw.startswith(MAGIC):
        if not passphrase:
            raise UsageError("This backup is encrypted; a passphrase is required")
        Fernet, InvalidToken = _crypto()
        salt, token = raw[len(MAGIC):len(MAGIC) + 16], raw[len(MAGIC) + 16:]
        try:
            raw = Fernet(_key(passphrase, salt)).decrypt(token)
        except InvalidToken:
            raise UsageError("Wrong passphrase or corrupted backup") from None
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        manifest = json.loads(archive.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise BhapError(f"Not a valid Bhap backup: {exc}") from exc
    if manifest.get("format") != FORMAT:
        raise BhapError("Not a Bhap backup (unknown format)")
    if manifest.get("format_version", 0) > FORMAT_VERSION:
        raise BhapError(f"Backup format v{manifest['format_version']} is newer than this Bhap supports; upgrade Bhap")
    return archive, manifest


def verify(path, passphrase=None):
    archive, manifest = _open(path, passphrase)
    problems = []
    for member, digest in manifest.get("files", {}).items():
        try:
            if hashlib.sha256(archive.read(member)).hexdigest() != digest:
                problems.append(f"checksum mismatch: {member}")
        except KeyError:
            problems.append(f"missing: {member}")
    return manifest, problems


def restore(conn, path, passphrase=None, rename=None, settings=None):
    settings = settings or config.load()
    archive, manifest = _open(path, passphrase)
    _, problems = verify(path, passphrase)
    if problems:
        raise BhapError("Backup failed integrity check: " + "; ".join(problems[:5]))
    records = [json.loads(line) for line in archive.read("data.jsonl").decode("utf-8").splitlines() if line.strip()]
    restored, notes = [], []
    current_model = settings["embedding_model"]
    with transaction(conn):
        ws_map, doc_map, ver_map, chunk_map, conv_map, msg_map, item_map = {}, {}, {}, {}, {}, {}, {}
        old_ws = None
        for record in records:
            table, row = record["t"], _decode(record["row"])
            if table == "workspaces":
                name = rename if (rename and len(manifest["workspaces"]) == 1) else row["name"]
                if conn.execute("SELECT 1 FROM workspaces WHERE name=?", (name,)).fetchone():
                    raise UsageError(f"Workspace '{name}' already exists; restore with --as <new-name>")
                old_ws = row["id"]
                ws_map[old_ws] = create_workspace(conn, name, row.get("description", ""))
                restored.append({"name": name, "id": ws_map[old_ws], "documents": 0})
                continue
            if table == "documents":
                old = row.pop("id")
                row["workspace_id"] = ws_map[row["workspace_id"]]
                row["current_version_id"] = None
                stored_member = next((m for m in archive.namelist() if m.startswith(f"originals/{old}.")), None)
                row["stored_path"] = ""
                doc_map[old] = _insert(conn, table, row)
                if stored_member:
                    target = workspace_dir(row["workspace_id"], "originals") / f"{doc_map[old]}{Path(stored_member).suffix}"
                    with archive.open(stored_member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    conn.execute("UPDATE documents SET stored_path=? WHERE id=?", (str(target), doc_map[old]))
                restored[-1]["documents"] += 1
            elif table == "document_versions":
                old = row.pop("id")
                row["document_id"] = doc_map[row["document_id"]]
                ver_map[old] = _insert(conn, table, row)
            elif table == "chunks":
                old = row.pop("id")
                row["document_id"] = doc_map[row["document_id"]]
                row["version_id"] = ver_map.get(row["version_id"])
                chunk_map[old] = _insert(conn, table, row)
            elif table == "embeddings":
                row["chunk_id"] = chunk_map[row["chunk_id"]]
                _insert(conn, table, row)
            elif table == "conversations":
                old = row.pop("id")
                row["workspace_id"] = ws_map[row["workspace_id"]]
                conv_map[old] = _insert(conn, table, row)
            elif table == "messages":
                old = row.pop("id")
                row["conversation_id"] = conv_map[row["conversation_id"]]
                msg_map[old] = _insert(conn, table, row)
            elif table == "message_citations":
                if row["chunk_id"] in chunk_map:
                    row["message_id"], row["chunk_id"] = msg_map[row["message_id"]], chunk_map[row["chunk_id"]]
                    _insert(conn, table, row)
            elif table in ("notes", "study_items"):
                old = row.pop("id")
                row["workspace_id"] = ws_map[row["workspace_id"]]
                refs = json.loads(row.get("source_refs") or "[]")
                for ref in refs:
                    if ref.get("chunk_id") in chunk_map:
                        ref["chunk_id"] = chunk_map[ref["chunk_id"]]
                row["source_refs"] = json.dumps(refs, ensure_ascii=False)
                new_id = _insert(conn, table, row)
                if table == "study_items":
                    item_map[old] = new_id
            elif table == "study_attempts":
                row.pop("id")
                row["item_id"] = item_map[row["item_id"]]
                _insert(conn, table, row)
            elif table == "concepts":
                row.pop("id")
                row["workspace_id"] = ws_map[row["workspace_id"]]
                _insert(conn, table, row)
        for new in ver_map.values():
            conn.execute("""UPDATE documents SET current_version_id=? WHERE id=(SELECT document_id FROM document_versions
                            WHERE id=?) AND (current_version_id IS NULL OR current_version_id < ?)""", (new, new, new))
        models = set(manifest.get("embedding_models", {}))
        if current_model and models and current_model not in models:
            for ws in restored:
                conn.execute("UPDATE documents SET status='stale' WHERE workspace_id=?", (ws["id"],))
            notes.append(f"backup embeddings use {', '.join(sorted(models))} but the active embedding model is "
                         f"{current_model}; run `bhap docs reindex --stale -w <workspace>`"
                         + ("" if manifest.get("includes_originals") else " (originals not included: re-add files)"))
    return restored, manifest, notes


def _insert(conn, table, row):
    columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    row = {k: v for k, v in row.items() if k in columns}
    keys = list(row)
    cursor = conn.execute(f"INSERT INTO {table}({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
                          [row[k] for k in keys])
    return cursor.lastrowid


def list_backups():
    directory = config.home() / "backups"
    if not directory.exists():
        return []
    return sorted(directory.glob("*.bhap"), key=lambda p: p.stat().st_mtime, reverse=True)
