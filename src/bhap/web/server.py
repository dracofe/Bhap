"""Local HTTP API + static UI for the Bhap desktop app.

Security model: binds 127.0.0.1 only; a random per-launch token is exchanged once for an
HttpOnly SameSite=Strict cookie; every API call must carry it; the Host header must be the
loopback address (DNS-rebinding defence); no CORS. The UI is served from package files —
no CDN, no remote fonts — so the app works fully offline.
"""
import json
import re
import secrets
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__, config
from ..commands.common import readiness
from ..core import citations, generation, history, ingest, library, retrieval
from ..core.ingest import Ingestor
from ..errors import BhapError
from ..i18n import set_language
from ..storage import sqlite

STATIC = Path(__file__).resolve().parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
UPLOAD_DIR = "uploads"


class ClientGone(Exception):
    """The browser aborted the request (Stop button / window closed)."""


class AppState:
    def __init__(self, token):
        self.token = token
        self.last_seen = time.monotonic()
        self.lock = threading.Lock()
        self.workspace = None  # name; None → resolve like the CLI

    def touch(self):
        self.last_seen = time.monotonic()


def _workspace(conn, state):
    if state.workspace:
        try:
            row = library.get_workspace(conn, state.workspace)
            return row["name"], row["id"]
        except BhapError:
            state.workspace = None
    name, wid, _ = library.resolve(conn)
    return name, wid


def _safe_name(name):
    name = Path(urllib.parse.unquote(name or "")).name
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name, flags=re.UNICODE).strip(" .") or "upload"
    return name[:180]


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Bhap"

        def log_message(self, *args):
            pass

        # ------------------------------------------------------------ plumbing
        def _host_ok(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _authed(self):
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            value = cookie["bhap_token"].value if "bhap_token" in cookie else self.headers.get("X-Bhap-Token")
            return bool(value) and secrets.compare_digest(value, state.token)

        def _send(self, status, body=b"", ctype="application/json; charset=utf-8", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, data, status=200):
            self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

        def _error(self, message, status=400, hint=None):
            self._json({"error": message, "hint": hint}, status)

        def _body(self, limit=1024 * 1024):
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise BhapError("Request too large")
            return self.rfile.read(length) if length else b""

        def _start_stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def _event(self, data):
            payload = (json.dumps(data, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            try:
                self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                raise ClientGone from exc

        def _end_stream(self):
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass

        # ------------------------------------------------------------ routing
        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _dispatch(self, method):
            if not self._host_ok():
                return self._send(403, b"forbidden host", "text/plain")
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            path = url.path
            if method == "GET" and path == "/" and "t" in query:
                if not secrets.compare_digest(query["t"][0], state.token):
                    return self._send(403, b"bad token", "text/plain")
                return self._send(303, extra={"Location": "/", "Set-Cookie":
                                  f"bhap_token={state.token}; HttpOnly; SameSite=Strict; Path=/"})
            if not self._authed():
                return self._send(403, b"Buka Bhap lewat perintah `bhap app`.", "text/plain; charset=utf-8")
            state.touch()
            try:
                if method == "GET" and not path.startswith("/api/"):
                    return self._static(path)
                route = getattr(self, f"api_{method.lower()}_" + path[5:].split("/")[0].replace("-", "_"), None)
                if route is None:
                    return self._error("not found", 404)
                parts = [urllib.parse.unquote(p) for p in path[5:].split("/")[1:] if p]
                return route(parts, query)
            except ClientGone:
                return None
            except BhapError as exc:
                return self._error(str(exc), 400, exc.hint)
            except Exception as exc:  # noqa: BLE001 - report, never kill the server
                from ..logs import log
                log("app.error", level="error", error=f"{type(exc).__name__}: {exc}", path=path)
                return self._error(f"{type(exc).__name__}: {exc}", 500)

        def _static(self, path):
            name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            target = (STATIC / name).resolve()
            if STATIC not in target.parents or not target.is_file():
                return self._error("not found", 404)
            self._send(200, target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream"))

        # ------------------------------------------------------------ API: state
        def api_get_state(self, parts, query):
            settings = config.load()
            ready = readiness(settings)
            with sqlite.db() as conn:
                name, wid = _workspace(conn, state)
                stats = library.stats(conn, wid)
                workspaces = [{"name": r["name"], "documents": r["docs"]} for r in library.workspaces(conn)]
                stale = library.stale_count(conn, wid, settings["embedding_model"])
            self._json({"version": __version__, "workspace": name, "workspaces": workspaces, "stats": stats,
                        "stale": stale, "mode": settings["mode"], "language": settings["language"],
                        "llm_model": settings["llm_model"], "embedding_model": settings["embedding_model"],
                        "ready": ready["ready"], "runtime_ok": ready["runtime_ok"], "llm_ready": ready["llm_ready"],
                        "embedding_ready": ready["embedding_ready"], "error": ready["error"],
                        "max_file_mb": settings["max_file_mb"]})

        def api_post_heartbeat(self, parts, query):
            self._json({"ok": True})

        def api_post_settings(self, parts, query):
            data = json.loads(self._body() or b"{}")
            allowed = {k: v for k, v in data.items() if k in ("mode", "language")}
            settings = config.update(**allowed)
            set_language(settings["language"])
            self._json({"mode": settings["mode"], "language": settings["language"]})

        def api_post_workspace(self, parts, query):
            data = json.loads(self._body() or b"{}")
            name = (data.get("name") or "").strip()
            with sqlite.db() as conn:
                if data.get("create"):
                    library.create_workspace(conn, name)
                library.get_workspace(conn, name)
            state.workspace = name
            config.update(workspace=name)
            self._json({"workspace": name})

        # ------------------------------------------------------------ API: conversations
        def api_get_conversations(self, parts, query):
            with sqlite.db() as conn:
                _, wid = _workspace(conn, state)
                if parts:
                    conv, messages = history.conversation(conn, wid, int(parts[0]))
                    return self._json({"conversation": conv, "messages": messages})
                rows = history.conversations(conn, wid, 200)
                self._json([{"id": r["id"], "title": r["title"] or "Chat", "updated_at": r["updated_at"],
                             "messages": r["messages"], "mode": r["mode"]} for r in rows])

        def api_delete_conversations(self, parts, query):
            with sqlite.db() as conn:
                _, wid = _workspace(conn, state)
                history.delete(conn, wid, int(parts[0]))
            self._json({"ok": True})

        def api_post_ask(self, parts, query):
            data = json.loads(self._body() or b"{}")
            question = (data.get("question") or "").strip()
            if not question:
                return self._error("Pertanyaan kosong")
            settings = config.load()
            mode = data.get("mode") if data.get("mode") in ("strict", "general", "brainstorm") else settings["mode"]
            with sqlite.db() as conn:
                _, wid = _workspace(conn, state)
                cid = data.get("conversation_id")
                if cid:
                    history.conversation(conn, wid, int(cid))
                turns = history.turns(conn, cid, 2 * settings["history_turns"]) if cid else []
                scope = retrieval.Scope([int(i) for i in data.get("doc_ids") or []])
                if mode == "strict" and not library.stats(conn, wid)["indexed"]:
                    return self._error("Belum ada dokumen. Unggah materi dulu dengan tombol + atau /upload.")
                self._start_stream()
                self._event({"type": "status", "text": "searching"})

                def on_token(delta):
                    self._event({"type": "token", "text": delta})

                try:
                    result = generation.ask(conn, wid, question, mode=mode, scope=scope or None, history=turns,
                                            on_token=on_token, settings=settings)
                except ClientGone:
                    raise
                except Exception as exc:  # noqa: BLE001 - headers are already sent: report inside the stream
                    self._event({"type": "error", "error": str(exc), "hint": getattr(exc, "hint", None)})
                    return self._end_stream()
                if settings["save_history"] and result["status"] != "cancelled":
                    cid = cid or history.start(conn, wid, mode)
                    history.record(conn, cid, result)
                    result["conversation_id"] = cid
                self._event({"type": "done", "result": result})
                self._end_stream()

        # ------------------------------------------------------------ API: documents
        def api_get_documents(self, parts, query):
            with sqlite.db() as conn:
                _, wid = _workspace(conn, state)
                rows = library.documents(conn, wid)
                self._json([{"id": r["id"], "file": r["filename"], "status": r["status"], "unit": r["unit"],
                             "units": r["units"], "chunks": r["chunk_count"], "size": r["size"], "kind": r["kind"],
                             "warnings": library.warnings(r), "updated_at": r["updated_at"]} for r in rows])

        def api_delete_documents(self, parts, query):
            with sqlite.db() as conn:
                _, wid = _workspace(conn, state)
                row = ingest.remove(conn, wid, int(parts[0]))
            self._json({"ok": True, "file": row["filename"]})

        def api_post_upload(self, parts, query):
            settings = config.load()
            name = _safe_name((query.get("name") or [""])[0])
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return self._error("File kosong")
            if length > settings["max_file_mb"] * 1024 * 1024:
                self.rfile.read(length)
                return self._error(f"File melebihi batas {settings['max_file_mb']} MB")
            with sqlite.db() as conn:
                _, wid = _workspace(conn, state)
                folder = library.workspace_dir(wid, UPLOAD_DIR)
                target = folder / name
                stem, suffix, n = target.stem, target.suffix, 1
                while target.exists():
                    n += 1
                    target = folder / f"{stem} ({n}){suffix}"
                remaining = length
                with open(target, "wb") as handle:
                    while remaining:
                        block = self.rfile.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        handle.write(block)
                        remaining -= len(block)
                self._start_stream()

                def progress(stage, info):
                    self._event({"type": "stage", "stage": stage, **{k: v for k, v in info.items() if k != "file"}})

                # The upload already lives in Bhap's data dir: index it in place (no second copy).
                local = dict(settings, store_originals=False)
                try:
                    result = Ingestor(conn, wid, local, progress=progress).import_path(target)
                except Exception as exc:  # noqa: BLE001 - report to the UI, drop the upload
                    target.unlink(missing_ok=True)
                    self._event({"type": "error", "error": str(exc), "hint": getattr(exc, "hint", None)})
                    return self._end_stream()
                if result["status"] in ("duplicate", "unchanged"):
                    target.unlink(missing_ok=True)
                self._event({"type": "done", "result": result})
                self._end_stream()

        def api_get_chunk(self, parts, query):
            with sqlite.db() as conn:
                _, wid = _workspace(conn, state)
                row = conn.execute("""SELECT c.*, d.filename, d.workspace_id FROM chunks c
                                      JOIN documents d ON d.id=c.document_id WHERE c.id=?""",
                                   (int(parts[0]),)).fetchone()
                if not row or row["workspace_id"] != wid:
                    return self._error("Sumber tidak ditemukan", 404)
                around = retrieval.neighbors(conn, row, 1)
                self._json({"id": row["id"], "document_id": row["document_id"], "file": row["filename"],
                            "locator": citations.locator(row), "visual": bool(row["visual"]), "kind": row["kind"],
                            "ocr_conf": row["ocr_conf"], "page": row["page_no"] or row["slide_no"],
                            "context": [{"id": c["id"], "text": c["text"], "current": c["id"] == row["id"]}
                                        for c in around]})

        def api_post_open(self, parts, query):
            from ..commands.library import open_original
            with sqlite.db() as conn:
                _, wid = _workspace(conn, state)
                doc = library.document(conn, wid, int(parts[0]))
            page = (query.get("page") or [None])[0]
            open_original(doc, page=int(page) if page and page.isdigit() else None)
            self._json({"ok": True})

    return Handler


class AppServer:
    def __init__(self, port=0):
        self.state = AppState(secrets.token_urlsafe(24))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.state))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self):
        return self.httpd.server_address[1]

    @property
    def launch_url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.state.token}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
