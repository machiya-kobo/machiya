"""A fake GitHub REST API for code-import's tests and the dev stack: the GET calls code-import makes, answered from a
seed (stdlib only, invented data). Shaped like GitHub's answers with fine-grained tokens: one token per resource
owner (`Authorization: Bearer …`); `/user` is the person who made the token; a token sees its owner's private repos
and only the public repos of anyone else; an empty repo's tree is 409; PRs show up in the issues list with
`pull_request.merged_at`; every answer carries an ETag, and a matching `If-None-Match` is a 304 (counted apart, as
GitHub doesn't count them against the rate limit). Anything but GET is 405 and recorded.

    GET /user  /user/repos?affiliation=owner&visibility=all  /orgs/{o}/repos?type=all
    GET /repos/{o}/{r}/git/trees/{ref}?recursive=1  /repos/{o}/{r}/git/blobs/{sha}
    GET /repos/{o}/{r}/issues?state=all&since=&sort=updated&direction=asc&per_page=&page=
    GET /repos/{o}/{r}/releases?per_page=&page=
    GET /healthz

The seed (FAKE_GITHUB_SEED, default seed/github.json) has fake_forgejo's shape, with `pushed_at` for a repo's last
push, plus "user": the person the tokens belong to.

    FAKE_GITHUB_TOKEN_FILES  owner=/path/to/token,…  (required when run as a server: one token per resource owner)
    FAKE_GITHUB_WEB (the web address in html_url; default https://github.example)
    FAKE_GITHUB_BIND (0.0.0.0)   FAKE_GITHUB_PORT (8080)
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


def blob_sha(text):
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def ts(value):
    return datetime.fromisoformat((value or "1970-01-01T00:00:00Z").replace("Z", "+00:00")).timestamp()


class FakeGitHub:
    def __init__(self, seed, tokens, web="https://github.example"):
        """tokens: {token: resource owner}."""
        self.seed, self.tokens, self.web = seed, dict(tokens), web.rstrip("/")
        self.requests = []              # (method, path, query, authorization header, status)
        self.not_modified = 0
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

    def repos(self):
        return self.seed.get("repos", [])

    def visible(self, r, owner):
        return not r.get("private") or (owner or "").lower() == r["owner"].lower()

    def find(self, o, n):
        for r in self.repos():
            if r["owner"].lower() == o.lower() and r["name"].lower() == n.lower():
                return r
        return None

    def repo_json(self, r):
        kind = self.seed.get("owners", {}).get(r["owner"], "user")
        return {"id": r["id"], "name": r["name"], "full_name": "%s/%s" % (r["owner"], r["name"]),
                "owner": {"login": r["owner"], "type": "Organization" if kind == "org" else "User"},
                "private": bool(r.get("private")), "visibility": "private" if r.get("private") else "public",
                "description": r.get("description"), "fork": bool(r.get("fork")), "archived": bool(r.get("archived")),
                "topics": r.get("topics", []), "language": r.get("language"), "homepage": r.get("website"),
                "default_branch": r.get("default_branch", "main"),
                "mirror_url": "https://git.example/upstream/%s.git" % r["name"] if r.get("mirror") else None,
                "html_url": "%s/%s/%s" % (self.web, r["owner"], r["name"]),
                "created_at": r.get("created_at", "2026-01-01T00:00:00Z"),
                "updated_at": r.get("updated_at", r.get("pushed_at", "2026-01-01T00:00:00Z")),
                "pushed_at": r.get("pushed_at", r.get("updated_at", "2026-01-01T00:00:00Z"))}

    def issue_json(self, r, i):
        kind = "pull" if i.get("pull") else "issues"
        out = {"id": r["id"] * 1000 + i["number"], "number": i["number"], "title": i["title"], "body": i.get("body"),
               "state": i.get("state", "open"), "comments": 2,
               "html_url": "%s/%s/%s/%s/%d" % (self.web, r["owner"], r["name"], kind, i["number"]),
               "created_at": i.get("created_at", "2026-01-01T00:00:00Z"), "updated_at": i.get("updated_at")}
        if i.get("pull"):
            out["pull_request"] = {"url": "", "html_url": out["html_url"],
                                   "merged_at": i.get("updated_at") if i.get("merged") else None}
        return out

    def release_json(self, r, rel):
        return {"id": rel["id"], "tag_name": rel["tag_name"], "name": rel.get("name"), "body": rel.get("body"),
                "draft": bool(rel.get("draft")), "prerelease": bool(rel.get("prerelease")),
                "html_url": "%s/%s/%s/releases/tag/%s" % (self.web, r["owner"], r["name"], quote(rel["tag_name"])),
                "created_at": rel.get("published_at"), "published_at": rel.get("published_at")}

    def answer(self, method, path, q, auth):
        if path == "/healthz":
            return 200, {"ok": True}
        if method != "GET":
            return 405, {"message": "Not allowed"}
        owner = None
        if auth:
            if not auth.startswith("Bearer ") or auth[7:] not in self.tokens:
                return 401, {"message": "Bad credentials"}
            owner = self.tokens[auth[7:]]
        if owner is None:
            return 401, {"message": "Requires authentication"}
        page = max(1, int(q.get("page", ["1"])[0]))
        per = min(100, max(1, int(q.get("per_page", ["30"])[0])))

        def paged(items):
            return 200, items[(page - 1) * per:page * per]

        parts = [unquote(p) for p in path.strip("/").split("/")]
        user = self.seed["user"]
        if parts == ["user"]:
            return 200, {"login": user, "type": "User"}
        if parts == ["user", "repos"]:
            if owner.lower() != user.lower():               # an org's token: none of the user's own repos
                return paged([])
            return paged([self.repo_json(r) for r in sorted(self.repos(), key=lambda r: r["name"].lower())
                          if r["owner"].lower() == user.lower()])
        if len(parts) == 3 and parts[0] == "orgs" and parts[2] == "repos":
            if self.seed.get("owners", {}).get(parts[1]) != "org":
                return 404, {"message": "Not Found"}
            return paged([self.repo_json(r) for r in sorted(self.repos(), key=lambda r: r["name"].lower())
                          if r["owner"] == parts[1] and self.visible(r, owner)])
        if len(parts) >= 3 and parts[0] == "repos":
            r = self.find(parts[1], parts[2])
            if r is None or not self.visible(r, owner):
                return 404, {"message": "Not Found"}
            rest, files = parts[3:], r.get("files") or {}
            if rest[:2] == ["git", "trees"]:
                if not files:
                    return 409, {"message": "Git Repository is empty."}
                if "/".join(rest[2:]) != r.get("default_branch", "main"):
                    return 404, {"message": "Not Found"}
                dirs = sorted({"/".join(p.split("/")[:k]) for p in files for k in range(1, p.count("/") + 1)})
                tree = [{"path": d, "type": "tree", "mode": "040000", "sha": blob_sha(d)} for d in dirs]
                tree += [{"path": p, "type": "blob", "mode": "100644", "sha": blob_sha(t), "size": len(t.encode())}
                         for p, t in sorted(files.items())]
                return 200, {"sha": "0" * 40, "tree": tree, "truncated": False}
            if rest[:2] == ["git", "blobs"] and len(rest) == 3:
                for t in files.values():
                    if blob_sha(t) == rest[2]:
                        return 200, {"sha": rest[2], "size": len(t.encode()), "encoding": "base64",
                                     "content": base64.encodebytes(t.encode()).decode()}
                return 404, {"message": "Not Found"}
            if rest == ["issues"]:
                since = ts(q["since"][0]) if q.get("since") else None
                state = q.get("state", ["open"])[0]
                out = [self.issue_json(r, i) for i in r.get("issues", [])
                       if (state == "all" or i.get("state", "open") == state)
                       and (since is None or ts(i.get("updated_at")) >= since)]
                out.sort(key=lambda x: x["updated_at"], reverse=q.get("direction", ["desc"])[0] == "desc")
                return paged(out)
            if rest == ["releases"]:
                return paged([self.release_json(r, rel) for rel in r.get("releases", [])])
        return 404, {"message": "Not Found"}

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
                auth = self.headers.get("Authorization")
                status, body = fake.answer(method, u.path, parse_qs(u.query), auth)
                data = json.dumps(body).encode()
                etag = '"%s"' % hashlib.sha1(data + (auth or "").encode()).hexdigest()
                if status == 200 and self.headers.get("If-None-Match") == etag:
                    status, data = 304, b""
                    with fake.lock:
                        fake.not_modified += 1
                with fake.lock:
                    fake.requests.append((method, u.path, parse_qs(u.query), auth, status))
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("x-ratelimit-remaining", "4999")
                if status in (200, 304):
                    self.send_header("ETag", etag)
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
    seed_path = os.environ.get("FAKE_GITHUB_SEED") or os.path.join(HERE, "seed", "github.json")
    with open(seed_path) as f:
        seed = json.load(f)
    tokens = {}
    for item in [x.strip() for x in os.environ.get("FAKE_GITHUB_TOKEN_FILES", "").split(",") if x.strip()]:
        owner, _, path = item.partition("=")
        try:
            with open(path) as f:
                token = f.readline().strip()
        except OSError:
            token = ""
        if not token:
            raise SystemExit("fake-github: no token in %s (for %s)" % (path, owner))
        tokens[token] = owner
    if not tokens:
        raise SystemExit("fake-github: FAKE_GITHUB_TOKEN_FILES must name owner=/path/to/token pairs")
    fake = FakeGitHub(seed, tokens, os.environ.get("FAKE_GITHUB_WEB", "https://github.example"))
    srv = ThreadingHTTPServer((os.environ.get("FAKE_GITHUB_BIND", "0.0.0.0"), int(os.environ.get("FAKE_GITHUB_PORT", "8080"))),
                              fake.handler())
    print("fake-github: %d repos, %d tokens on :%d" % (len(fake.repos()), len(tokens), srv.server_address[1]), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
