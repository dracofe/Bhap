"""Desktop app API: token handshake, security checks, ask streaming, upload, sources, history."""
import http.client
import json

from helpers import DIJKSTRA, BhapTestCase

from bhap.web.server import AppServer


class AppApiTests(BhapTestCase):
    def setUp(self):
        super().setUp()
        self.server = AppServer().start()
        self.cookie = None

    def tearDown(self):
        self.server.stop()
        super().tearDown()

    def request(self, method, path, body=None, headers=None, host=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=30)
        hdrs = {"Host": host or f"127.0.0.1:{self.server.port}", **(headers or {})}
        if auth and self.cookie:
            hdrs["Cookie"] = self.cookie
        conn.request(method, path, body=body, headers=hdrs)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def login(self):
        res, _ = self.request("GET", f"/?t={self.server.state.token}", auth=False)
        self.assertEqual(res.status, 303)
        self.cookie = res.getheader("Set-Cookie").split(";")[0]
        self.assertIn("HttpOnly", res.getheader("Set-Cookie"))

    def events(self, data):
        return [json.loads(line) for line in data.decode().splitlines() if line.strip()]

    def test_security_checks(self):
        res, _ = self.request("GET", "/api/state", auth=False)
        self.assertEqual(res.status, 403)
        res, _ = self.request("GET", "/?t=wrong", auth=False)
        self.assertEqual(res.status, 403)
        self.login()
        res, _ = self.request("GET", "/api/state", host="evil.example:80")  # DNS rebinding
        self.assertEqual(res.status, 403)
        res, _ = self.request("GET", "/../../etc/passwd")
        self.assertEqual(res.status, 404)
        res, body = self.request("GET", "/")
        self.assertEqual(res.status, 200)
        self.assertIn(b"app.js", body)
        self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))
        self.assertEqual(self.server.httpd.server_address[0], "127.0.0.1")

    def test_upload_ask_sources_history_delete(self):
        self.login()
        res, data = self.request("POST", "/api/upload?name=" + "materi%20graf.md", body=("# Graf\n" + DIJKSTRA).encode(),
                                 headers={"Content-Type": "application/octet-stream"})
        events = self.events(data)
        self.assertEqual(events[-1]["type"], "done", events)
        self.assertEqual(events[-1]["result"]["status"], "indexed")
        self.assertTrue(any(e["type"] == "stage" and e["stage"] == "embedding" for e in events))
        doc_id = events[-1]["result"]["id"]
        _, again = self.request("POST", "/api/upload?name=copy.md", body=("# Graf\n" + DIJKSTRA).encode())
        self.assertEqual(self.events(again)[-1]["result"]["status"], "duplicate")

        state = json.loads(self.request("GET", "/api/state")[1])
        self.assertEqual(state["stats"]["indexed"], 1)
        self.assertTrue(state["ready"])

        res, data = self.request("POST", "/api/ask", body=json.dumps({"question": "Apa syarat bobot Dijkstra?"}))
        events = self.events(data)
        self.assertTrue(any(e["type"] == "token" for e in events))
        result = events[-1]["result"]
        self.assertEqual(result["status"], "answered")
        cite = result["citations"][0]
        chunk = json.loads(self.request("GET", f"/api/chunk/{cite['chunk_id']}")[1])
        self.assertTrue(any(part["current"] and "non-negatif" in part["text"] for part in chunk["context"]))

        cid = result["conversation_id"]
        convs = json.loads(self.request("GET", "/api/conversations")[1])
        self.assertEqual(convs[0]["id"], cid)
        detail = json.loads(self.request("GET", f"/api/conversations/{cid}")[1])
        self.assertEqual(len(detail["messages"]), 2)
        _, data = self.request("POST", "/api/ask", body=json.dumps({"question": "contohnya?", "conversation_id": cid}))
        self.assertEqual(self.events(data)[-1]["result"]["conversation_id"], cid)

        docs = json.loads(self.request("GET", "/api/documents")[1])
        self.assertEqual(docs[0]["file"], "materi graf.md")
        res, _ = self.request("DELETE", f"/api/documents/{doc_id}")
        self.assertEqual(res.status, 200)
        uploads = list((self.root / "home" / "workspaces").rglob("uploads/*"))
        self.assertEqual(uploads, [])  # the uploaded file itself is removed with the document
        res, _ = self.request("DELETE", f"/api/conversations/{cid}")
        self.assertEqual(json.loads(self.request("GET", "/api/conversations")[1]), [])

    def test_errors_and_settings(self):
        self.login()
        res, data = self.request("POST", "/api/ask", body=json.dumps({"question": "x"}))
        self.assertEqual(res.status, 400)  # strict mode without documents
        res, data = self.request("POST", "/api/upload?name=evil.exe", body=b"MZ\x90\x00\x03\x00" * 40)
        self.assertEqual(self.events(data)[-1]["type"], "error")
        res, data = self.request("POST", "/api/settings", body=json.dumps({"mode": "general", "llm_model": "x"}))
        self.assertEqual(json.loads(data)["mode"], "general")
        res, data = self.request("POST", "/api/workspace", body=json.dumps({"name": "kuliah", "create": True}))
        self.assertEqual(json.loads(self.request("GET", "/api/state")[1])["workspace"], "kuliah")
        res, data = self.request("POST", "/api/upload?name=..%2F..%2Fescape.md", body=DIJKSTRA.encode())
        self.assertEqual(self.events(data)[-1]["result"]["file"], "escape.md")
