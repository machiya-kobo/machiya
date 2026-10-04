"""A stand-in room for the hister-login dev stack (gate 0): the smallest server that uses vaultkit.histerauth the way
Kura, Niwa and Konbini will. ROOM=kura|niwa|konbini picks the settings prefix (KURA_, NIWA_, KANBAN_).

  GET  /              a page: who is signed in, the fallback banner, a Sign Out button and the Rooms menu
  GET  /api/whoami    the API: JSON, or 401 {"error","signin"} (never redirected), 503 when sign-in is unavailable
  POST /signout       same-origin only: the helper ends the session everywhere; the cookie is cleared
  GET  /api/status    probes: always open; fallback_total
Dev only: never deployed.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

sys.path.insert(0, "/app")                 # the hister-login image's vendored vaultkit

from vaultkit import histerauth, shell, signin as vsignin   # noqa: E402

ROOM = os.environ.get("ROOM", "niwa")
PREFIX = {"konbini": "KANBAN"}.get(ROOM, ROOM.upper())
PUBLIC = os.environ.get("KANBAN_BOARD_URL" if ROOM == "konbini" else PREFIX + "_PUBLIC_URL", "")
AUTH = histerauth.load_for(ROOM, bind="0.0.0.0")
if AUTH is None:
    sys.exit("room: %s_AUTH must be hister" % PREFIX)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send(self, status, headers, body=b""):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/status":
            return self.send(200, [("Content-Type", "application/json")],
                             json.dumps({"ok": True, "room": ROOM, "fallback_total": AUTH.fallback_total}).encode())
        if path.startswith("/static/") and path[8:] in ("machiya.css", "machiya.js"):
            with open(os.path.join(shell.UI_DIR, path[8:]), "rb") as f:
                ctype = "text/css" if path.endswith(".css") else "text/javascript"
                return self.send(200, [("Content-Type", ctype)], f.read())
        if path == "/signed-out":
            body = shell.message("Signed Out", "You're signed out of every room.", [("/", "Sign In Again")])
            return self.page(200, body, [])
        is_page = not path.startswith("/api/")
        r = AUTH.resolve(self.headers, is_page=is_page, path=self.path)
        print("%s %s -> %d %s %s" % (ROOM, path, r.status, r.reason, r.actor), file=sys.stderr, flush=True)
        if not r:
            return self.send(*AUTH.respond(r, is_page, shell.prefs(self.headers.get("Cookie"))))
        extra = [("Set-Cookie", c) for c in r.cookies]
        if not is_page:
            return self.send(200, [("Content-Type", "application/json")] + extra,
                             json.dumps({"room": ROOM, "user": r.principal.name, "via": r.principal.via,
                                         "actor": r.actor, "banner": r.banner}).encode())
        banner = histerauth.banner_html() if r.banner else ""
        body = ('<main class="msg">%s<div class="empty"><h2>%s</h2><p data-who="%s">Signed in as <b>%s</b> (%s).</p>'
                '<p class="actions"><a class="button primary" href="/api/whoami">API</a></p>'
                '</div><form class="signin" method="post" action="/signout"><button type="submit">Sign Out</button>'
                '</form></main>'
                % (banner, shell.e(ROOM.title()), shell.e(r.principal.name), shell.e(r.principal.name),
                   shell.e(r.actor)))
        self.page(200, body, extra, who=r.principal.name)

    def page(self, status, body, extra, who=""):
        ctx = shell.prefs(self.headers.get("Cookie"))
        html = shell.page(ctx, ROOM, ROOM.title(), shell.header(ROOM, [("/", "home", "Home")], "home", shell.rooms(),
                                                               who=who) + body,
                          tabs=[("/", "home", "Home")], current="home", manifest=False,
                          head=histerauth.signin_meta(), who=who)
        self.send(status, [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")] + extra,
                  html.encode())

    def do_POST(self):
        if urlsplit(self.path).path != "/signout":
            return self.send(404, [], b"")
        vsignin.read_body(self.headers, self.rfile, 4096)
        if not vsignin.same_origin(self.headers, True, (PUBLIC,)):
            return self.send(403, [("Content-Type", "text/plain")], b"cross-site sign-out refused\n")
        ended, cookies = AUTH.signout(self.headers)
        print("%s signout ended=%s" % (ROOM, ended), file=sys.stderr, flush=True)
        self.send(303, [("Location", "/signed-out"), ("Cache-Control", "no-store")]
                  + [("Set-Cookie", c) for c in cookies])


if __name__ == "__main__":
    print("room %s: AUTH=hister fallback=%s" % (ROOM, AUTH.fallback), file=sys.stderr, flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()
