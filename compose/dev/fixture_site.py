"""The dev stack's synthetic web: static pages from seed/site/<host>/ (stdlib only, dev only, invented content).

A request is answered from the folder named by its Host header (`lantern-supply.example`, `kyoto-guide.example`,
`workshop-journal.example`: the container's network aliases, so feed-import and smallweb fetch them by name), or, for
a browser on the published port, from the first path segment: http://localhost:19209/kyoto-guide.example/packing.html.
`/` without a known host lists the sites.

    FIXTURE_ROOT (default: seed/site next to this file)   FIXTURE_BIND (0.0.0.0)   FIXTURE_PORT (8080)
"""
import html
import mimetypes
import os
import posixpath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = os.path.abspath(os.environ.get("FIXTURE_ROOT") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                      "seed", "site"))


def sites():
    return sorted(d for d in os.listdir(ROOT) if os.path.isdir(os.path.join(ROOT, d)))


def resolve(host, path):
    """-> a file under ROOT, or None. Never leaves ROOT."""
    host = (host or "").split(":")[0].lower()
    parts = [p for p in posixpath.normpath(unquote(path)).split("/") if p not in ("", ".", "..")]
    if host not in sites():
        if not parts or parts[0] not in sites():
            return None
        host, parts = parts[0], parts[1:]
    f = os.path.join(ROOT, host, *parts)
    if os.path.isdir(f):
        f = os.path.join(f, "index.html")
    f = os.path.realpath(f)
    return f if f.startswith(os.path.realpath(ROOT) + os.sep) and os.path.isfile(f) else None


class H(BaseHTTPRequestHandler):
    server_version = "machiya-dev-fixtures"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self, head=False):
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self.reply(200, b'{"ok": true}', "application/json", head)
        f = resolve(self.headers.get("Host"), path)
        if f is None:
            if path == "/":
                items = "".join('<li><a href="/%s/">%s</a></li>' % (html.escape(s), html.escape(s)) for s in sites())
                body = ("<!doctype html><title>Dev fixtures</title><h1>Synthetic sites</h1><ul>%s</ul>"
                        "<p>Invented pages for the Machiya dev stack.</p>" % items).encode()
                return self.reply(200, body, "text/html; charset=utf-8", head)
            return self.reply(404, b"not found\n", "text/plain", head)
        with open(f, "rb") as fh:
            body = fh.read()
        ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self.reply(200, body, ctype, head)

    def do_HEAD(self):
        self.do_GET(head=True)

    def reply(self, status, body, ctype, head=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", "Thu, 01 Oct 2026 00:00:00 GMT")
        self.end_headers()
        if not head:
            self.wfile.write(body)


if __name__ == "__main__":
    bind, port = os.environ.get("FIXTURE_BIND", "0.0.0.0"), int(os.environ.get("FIXTURE_PORT", "8080"))
    print("fixtures: %s on %s:%d (%s)" % (ROOT, bind, port, ", ".join(sites())), flush=True)
    ThreadingHTTPServer((bind, port), H).serve_forever()
