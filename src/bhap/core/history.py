"""Local conversation history and source-linked notes."""
import json

from ..errors import NotFoundError
from ..storage import transaction
from .retrieval import fts_query


# ------------------------------------------------------------ conversations
def start(conn, wid, mode, title=""):
    return conn.execute("INSERT INTO conversations(workspace_id, title, mode) VALUES(?,?,?)",
                        (wid, title[:120], mode)).lastrowid


def latest(conn, wid):
    row = conn.execute("SELECT id FROM conversations WHERE workspace_id=? ORDER BY updated_at DESC, id DESC LIMIT 1",
                       (wid,)).fetchone()
    return row["id"] if row else None


def record(conn, conversation_id, result):
    """Persist one question/answer pair with its verified citations."""
    with transaction(conn):
        conn.execute("INSERT INTO messages(conversation_id, role, text, mode) VALUES(?, 'user', ?, ?)",
                     (conversation_id, result["question"], result["mode"]))
        message_id = conn.execute("INSERT INTO messages(conversation_id, role, text, mode, status) "
                                  "VALUES(?, 'assistant', ?, ?, ?)",
                                  (conversation_id, result["answer"], result["mode"], result["status"])).lastrowid
        for cite in result.get("citations", []):
            conn.execute("INSERT OR IGNORE INTO message_citations(message_id, chunk_id, marker, locator, evidence_excerpt) "
                         "SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM chunks WHERE id=?)",
                         (message_id, cite["chunk_id"], cite["marker"], f"{cite['file']} — {cite['locator']}",
                          cite["excerpt"], cite["chunk_id"]))
        conn.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP, title=CASE WHEN title='' THEN ? "
                     "ELSE title END WHERE id=?", (result["question"][:80], conversation_id))
    return message_id


def turns(conn, conversation_id, limit=6):
    rows = conn.execute("SELECT role, text, status FROM messages WHERE conversation_id=? AND status != 'cancelled' "
                        "ORDER BY id DESC LIMIT ?", (conversation_id, limit)).fetchall()
    return [dict(row) for row in reversed(rows)]


def conversations(conn, wid, limit=50):
    return conn.execute("""SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) messages
                           FROM conversations c WHERE workspace_id=? ORDER BY updated_at DESC, id DESC LIMIT ?""",
                        (wid, limit)).fetchall()


def conversation(conn, wid, cid):
    row = conn.execute("SELECT * FROM conversations WHERE id=? AND workspace_id=?", (cid, wid)).fetchone()
    if not row:
        raise NotFoundError(f"Conversation #{cid} not found in this workspace", hint="bhap history list")
    messages = []
    for message in conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (cid,)):
        item = dict(message)
        item["citations"] = [dict(c) for c in conn.execute(
            "SELECT marker, chunk_id, locator, evidence_excerpt FROM message_citations WHERE message_id=? ORDER BY marker",
            (message["id"],))]
        messages.append(item)
    return dict(row), messages


def search(conn, wid, query, limit=30):
    expression = fts_query(query)
    if not expression:
        return []
    return conn.execute("""SELECT m.id, m.conversation_id, m.role, m.text, m.created_at FROM message_fts
                           JOIN messages m ON m.id=message_fts.rowid JOIN conversations c ON c.id=m.conversation_id
                           WHERE message_fts MATCH ? AND c.workspace_id=? ORDER BY bm25(message_fts) LIMIT ?""",
                        (expression, wid, limit)).fetchall()


def delete(conn, wid, cid=None):
    if cid is None:
        return conn.execute("DELETE FROM conversations WHERE workspace_id=?", (wid,)).rowcount
    conversation(conn, wid, cid)
    return conn.execute("DELETE FROM conversations WHERE id=?", (cid,)).rowcount


def to_markdown(conv, messages):
    lines = [f"# {conv['title'] or 'Bhap conversation'}", "",
             f"_#{conv['id']} · mode {conv['mode']} · {conv['created_at']}_", ""]
    for message in messages:
        who = "**Anda / You**" if message["role"] == "user" else "**Bhap**"
        lines += [f"{who}:", "", message["text"], ""]
        for cite in message.get("citations", []):
            lines.append(f"> [{cite['marker']}] {cite['locator']} — “{cite['evidence_excerpt']}”")
        if message.get("citations"):
            lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------ notes
def add_note(conn, wid, title, body, refs=()):
    return conn.execute("INSERT INTO notes(workspace_id, title, body, source_refs) VALUES(?,?,?,?)",
                        (wid, title[:200], body, json.dumps(list(refs), ensure_ascii=False))).lastrowid


def notes(conn, wid):
    return conn.execute("SELECT * FROM notes WHERE workspace_id=? ORDER BY id DESC", (wid,)).fetchall()


def note(conn, wid, note_id):
    row = conn.execute("SELECT * FROM notes WHERE id=? AND workspace_id=?", (note_id, wid)).fetchone()
    if not row:
        raise NotFoundError(f"Note #{note_id} not found", hint="bhap notes list")
    return row


def note_markdown(row, conn=None):
    refs = json.loads(row["source_refs"] or "[]")
    lines = [f"# {row['title']}", "", row["body"].strip(), ""]
    if refs:
        lines += ["## Sumber / Sources", ""]
        for ref in refs:
            alive = ""
            if conn is not None and ref.get("chunk_id"):
                exists = conn.execute("SELECT 1 FROM chunks WHERE id=?", (ref["chunk_id"],)).fetchone()
                alive = "" if exists else " _(source removed)_"
            lines.append(f"- [{ref.get('marker', '')}] {ref.get('file')} — {ref.get('locator')}"
                         f"{': “' + ref['excerpt'] + '”' if ref.get('excerpt') else ''}{alive}")
    return "\n".join(lines) + "\n"
