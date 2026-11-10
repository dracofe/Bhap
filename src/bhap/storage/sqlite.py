"""SQLite metadata/chat/study store with versioned migrations and FTS5 search."""
import sqlite3
from contextlib import contextmanager

from ..config import home
from ..errors import StorageError

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY,
  workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title TEXT NOT NULL, filename TEXT NOT NULL, original_path TEXT NOT NULL,
  stored_path TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', units INTEGER NOT NULL DEFAULT 0,
  warnings TEXT NOT NULL DEFAULT '[]', current_version_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(workspace_id, original_path));
CREATE INDEX IF NOT EXISTS documents_sha ON documents(workspace_id, sha256);
CREATE TABLE IF NOT EXISTS document_versions(
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  sha256 TEXT NOT NULL, parser_version TEXT NOT NULL, embedding_model TEXT NOT NULL,
  chunk_count INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0,
  indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_id INTEGER REFERENCES document_versions(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL, page_no INTEGER, slide_no INTEGER, line_no INTEGER,
  section_path TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'text', approx INTEGER NOT NULL DEFAULT 0,
  visual INTEGER NOT NULL DEFAULT 0, ocr_conf REAL, text TEXT NOT NULL, text_hash TEXT NOT NULL,
  token_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id, ordinal);
CREATE TABLE IF NOT EXISTS embeddings(
  chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  model TEXT NOT NULL, dims INTEGER NOT NULL, vector BLOB NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS embed_cache(
  text_hash TEXT NOT NULL, model TEXT NOT NULL, dims INTEGER NOT NULL, vector BLOB NOT NULL,
  PRIMARY KEY(text_hash, model));
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  text, section, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunk_fts(rowid, text, section) VALUES(new.id, new.text, new.section_path); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, text, section) VALUES('delete', old.id, old.text, old.section_path); END;
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'strict',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL, text TEXT NOT NULL, mode TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
  text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO message_fts(rowid, text) VALUES(new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO message_fts(message_fts, rowid, text) VALUES('delete', old.id, old.text); END;
CREATE TABLE IF NOT EXISTS message_citations(
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  marker INTEGER NOT NULL, locator TEXT NOT NULL, evidence_excerpt TEXT NOT NULL,
  PRIMARY KEY(message_id, marker));
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title TEXT NOT NULL, body TEXT NOT NULL, source_refs TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS study_items(
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  topic TEXT NOT NULL, type TEXT NOT NULL, prompt TEXT NOT NULL, choices TEXT NOT NULL DEFAULT '[]',
  solution TEXT NOT NULL DEFAULT '', explanation TEXT NOT NULL DEFAULT '', difficulty TEXT NOT NULL DEFAULT '',
  source_refs TEXT NOT NULL DEFAULT '[]', generated INTEGER NOT NULL DEFAULT 1,
  ease REAL NOT NULL DEFAULT 2.5, interval_days REAL NOT NULL DEFAULT 0, reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0, due_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS study_attempts(
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES study_items(id) ON DELETE CASCADE,
  response TEXT NOT NULL, score REAL NOT NULL, feedback TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS concepts(
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  topic TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', difficulty TEXT NOT NULL DEFAULT '',
  source_refs TEXT NOT NULL DEFAULT '[]', last_studied TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(workspace_id, topic));
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, workspace_id INTEGER REFERENCES workspaces(id) ON DELETE CASCADE,
  type TEXT NOT NULL, target TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
  document_id INTEGER, pid INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
"""


def db_path():
    return home() / "db" / "bhap.sqlite"


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def _run_script(conn, script):
    """Execute statements one by one so they stay inside the caller's transaction."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            conn.execute(buffer)
            buffer = ""


def _migrate_legacy(conn):
    """0.1 schema (documents.name, chunks.vector JSON, history) -> v2.

    Documents are kept with status 'stale' so `bhap docs reindex --stale` rebuilds
    their chunks with the new structure-aware parser; flat history becomes one
    conversation per workspace.
    """
    for table in ("documents", "chunks", "history"):
        if _table_exists(conn, table):
            conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
    for name in ("chunk_fts", "chunks_ai", "chunks_ad"):
        kind = "TABLE" if name == "chunk_fts" else "TRIGGER"
        conn.execute(f"DROP {kind} IF EXISTS {name}")
    if _table_exists(conn, "workspaces"):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(workspaces)")}
        if "description" not in columns:
            conn.execute("ALTER TABLE workspaces ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if "created_at" not in columns:
            conn.execute("ALTER TABLE workspaces ADD COLUMN created_at TEXT NOT NULL DEFAULT '1970-01-01 00:00:00'")
    _run_script(conn, SCHEMA)
    if _table_exists(conn, "legacy_documents"):
        conn.execute("""INSERT INTO documents(id, workspace_id, title, filename, original_path, sha256, status,
                        created_at) SELECT id, workspace_id, name, name, original_path, '', 'stale', created_at
                        FROM legacy_documents""")
    if _table_exists(conn, "legacy_history"):
        for (wid,) in conn.execute("SELECT DISTINCT workspace_id FROM legacy_history").fetchall():
            cid = conn.execute("INSERT INTO conversations(workspace_id, title) VALUES(?, ?)",
                               (wid, "Imported 0.1 history")).lastrowid
            conn.execute("""INSERT INTO messages(conversation_id, role, text, created_at)
                            SELECT ?, role, text, created_at FROM legacy_history WHERE workspace_id=? ORDER BY id""",
                         (cid, wid))
    for table in ("legacy_chunks", "legacy_documents", "legacy_history"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def migrate(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise StorageError(f"Database schema v{version} is newer than this Bhap (v{SCHEMA_VERSION}); upgrade Bhap")
    if version == SCHEMA_VERSION:
        return
    legacy = version == 0 and _table_exists(conn, "history") and not _table_exists(conn, "conversations")
    if legacy:
        backup = db_path().with_name("bhap-0.1-backup.sqlite")
        if not backup.exists():
            conn.execute("VACUUM INTO ?", (str(backup),))
    conn.execute("PRAGMA foreign_keys=OFF")  # must be set outside a transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        if legacy:
            _migrate_legacy(conn)
        else:
            _run_script(conn, SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def connect():
    target = db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    from ..config import migrate_layout
    if not target.exists():
        migrate_layout()
    try:
        conn = sqlite3.connect(target, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        migrate(conn)
    except sqlite3.DatabaseError as exc:
        raise StorageError(f"Database {target} is unreadable or corrupt: {exc}",
                           hint="Restore a backup (`bhap backup restore`) or move the file aside") from exc
    if not _fts_available(conn):
        raise StorageError("SQLite was built without FTS5; install a Python with FTS5 support")
    return conn


def _fts_available(conn):
    return _table_exists(conn, "chunk_fts")


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    """Atomic unit of work (connections run in autocommit mode otherwise)."""
    if conn.in_transaction:
        conn.execute("SAVEPOINT bhap_tx")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK TO bhap_tx")
            conn.execute("RELEASE bhap_tx")
            raise
        conn.execute("RELEASE bhap_tx")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def integrity(conn):
    return conn.execute("PRAGMA quick_check").fetchone()[0]
