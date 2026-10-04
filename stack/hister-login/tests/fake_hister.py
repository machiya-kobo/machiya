"""A small Hister v0.20.0 for the helper's tests: users with passwords and tokens, host-only `hister` session cookies
(32 random bytes, base64url), /api/login, /api/logout (CSRF: Origin hister:// or Sec-Fetch-Site same-origin), /api/profile
(403 with an empty body when nobody is signed in; 200 with an EMPTY body to anyone when user handling is off), /health,
and /api/oauth/callback (state kept in the session; a consumed-flow Set-Cookie first, then the rotated one, then a 302
to "/", as the real one does). `fail` makes every answer a 502; `calls` counts requests by path."""
import base64
import json
import secrets
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def new_session():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


class FakeHister:
    def __init__(self):
        self.users = {"owner": {"id": 1, "password": "correct horse", "token": "tok-owner"},
                      "other": {"id": 2, "password": "other pass", "token": "tok-other"}}
        self.sessions = {}              # value -> {"uid": int, "state": str}
        self.user_handling = True
        self.fail = False
        self.calls = Counter()
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def answer(self, status, body=b"", headers=()):
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def session(self):
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "hister" and v in fake.sessions:
                        return v
                return None

            def user(self):
                s = self.session()
                if s and fake.sessions[s].get("uid"):
                    return fake.sessions[s]["uid"]
                tok = self.headers.get("X-Access-Token") or ""
                for u in fake.users.values():
                    if tok and tok == u["token"]:
                        return u["id"]
                return 0

            def name_of(self, uid):
                return next(n for n, u in fake.users.items() if u["id"] == uid)

            def do_GET(self):
                path = urlsplit(self.path).path
                fake.calls[path] += 1
                if fake.fail:
                    return self.answer(502, b"bad gateway")
                if path == "/health":
                    return self.answer(200, b"OK")
                if path == "/api/profile":
                    if not fake.user_handling:
                        return self.answer(200)
                    uid = self.user()
                    if not uid:
                        return self.answer(403)
                    return self.answer(200, json.dumps({"user_id": uid, "username": self.name_of(uid),
                                                        "is_admin": uid == 1}).encode(),
                                       [("Content-Type", "application/json")])
                if path == "/api/oauth/callback":
                    q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                    s = self.session()
                    if not s or fake.sessions[s].get("state") != q.get("state"):
                        return self.answer(400, b"invalid oauth state")
                    fake.sessions[s]["state"] = ""
                    new = new_session()
                    del fake.sessions[s]
                    fake.sessions[new] = {"uid": 1}
                    return self.answer(302, b"", [
                        ("Set-Cookie", "hister=%s; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax" % s),
                        ("Set-Cookie", "hister=%s; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax" % new),
                        ("Location", "https://hister.example.test/")])
                self.answer(404)

            def do_POST(self):
                path = urlsplit(self.path).path
                fake.calls[path] += 1
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if fake.fail:
                    return self.answer(502, b"bad gateway")
                if self.headers.get("Origin") != "hister://" and self.headers.get("Sec-Fetch-Site") != "same-origin":
                    return self.answer(403, b"CSRF token mismatch")
                if path == "/api/login":
                    req = json.loads(body or b"{}")
                    u = fake.users.get(req.get("username"))
                    if not u or u["password"] != req.get("password"):
                        return self.answer(401, b"invalid credentials")
                    old = self.session()
                    if old:
                        del fake.sessions[old]
                    s = new_session()
                    fake.sessions[s] = {"uid": u["id"]}
                    return self.answer(200, json.dumps({"username": req["username"]}).encode(), [
                        ("Set-Cookie", "hister=%s; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax" % s)])
                if path == "/api/logout":
                    if not self.user():
                        return self.answer(403)
                    s = self.session()
                    if s:
                        del fake.sessions[s]
                    return self.answer(200, b"", [("Set-Cookie", "hister=; Path=/; Max-Age=0; HttpOnly; Secure; "
                                                                 "SameSite=Lax")])
                self.answer(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def signed_in(self, name="owner"):
        """A browser that signed in to Hister directly: its session value."""
        s = new_session()
        self.sessions[s] = {"uid": self.users[name]["id"]}
        return s

    def oauth_started(self):
        """A browser that went to /api/oauth: an anonymous session holding the state."""
        s = new_session()
        self.sessions[s] = {"uid": 0, "state": "st-" + s[:6]}
        return s, "st-" + s[:6]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
