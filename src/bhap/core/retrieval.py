"""Hybrid retrieval: FTS5/BM25 lexical + vector cosine → reciprocal-rank fusion → diversity."""
import re
import time

from ..config import load
from ..storage.vectors import Matrix
from .embedder import Embedder

RRF_K = 60
CANDIDATES = 60
STOPWORDS = set("""
yang dan di ke dari untuk dengan pada adalah ini itu atau dalam tidak akan juga sebagai oleh bisa dapat ada
apa apakah bagaimana mengapa kenapa siapa kapan dimana mana berapa jelaskan sebutkan tolong saya aku kamu
anda kita kami mereka nya lah kah pun para secara antara bagi tentang menurut materi berdasarkan buat
the a an and or of to in on for with is are was were be been by as at from that this these those what which
who whom how why when where explain describe tell me please about according does do did can could should
would will shall into than then there their its it not no yes
""".split())

_matrix_cache = {}


class Scope:
    """Restrict retrieval to documents and/or folders inside a workspace."""

    def __init__(self, doc_ids=None, folders=None):
        self.doc_ids = sorted(set(doc_ids or []))
        self.folders = [f.strip("/") for f in (folders or []) if f.strip("/")]

    def __bool__(self):
        return bool(self.doc_ids or self.folders)

    def sql(self, alias="d"):
        clauses, params = [], []
        if self.doc_ids:
            clauses.append(f"{alias}.id IN ({','.join('?' * len(self.doc_ids))})")
            params += self.doc_ids
        for folder in self.folders:
            clauses.append(f"({alias}.folder=? OR {alias}.folder LIKE ?)")
            params += [folder, folder + "/%"]
        if not clauses:
            return "", []
        return " AND (" + " OR ".join(clauses) + ")", params

    def describe(self):
        parts = [f"#{i}" for i in self.doc_ids] + [f"{f}/" for f in self.folders]
        return ", ".join(parts)


def terms(text):
    words = re.findall(r"[\w][\w\-']*", text.lower(), re.UNICODE)
    return [w for w in words if len(w) >= 2 and w not in STOPWORDS]


def fts_query(text):
    selected = []
    for word in terms(text)[:24]:
        clean = word.replace('"', "")
        if not clean:
            continue
        selected.append(f'"{clean}"*' if len(clean) >= 4 else f'"{clean}"')
    return " OR ".join(selected)


def lexical(conn, wid, query, scope=None, limit=CANDIDATES):
    expression = fts_query(query)
    if not expression:
        return []
    where, params = (scope or Scope()).sql()
    try:
        rows = conn.execute(f"""SELECT c.id, bm25(chunk_fts, 1.0, 0.4) AS rank FROM chunk_fts
                                JOIN chunks c ON c.id = chunk_fts.rowid JOIN documents d ON d.id = c.document_id
                                WHERE chunk_fts MATCH ? AND d.workspace_id=? AND d.status='indexed' {where}
                                ORDER BY rank LIMIT ?""", (expression, wid, *params, limit)).fetchall()
    except Exception:  # noqa: BLE001 - malformed FTS expression should not break search
        return []
    return [(row["id"], -row["rank"]) for row in rows]


def anchors(conn, wid, query, scope=None, limit=CANDIDATES):
    """Chunks containing *rare* query terms (names, codes, jargon) — RRF alone under-weights them.

    A term is an anchor when it occurs in at most max(3, 1%) of the workspace's chunks.
    Returns {chunk_id: number_of_anchor_terms_matched}.
    """
    where, params = (scope or Scope()).sql()
    total = conn.execute(f"""SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id
                             WHERE d.workspace_id=? AND d.status='indexed' {where}""", (wid, *params)).fetchone()[0]
    if total < 20:
        return {}
    threshold = max(3, int(total * 0.01))
    found = {}
    for word in dict.fromkeys(terms(query)):
        if len(word) < 4:
            continue
        try:
            rows = conn.execute(f"""SELECT c.id FROM chunk_fts JOIN chunks c ON c.id=chunk_fts.rowid
                                    JOIN documents d ON d.id=c.document_id
                                    WHERE chunk_fts MATCH ? AND d.workspace_id=? AND d.status='indexed' {where}
                                    LIMIT ?""", (f'"{word}"*', wid, *params, threshold + 1)).fetchall()
        except Exception:  # noqa: BLE001
            continue
        if 0 < len(rows) <= threshold:
            for row in rows:
                found[row["id"]] = found.get(row["id"], 0) + 1
    return found


def _matrix(conn, wid, model, scope):
    """Load (and memoize per process) the normalized vectors for a workspace scope."""
    where, params = (scope or Scope()).sql()
    signature = conn.execute(f"""SELECT COUNT(*), COALESCE(MAX(c.id), 0), COALESCE(SUM(c.id), 0) FROM chunks c
                                 JOIN documents d ON d.id=c.document_id JOIN embeddings e ON e.chunk_id=c.id
                                 WHERE d.workspace_id=? AND d.status='indexed' AND e.model=? {where}""",
                             (wid, model, *params)).fetchone()
    key = (wid, model, tuple(params), tuple(signature))
    if key in _matrix_cache:
        return _matrix_cache[key]
    rows = conn.execute(f"""SELECT c.id, e.dims, e.vector FROM chunks c JOIN documents d ON d.id=c.document_id
                            JOIN embeddings e ON e.chunk_id=c.id
                            WHERE d.workspace_id=? AND d.status='indexed' AND e.model=? {where}""",
                        (wid, model, *params)).fetchall()
    dims = rows[0]["dims"] if rows else 0
    rows = [row for row in rows if row["dims"] == dims]
    matrix = Matrix([r["id"] for r in rows], [r["vector"] for r in rows], dims)
    _matrix_cache.clear()
    _matrix_cache[key] = matrix
    return matrix


def fetch(conn, ids):
    if not ids:
        return {}
    rows = conn.execute(f"""SELECT c.*, d.filename, d.title, d.original_path, d.stored_path, d.folder, d.kind doc_kind
                            FROM chunks c JOIN documents d ON d.id=c.document_id
                            WHERE c.id IN ({','.join('?' * len(ids))})""", list(ids)).fetchall()
    return {row["id"]: dict(row) for row in rows}


def search(conn, wid, query, *, scope=None, limit=None, settings=None, embedder=None, diversify=True):
    """Return ranked chunk dicts with `score` (fused), `similarity` and `lexical` fields."""
    settings = settings or load()
    limit = limit or settings["top_k"]
    started = time.monotonic()
    lex = lexical(conn, wid, query, scope)
    vec = []
    matrix = _matrix(conn, wid, settings["embedding_model"], scope)
    if matrix.ids:
        embedder = embedder or Embedder(settings)
        vec = matrix.top(embedder.query(query), CANDIDATES)
    fused = {}
    for rank, (chunk_id, _) in enumerate(vec):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (chunk_id, _) in enumerate(lex):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    for chunk_id, count in anchors(conn, wid, query, scope).items():
        fused[chunk_id] = fused.get(chunk_id, 0.0) + count / (RRF_K + 1)  # like an extra first place per rare term
    similarity = dict(vec)
    query_terms = set(terms(query))
    lexical_score = dict(lex)
    ordered = sorted(fused, key=lambda cid: fused[cid], reverse=True)
    rows = fetch(conn, ordered[: CANDIDATES])
    for chunk_id, row in rows.items():
        if looks_like_toc(row["text"]):
            fused[chunk_id] *= 0.35  # a table of contents mentions every topic but explains none
    ordered = sorted(fused, key=lambda cid: fused[cid], reverse=True)
    results, per_doc = [], {}
    cap = settings["max_chunks_per_doc"] if diversify else 10 ** 6
    for chunk_id in ordered:
        row = rows.get(chunk_id)
        if not row:
            continue
        if per_doc.get(row["document_id"], 0) >= cap:
            continue
        per_doc[row["document_id"]] = per_doc.get(row["document_id"], 0) + 1
        row["score"] = round(fused[chunk_id] * RRF_K, 4)
        row["similarity"] = round(similarity.get(chunk_id, 0.0), 4)
        row["lexical"] = _coverage(query_terms, row["text"]) if chunk_id in lexical_score else 0.0
        results.append(row)
        if len(results) >= limit:
            break
    search.last_ms = int((time.monotonic() - started) * 1000)
    return results


search.last_ms = 0


TOC_TITLES = re.compile(r"\b(daftar isi|table of contents|contents|daftar gambar|daftar tabel|list of figures)\b", re.I)


def looks_like_toc(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if TOC_TITLES.search(text[:200]):
        return True
    if len(lines) < 5:
        numbered = len(re.findall(r"\s\d{1,3}(?=\s|$)", text))
        return numbered >= 8 and numbered / max(len(text.split()), 1) > 0.15
    page_refs = sum(1 for line in lines if re.search(r"(\.{3,}|\s)\d{1,3}$", line))
    return page_refs / len(lines) >= 0.4


def _coverage(query_terms, text):
    """Share of query terms (prefix match for long terms) that occur in the text."""
    if not query_terms:
        return 0.0
    words = set(terms(text))
    hit = sum(1 for q in query_terms if q in words or (len(q) >= 4 and any(w.startswith(q) for w in words)))
    return round(hit / len(query_terms), 3)


def budget(hits, max_tokens):
    """Keep best hits until the evidence token budget is used."""
    kept, used = [], 0
    for hit in hits:
        cost = hit.get("token_count") or int(len(hit["text"].split()) * 1.3)
        if kept and used + cost > max_tokens:
            continue
        kept.append(hit)
        used += cost
    return kept


def missing_terms(conn, wid, query):
    """Query terms that appear nowhere in the workspace (knowledge-gap detector)."""
    gaps = []
    for word in dict.fromkeys(terms(query)):
        if len(word) < 4 or word.isdigit():
            continue
        found = conn.execute("""SELECT 1 FROM chunk_fts JOIN chunks c ON c.id=chunk_fts.rowid
                                JOIN documents d ON d.id=c.document_id
                                WHERE chunk_fts MATCH ? AND d.workspace_id=? LIMIT 1""",
                             (f'"{word}"*', wid)).fetchone()
        if not found:
            gaps.append(word)
    return gaps


def neighbors(conn, chunk_row, radius=1):
    rows = conn.execute("""SELECT * FROM chunks WHERE document_id=? AND ordinal BETWEEN ? AND ? ORDER BY ordinal""",
                        (chunk_row["document_id"], chunk_row["ordinal"] - radius,
                         chunk_row["ordinal"] + radius)).fetchall()
    return [dict(row) for row in rows]
