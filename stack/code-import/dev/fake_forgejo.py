"""A fake Forgejo (API v1) for code-import's tests and the dev stack: the GET calls code-import makes, answered from a
seed (stdlib only, invented data). Shaped like Forgejo 16's answers (Gitea's API): `Authorization: token …`, 50
items a page at most (max_response_items), git trees paged with `truncated`, blobs in base64, the cross-repo issue
search with `owner`, `type`, `state` and `since`. Anything but GET is answered 405 and recorded, so a test can prove
the importer only reads.

    GET /api/v1/version  /api/v1/user  /api/v1/user/repos  /api/v1/orgs/{o}  /api/v1/users/{o}
    GET /api/v1/orgs/{o}/repos  /api/v1/users/{o}/repos
    GET /api/v1/repos/{o}/{r}/git/trees/{ref}?recursive=true&per_page=&page=
    GET /api/v1/repos/{o}/{r}/git/blobs/{sha}
    GET /api/v1/repos/issues/search?owner=&type=issues|pulls&state=all&since=&limit=&page=
    GET /api/v1/repos/{o}/{r}/releases?limit=&page=
    GET /healthz

The seed (FAKE_FORGEJO_SEED, default seed/forgejo.json):
    {"login": "<the token's user>", "owners": {"<name>": "user"|"org"},
     "repos": [{"id", "owner", "name", "description", "private", "fork", "archived", "mirror", "topics", "language",
                "default_branch", "created_at", "updated_at",
                "files": {"<path>": "<text>"},
                "issues": [{"number", "title", "body", "state", "pull", "merged", "created_at", "updated_at"}],
                "releases": [{"id", "tag_name", "name", "body", "draft", "prerelease", "published_at"}]}]}

    FAKE_FORGEJO_TOKEN_FILE (required when run as a server: the token code-import must send)
    FAKE_FORGEJO_ROOT_URL (the web address in html_url; default http://forgejo.example)
    FAKE_FORGEJO_BIND (0.0.0.0)   FAKE_FORGEJO_PORT (8080)
"""
import base64
import hashlib
import json
import os
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_ITEMS = 50


def blob_sha(text):
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def ts(value):
    return datetime.fromisoformat((value or "1970-01-01T00:00:00Z").replace("Z", "+00:00")).timestamp()


class FakeForgejo:
    def __init__(self, seed, token, root_url="http://forgejo.example"):
        self.seed, self.token, self.root = seed, token, root_url.rstrip("/")
        self.requests = []              # (method, path, query, authorization header)
        self.lock = threading.Lock()
        self.faults = []                # [path substring, action, times or None (always)]: tests' failures
        self.sleep = 1.0

    def fault(self, path):
        """The injected failure for this request, if any: "sleep" (answer after self.sleep s: a read timeout),
        "reset" (close the connection unanswered), "503", "429" (Retry-After: 0)."""
        with self.lock:
            for f in self.faults:
                if f[0] in path and (f[2] is None or f[2] > 0):
                    if f[2] is not None:
                        f[2] -= 1
                    return f[1]
        return None

    # -- the seed as Forgejo shows it ----------------------------------------------------------------------------------

    def repos(self):
        return self.seed.get("repos", [])

    def find(self, owner, name):
        for r in self.repos():
            if r["owner"].lower() == owner.lower() and r["name"].lower() == name.lower():
                return r
        return None

    def repo_json(self, r):
        kind = self.seed.get("owners", {}).get(r["owner"], "user")
        return {"id": r["id"], "name": r["name"], "full_name": "%s/%s" % (r["owner"], r["name"]),
                "owner": {"id": int(hashlib.md5(r["owner"].encode()).hexdigest()[:4], 16), "login": r["owner"], "username": r["owner"],
                          "visibility": self.seed.get("visibility", {}).get(r["owner"], "public"),
                          "type": "Organization" if kind == "org" else "User"},
                "description": r.get("description", ""), "private": bool(r.get("private")), "fork": bool(r.get("fork")),
                "archived": bool(r.get("archived")), "mirror": bool(r.get("mirror")), "empty": not r.get("files"),
                "internal": False, "topics": r.get("topics", []), "language": r.get("language", ""),
                "website": r.get("website", ""), "default_branch": r.get("default_branch", "main"),
                "html_url": "%s/%s/%s" % (self.root, r["owner"], r["name"]),
                "created_at": r.get("created_at", "2026-01-01T00:00:00Z"),
                "updated_at": r.get("updated_at", "2026-01-01T00:00:00Z")}

    def issue_json(self, r, i):
        url = "%s/%s/%s/%s/%d" % (self.root, r["owner"], r["name"], "pulls" if i.get("pull") else "issues", i["number"])
        pr = None
        if i.get("pull"):
            pr = {"merged": bool(i.get("merged")), "merged_at": i.get("updated_at") if i.get("merged") else None}
        return {"id": r["id"] * 1000 + i["number"], "number": i["number"], "title": i["title"], "body": i.get("body", ""),
                "state": i.get("state", "open"), "html_url": url, "pull_request": pr, "comments": 3,
                "created_at": i.get("created_at", "2026-01-01T00:00:00Z"), "updated_at": i.get("updated_at"),
                "repository": {"id": r["id"], "name": r["name"], "owner": r["owner"],
                               "full_name": "%s/%s" % (r["owner"], r["name"])}}

    def release_json(self, r, rel):
        return {"id": rel["id"], "tag_name": rel["tag_name"], "name": rel.get("name", rel["tag_name"]),
                "body": rel.get("body", ""), "draft": bool(rel.get("draft")), "prerelease": bool(rel.get("prerelease")),
                "html_url": "%s/%s/%s/releases/tag/%s" % (self.root, r["owner"], r["name"], quote(rel["tag_name"])),
                "created_at": rel.get("published_at"), "published_at": rel.get("published_at")}

    # -- answers -------------------------------------------------------------------------------------------------------

    def answer(self, method, path, q, auth):
        """(status, JSON-able body or None, extra headers)."""
        with self.lock:
            self.requests.append((method, path, q, auth))
        if path == "/healthz":
            return 200, {"ok": True}, {}
        if method != "GET":
            return 405, {"message": "method not allowed"}, {}
        if path == "/api/v1/version":
            return 200, {"version": "16.0.5+fake"}, {}
        if auth != "token " + self.token:
            return 401, {"message": "token is required"}, {}
        page = max(1, int(q.get("page", ["1"])[0]))
        limit = min(MAX_ITEMS, max(1, int(q.get("limit", [str(MAX_ITEMS)])[0])))

        def paged(items):
            return 200, items[(page - 1) * limit:page * limit], {"X-Total-Count": str(len(items))}

        parts = [unquote(p) for p in path.split("/")[3:]]          # after /api/v1/
        login = self.seed["login"]
        owners = self.seed.get("owners", {})
        if parts == ["user"]:
            return 200, {"id": 1, "login": login, "username": login}, {}
        if parts == ["user", "repos"]:
            return paged([self.repo_json(r) for r in self.repos() if r["owner"] == login])
        if len(parts) == 2 and parts[0] in ("orgs", "users"):
            want = "org" if parts[0] == "orgs" else "user"
            if owners.get(parts[1]) != want:
                return 404, {"message": "not found"}, {}
            return 200, {"id": 2, "login": parts[1], "username": parts[1]}, {}
        if len(parts) == 3 and parts[0] in ("orgs", "users") and parts[2] == "repos":
            want = "org" if parts[0] == "orgs" else "user"
            if owners.get(parts[1]) != want:
                return 404, {"message": "not found"}, {}
            return paged([self.repo_json(r) for r in self.repos() if r["owner"] == parts[1]])
        if parts[:3] == ["repos", "issues", "search"]:
            since = ts(q["since"][0]) if q.get("since") else None
            kind, owner, state = q.get("type", [""])[0], q.get("owner", [""])[0], q.get("state", ["open"])[0]
            out = []
            for r in self.repos():
                if owner and r["owner"].lower() != owner.lower():
                    continue
                for i in r.get("issues", []):
                    if kind == "issues" and i.get("pull") or kind == "pulls" and not i.get("pull"):
                        continue
                    if state != "all" and i.get("state", "open") != state:
                        continue
                    if since is not None and ts(i.get("updated_at")) < since:
                        continue
                    out.append(self.issue_json(r, i))
            out.sort(key=lambda x: x["updated_at"], reverse=True)
            return paged(out)
        if len(parts) >= 3 and parts[0] == "repos":
            r = self.find(parts[1], parts[2])
            if r is None:
                return 404, {"message": "repo not found"}, {}
            rest = parts[3:]
            files = r.get("files") or {}
            if rest[:2] == ["git", "trees"]:
                if not files or "/".join(rest[2:]) != r.get("default_branch", "main"):
                    return 404, {"message": "not found"}, {}
                per = min(1000, int(q.get("per_page", ["1000"])[0]))
                entries = []
                dirs = sorted({"/".join(p.split("/")[:k]) for p in files for k in range(1, p.count("/") + 1)})
                for d in dirs:
                    entries.append({"path": d, "type": "tree", "mode": "040000", "sha": blob_sha(d), "size": 0})
                for p in sorted(files):
                    entries.append({"path": p, "type": "blob", "mode": "100644", "sha": blob_sha(files[p]),
                                    "size": len(files[p].encode())})
                chunk = entries[(page - 1) * per:page * per]
                return 200, {"sha": "0" * 40, "tree": chunk, "truncated": page * per < len(entries), "page": page,
                             "total_count": len(entries)}, {}
            if rest[:2] == ["git", "blobs"] and len(rest) == 3:
                for p, text in files.items():
                    if blob_sha(text) == rest[2]:
                        data = text.encode()
                        return 200, {"sha": rest[2], "size": len(data), "encoding": "base64",
                                     "content": base64.b64encode(data).decode()}, {}
                return 404, {"message": "blob not found"}, {}
            if rest == ["releases"]:
                return paged([self.release_json(r, rel) for rel in r.get("releases", [])])
        return 404, {"message": "not found"}, {}

    def handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def any(self, method):
                u = urlsplit(self.path)
                action = fake.fault(u.path)
                if action == "sleep":
                    time.sleep(fake.sleep)
                elif action == "reset":
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                elif action in ("503", "429"):
                    self.send_response(int(action))
                    if action == "429":
                        self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status, body, extra = fake.answer(method, u.path, parse_qs(u.query), self.headers.get("Authorization"))
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:                     # the client gave up (a "sleep" fault's timeout)
                    pass

            def finish(self):
                try:
                    super().finish()
                except OSError:                     # a "reset" fault closed the connection
                    pass

            def do_GET(self):
                self.any("GET")

            def do_POST(self):
                self.any("POST")

            def do_PUT(self):
                self.any("PUT")

            def do_PATCH(self):
                self.any("PATCH")

            def do_DELETE(self):
                self.any("DELETE")

        return Handler

    def serve(self, bind="127.0.0.1", port=0):
        srv = ThreadingHTTPServer((bind, port), self.handler())
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return srv, "http://%s:%d" % (bind, srv.server_address[1])


def main():
    seed_path = os.environ.get("FAKE_FORGEJO_SEED") or os.path.join(HERE, "seed", "forgejo.json")
    with open(seed_path) as f:
        seed = json.load(f)
    try:
        with open(os.environ["FAKE_FORGEJO_TOKEN_FILE"]) as f:
            token = f.readline().strip()
    except (KeyError, OSError):
        raise SystemExit("fake-forgejo: FAKE_FORGEJO_TOKEN_FILE must name a file holding the token")
    if not token:
        raise SystemExit("fake-forgejo: the token file is empty")
    fake = FakeForgejo(seed, token, os.environ.get("FAKE_FORGEJO_ROOT_URL", "http://forgejo.example"))
    srv = ThreadingHTTPServer((os.environ.get("FAKE_FORGEJO_BIND", "0.0.0.0"), int(os.environ.get("FAKE_FORGEJO_PORT", "8080"))),
                              fake.handler())
    print("fake-forgejo: %d repos on :%d" % (len(fake.repos()), srv.server_address[1]), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
