"""A deterministic stand-in for the Ollama HTTP API, bound to 127.0.0.1.

Embeddings are hashed bag-of-words vectors, so semantic search behaves sensibly.
Chat answers by quoting the best-overlapping source with a valid [n] marker, and
abstains when nothing overlaps. Magic words in the question trigger edge cases:
  zzbadcite  -> cites a non-existent source [99]
  zznocite   -> answers without any marker
  zzslow     -> streams slowly (for cancellation tests)
"""
import hashlib
import json
import math
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMS = 64
ABSTAIN_ID = "Tidak ditemukan dalam materi yang dipilih."
ABSTAIN_EN = "Not found in the selected material."
STOP = set("yang dan di ke dari untuk dengan pada adalah ini itu apa the a an of to in is are what how jelaskan "
           "explain apakah bagaimana menurut materi question".split())


def words(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP]


def embed(text):
    vector = [0.0] * DIMS
    for word in words(text):
        stem = word[:6]
        digest = hashlib.md5(stem.encode()).digest()
        vector[digest[0] % DIMS] += 1.0
        vector[digest[1] % DIMS] += 0.5
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def parse_sources(content):
    sources = {}
    for match in re.finditer(r"\[(\d+)\] file: (.*?) \| location: (.*?)\n<<<\n(.*?)\n>>>", content, re.S):
        sources[int(match.group(1))] = match.group(4)
    return sources


class State:
    def __init__(self):
        self.models = {"fake-chat:latest": 1_000_000, "fake-embed:latest": 100_000}
        self.calls = {"embed": 0, "embed_texts": 0, "chat": 0}
        self.last_messages = None
        self.lock = threading.Lock()


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream(self, events, delay=0.0):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for event in events:
                    data = (json.dumps(event) + "\n").encode()
                    self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()
                    if delay:
                        time.sleep(delay)
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path == "/api/version":
                return self._send({"version": "0.0-fake"})
            if self.path == "/api/tags":
                return self._send({"models": [{"name": n, "size": s, "details": {"family": "bert" if "embed" in n else "llama",
                                                                                 "parameter_size": "1B"}}
                                              for n, s in state.models.items()]})
            self._send({"error": "not found"}, 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            model = payload.get("model", "")
            known = model in state.models or f"{model}:latest" in state.models
            if self.path == "/api/show":
                return self._send({"model_info": {"llama.context_length": 8192}} if known else {"error": "missing"},
                                  200 if known else 404)
            if self.path == "/api/embed":
                if not known:
                    return self._send({"error": f"model '{model}' not found"}, 404)
                texts = payload["input"] if isinstance(payload["input"], list) else [payload["input"]]
                with state.lock:
                    state.calls["embed"] += 1
                    state.calls["embed_texts"] += len(texts)
                return self._send({"embeddings": [embed(t) for t in texts]})
            if self.path == "/api/pull":
                state.models[model if ":" in model else model + ":latest"] = 123
                return self._stream([{"status": "pulling manifest"},
                                     {"status": "downloading", "total": 100, "completed": 50},
                                     {"status": "success"}])
            if self.path == "/api/chat":
                if not known:
                    return self._send({"error": f"model '{model}' not found"}, 404)
                with state.lock:
                    state.calls["chat"] += 1
                    state.last_messages = payload["messages"]
                text = respond(payload)
                delay = 0.4 if "zzslow" in json.dumps(payload["messages"][-1]) else 0.0
                pieces = re.findall(r"\S+\s*", text) or [text]
                events = [{"message": {"role": "assistant", "content": p}, "done": False} for p in pieces]
                events.append({"message": {"role": "assistant", "content": ""}, "done": True})
                return self._stream(events, delay)
            self._send({"error": "not found"}, 404)

    return Handler


def respond(payload):
    messages = payload["messages"]
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    user = messages[-1]["content"]
    sources = parse_sources(user)
    english = "Answer in English" in system or "Write in English" in system
    abstain = ABSTAIN_EN if english else ABSTAIN_ID
    if "single word: OK" in user:
        return "OK"
    if payload.get("format") == "json":
        first = min(sources) if sources else 1
        if '"questions"' in system:
            return json.dumps({"questions": [
                {"type": "mcq", "question": "Syarat bobot Dijkstra?", "choices": ["Non-negatif", "Negatif", "Nol", "Acak"],
                 "answer": 0, "explanation": f"Bobot harus non-negatif [{first}]", "sources": [first]},
                {"type": "mcq", "question": "Tanpa sumber", "choices": ["a", "b"], "answer": 0, "sources": [999]},
                {"type": "essay", "question": "Jelaskan langkah Dijkstra", "answer": f"Pilih jarak terkecil [{first}]",
                 "explanation": "", "sources": [first]}]})
        if '"cards"' in system:
            return json.dumps({"cards": [{"front": "Dijkstra", "back": f"Algoritma jalur terpendek [{first}]",
                                          "sources": [first]},
                                         {"front": "Ungrounded", "back": "x", "sources": []}]})
        if '"score"' in system:
            return json.dumps({"score": 0.8, "feedback": f"Bagus, sesuai materi [{first}]", "sources": [first]})
        return "{}"
    question = user.split("QUESTION:", 1)[-1] if "QUESTION:" in user else ""
    if "zzbadcite" in question:
        return "Jawaban dengan sitasi palsu [99]."
    if "zznocite" in question:
        return "Jawaban tanpa sitasi sama sekali."
    if not question:  # summarize / compare / teach / notes tasks
        if not sources and "<NOTES>" in user:
            markers = re.findall(r"\[\d+\]", user)
            return "Ringkasan akhir " + " ".join(markers[:3])
        refs = sorted(sources)[:2]
        return "Ringkasan: " + " ".join(f"{sources[n].split('.')[0].strip()} [{n}]." for n in refs)
    qwords = set(words(question))
    for message in messages[1:-1]:  # follow-ups: the real model sees the conversation too
        if message["role"] == "user":
            qwords |= set(words(message["content"]))
    best, best_score = None, 0
    for number, text in sources.items():
        score = len(qwords & set(words(text)))
        if score > best_score:
            best, best_score = number, score
    if best is None:
        return abstain
    sentence = re.split(r"(?<=[.!?])\s+", sources[best].strip())[0]
    return f"{sentence} [{best}]"


class FakeOllama:
    def __init__(self):
        self.state = State()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":  # manual use: python tests/fake_ollama.py [serve]  (honours OLLAMA_HOST=host:port)
    import os
    host = os.environ.get("OLLAMA_HOST")
    if host:
        address, port = host.rsplit(":", 1)
        server = ThreadingHTTPServer((address, int(port)), make_handler(State()))
        server.serve_forever()
    with FakeOllama() as fake:
        print(fake.url, flush=True)
        threading.Event().wait()
