"""A small stateful Hister for the label and collection tests: documents, the query language the tools use (words, label:, label:(a|b),
-label:, -metadata.source:, domain:, url:"…", @alias, *), 100-per-page search with page_key (present even on the last page,
as the real one does), /api/document, /api/label, /api/update (matched/updated), aliases (form posts; deleting a missing one
is a 500), and Origin: hister:// required on every POST. Every write is recorded in `writes`."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def make_docs():
    docs = {}

    def add(url, title, label="", source="", domain=None):
        docs[url] = {"url": url, "title": title, "domain": domain or urlsplit(url).hostname, "label": label,
                     "metadata": {"source": source} if source else {}, "text": "text of " + title, "score": 1.0, "added": 1}
    for i in range(120):
        add("https://tech.example/p/%03d" % i, "Tech page %d" % i, "tech")
    for i in range(60):
        add("https://hardware.example/p/%03d" % i, "Hardware page %d" % i, "hardware")
    for i in range(30):
        add("https://music.example/p/%03d" % i, "Music page %d" % i, "music")
    for i in range(20):
        add("https://visited.example/p/%03d" % i, "Visited page %d" % i, "")
    for i in range(10):
        add("https://news.example/p/%03d" % i, "Feed item %d" % i, "feedreader", "feedreader")
    for i in range(4):
        add("https://box.example/p/%03d" % i, "Archived %d" % i, "importer")
    for i in range(5):
        add("https://kura.test/n/Notes/Note%d" % i, "Vault note %d" % i, "vault", "vault")
    for i in range(3):
        add("https://konbini.test/p/card%d" % i, "Card %d" % i, "konbini")
    return docs


class FakeHister:
    def __init__(self, aliases=None):
        self.docs = make_docs()
        self.aliases = dict(aliases if aliases is not None else {
            "@tech": "label:(tech|hardware)", "@legacy": "label:(hardware|dead)", "@mix": "label:tech -domain:x.example",
            "@pages": "* -label:vault -metadata.source:vault", "@notes": "label:vault",
            "everything": "label:(tech|music|hardware|feedreader)"})
        self.writes, self.requests, self.lock = [], [], threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send_json(self, code, body=""):
                data = (json.dumps(body) if not isinstance(body, str) else body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                with fake.lock:
                    fake.requests.append(("GET", u.path, q, dict(self.headers)))
                    if u.path == "/search":
                        return self.send_json(200, fake.search(q))
                    if u.path == "/api/document":
                        d = fake.docs.get(q.get("url"))
                        return self.send_json(200, d) if d else self.send_json(404, {"error": "no such document"})
                    if u.path == "/api/rules":
                        return self.send_json(200, {"allow": [], "skip": [], "aliases": dict(fake.aliases)})
                    if u.path == "/api/stats":
                        return self.send_json(200, {"doc_count": len(fake.docs)})
                self.send_json(404, {"error": "no route"})

            def do_POST(self):
                u = urlsplit(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.headers.get("Origin") != "hister://":
                    return self.send_json(403, "forbidden")
                ctype = self.headers.get("Content-Type", "")
                body = json.loads(raw) if "json" in ctype and raw else {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                with fake.lock:
                    fake.writes.append({"path": u.path, "body": body, "ctype": ctype, "headers": dict(self.headers)})
                    if u.path == "/api/label":
                        d = fake.docs.get(body.get("url"))
                        if not d:
                            return self.send_json(404, {"error": "no such document"})
                        d["label"] = body.get("label", "")
                        return self.send_json(200, {"ok": True})
                    if u.path == "/api/update":
                        hits = [d for d in fake.docs.values() if fake.matches(d, body["query"])]
                        ch = body.get("changes", {})
                        updated = 0
                        for d in hits:
                            if "label" in ch and d["label"] != ch["label"]:
                                d["label"] = ch["label"]
                                updated += 1
                        return self.send_json(200, {"matched": len(hits), "updated": updated, "unchanged": len(hits) - updated, "conflicts": 0})
                    if u.path == "/api/add_alias":
                        fake.aliases[body["alias-keyword"]] = body["alias-value"]
                        return self.send_json(200, "")
                    if u.path == "/api/delete_alias":
                        if body["alias"] not in fake.aliases:
                            return self.send_json(500, "Internal Server Error")
                        del fake.aliases[body["alias"]]
                        return self.send_json(200, "")
                self.send_json(404, {"error": "no route"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- the query language -----------------------------------------------------------------------------------------
    def expand(self, query):
        return re.sub(r"@([A-Za-z0-9_-]+)", lambda m: self.aliases.get("@" + m.group(1), m.group(0)), query)

    def matches(self, d, query):
        query = self.expand(query)
        for m in re.finditer(r'-label:(\S+)', query):
            if d["label"] == m.group(1):
                return False
        for m in re.finditer(r'-metadata\.source:(\S+)', query):
            if d["metadata"].get("source") == m.group(1):
                return False
        for m in re.finditer(r'-domain:(\S+)', query):
            if d["domain"] == m.group(1):
                return False
        rest = re.sub(r'-(?:label|metadata\.source|domain):\S+', " ", query)
        m = re.search(r'url:"((?:[^"\\]|\\.)*)"', rest)
        if m:
            if d["url"] != m.group(1).replace('\\"', '"').replace("\\\\", "\\"):
                return False
            rest = rest.replace(m.group(0), " ")
        for m in re.finditer(r'label:\(([^)]*)\)', rest):
            if d["label"] not in m.group(1).split("|"):
                return False
        rest = re.sub(r'label:\([^)]*\)', " ", rest)
        for m in re.finditer(r'(?<!-)label:(\S+)', rest):
            if d["label"] != m.group(1):
                return False
        rest = re.sub(r'label:\S+', " ", rest)
        for m in re.finditer(r'domain:(\S+)', rest):
            if d["domain"] != m.group(1):
                return False
        rest = re.sub(r'domain:\S+', " ", rest)
        for w in rest.split():
            if w != "*" and w.lower() not in (d["title"] + " " + d["text"]).lower():
                return False
        return True

    def search(self, q):
        hits = sorted((d for d in self.docs.values() if self.matches(d, q["q"])), key=lambda d: d["url"])
        limit = min(int(q.get("limit", 20)), 100)
        offset = json.loads(q["page_key"])[0] if q.get("page_key") else 0
        page = hits[int(offset):int(offset) + limit]
        return {"total": len(hits), "documents": page, "page_key": json.dumps([int(offset) + len(page)]) if page else ""}
