"""The password sign-in from a hosted page, in real browsers, against the tests' fake Hister: no containers, dummy data.

    ~/data/venvs/shots/bin/python dev/signin_check.py        (a Python with Playwright, Chromium and WebKit)

A TLS front plays Tailscale Serve for hister.example.test (/machiya/ and the OAuth callback to the helper, the rest to
the fake Hister) and shiori-web's nginx for search.example.test (/machiya/start|callback… to the helper with its Host,
/ behind /v1/nginx with the host's room cookie, a 401 sent to the helper's sign-in). Both engines reach it through a
CONNECT proxy. In Chromium, WebKit and Firefox, each case starts on search.example.test/ and must end there signed
in, with nothing ever posted to the helper's /machiya/signin:
  - typed: the name and password typed, Sign In clicked;
  - enter: typed, then Enter in the password field;
  - manager: a password manager's fill (values set, input and change events), then its click on Sign In;
  - auto-submit: a manager's auto-submit, the field's form.submit() when it has a form (from its own script world:
    in Chromium an unpacked extension's content script, elsewhere the native HTMLFormElement.submit, which is what
    such a script reaches) and a click on the button when it has none, as on Hister's own page;
  - submit-only: a manager that only ever calls form.submit(): with no form it does nothing, and the page must stay,
    fields filled, nothing posted (the owner then clicks Sign In).
Everything it writes goes under a temporary directory in WORK (default /var/tmp/machiya-login); the exit code is the
number of failures."""
import http.client
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "tests")]

import hister_login as hl                      # noqa: E402
from fake_hister import FakeHister             # noqa: E402
from playwright.sync_api import sync_playwright   # noqa: E402

WORK = os.environ.get("WORK", "/var/tmp/machiya-login")
HISTER, SEARCH = "hister.example.test", "search.example.test"
EXTENSION = {
    "manifest.json": '{"manifest_version": 3, "name": "auto-submit", "version": "1", "content_scripts": [{"matches": '
                     '["https://hister.example.test/machiya/*"], "js": ["fill.js"], "run_at": "document_idle"}]}',
    "fill.js": """const MODE = "%s";
setTimeout(() => {
  const user = document.querySelector('input[autocomplete="username"]');
  const pass = document.querySelector('input[autocomplete="current-password"]');
  if (!user || !pass) return;
  for (const [el, v] of [[user, "owner"], [pass, "correct horse"]]) {
    el.focus();
    el.value = v;
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
  }
  if (pass.form) pass.form.submit();
  else if (MODE === "auto-submit") document.querySelector("#hister-signin button").click();
}, 300);""",
}


def main():
    os.makedirs(WORK, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="signin-check-", dir=WORK)
    try:
        return run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


POSTED = []     # every POST to the helper on Hister's host: a sign-in page must never send one


def run(tmp):
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=example.test",
                    "-addext", "subjectAltName=DNS:*.example.test", "-keyout", tmp + "/key.pem", "-out", tmp + "/cert.pem"],
                   check=True, capture_output=True)
    for mode in ("auto-submit", "submit-only"):
        os.makedirs(os.path.join(tmp, mode))
        for name, text in EXTENSION.items():
            with open(os.path.join(tmp, mode, name), "w") as f:
                f.write(text % mode if name == "fill.js" else text)
    fake = FakeHister()
    env = {"HISTER_LOGIN_PUBLIC_URL": "https://" + HISTER, "HISTER_LOGIN_HISTER_URL": fake.url,
           "HISTER_LOGIN_DB": tmp + "/hl.db", "HISTER_LOGIN_PROVIDERS": "oidc", "HISTER_LOGIN_LEGACY": "none",
           "HISTER_LOGIN_PROXIED_ORIGINS": "https://" + SEARCH, "HISTER_LOGIN_RETURN_HOSTS": SEARCH,
           "MACHIYA_ROOMS": "hister=https://" + HISTER}
    login = hl.Login(hl.Settings(env))
    servers = hl.serve(login, "127.0.0.1", 0, 0)
    pub, internal = servers[0].server_address[1], servers[1].server_address[1]
    fake_port = int(fake.url.rsplit(":", 1)[1])

    class Front(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def upstream(self, port, method, path, headers, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, path, body=body, headers=headers)
            r = c.getresponse()
            return r.status, r.getheaders(), r.read()

        def handle_any(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            path = urlsplit(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            headers = dict(self.headers.items())
            if host == HISTER and self.command == "POST" and path.startswith("/machiya/"):
                POSTED.append(path)
            if host == HISTER:
                helper = path.startswith("/machiya/") or path == "/api/oauth/callback"
                answer = self.upstream(pub if helper else fake_port, self.command, self.path, headers, body)
            elif host == SEARCH and (re.match(r"/machiya/(start|callback|signed-out|signout|api/prefs)$", path)
                                     or path.startswith("/machiya/static/")):
                answer = self.upstream(pub, self.command, self.path, headers, body)
            elif host == SEARCH:
                m = re.search(r"(?:^|;\s*)__Host-machiya_sso_shiori=(mhr_[A-Za-z0-9_-]{43})", headers.get("Cookie", ""))
                st, hd, _ = self.upstream(internal, "GET", "/v1/nginx", {"X-Machiya-Session": m.group(1) if m else "",
                                                                         "X-Machiya-Room": "https://" + host})
                if st == 200:
                    answer = (200, [("Content-Type", "text/html")], b"<p id=signed-in>signed in</p>")
                else:
                    back = quote("https://%s%s" % (host, self.path), safe="")
                    answer = (302, [("Location", "https://%s/machiya/signin?return=%s" % (HISTER, back))], b"")
            else:
                answer = (404, [], b"")
            status, hd, data = answer
            self.send_response(status)
            for k, v in hd:
                if k.lower() not in ("content-length", "connection", "transfer-encoding", "date", "server"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = handle_any

    front = ThreadingHTTPServer(("127.0.0.1", 0), Front)
    tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls.load_cert_chain(tmp + "/cert.pem", tmp + "/key.pem")
    front.socket = tls.wrap_socket(front.socket, server_side=True)
    threading.Thread(target=front.serve_forever, daemon=True).start()
    proxy = connect_proxy(front.server_address[1])

    failures = 0
    manager_fill = """() => {
        for (const [id, v] of [["signin-username", "owner"], ["signin-password", "correct horse"]]) {
            const el = document.getElementById(id);
            el.focus();
            el.value = v;
            el.dispatchEvent(new Event("input", {bubbles: true}));
            el.dispatchEvent(new Event("change", {bubbles: true}));
        }
    }"""
    native_submit = """(mode) => {
        const pass = document.getElementById("signin-password");
        if (pass.form) HTMLFormElement.prototype.submit.call(pass.form);
        else if (mode === "auto-submit") document.querySelector("#hister-signin button").click();
    }"""
    with sync_playwright() as pw:
        for engine in ("chromium", "webkit", "firefox"):
            if not os.path.exists(getattr(pw, engine).executable_path):
                print("%s: not installed, skipped (playwright install %s)" % (engine, engine))
                continue
            for case in ("typed", "enter", "manager", "auto-submit", "submit-only"):
                POSTED.clear()
                profile = tempfile.mkdtemp(dir=tmp)
                extension = engine == "chromium" and case in ("auto-submit", "submit-only")
                if extension:
                    ext = os.path.join(tmp, case)
                    ctx = pw.chromium.launch_persistent_context(
                        profile, headless=False, proxy={"server": proxy}, ignore_https_errors=True,
                        args=["--headless=new", "--disable-extensions-except=" + ext, "--load-extension=" + ext])
                    browser = None
                else:
                    browser = getattr(pw, engine).launch(proxy={"server": proxy})
                    ctx = browser.new_context(ignore_https_errors=True)
                page = ctx.new_page()
                page.goto("https://%s/" % SEARCH)
                page.wait_for_selector("#hister-signin")
                if case in ("typed", "enter"):
                    page.fill("#signin-username", "owner")
                    page.fill("#signin-password", "correct horse")
                    if case == "typed":
                        page.click("#hister-signin button")
                    else:
                        page.press("#signin-password", "Enter")
                elif case == "manager":
                    page.evaluate(manager_fill)
                    page.click("#hister-signin button")
                elif not extension:
                    page.evaluate(manager_fill)
                    page.evaluate(native_submit, case)
                if case == "submit-only":
                    page.wait_for_timeout(1500)
                    filled = page.query_selector("#signin-password") and \
                        page.input_value("#signin-password") == "correct horse"
                    ok = page.url.startswith("https://%s/machiya/signin?" % HISTER) and filled
                    where = "stayed on the page, fields filled" if ok else page.url
                else:
                    try:
                        page.wait_for_selector("#signed-in", timeout=8000)
                        ok, where = True, page.url
                    except Exception:
                        ok, where = False, "%s: %s" % (page.url, page.inner_text("main")[:120].replace("\n", " | ")
                                                       if page.query_selector("main") else "")
                if POSTED:
                    ok, where = False, "posted to the helper: %s" % POSTED
                print("%s  [%s] %s  %s" % ("PASS" if ok else "FAIL", engine, case, where), flush=True)
                failures += not ok
                ctx.close()
                if browser:
                    browser.close()
    for s in servers + [front]:
        s.shutdown()
    fake.close()
    return failures


def connect_proxy(port):
    """A CONNECT proxy to the front for *.example.test (WebKit has no host-resolver rules)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)

    def pipe(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def handle(c):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = c.recv(4096)
            if not chunk:
                return c.close()
            head += chunk
        method, target, _ = head.split(b"\r\n")[0].decode().split(" ", 2)
        if method != "CONNECT" or not target.split(":")[0].endswith(".example.test"):
            c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return c.close()
        up = socket.create_connection(("127.0.0.1", port))
        c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        threading.Thread(target=pipe, args=(c, up), daemon=True).start()
        threading.Thread(target=pipe, args=(up, c), daemon=True).start()

    def loop():
        while True:
            c, _ = srv.accept()
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return "http://127.0.0.1:%d" % srv.getsockname()[1]


if __name__ == "__main__":
    sys.exit(main())
