"""hister-login tests: both ports against a fake Hister on a local port (fake_hister.py).
Run: python3 -m unittest discover -s tests   (from stack/hister-login; needs markdown and pyyaml for vaultkit)"""
import http.client
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from urllib.parse import parse_qs, urlencode, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import hister_login as hl                      # noqa: E402
from fake_hister import FakeHister             # noqa: E402

PUBLIC = "https://hister.example.test"
KURA = "https://kura.example.test/n/Note?x=1"
ENV = {"HISTER_LOGIN_PUBLIC_URL": PUBLIC, "MACHIYA_COOKIE_DOMAIN": "example.test",
       "MACHIYA_ROOMS": "kura=https://kura.example.test,niwa=https://niwa.example.test,"
                        "searxng=https://searxng.example.test,hister=https://hister.example.test",
       "HISTER_LOGIN_PROVIDERS": "oidc",
       # 0.5.0: legacy is off by default; most tests here still cover the opted-in old ways (LegacyDefaultTest: none)
       "HISTER_LOGIN_LEGACY": "domain-cookie,hister-token"}


def cookies_of(headers):
    return [v for k, v in headers if k.lower() == "set-cookie"]


def cookie_value(headers, name):
    for c in cookies_of(headers):
        k, _, rest = c.partition("=")
        if k == name:
            return rest.split(";", 1)[0]
    return None


class HelperBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake = FakeHister()
        env = dict(ENV, HISTER_LOGIN_HISTER_URL=self.fake.url, HISTER_LOGIN_DB=os.path.join(self.tmp, "d", "hl.db"),
                   **getattr(self, "EXTRA", {}))
        env = {k: v for k, v in env.items() if v is not None}          # EXTRA's None: the setting left unset
        self.err = io.StringIO()
        with redirect_stderr(self.err):
            self.settings = hl.Settings(env)
            self.login = hl.Login(self.settings)
        self.login.hister.health_at = 0
        self._stderr = sys.stderr
        sys.stderr = self.err
        self.servers = hl.serve(self.login, "127.0.0.1", 0, 0)
        self.pub = self.servers[0].server_address[1]
        self.int = self.servers[1].server_address[1]

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        self.fake.close()
        sys.stderr = self._stderr
        shutil.rmtree(self.tmp)

    def req(self, port, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            r = conn.getresponse()
            return r.status, r.getheaders(), r.read()
        finally:
            conn.close()

    def public(self, method, path, headers=None, body=None):
        return self.req(self.pub, method, path, headers, body)

    def internal(self, method, path, headers=None, body=None):
        return self.req(self.int, method, path, headers, body)

    def sign_in(self, return_to=KURA, session=None):
        session = session or self.fake.signed_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": return_to}),
                                         {"Cookie": "hister=" + session, "User-Agent": "Mozilla/5.0 (iPhone) Safari/1"})
        self.assertEqual(status, 303)
        return session, cookie_value(headers, "machiya_sso"), headers

    def check(self, sid=None, token=None):
        h = {"X-Machiya-Session": sid} if sid is not None else {"X-Access-Token": token}
        status, _, body = self.internal("GET", "/v1/check", h)
        return status, json.loads(body or b"null")

    def age(self, seconds=60):
        self.login.store.q("UPDATE sessions SET verified_at = verified_at - ?", (seconds,))

    def one_connection(self, port, requests):
        """[(status, headers, body)] for requests sent one after another down ONE kept-alive connection."""
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        out = []
        try:
            for method, path, headers, *body in requests:
                conn.request(method, path, body=body[0] if body else None, headers=headers)
                r = conn.getresponse()
                out.append((r.status, dict(r.getheaders()), r.read()))
        finally:
            conn.close()
        return out

    def raw(self, port, data):
        """Everything the server sends back for `data` on one connection, and whether it closed the connection."""
        import socket
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.sendall(data)
        out, closed = b"", False
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    closed = True
                    break
                out += chunk
        except socket.timeout:
            pass
        s.close()
        return out, closed


class HelperTest(HelperBase):
    # -- sign-in

    def test_signin_page_names_the_app_and_never_names_the_password(self):
        _, _, body = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}))
        self.assertIn(b"<h1>Sign In to Kura</h1>", body)
        self.assertIn(b'<span class="word">Machiya</span>', body)       # the header is Machiya's, not Hister's
        self.assertIn(b'autocomplete="current-password"', body)
        self.assertNotIn(b'name="password"', body)                      # a direct submit can't carry it
        self.assertNotIn(b'name="username"', body)
        self.assertNotIn(b"<form", body)                                # nothing on the page posts to the helper
        self.assertIn(b'<div class="group" id="hister-signin" data-next="', body)
        self.assertIn(b'<button type="button">Sign In</button>', body)
        self.assertNotIn(b"direct=1", body)
        _, _, plain = self.public("GET", "/machiya/signin")
        self.assertIn(b"<h1>Sign In</h1>", plain)                       # no return: no app named

    def test_a_direct_submit_comes_back_to_the_form(self):
        # the 0.4.0 page's direct submit (a cached copy; today's page has no form): the POST has no fields, and the
        # page comes back with what to do, the return address kept, instead of a dead end
        path = "/machiya/signin?" + urlencode({"return": KURA, "direct": "1"})
        status, headers, body = self.public("POST", path, {"Origin": PUBLIC,
                                                            "Content-Type": "application/x-www-form-urlencoded"}, b"")
        self.assertEqual(status, 200)
        self.assertIn(b'id="hister-signin"', body)
        self.assertIn(b"Press Sign In to finish signing in.", body)
        self.assertIn(b"<h1>Sign In to Kura</h1>", body)
        self.assertNotIn(b"Only an app", body)

    def test_signin_page_when_not_signed_in(self):
        status, headers, body = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}))
        self.assertEqual(status, 200)
        self.assertIn(b'id="hister-signin"', body)
        self.assertIn(b'href="/api/oauth?provider=oidc"', body)
        self.assertIn(b"/machiya/static/signin.js", body)
        self.assertIn(b"/machiya/static/machiya.css", body)
        self.assertNotIn(b'"/static/', body)                           # Hister owns /static/ on this host
        self.assertNotIn(b"<script>", body)                            # no inline script (CSP script-src 'self')
        ret = cookie_value(headers, "__Host-machiya_return")
        self.assertTrue(ret)
        c = [x for x in cookies_of(headers) if x.startswith("__Host-machiya_return=")][0]
        self.assertIn("Max-Age=600", c)
        self.assertNotIn("Domain", c)                                   # host-only
        csp = dict((k.lower(), v) for k, v in headers)["content-security-policy"]
        self.assertIn("script-src 'self'", csp)

    def test_signin_with_hister_session_issues_id(self):
        session, sid, headers = self.sign_in()
        self.assertEqual(dict(headers)["Location"], KURA)
        self.assertTrue(sid.startswith("mhs_") and hl.SID_RE.match(sid))
        c = [x for x in cookies_of(headers) if x.startswith("machiya_sso=")][0]
        for attr in ("Domain=example.test", "Secure", "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=15552000"):
            self.assertIn(attr, c)
        status, data = self.check(sid)
        self.assertEqual((status, data["username"], data["user_id"], data["via"], data["kind"]),
                         (200, "owner", 1, "session", "browser"))
        rows = self.login.store.q("SELECT label FROM sessions")
        self.assertEqual(rows[0][0], "Safari on iPhone")

    def test_signin_reuses_the_browsers_id(self):
        session, sid, _ = self.sign_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}),
                                         {"Cookie": "hister=%s; machiya_sso=%s" % (session, sid)})
        self.assertEqual(status, 303)
        self.assertIsNone(cookie_value(headers, "machiya_sso"))
        self.assertEqual(self.login.store.count(), 1)

    def test_open_redirects_refused(self):
        session = self.fake.signed_in()
        for bad in ("https://evil.example/", "https://searxng.example.test/", "http://kura.example.test/",
                    "//kura.example.test/", "https://kura.example.test.evil.example/", "javascript:alert(1)",
                    "https://kura.example.test@evil.example/", "shiori://signed-in"):
            status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": bad}),
                                             {"Cookie": "hister=" + session})
            self.assertEqual((status, dict(headers)["Location"]), (303, PUBLIC + "/"), bad)
            status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": bad}))
            self.assertIsNone(cookie_value(headers, "__Host-machiya_return") or None, bad)
        # the app flow takes the app's scheme only, never a page
        status, _, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA, "app": "1"}),
                                   {"Cookie": "hister=" + session})
        self.assertEqual(status, 400)
        # a forged machiya_return is checked again when used
        forged = hl.b64e(json.dumps({"r": "https://evil.example/", "a": 0}).encode())
        self.assertEqual(self.login.read_return("__Host-machiya_return=" + forged), (None, False, ""))

    def test_return_hosts_default(self):
        self.assertEqual(self.settings.return_hosts, ["hister.example.test", "kura.example.test",
                                                      "niwa.example.test"])

    def test_signin_when_hister_down_or_users_off(self):
        self.fake.fail = True
        status, _, body = self.public("GET", "/machiya/signin")
        self.assertEqual(status, 503)
        self.assertIn(b"Sign-In Is Unavailable", body)
        self.fake.fail = False
        self.fake.user_handling = False
        self.login.hister.health_at = 0
        status, _, body = self.public("GET", "/machiya/signin")
        self.assertEqual(status, 503)
        self.assertIn(b"switched off", body)

    # -- /v1/check

    def test_check_caches_30s_then_asks_hister(self):
        session, sid, _ = self.sign_in()
        before = self.fake.calls["/api/profile"]
        self.check(sid)
        self.check(sid)
        self.assertEqual(self.fake.calls["/api/profile"], before)       # verified at sign-in, < 30 s ago
        self.age()
        self.assertEqual(self.check(sid)[0], 200)
        self.assertEqual(self.fake.calls["/api/profile"], before + 1)

    def test_hister_ui_signout_is_noticed(self):
        session, sid, _ = self.sign_in()
        _, sid2, _ = self.sign_in("https://niwa.example.test/", session)   # a second id on the same session
        self.fake.sessions.pop(session)                                  # signed out in Hister's own UI
        self.assertEqual(self.check(sid)[0], 200)                        # still within 30 s
        self.age()
        self.assertEqual(self.check(sid), (401, {"reason": "signed-out"}))
        self.assertEqual(self.login.store.count(), 0)                    # every id on that session went
        self.assertEqual(self.check(sid2)[0], 401)

    def test_check_unknown_and_malformed(self):
        self.assertEqual(self.check("mhs_" + "x" * 43)[0], 401)
        self.assertEqual(self.check("junk")[0], 401)
        status, _, _ = self.internal("GET", "/v1/check")
        self.assertEqual(status, 400)
        status, _, _ = self.internal("GET", "/v1/check", {"X-Machiya-Session": "a", "X-Access-Token": "b"})
        self.assertEqual(status, 400)

    def test_tokens(self):
        self.assertEqual(self.check(token="tok-owner"), (200, {"username": "owner", "user_id": 1, "via": "token",
                                                              "kind": "hister-token", "room": None, "prefs": {}}))
        self.assertEqual(self.check(token="tok-other")[1]["username"], "other")
        self.assertEqual(self.check(token="nope"), (401, {"reason": "signed-out"}))
        n = self.fake.calls["/api/profile"]
        self.check(token="tok-owner")
        self.assertEqual(self.fake.calls["/api/profile"], n)             # cached

    def test_unavailable_and_user_handling_off(self):
        session, sid, _ = self.sign_in()
        self.age()
        self.fake.fail = True
        self.assertEqual(self.check(sid), (503, {"reason": "hister-unavailable"}))
        self.assertEqual(self.login.store.count(), 1)                    # unavailable never deletes
        status, _, body = self.internal("GET", "/healthz")
        self.assertEqual((status, json.loads(body)["hister"]), (503, "down"))
        self.fake.fail = False
        self.fake.user_handling = False
        self.login.hister.health_at = 0
        self.assertEqual(self.check(sid), (503, {"reason": "user-handling-off"}))
        self.assertEqual(self.check(token="tok-owner"), (503, {"reason": "user-handling-off"}))
        status, _, body = self.public("GET", "/machiya/healthz")             # the probe's: the helper works
        self.assertEqual((status, json.loads(body)["ok"], json.loads(body)["hister"]), (200, True, "user-handling-off"))
        self.assertIn("HISTER USER HANDLING IS OFF", self.err.getvalue())

    def test_public_healthz_is_the_helpers_own(self):
        self.fake.fail = True                                                # Hister down: still 200, degraded
        status, _, body = self.public("GET", "/machiya/healthz")
        self.assertEqual((status, json.loads(body)["ok"], json.loads(body)["hister"]), (200, True, "down"))
        status, _, _ = self.internal("GET", "/healthz")                      # the rooms' flag: 503
        self.assertEqual(status, 503)
        self.login.store.db.close()                                          # the helper's own state fails: 503
        status, _, body = self.public("GET", "/machiya/healthz")
        self.assertEqual((status, json.loads(body)), (503, {"ok": False, "state": "error", "version": hl.VERSION}))

    def test_healthz_ok(self):
        status, _, body = self.internal("GET", "/healthz")
        data = json.loads(body)
        self.assertEqual((status, data["ok"], data["hister"]), (200, True, "ok"))
        self.assertEqual(self.fake.calls["/api/profile"], 1)            # anonymous: 403 means users are on
        self.internal("GET", "/healthz")
        self.assertEqual(self.fake.calls["/health"], 1)                  # cached 10 s

    def test_expiry_and_cap(self):
        session, sid, _ = self.sign_in()
        self.login.store.q("UPDATE sessions SET expires_at = ?", (hl.now() - 1,))
        self.assertEqual(self.check(sid)[0], 401)
        self.assertEqual(self.login.store.count(), 0)
        session, sid, _ = self.sign_in()
        self.login.store.q("UPDATE sessions SET created_at = ?, verified_at = 0", (hl.now() - 179 * 86400,))
        self.assertEqual(self.check(sid)[0], 200)
        exp = self.login.store.q("SELECT expires_at, created_at FROM sessions")[0]
        self.assertEqual(exp[0], exp[1] + 180 * 86400)                   # the 180-day cap, not 30 more days

    # -- sign-out

    def test_v1_signout_ends_everywhere(self):
        session, sid, _ = self.sign_in()
        _, sid2, _ = self.sign_in("https://niwa.example.test/", session)
        status, _, _ = self.internal("POST", "/v1/signout", {"X-Machiya-Session": sid, "Content-Length": "0"})
        self.assertEqual(status, 204)
        self.assertNotIn(session, self.fake.sessions)                    # Hister's own session ended
        self.assertEqual((self.check(sid)[0], self.check(sid2)[0]), (401, 401))
        status, _, _ = self.internal("POST", "/v1/signout", {"X-Machiya-Session": sid, "Content-Length": "0"})
        self.assertEqual(status, 204)                                    # again: nothing to do

    def test_signout_while_hister_down_is_retried(self):
        session, sid, _ = self.sign_in()
        self.fake.fail = True
        self.internal("POST", "/v1/signout", {"X-Machiya-Session": sid, "Content-Length": "0"})
        self.assertEqual(self.check(sid)[0], 401)                        # the rooms see it at once
        self.assertEqual(len(self.login.store.pending()), 1)
        self.fake.fail = False
        self.login.retry_pending()
        self.assertEqual(self.login.store.pending(), [])
        self.assertNotIn(session, self.fake.sessions)

    def test_public_signout(self):
        session, sid, _ = self.sign_in()
        cookie = "hister=%s; machiya_sso=%s" % (session, sid)
        status, _, _ = self.public("POST", "/machiya/signout", {"Cookie": cookie, "Content-Length": "0",
                                                                "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.public("POST", "/machiya/signout", {"Cookie": cookie, "Content-Length": "0",
                                                                "Origin": "https://kura.example.test"})
        self.assertEqual(status, 403)                                    # a sibling site: same-site, not same-origin
        status, headers, _ = self.public("POST", "/machiya/signout", {"Cookie": cookie, "Content-Length": "0",
                                                                      "Origin": PUBLIC})
        self.assertEqual((status, dict(headers)["Location"]), (303, "/machiya/signed-out"))
        cs = cookies_of(headers)
        self.assertTrue(any(c.startswith("machiya_sso=;") and "Domain=example.test" in c and "Max-Age=0" in c
                            for c in cs))
        self.assertTrue(any(c.startswith("hister=;") for c in cs))
        self.assertNotIn(session, self.fake.sessions)
        self.assertEqual(self.check(sid)[0], 401)

    def test_app_signout_by_bearer(self):
        session = self.fake.signed_in()
        _, _, body = self.public("POST", "/machiya/api/app-session", {"Content-Type": "application/json"},
                                 json.dumps({"hister": session, "label": "iPhone"}))
        sid = json.loads(body)["sid"]
        status, _, _ = self.public("POST", "/machiya/signout", {"Authorization": "Bearer " + sid,
                                                                "Content-Length": "0"})
        self.assertEqual(status, 204)
        self.assertNotIn(session, self.fake.sessions)

    # -- nginx

    def test_nginx_takes_no_helper_id(self):
        """0.5.0: /v1/nginx hands Hister's session only to a hosted page's own room session (RoomSessionTest); a
        browser's or an app's helper id gets nothing, with or without a room named (here no proxied origin at all)."""
        session, sid, _ = self.sign_in()
        app = self.login.store.create(self.fake.signed_in(), "owner", 1, "app", "iPhone")
        for h in ({"X-Machiya-Session": sid}, {"X-Machiya-Session": app},
                  {"X-Machiya-Session": sid, "X-Machiya-Room": "https://kura.example.test"},
                  {"X-Machiya-Session": ""}, {}):
            status, headers, _ = self.internal("GET", "/v1/nginx", h)
            self.assertEqual(status, 401, h)
            self.assertNotIn("X-Hister-Cookie", dict(headers), h)

    def test_internal_paths_not_public(self):
        session, sid, _ = self.sign_in()
        for path in ("/v1/check", "/v1/nginx", "/healthz"):
            self.assertEqual(self.public("GET", path, {"X-Machiya-Session": sid})[0], 404, path)

    # -- the OAuth callback shim

    def test_callback_shim_returns_to_the_room(self):
        anon, state = self.fake.oauth_started()
        ret = hl.b64e(json.dumps({"r": KURA, "a": 0}).encode())
        status, headers, _ = self.public("GET", "/api/oauth/callback?provider=oidc&code=c&state=" + state,
                                         {"Cookie": "hister=%s; __Host-machiya_return=%s; machiya_theme=night" % (anon, ret)})
        self.assertEqual(status, 302)
        self.assertEqual(dict(headers)["Location"], KURA)
        histers = [c for c in cookies_of(headers) if c.startswith("hister=")]
        self.assertEqual(len(histers), 2)                                # Hister's own Set-Cookies, passed through
        new = histers[-1].split(";")[0].split("=", 1)[1]
        self.assertNotIn("Domain", histers[-1])
        sid = cookie_value(headers, "machiya_sso")
        self.assertEqual(self.check(sid)[1]["username"], "owner")
        self.assertEqual(self.login.store.q("SELECT hister_session FROM sessions")[0][0], new)
        self.assertTrue(any(c.startswith("__Host-machiya_return=;") for c in cookies_of(headers)))

    def test_callback_shim_without_return_keeps_histers_location(self):
        anon, state = self.fake.oauth_started()
        status, headers, _ = self.public("GET", "/api/oauth/callback?provider=oidc&code=c&state=" + state,
                                         {"Cookie": "hister=" + anon})
        self.assertEqual((status, dict(headers)["Location"]), (302, "https://hister.example.test/"))
        self.assertTrue(cookie_value(headers, "machiya_sso"))

    def test_callback_shim_passes_errors_through(self):
        status, headers, body = self.public("GET", "/api/oauth/callback?provider=oidc&code=c&state=bad")
        self.assertEqual((status, body), (400, b"invalid oauth state"))
        self.assertIsNone(cookie_value(headers, "machiya_sso"))

    def test_callback_app_flow(self):
        anon, state = self.fake.oauth_started()
        ret = hl.b64e(json.dumps({"r": "shiori://signed-in", "a": 1}).encode())
        status, headers, _ = self.public("GET", "/api/oauth/callback?provider=oidc&code=c&state=" + state,
                                         {"Cookie": "hister=%s; __Host-machiya_return=%s" % (anon, ret)})
        loc = urlsplit(dict(headers)["Location"])
        self.assertEqual((loc.scheme, loc.netloc), ("shiori", "signed-in"))
        frag = {k: v[0] for k, v in parse_qs(loc.fragment).items()}
        self.assertEqual(self.check(frag["sid"])[1]["kind"], "app")
        self.assertIn(frag["hister"], self.fake.sessions)
        self.assertIsNone(cookie_value(headers, "machiya_sso"))          # the app holds the id, not a cookie

    # -- the automatic sign-in (MACHIYA_SIGNIN_PROVIDER in the rooms: provider=oidc&auto=1)

    def test_auto_signin_goes_through_the_provider(self):
        q = urlencode({"return": KURA, "provider": "oidc", "auto": "1"})
        status, headers, _ = self.public("GET", "/machiya/signin?" + q)
        self.assertEqual((status, dict(headers)["Location"]), (303, "/api/oauth?provider=oidc"))
        self.assertTrue(cookie_value(headers, "__Host-machiya_return"))

    def test_auto_signin_after_a_deliberate_signout_shows_the_page(self):
        session, sid, _ = self.sign_in()
        status, headers, _ = self.public("POST", "/machiya/signout", {
            "Cookie": "hister=%s; machiya_sso=%s" % (session, sid), "Origin": PUBLIC, "Content-Length": "0"})
        marker = [c for c in cookies_of(headers) if c.startswith("__Host-machiya_sso_out=1")]
        self.assertEqual(len(marker), 1)
        self.assertNotIn("Domain", marker[0])                    # 0.3.0: host-only (the helper remembers ended ids)
        q = urlencode({"return": KURA, "provider": "oidc", "auto": "1"})
        status, headers, body = self.public("GET", "/machiya/signin?" + q, {"Cookie": "__Host-machiya_sso_out=1"})
        self.assertEqual(status, 200)                                       # the page, not straight back in
        self.assertIn(b'id="hister-signin"', body)
        self.assertIn(b'href="/api/oauth?provider=oidc"', body)            # one tap still works
        # a tap (provider= without auto, Shiori's button) is a choice: it goes through
        q = urlencode({"return": KURA, "provider": "oidc"})
        self.assertEqual(self.public("GET", "/machiya/signin?" + q, {"Cookie": "__Host-machiya_sso_out=1"})[0], 303)
        # signing in again clears the marker, so the next time is automatic again
        session = self.fake.signed_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}),
                                         {"Cookie": "hister=%s; __Host-machiya_sso_out=1" % session})
        self.assertEqual(status, 303)
        cleared = [c for c in cookies_of(headers) if c.startswith("__Host-machiya_sso_out=;")]
        self.assertTrue(cleared and all("Max-Age=0" in c for c in cleared))

    def test_sessions_page_signout_sets_the_marker(self):
        session, sid, _ = self.sign_in()
        status, headers, _ = self.public("POST", "/machiya/sessions", {
            "Cookie": "machiya_sso=" + sid, "Origin": PUBLIC, "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": "6"}, b"do=all")
        self.assertEqual(status, 303)
        self.assertTrue(any(c.startswith("__Host-machiya_sso_out=1") for c in cookies_of(headers)))

    def test_failed_round_trip_lands_on_the_page_once(self):
        """The provider's round trip didn't finish (a bad state, Hister refusing): the page with a message and a short
        marker, so the next automatic try shows the page instead of looping."""
        ret = hl.b64e(json.dumps({"r": KURA, "a": 0}).encode())
        status, headers, body = self.public("GET", "/api/oauth/callback?provider=oidc&code=c&state=bad",
                                            {"Cookie": "__Host-machiya_return=" + ret})
        self.assertEqual(status, 401)
        self.assertIn(b"didn&#x27;t work", body)
        self.assertIn(b'id="hister-signin"', body)
        failed = [c for c in cookies_of(headers) if c.startswith("__Host-machiya_sso_out=failed")]
        self.assertTrue(failed and "Max-Age=600" in failed[0] and "Domain" not in failed[0])
        self.assertTrue(any(c.startswith("__Host-machiya_return=;") for c in cookies_of(headers)))
        self.assertIsNone(cookie_value(headers, "machiya_sso"))
        q = urlencode({"return": KURA, "provider": "oidc", "auto": "1"})
        self.assertEqual(self.public("GET", "/machiya/signin?" + q, {"Cookie": "__Host-machiya_sso_out=failed"})[0], 200)

    def test_signin_straight_to_a_provider(self):
        """Shiori's "Sign In with Tailscale" (0.1.3): ?provider=oidc sets the return cookie and goes to Hister's OAuth."""
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": "shiori://signed-in",
                                                                                "app": "1", "provider": "oidc"}),
                                         {"Sec-Fetch-Site": "none"})                 # the app's own web session
        self.assertEqual(status, 303)
        self.assertEqual(dict(headers)["Location"], "/api/oauth?provider=oidc")
        self.assertTrue(cookie_value(headers, "__Host-machiya_return"))
        status, headers, body = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA, "provider": "github"}))
        self.assertEqual(status, 200)                                  # a provider not offered: the page, as before
        self.assertIn(b'id="hister-signin"', body)
        session = self.fake.signed_in()                                # already signed in: finishes at once
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": "shiori://signed-in",
                                                                                "app": "1", "provider": "oidc"}),
                                         {"Cookie": "hister=" + session, "Sec-Fetch-Site": "none"})
        self.assertTrue(dict(headers)["Location"].startswith("shiori://signed-in#sid=mhs_"))

    def test_app_flow_from_signin(self):
        session = self.fake.signed_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": "shiori://signed-in",
                                                                                "app": "1"}),
                                         {"Cookie": "hister=" + session, "Sec-Fetch-Site": "none"})
        self.assertEqual(status, 303)
        self.assertTrue(dict(headers)["Location"].startswith("shiori://signed-in#sid=mhs_"))

    def test_app_signin_never_finishes_on_a_cross_site_get(self):
        """0.2.1 (sweep LEAD-3): any web page could navigate the owner to ?app=1&return=shiori://… with Hister's Lax
        cookie and get both secrets sent to whatever app owns the scheme. A navigation that isn't the app's own (its
        web session: Sec-Fetch-Site none) or this page's (same-origin) gets a confirmation page instead: no app
        session, no return cookie, no redirect."""
        session = self.fake.signed_in()
        q = "/machiya/signin?" + urlencode({"return": "shiori://signed-in", "app": "1"})
        before = self.login.store.count()
        for site in ("cross-site", "same-site", None):
            h = {"Cookie": "hister=" + session}
            if site:
                h["Sec-Fetch-Site"] = site
            status, headers, body = self.public("GET", q, h)
            self.assertEqual(status, 200, site)
            self.assertNotIn("Location", dict(headers), site)
            self.assertIsNone(cookie_value(headers, "__Host-machiya_return"), site)
            self.assertIn(b'<form class="group" method="post" action="/machiya/signin">', body)
            self.assertIn(b'name="return" value="shiori://signed-in"', body)
            self.assertNotIn(b"mhs_", body)
            self.assertNotIn(session.encode(), body)
        # nor straight on to the provider (that would set the return cookie and finish in the callback)
        status, headers, _ = self.public("GET", q + "&provider=oidc", {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 200)
        self.assertIsNone(cookie_value(headers, "__Host-machiya_return"))
        self.assertEqual(self.login.store.count(), before)                 # no stray "Shiori app" session
        # the app's own web session and this page's own navigation finish as before
        for site in ("none", "same-origin"):
            status, headers, _ = self.public("GET", q, {"Cookie": "hister=" + session, "Sec-Fetch-Site": site})
            self.assertEqual(status, 303, site)
            self.assertTrue(dict(headers)["Location"].startswith("shiori://signed-in#sid=mhs_"), site)

    def test_app_signin_confirmed_by_a_same_origin_post(self):
        session = self.fake.signed_in()
        form = urlencode({"return": "shiori://signed-in", "app": "1"}).encode()

        def post(extra, body=form):
            h = {"Cookie": "hister=" + session, "Content-Type": "application/x-www-form-urlencoded",
                 "Content-Length": str(len(body))}
            h.update(extra)
            return self.public("POST", "/machiya/signin", h, body)
        before = self.login.store.count()
        for bad in ({}, {"Origin": "https://evil.example"}, {"Origin": "null"}, {"Referer": "https://evil.example/x"}):
            status, headers, _ = post(bad)
            self.assertEqual(status, 403, bad)
            self.assertNotIn("Location", dict(headers))
        self.assertEqual(self.login.store.count(), before)
        status, headers, _ = post({"Origin": PUBLIC})
        self.assertEqual(status, 303)
        loc = urlsplit(dict(headers)["Location"])
        self.assertEqual((loc.scheme, loc.netloc), ("shiori", "signed-in"))
        frag = {k: v[0] for k, v in parse_qs(loc.fragment).items()}
        self.assertEqual(self.check(frag["sid"])[1]["kind"], "app")
        # signed out of Hister: the confirmed POST goes on to the sign-in (the return cookie set now)
        status, headers, body = self.public("POST", "/machiya/signin", {
            "Origin": PUBLIC, "Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(form))}, form)
        self.assertEqual(status, 200)
        self.assertIn(b'id="hister-signin"', body)
        self.assertTrue(cookie_value(headers, "__Host-machiya_return"))
        # only the app flow is confirmed this way; a bad return is still refused
        bad = urlencode({"return": "shiori://anything", "app": "1"}).encode()
        self.assertEqual(post({"Origin": PUBLIC}, bad)[0], 400)

    def test_app_return_is_exactly_signed_in(self):
        """LEAD-3: the app flow returns only to shiori://signed-in (the address HisterKit sends), never another path."""
        session = self.fake.signed_in()
        for bad in ("shiori://anything", "shiori://signed-in/x", "shiori://signed-in?x=1", "shiori:signed-in",
                    "shiori://evil@signed-in", "SHIORI://anything"):
            status, _, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": bad, "app": "1"}),
                                       {"Cookie": "hister=" + session, "Sec-Fetch-Site": "none"})
            self.assertEqual(status, 400, bad)
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": "shiori://signed-in",
                                                                                "app": "1"}),
                                         {"Cookie": "hister=" + session, "Sec-Fetch-Site": "none"})
        self.assertEqual(status, 303)

    def test_try_again_link_is_this_path(self):
        """0.2.1 (sweep LEAD-6): routing reads the path of the target, so GET //evil.example/machiya/signin reaches the
        sign-in; while Hister is down its Try Again link was //evil.example/…, another site."""
        # http.server on Python 3.12+ already folds a leading // (so the server answers 404 there); the link is built
        # from the path alone anyway, whatever the server hands over
        for target, want in (("//evil.example/machiya/signin?return=x", "/machiya/signin?return=x"),
                             ("/\\evil.example/x", "/machiya/signin"), ("/machiya/signin", "/machiya/signin")):
            self.assertEqual(hl.Public.again(type("R", (), {"path": target})()), want, target)
        self.fake.fail = True
        status, _, body = self.public("GET", "/machiya/signin?return=x")
        self.assertEqual(status, 503)
        self.assertIn(b'href="/machiya/signin?return=x"', body)

    # -- app sessions

    def test_app_session(self):
        session = self.fake.signed_in()
        h = {"Content-Type": "application/json"}
        status, _, body = self.public("POST", "/machiya/api/app-session", h,
                                      json.dumps({"hister": session, "label": "Mac\x00 app"}))
        data = json.loads(body)
        self.assertEqual((status, data["username"]), (200, "owner"))
        self.assertEqual(self.check(data["sid"])[1]["kind"], "app")
        self.assertEqual(self.login.store.q("SELECT label FROM sessions")[0][0], "Mac app")
        self.assertEqual(self.public("POST", "/machiya/api/app-session", h,
                                     json.dumps({"hister": "A" * 43}))[0], 401)
        self.assertEqual(self.public("POST", "/machiya/api/app-session", h, json.dumps({"hister": "x"}))[0], 400)
        self.assertEqual(self.public("POST", "/machiya/api/app-session", {"Content-Type": "text/plain"},
                                     json.dumps({"hister": session}))[0], 415)
        self.assertEqual(self.public("POST", "/machiya/api/app-session", h, "{" * 5000)[0], 413)

    # -- the sessions page

    def test_sessions_page(self):
        status, headers, _ = self.public("GET", "/machiya/sessions")
        self.assertEqual(status, 303)
        self.assertTrue(dict(headers)["Location"].startswith("/machiya/signin?return="))
        session, sid, _ = self.sign_in()
        other = self.fake.signed_in()
        self.public("POST", "/machiya/api/app-session", {"Content-Type": "application/json"},
                    json.dumps({"hister": other, "label": "iPhone app"}))
        cookie = {"Cookie": "machiya_sso=" + sid}
        status, _, body = self.public("GET", "/machiya/sessions", cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"iPhone app", body)
        self.assertIn(b"this device", body)
        self.assertNotIn(session.encode(), body)
        rowid = [r for r in self.login.store.of_user(1) if r["kind"] == "app"][0]["rowid"]
        form = urlencode({"do": "one", "id": rowid})
        post = dict(cookie, Origin=PUBLIC, **{"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(self.public("POST", "/machiya/sessions", dict(post, Origin="https://evil.example"), form)[0],
                         403)
        status, headers, _ = self.public("POST", "/machiya/sessions", post, form)
        self.assertEqual((status, dict(headers)["Location"]), (303, "/machiya/sessions?done=one"))
        self.assertNotIn(other, self.fake.sessions)
        self.assertIn(session, self.fake.sessions)
        status, headers, _ = self.public("POST", "/machiya/sessions", post, urlencode({"do": "all"}))
        self.assertEqual(dict(headers)["Location"], "/machiya/signed-out")
        self.assertEqual(self.login.store.count(), 0)
        self.assertNotIn(session, self.fake.sessions)

    # -- state at rest

    def test_state_at_rest(self):
        session, sid, _ = self.sign_in()
        path = self.settings.db
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        raw = b""
        for suffix in ("", "-wal"):
            if os.path.exists(path + suffix):
                with open(path + suffix, "rb") as f:
                    raw += f.read()
        self.assertNotIn(sid.encode(), raw)                              # the id is stored only as its hash
        self.assertIn(hl.sha(sid).encode(), raw)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        db.close()

    def test_static_and_404(self):
        self.assertEqual(self.public("GET", "/machiya/static/machiya.css")[0], 200)
        self.assertEqual(self.public("GET", "/machiya/static/signin.js")[0], 200)
        self.assertEqual(self.public("GET", "/machiya/static/../hister_login.py")[0], 404)
        self.assertEqual(self.public("GET", "/machiya/nope")[0], 404)
        self.assertEqual(self.public("GET", "/machiya/")[0], 303)

    def test_logs_no_secrets(self):
        session, sid, _ = self.sign_in()
        self.check(sid)
        self.internal("GET", "/v1/nginx", {"X-Machiya-Session": sid})
        log = self.err.getvalue()
        for secret in (sid, session, "tok-owner", "kura.example.test/n/Note"):
            self.assertNotIn(secret, log)

    # -- keep-alive (0.1.2): Tailscale Serve sends different people's requests down one connection

    def test_keep_alive_check_answers_each_request_alone(self):
        _, sid, _ = self.sign_in()
        _, other, _ = self.sign_in(session=self.fake.signed_in("other"))
        sess = lambda v: {"X-Machiya-Session": v}
        tok = lambda v: {"X-Access-Token": v}
        got = self.one_connection(self.int, [
            ("GET", "/v1/check", {}),                               # no credential: 400
            ("GET", "/v1/check", sess(sid)),                        # the owner
            ("GET", "/v1/check", sess("mhs_" + "x" * 43)),          # an unknown id
            ("GET", "/v1/check", sess(other)),                      # another person
            ("GET", "/v1/check", tok("tok-owner")),                 # the owner's token
            ("GET", "/v1/check", tok("nope")),                      # a bad token
            ("GET", "/v1/check", tok("tok-other")),                 # another person's token
            ("GET", "/v1/check", {}),                               # nobody again
            ("GET", "/v1/nginx", sess(sid)),                        # nginx: a helper id gets no Hister cookie
            ("GET", "/v1/nginx", {}),                               # ... nobody: none
            ("GET", "/v1/nginx", sess(other)),
            ("GET", "/v1/nginx", sess("junk")),
        ])
        self.assertEqual([g[0] for g in got], [400, 200, 401, 200, 200, 401, 200, 400, 401, 401, 401, 401])
        names = [json.loads(g[2]).get("username") if g[2] else None for g in got[:8]]
        self.assertEqual(names, [None, "owner", None, "other", "owner", None, "other", None])
        self.assertTrue(all("X-Hister-Cookie" not in g[1] and "X-Hister-User" not in g[1] for g in got[8:]))

    def test_keep_alive_public_pages_answer_each_request_alone(self):
        _, sid, _ = self.sign_in()
        _, other, _ = self.sign_in(session=self.fake.signed_in("other"))
        sso = lambda v: {"Cookie": "machiya_sso=" + v}
        got = self.one_connection(self.pub, [
            ("GET", "/machiya/sessions", {}),                       # nobody: to sign in
            ("GET", "/machiya/sessions", sso(sid)),                 # the owner's sessions
            ("GET", "/machiya/sessions", {}),                       # nobody again
            ("GET", "/machiya/sessions", sso("mhs_" + "x" * 43)),   # an unknown id
            ("GET", "/machiya/sessions", sso(other)),               # another person's sessions
            ("GET", "/machiya/sessions", sso("junk")),
        ])
        self.assertEqual([g[0] for g in got], [303, 200, 303, 303, 200, 303])
        self.assertIn(b"Signed In as owner", got[1][2])
        self.assertIn(b"Signed In as other", got[4][2])
        self.assertNotIn(b"owner", got[4][2])
        self.assertTrue(all(g[1]["Location"].startswith("/machiya/signin?") for g in (got[0], got[2], got[3], got[5])))

    def test_keep_alive_unread_body_never_becomes_a_request(self):
        """A body the helper doesn't read (a GET's, a 404 POST's) closes the connection: kept, its bytes would be the
        next request, one that never went through Serve."""
        _, sid, _ = self.sign_in()
        smuggled = b"GET /v1/nginx HTTP/1.1\r\nHost: x\r\nX-Machiya-Session: %s\r\n\r\n" % sid.encode()
        for port, head in ((self.int, b"GET /v1/check HTTP/1.1\r\nHost: x\r\nX-Access-Token: nope\r\n"),
                           (self.int, b"POST /v1/nope HTTP/1.1\r\nHost: x\r\n"),
                           (self.pub, b"GET /machiya/healthz HTTP/1.1\r\nHost: x\r\n"),
                           (self.pub, b"POST /machiya/nope HTTP/1.1\r\nHost: x\r\n")):
            out, closed = self.raw(port, head + b"Content-Length: %d\r\n\r\n" % len(smuggled) + smuggled)
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, head)
            self.assertTrue(closed, head)
            self.assertIn(b"\r\nConnection: close\r\n", out)
            self.assertNotIn(b"X-Hister-Cookie", out)
        # a body that was read keeps the connection
        got = self.one_connection(self.int, [("POST", "/v1/signout", {"X-Machiya-Session": "x"}, b"{}"),
                                             ("GET", "/v1/check", {"X-Machiya-Session": sid})])
        self.assertEqual([g[0] for g in got], [204, 200])


KURA_O, NIWA_O, SEARCH_O = "https://kura.example.test", "https://niwa.example.test", "https://search.example.test"


class RoomSessionTest(HelperBase):
    """0.3.0: the helper's own cookie host-only; a one-time code per room; room sessions bound to their room and to
    their helper session; room tokens; HISTER_LOGIN_LEGACY; the hosted pages' hosts (proxied origins)."""
    EXTRA = {"HISTER_LOGIN_PROXIED_ORIGINS": SEARCH_O, "MACHIYA_SIGNIN_PROVIDER": "oidc",
             "MACHIYA_ROOMS": ENV["MACHIYA_ROOMS"] + ",machiya=https://machiya.example.test"}

    def trip(self, ret=KURA, nonce=None, session=None, own=None):
        """A room's trip to the helper with a state: -> (session, own id, nonce, code, headers)."""
        session = session or self.fake.signed_in()
        nonce = nonce or hl.b64e(os.urandom(32))
        cookie = "hister=" + session + ("; __Host-machiya_sso=" + own if own else "")
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode(
            {"return": ret, "state": hl.histerauth.state_hash(nonce)}), {"Cookie": cookie})
        self.assertEqual(status, 303)
        loc = urlsplit(dict(headers)["Location"])
        code = parse_qs(loc.query).get("code", [None])[0]
        return session, cookie_value(headers, "__Host-machiya_sso") or own, nonce, code, headers

    def redeem(self, code, room=KURA_O, nonce="x" * 43, extra=None):
        h = {"X-Machiya-Code": code or "", "X-Machiya-Room": room, "X-Machiya-State": nonce, "Content-Length": "0"}
        h.update(extra or {})
        status, _, body = self.internal("POST", "/v1/redeem", h)
        return status, json.loads(body or b"null")

    def room_check(self, value, room=KURA_O):
        h = {"X-Machiya-Session": value}
        if room is not None:
            h["X-Machiya-Room"] = room
        status, _, body = self.internal("GET", "/v1/check", h)
        return status, json.loads(body or b"null")

    def test_own_cookie_is_host_only(self):
        _, sid, _, _, headers = self.trip()
        own = [c for c in cookies_of(headers) if c.startswith("__Host-machiya_sso=")][0]
        for attr in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
            self.assertIn(attr, own)
        self.assertNotIn("Domain", own)
        legacy = [c for c in cookies_of(headers) if c.startswith("machiya_sso=")]      # legacy on (the default):
        self.assertTrue(legacy and "Domain=example.test" in legacy[0])                 # the old copy too, same id
        self.assertEqual(cookie_value(headers, "machiya_sso"), sid)

    def test_code_goes_to_the_rooms_callback_and_works_once(self):
        _, sid, nonce, code, headers = self.trip()
        loc = urlsplit(dict(headers)["Location"])
        self.assertEqual((loc.scheme, loc.netloc, loc.path), ("https", "kura.example.test", "/machiya/callback"))
        self.assertTrue(hl.histerauth.CODE_RE.match(code))
        self.assertNotIn(nonce, dict(headers)["Location"])
        status, data = self.redeem(code, nonce=nonce)
        self.assertEqual((status, data["return"], data["username"]), (200, KURA, "owner"))
        self.assertTrue(hl.histerauth.RSID_RE.match(data["session"]))
        self.assertTrue(86400 * 170 < data["max_age"] <= 86400 * 180)
        self.assertEqual(self.redeem(code, nonce=nonce), (401, {"reason": "bad-code"}))       # once
        status, info = self.room_check(data["session"])
        self.assertEqual((status, info["kind"], info["room"], info["username"]), (200, "room", KURA_O, "owner"))
        self.assertEqual(self.room_check(data["session"], NIWA_O), (401, {"reason": "wrong-room"}))
        self.assertEqual(self.room_check(data["session"], None), (401, {"reason": "wrong-room"}))   # an old room
        # a room that also accepts another origin (the hosted pages): its list
        self.assertEqual(self.room_check(data["session"], NIWA_O + ", " + KURA_O)[0], 200)
        stored = self.login.store.q("SELECT count(*) FROM room_sessions WHERE rsid_hash = ?",
                                    (hl.sha(data["session"]),))[0][0]
        self.assertEqual(stored, 1)
        self.assertFalse(self.login.store.q("SELECT 1 FROM room_sessions WHERE rsid_hash = ?", (data["session"],)))

    def test_code_is_bound(self):
        _, _, nonce, code, _ = self.trip()
        self.assertEqual(self.redeem(code, room=NIWA_O, nonce=nonce)[0], 401)          # another room's: and gone
        self.assertEqual(self.redeem(code, nonce=nonce)[0], 401)
        _, _, nonce, code, _ = self.trip()
        self.assertEqual(self.redeem(code, nonce="y" * 43)[0], 401)                     # another browser's nonce
        _, _, nonce, code, _ = self.trip()
        self.login.store.q("UPDATE codes SET expires_at = ?", (hl.now() - 1,))
        self.assertEqual(self.redeem(code, nonce=nonce)[0], 401)                        # 60 s
        session, sid, nonce, code, _ = self.trip()
        self.login.end_hister(session)                                                  # signed out meanwhile
        self.assertEqual(self.redeem(code, nonce=nonce)[0], 401)
        for bad in ({"X-Machiya-Room": "junk"}, {"X-Machiya-Room": KURA_O + ", " + NIWA_O}):
            _, _, nonce, code, _ = self.trip()
            self.assertEqual(self.redeem(code, nonce=nonce, extra=bad)[0], 401)

    def test_no_state_no_code(self):
        session = self.fake.signed_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}),
                                         {"Cookie": "hister=" + session})
        self.assertEqual((status, dict(headers)["Location"]), (303, KURA))      # back as it was: the room's own trip
        self.assertEqual(self.login.store.q("SELECT count(*) FROM codes")[0][0], 0)

    def test_signed_in_by_own_cookie_alone(self):
        """Hister's own cookie gone, the helper's still there: the trip finishes on the helper's session."""
        _, sid, _, _, _ = self.trip()
        nonce = "z" * 43
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode(
            {"return": KURA, "state": hl.histerauth.state_hash(nonce)}), {"Cookie": "__Host-machiya_sso=" + sid})
        self.assertEqual(status, 303)
        code = parse_qs(urlsplit(dict(headers)["Location"]).query)["code"][0]
        self.assertEqual(self.redeem(code, nonce=nonce)[0], 200)

    def test_signout_in_a_room_ends_every_room_and_shows_the_page_next(self):
        session, sid, nonce, code, _ = self.trip()
        kura = self.redeem(code, nonce=nonce)[1]["session"]
        _, _, nonce, code, _ = self.trip(ret="https://niwa.example.test/", session=session, own=sid)
        niwa = self.redeem(code, room=NIWA_O, nonce=nonce)[1]["session"]
        self.assertEqual(self.login.store.count(), 1)                                   # one browser, two rooms
        status, _, _ = self.internal("POST", "/v1/signout", {"X-Machiya-Session": niwa, "X-Machiya-Room": NIWA_O,
                                                             "Content-Length": "0"})
        self.assertEqual(status, 204)
        self.assertEqual(self.room_check(kura)[0], 401)
        self.assertEqual(self.room_check(niwa, NIWA_O)[0], 401)
        self.assertEqual(self.check(sid)[0], 401)
        self.assertEqual(self.fake.sessions.get(session), None)                         # Hister's logout too
        # the next automatic trip from another room: the page, the marker set here, the dead cookie dropped
        q = urlencode({"return": KURA, "state": "s" * 43, "provider": "oidc", "auto": "1"})
        status, headers, body = self.public("GET", "/machiya/signin?" + q,
                                            {"Cookie": "hister=%s; __Host-machiya_sso=%s" % (session, sid)})
        self.assertEqual(status, 200)
        self.assertIn(b'id="hister-signin"', body)
        self.assertEqual(cookie_value(headers, "__Host-machiya_sso_out"), "1")
        self.assertEqual(cookie_value(headers, "__Host-machiya_sso"), "")
        # a room session of another room can't sign this one out
        session, sid, nonce, code, _ = self.trip()
        kura = self.redeem(code, nonce=nonce)[1]["session"]
        self.internal("POST", "/v1/signout", {"X-Machiya-Session": kura, "X-Machiya-Room": NIWA_O,
                                              "Content-Length": "0"})
        self.assertEqual(self.room_check(kura)[0], 200)

    def test_hister_ui_signout_ends_room_sessions(self):
        session, sid, nonce, code, _ = self.trip()
        kura = self.redeem(code, nonce=nonce)[1]["session"]
        self.fake.sessions.pop(session)                                                 # signed out in Hister's UI
        self.login.store.q("UPDATE sessions SET verified_at = verified_at - 60")
        self.assertEqual(self.room_check(kura), (401, {"reason": "signed-out"}))
        self.assertEqual(self.login.store.q("SELECT count(*) FROM room_sessions")[0][0], 0)

    def test_sessions_page_lists_rooms_and_tokens(self):
        session, sid, nonce, code, _ = self.trip()
        self.redeem(code, nonce=nonce)
        c = {"Cookie": "__Host-machiya_sso=" + sid}
        status, _, body = self.public("GET", "/machiya/sessions", c)
        self.assertEqual(status, 200)
        self.assertIn(b"Kura", body)
        self.assertIn(b"Room Tokens", body)
        form = urlencode([("do", "token-new"), ("label", "pm on the laptop"), ("room", "konbini"),
                          ("room", "niwa"), ("room", "bogus")]).encode()
        post = dict(c, Origin=PUBLIC, **{"Content-Type": "application/x-www-form-urlencoded",
                                         "Content-Length": str(len(form))})
        status, _, body = self.public("POST", "/machiya/sessions", post, form)
        self.assertEqual(status, 200)
        value = body.split(b'<code class="token-value">')[1].split(b"<")[0].decode()
        self.assertTrue(hl.histerauth.RTOKEN_RE.match(value))
        self.assertEqual(self.room_check(value, NIWA_O)[0], 200)
        self.assertEqual(self.room_check(value, KURA_O), (401, {"reason": "wrong-room"}))
        status, _, body = self.public("GET", "/machiya/sessions", c)
        self.assertIn(b"pm on the laptop", body)
        self.assertNotIn(value.encode(), body)                                          # shown once
        tid = self.login.store.tokens_of("owner")[0]["id"]
        form = urlencode({"do": "token-revoke", "token": tid}).encode()
        post["Content-Length"] = str(len(form))
        self.assertEqual(self.public("POST", "/machiya/sessions", post, form)[0], 303)
        self.assertEqual(self.room_check(value, NIWA_O)[0], 401)
        self.assertNotIn(value, self.err.getvalue())

    def test_token_cli(self):
        out = os.path.join(self.tmp, "pm-token")
        env = {"HISTER_LOGIN_DB": self.settings.db, "MACHIYA_ROOMS": self.EXTRA["MACHIYA_ROOMS"]}
        buf = io.StringIO()
        self.assertEqual(hl.token_cli(["mint", "--user", "owner", "--label", "pm", "--rooms", "kura,niwa",
                                       "--out", out], env, buf), 0)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        with open(out) as f:
            value = f.read().strip()
        self.assertNotIn(value, buf.getvalue())
        self.assertEqual(self.room_check(value, NIWA_O)[1]["kind"], "token")
        buf = io.StringIO()
        self.assertEqual(hl.token_cli(["mint", "--user", "owner", "--label", "x", "--rooms", "nowhere"], env, buf), 1)
        mine = os.path.join(self.tmp, "made-elsewhere")
        with open(mine, "w") as f:
            f.write("mht_" + "M" * 43 + "\n")
        for _ in range(2):                                                              # idempotent
            self.assertEqual(hl.token_cli(["add", "--user", "owner", "--label", "dev", "--rooms",
                                           "https://kura.example.test", "--from-file", mine], env, io.StringIO()), 0)
        self.assertEqual(self.room_check("mht_" + "M" * 43)[0], 200)
        buf = io.StringIO()
        hl.token_cli(["list"], env, buf)
        self.assertEqual(len(buf.getvalue().strip().splitlines()), 2)
        self.assertNotIn("mht_", buf.getvalue())
        tid = buf.getvalue().split()[0]
        self.assertEqual(hl.token_cli(["revoke", tid], env, io.StringIO()), 0)
        self.assertEqual(hl.token_cli(["revoke", tid], env, io.StringIO()), 1)

    def test_prefs_with_a_room_session(self):
        _, _, nonce, code, _ = self.trip()
        rs = self.redeem(code, nonce=nonce)[1]["session"]
        h = {"X-Machiya-Session": rs, "X-Machiya-Room": KURA_O}
        body = json.dumps({"prefs": {"theme": "day"}}).encode()
        status, _, out = self.internal("PUT", "/v1/prefs", dict(h, **{"Content-Type": "application/json",
                                                                      "Content-Length": str(len(body))}), body)
        self.assertEqual((status, json.loads(out)["prefs"]["theme"]), (200, "day"))
        status, _, out = self.internal("GET", "/v1/prefs", dict(h, **{"X-Machiya-Room": NIWA_O}))
        self.assertEqual((status, json.loads(out)["reason"]), (401, "wrong-room"))

    # the hosted pages' hosts (proxied origins)

    def test_proxied_origin_round_trip(self):
        session = self.fake.signed_in()
        page = SEARCH_O + "/search?q=x"
        # 1. a hosted page's 401 goes to the helper as today (no state): the helper sends it through its /start
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": page}),
                                         {"Cookie": "hister=" + session})
        start = dict(headers)["Location"]
        self.assertEqual(start, SEARCH_O + "/machiya/start?" + urlencode({"return": page}))
        sid = cookie_value(headers, "__Host-machiya_sso")
        # 2. /machiya/start on search.* (nginx passes Host): a state cookie there, then the sign-in with its hash
        host = {"Host": "search.example.test"}
        status, headers, _ = self.public("GET", urlsplit(start).path + "?" + urlsplit(start).query, host)
        self.assertEqual(status, 302)
        nonce = cookie_value(headers, "__Host-machiya_sso_shiori_state")
        state = [c for c in cookies_of(headers) if c.startswith("__Host-machiya_sso_shiori_state=")][0]
        self.assertNotIn("Domain", state)
        loc = urlsplit(dict(headers)["Location"])
        q = parse_qs(loc.query)
        self.assertEqual((PUBLIC + loc.path, q["return"], q["state"], q["provider"]),
                         (PUBLIC + "/machiya/signin", [page], [hl.histerauth.state_hash(nonce)], ["oidc"]))
        # 3. the sign-in (already signed in) sends a code to search.*'s callback
        status, headers, _ = self.public("GET", loc.path + "?" + loc.query,
                                         {"Cookie": "hister=%s; __Host-machiya_sso=%s" % (session, sid)})
        cb = urlsplit(dict(headers)["Location"])
        self.assertEqual(cb.scheme + "://" + cb.netloc + cb.path, SEARCH_O + "/machiya/callback")
        # 4. the callback on search.* (the helper answers as that host): the room cookie there, then the page
        status, headers, _ = self.public("GET", cb.path + "?" + cb.query,
                                         dict(host, Cookie="__Host-machiya_sso_shiori_state=" + nonce))
        self.assertEqual((status, dict(headers)["Location"]), (302, page))
        rs = cookie_value(headers, "__Host-machiya_sso_shiori")
        self.assertTrue(hl.histerauth.RSID_RE.match(rs))
        # 5. nginx's auth_request: the session (or the cookie itself) and the host's origin
        for h in ({"X-Machiya-Session": rs, "X-Machiya-Room": SEARCH_O},
                  {"Cookie": "a=b; __Host-machiya_sso_shiori=" + rs, "X-Machiya-Room": SEARCH_O}):
            status, headers, _ = self.internal("GET", "/v1/nginx", h)
            self.assertEqual((status, dict(headers).get("X-Hister-Cookie")), (200, "hister=" + session))
        self.assertEqual(self.internal("GET", "/v1/nginx", {"X-Machiya-Session": rs, "X-Machiya-Room": KURA_O})[0],
                         401)
        # prefs on search.* with its cookie; a PUT from another origin is refused
        c = dict(host, Cookie="__Host-machiya_sso_shiori=" + rs)
        self.assertEqual(self.public("GET", "/machiya/api/prefs", c)[0], 200)
        body = json.dumps({"prefs": {"theme": "night"}}).encode()
        put = dict(c, **{"Content-Type": "application/json", "Content-Length": str(len(body))})
        self.assertEqual(self.public("PUT", "/machiya/api/prefs", dict(put, Origin=PUBLIC), body)[0], 403)
        self.assertEqual(self.public("PUT", "/machiya/api/prefs", dict(put, Origin=SEARCH_O), body)[0], 200)
        # the helper's own pages aren't served on search.*
        self.assertEqual(self.public("GET", "/machiya/sessions", c)[0], 404)
        self.assertEqual(self.public("GET", "/machiya/signin", c)[0], 404)
        # Sign Out on search.* (same-origin): everything ends, the marker stops the automatic trip from there
        self.assertEqual(self.public("POST", "/machiya/signout", dict(c, **{"Content-Length": "0"}))[0], 403)
        status, headers, _ = self.public("POST", "/machiya/signout", dict(c, Origin=SEARCH_O, **{"Content-Length": "0"}))
        self.assertEqual((status, cookie_value(headers, "__Host-machiya_sso_shiori_out")), (303, "1"))
        self.assertEqual(self.internal("GET", "/v1/nginx", {"X-Machiya-Session": rs, "X-Machiya-Room": SEARCH_O})[0],
                         401)
        status, headers, _ = self.public("GET", "/machiya/start?return=%2F",
                                         dict(host, Cookie="__Host-machiya_sso_shiori_out=1"))
        self.assertNotIn("provider", dict(headers)["Location"])

    def test_proxied_origin_password_path_as_a_browser(self):
        """A hosted page's 401, the helper's page, the password to Hister's own /api/login, then every redirect with a
        cookie jar per host (host-only cookies) and the Sec-Fetch-Site a browser sends: back on the page, signed in."""
        jar = {}

        def go(method, url, site, body=None, extra=None):
            u = urlsplit(url)
            h = {"Host": u.hostname, "Sec-Fetch-Site": site}
            if jar.get(u.hostname):
                h["Cookie"] = "; ".join("%s=%s" % kv for kv in jar[u.hostname].items())
            if body is not None:
                h["Content-Length"] = str(len(body))
            h.update(extra or {})
            target = u.path + ("?" + u.query if u.query else "")
            if u.hostname == "hister.example.test" and not target.startswith("/machiya/"):
                status, headers, out = self.req(int(self.fake.url.rsplit(":", 1)[1]), method, target, h, body)
            else:
                status, headers, out = self.public(method, target, h, body)
            for c in cookies_of(headers):
                name, _, rest = c.partition("=")
                value = rest.split(";", 1)[0]
                if name.startswith("__Host-"):
                    self.assertNotIn("Domain=", c)
                if value and "Max-Age=0" not in c:
                    jar.setdefault(u.hostname, {})[name] = value
                else:
                    jar.get(u.hostname, {}).pop(name, None)
            return status, dict(headers), out

        page = SEARCH_O + "/search?q=x"
        status, _, body = go("GET", PUBLIC + "/machiya/signin?" + urlencode({"return": page}), "same-site")
        self.assertEqual(status, 200)
        nxt = body.split(b'data-next="')[1].split(b'"')[0].decode().replace("&amp;", "&")
        login = json.dumps({"username": "owner", "password": "correct horse"}).encode()
        status, _, _ = go("POST", PUBLIC + "/api/login", "same-origin", login,
                          {"Origin": PUBLIC, "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        url, site, hops = PUBLIC + nxt, "same-origin", []
        for _ in range(6):
            status, headers, _ = go("GET", url, site)
            hops.append((urlsplit(url).hostname, urlsplit(url).path, status))
            if status not in (302, 303):
                break
            url, site = headers["Location"], "same-site"        # a chain through two hosts is same-site from here
        self.assertEqual(hops, [("hister.example.test", "/machiya/signin", 303),
                                ("search.example.test", "/machiya/start", 302),
                                ("hister.example.test", "/machiya/signin", 303),
                                ("search.example.test", "/machiya/callback", 302),
                                ("search.example.test", "/search", 404)])     # the page itself isn't the helper's
        self.assertEqual(url, page)
        rs = jar["search.example.test"]["__Host-machiya_sso_shiori"]
        status, headers, _ = self.internal("GET", "/v1/nginx", {"X-Machiya-Session": rs, "X-Machiya-Room": SEARCH_O})
        self.assertEqual(status, 200)
        self.assertEqual(dict(headers)["X-Hister-Cookie"], "hister=" + jar["hister.example.test"]["hister"])
        self.assertNotIn("__Host-machiya_sso_shiori_state", jar["search.example.test"])

    def test_signin_page_has_no_password_path_to_the_helper(self):
        """No <form> (as Hister's own page): the fields have no names, the button only runs the script, and the script
        signs in on its click or Enter, never with a form submission (the browsers themselves: dev/signin_check.py)."""
        status, _, body = self.public("GET", "/machiya/signin?" + urlencode({"return": SEARCH_O + "/"}))
        self.assertEqual(status, 200)
        box = body.split(b'id="hister-signin"')[1].split(b"</div>")[0]
        self.assertNotIn(b"name=", box)
        self.assertIn(b'<button type="button">', box)
        self.assertNotIn(b"<form", body)
        with open(os.path.join(HERE, "..", "static", "signin.js")) as f:
            script = f.read()
        for want in ('button.addEventListener("click"', 'ev.key === "Enter"', 'fetch("/api/login"'):
            self.assertIn(want, script)
        for gone in ("requestSubmit", "form.submit", '"submit"', '"formdata"'):
            self.assertNotIn(gone, script)

    def test_nginx_only_for_a_proxied_origin_and_its_own_session(self):
        """0.5.0: X-Hister-Cookie (the raw Hister session) only for an origin in HISTER_LOGIN_PROXIED_ORIGINS and a
        room session made for that same origin; the internal port can't tell who asks, so X-Machiya-Room alone
        proves nothing. Anything else is answered as signed out."""
        session, _, nonce, code, _ = self.trip(ret=SEARCH_O + "/search?q=x")
        search = self.redeem(code, room=SEARCH_O, nonce=nonce)[1]["session"]
        _, _, nonce, code, _ = self.trip(session=session)
        kura = self.redeem(code, room=KURA_O, nonce=nonce)[1]["session"]
        self.assertEqual(self.room_check(kura)[0], 200)                    # a good session, for Kura
        app = self.login.store.create(session, "owner", 1, "app", "iPhone")

        def nginx(sid, room):
            h = {"X-Machiya-Session": sid}
            if room is not None:
                h["X-Machiya-Room"] = room
            status, headers, _ = self.internal("GET", "/v1/nginx", h)
            return status, dict(headers).get("X-Hister-Cookie")

        # the right origin and its own session
        self.assertEqual(nginx(search, SEARCH_O), (200, "hister=" + session))
        self.assertEqual(nginx(search, SEARCH_O + ", " + KURA_O), (200, "hister=" + session))
        # a session from another room, under the hosted pages' origin or its own
        self.assertEqual(nginx(kura, SEARCH_O), (401, None))
        self.assertEqual(nginx(kura, KURA_O), (401, None))
        self.assertEqual(nginx(kura, KURA_O + ", " + SEARCH_O), (401, None))
        # an origin not in the list, even with a session of that origin's... or of the listed one
        self.assertEqual(nginx(search, KURA_O), (401, None))
        self.assertEqual(nginx(search, NIWA_O), (401, None))
        self.assertEqual(nginx(search, None), (401, None))
        # an app's or a browser's helper id is no room session
        for sid in (app, self.login.store.create(session, "owner", 1, "browser", "Firefox")):
            self.assertEqual(nginx(sid, SEARCH_O), (401, None))
            self.assertEqual(nginx(sid, KURA_O), (401, None))
            self.assertEqual(nginx(sid, None), (401, None))
        # Hister down: the right session still says so (503), a wrong one is still just signed out
        self.age()
        self.fake.fail = True
        self.assertEqual(nginx(search, SEARCH_O), (503, None))
        self.assertEqual(nginx(kura, SEARCH_O), (401, None))

    # keep-alive and smuggling on the new endpoints

    def test_keep_alive_redeem_and_room_checks_answer_each_request_alone(self):
        _, _, nonce, code, _ = self.trip()
        _, _, nonce2, code2, _ = self.trip(session=self.fake.signed_in("other"))
        got = self.one_connection(self.int, [
            ("POST", "/v1/redeem", {"X-Machiya-Code": code, "X-Machiya-Room": KURA_O, "X-Machiya-State": nonce,
                                    "Content-Length": "0"}),
            ("POST", "/v1/redeem", {"X-Machiya-Code": code, "X-Machiya-Room": KURA_O, "X-Machiya-State": nonce,
                                    "Content-Length": "0"}),                               # used
            ("POST", "/v1/redeem", {"X-Machiya-Code": code2, "X-Machiya-Room": KURA_O, "X-Machiya-State": nonce2,
                                    "Content-Length": "0"}),                               # another person's
            ("POST", "/v1/redeem", {"Content-Length": "0"}),
        ])
        self.assertEqual([g[0] for g in got], [200, 401, 200, 401])
        mine, theirs = json.loads(got[0][2])["session"], json.loads(got[2][2])["session"]
        got = self.one_connection(self.int, [
            ("GET", "/v1/check", {"X-Machiya-Session": mine, "X-Machiya-Room": KURA_O}),
            ("GET", "/v1/check", {"X-Machiya-Session": mine, "X-Machiya-Room": NIWA_O}),
            ("GET", "/v1/check", {"X-Machiya-Session": theirs, "X-Machiya-Room": KURA_O}),
            ("GET", "/v1/check", {"X-Machiya-Room": KURA_O}),
            ("GET", "/v1/check", {"X-Machiya-Session": mine, "X-Machiya-Room": KURA_O}),
        ])
        self.assertEqual([g[0] for g in got], [200, 401, 200, 400, 200])
        self.assertEqual([json.loads(g[2]).get("username") for g in got], ["owner", None, "other", None, "owner"])

    def test_keep_alive_unread_or_chunked_body_never_becomes_a_request(self):
        _, sid, _, _, _ = self.trip()
        smuggled = b"GET /v1/nginx HTTP/1.1\r\nHost: x\r\nX-Machiya-Session: %s\r\n\r\n" % sid.encode()
        for port, head in ((self.int, b"POST /v1/redeem HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"),
                           (self.pub, b"GET /machiya/callback?code=x HTTP/1.1\r\nHost: search.example.test\r\n"),
                           (self.pub, b"GET /machiya/start HTTP/1.1\r\nHost: search.example.test\r\n"),
                           (self.pub, b"POST /machiya/signout HTTP/1.1\r\nHost: search.example.test\r\n"
                                      b"Transfer-Encoding: chunked\r\n")):
            out, closed = self.raw(port, head + b"Content-Length: %d\r\n\r\n" % len(smuggled) + smuggled)
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, head)
            self.assertTrue(closed, head)
            self.assertNotIn(b"X-Hister-Cookie", out)
        # a redeem's body, when read, is only a body: one answer, never a second request
        out, _ = self.raw(self.int, b"POST /v1/redeem HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n"
                          % len(smuggled) + smuggled)
        self.assertEqual(out.count(b"HTTP/1.1 "), 1)
        self.assertNotIn(b"X-Hister-Cookie", out)


class LegacyOffTest(HelperBase):
    """HISTER_LOGIN_LEGACY=none: the switch. No shared-domain cookie, and rooms refuse a browser's helper id and
    Hister's raw token; apps keep their mhs_; Hister's own host (public prefs) still takes the token."""
    EXTRA = {"HISTER_LOGIN_LEGACY": "none"}

    def test_switch(self):
        session = self.fake.signed_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}),
                                         {"Cookie": "hister=" + session})
        sid = cookie_value(headers, "__Host-machiya_sso")
        self.assertTrue(sid)
        self.assertIsNone(cookie_value(headers, "machiya_sso"))                          # no shared-domain copy
        room = {"X-Machiya-Room": KURA_O}
        for h in ({"X-Machiya-Session": sid}, {"X-Access-Token": "tok-owner"}):
            status, _, body = self.internal("GET", "/v1/check", dict(h, **room))
            self.assertEqual((status, json.loads(body)), (401, {"reason": "legacy-off"}), h)
            status, _, body = self.internal("GET", "/v1/prefs", dict(h, **room))
            self.assertEqual(status, 401)
        status, _, body = self.internal("GET", "/v1/check", {"X-Access-Token": "tok-owner"})   # an old room too
        self.assertEqual(status, 401)
        app = self.login.store.create(self.fake.signed_in(), "owner", 1, "app", "iPhone")
        self.assertEqual(self.internal("GET", "/v1/check", {"X-Machiya-Session": app, **room})[0], 200)
        self.assertEqual(self.public("GET", "/machiya/api/prefs", {"X-Access-Token": "tok-owner"})[0], 200)
        self.assertEqual(self.public("GET", "/machiya/sessions", {"Cookie": "machiya_sso=" + sid})[0], 303)
        self.assertEqual(self.public("GET", "/machiya/sessions", {"Cookie": "__Host-machiya_sso=" + sid})[0], 200)

    def test_settings(self):
        with self.assertRaises(SystemExit):
            hl.Settings(dict(ENV, HISTER_LOGIN_LEGACY="cookies"))
        self.assertEqual(hl.Settings(dict(ENV, HISTER_LOGIN_LEGACY="hister-token")).legacy, {"hister-token"})
        self.assertEqual(hl.Settings(dict(ENV)).legacy, {"domain-cookie", "hister-token"})
        unset = {k: v for k, v in ENV.items() if k != "HISTER_LOGIN_LEGACY"}
        self.assertEqual(hl.Settings(unset).legacy, set())                                  # 0.5.0: the default
        for raw in ("", " ", "none", "NONE"):
            self.assertEqual(hl.Settings(dict(unset, HISTER_LOGIN_LEGACY=raw)).legacy, set(), raw)
        self.assertEqual(hl.Settings(dict(unset, HISTER_LOGIN_LEGACY="domain-cookie")).legacy, {"domain-cookie"})
        with self.assertRaises(SystemExit):
            hl.Settings(dict(ENV, HISTER_LOGIN_PROXIED_ORIGINS="https://search.example.test/path"))


class LegacyDefaultTest(LegacyOffTest):
    """0.5.0: HISTER_LOGIN_LEGACY unset is the switch (none), every LegacyOffTest check with the setting unset."""
    EXTRA = {"HISTER_LOGIN_LEGACY": None}


class LegacyOptInTest(HelperBase):
    """0.5.0: each legacy way is its own opt-in; one doesn't bring the other."""
    MODE = "domain-cookie"
    EXTRA = {"HISTER_LOGIN_LEGACY": MODE}

    def test_only_what_was_asked_for(self):
        session = self.fake.signed_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}),
                                         {"Cookie": "hister=" + session})
        sid = cookie_value(headers, "__Host-machiya_sso")
        self.assertTrue(sid)
        cookie_on, token_on = self.MODE == "domain-cookie", self.MODE == "hister-token"
        self.assertEqual(cookie_value(headers, "machiya_sso") == sid, cookie_on)         # the shared-domain copy
        room = {"X-Machiya-Room": KURA_O}
        status, _, body = self.internal("GET", "/v1/check", {"X-Machiya-Session": sid, **room})
        self.assertEqual(status, 200 if cookie_on else 401)
        if not cookie_on:
            self.assertEqual(json.loads(body), {"reason": "legacy-off"})
        status, _, body = self.internal("GET", "/v1/check", {"X-Access-Token": "tok-owner", **room})
        self.assertEqual(status, 200 if token_on else 401)
        if not token_on:
            self.assertEqual(json.loads(body), {"reason": "legacy-off"})


class LegacyTokenOptInTest(LegacyOptInTest):
    MODE = "hister-token"
    EXTRA = {"HISTER_LOGIN_LEGACY": MODE}


class PrefsTest(unittest.TestCase):
    """0.2.0: the account's settings (docs/contracts/prefs.md), one store keyed by the Hister user: the rooms' /v1/prefs
    with the caller's own credential, the apps' and hosted pages' /machiya/api/prefs."""
    setUp, tearDown, req, public, internal, check, sign_in = (
        HelperTest.setUp, HelperTest.tearDown, HelperTest.req, HelperTest.public, HelperTest.internal,
        HelperTest.check, HelperTest.sign_in)
    one_connection, raw, age = HelperTest.one_connection, HelperTest.raw, HelperTest.age

    def put(self, port, path, prefs, headers):
        body = json.dumps({"prefs": prefs}).encode()
        h = dict(headers, **{"Content-Type": "application/json", "Content-Length": str(len(body))})
        status, hdrs, out = self.req(port, "PUT", path, h, body)
        return status, dict(hdrs), json.loads(out or b"null")

    def get(self, port, path, headers):
        status, hdrs, out = self.req(port, "GET", path, headers)
        return status, dict(hdrs), json.loads(out) if out else None

    def test_one_store_every_credential(self):
        _, sid, _ = self.sign_in()
        status, hdrs, data = self.put(self.int, "/v1/prefs", {"theme": "auto", "palette": "nord"},
                                      {"X-Machiya-Session": sid})
        self.assertEqual((status, data["v"], data["rev"], data["prefs"]), (200, 1, 1, {"palette": "nord",
                                                                                      "theme": "system"}))
        self.assertEqual(hdrs["ETag"], '"1"')
        self.assertEqual(hdrs["Cache-Control"], "no-store")
        # the owner's Hister token (a room's /api/prefs for an agent, or an extension) sees the same account
        status, _, data = self.get(self.int, "/v1/prefs", {"X-Access-Token": "tok-owner"})
        self.assertEqual(data["prefs"], {"palette": "nord", "theme": "system"})
        # an app: Bearer mhs_ on the public port; a 304 when nothing changed
        status, hdrs, data = self.get(self.pub, "/machiya/api/prefs", {"Authorization": "Bearer " + sid,
                                                                     "If-None-Match": '"1"'})
        self.assertEqual((status, data, hdrs["ETag"]), (304, None, '"1"'))
        status, _, data = self.put(self.pub, "/machiya/api/prefs", {"text_size": "large"},
                                   {"Authorization": "Bearer tok-owner"})
        self.assertEqual((status, data["rev"], sorted(data["updated"])), (200, 2, ["palette", "text_size", "theme"]))
        # another user's account is another key: never the owner's
        status, _, data = self.get(self.pub, "/machiya/api/prefs", {"X-Access-Token": "tok-other"})
        self.assertEqual((status, data["prefs"], data["rev"]), (200, {}, 0))
        rows = self.login.prefs.principals()
        self.assertEqual(rows, [hl.user_key("owner")])
        self.assertTrue(rows[0].startswith("hi:") and len(rows[0]) == 35)
        # the check carries the shared ones, for a fresh browser's first render
        self.assertEqual(self.check(sid)[1]["prefs"], {"palette": "nord", "text_size": "large", "theme": "system"})
        self.assertEqual(os.stat(self.settings.prefs_db).st_mode & 0o777, 0o600)
        self.assertEqual(os.path.dirname(self.settings.prefs_db), os.path.dirname(self.settings.db))

    def test_refusals(self):
        _, sid, _ = self.sign_in()
        sess = {"X-Machiya-Session": sid}
        self.assertEqual(self.put(self.int, "/v1/prefs", {"theme": "sepia"}, sess)[0], 400)
        self.assertEqual(self.put(self.int, "/v1/prefs", {"x": "1"}, sess)[0], 400)
        self.assertEqual(self.put(self.int, "/v1/prefs", {"theme": "day"}, {})[0], 400)            # no credential
        self.assertEqual(self.put(self.int, "/v1/prefs", {"theme": "day"},
                                  dict(sess, **{"X-Access-Token": "tok-owner"}))[0], 400)      # two
        self.assertEqual(self.get(self.int, "/v1/prefs", {"X-Machiya-Session": "mhs_" + "x" * 43})[0], 401)
        self.assertEqual(self.get(self.int, "/v1/prefs", {"X-Access-Token": "nope"})[0], 401)
        status, _, data = self.get(self.pub, "/machiya/api/prefs", {})
        self.assertEqual((status, data["signin"]), (401, PUBLIC + "/machiya/signin"))
        status, _, out = self.req(self.int, "PUT", "/v1/prefs", dict(sess, **{"Content-Type": "text/plain",
                                                                              "Content-Length": "2"}), b"{}")
        self.assertEqual(status, 415)
        self.fake.fail = True                                                # Hister down: no account to ask
        self.age()
        status, _, data = self.get(self.int, "/v1/prefs", sess)
        self.assertEqual((status, data["error"]), (503, "preferences unavailable"))

    def test_cookie_put_needs_an_origin_of_the_house(self):
        """The hosted pages reach /machiya/api/prefs through their own nginx with the sign-in cookie: a GET is fine,
        a PUT only from a return host's page (or the helper's own)."""
        _, sid, _ = self.sign_in()
        cookie = {"Cookie": "machiya_sso=" + sid}
        self.assertEqual(self.get(self.pub, "/machiya/api/prefs", cookie)[0], 200)
        self.assertEqual(self.put(self.pub, "/machiya/api/prefs", {"theme": "day"}, cookie)[0], 403)
        self.assertEqual(self.put(self.pub, "/machiya/api/prefs", {"theme": "day"},
                                  dict(cookie, Origin="https://evil.example"))[0], 403)
        status, _, data = self.put(self.pub, "/machiya/api/prefs", {"theme": "day"},
                                   dict(cookie, Origin="https://kura.example.test"))
        self.assertEqual((status, data["prefs"]), (200, {"theme": "day"}))
        status, hdrs, _ = self.req(self.pub, "OPTIONS", "/machiya/api/prefs", {"Origin": "https://evil.example"})
        self.assertFalse(any(k.lower().startswith("access-control-") for k, _ in hdrs))     # no CORS

    def test_keep_alive_prefs_answer_each_request_alone(self):
        """Different people's prefs requests down one connection (as Tailscale Serve sends them): each is answered
        for its own credential only."""
        _, sid, _ = self.sign_in()
        _, other, _ = self.sign_in(session=self.fake.signed_in("other"))
        body = json.dumps({"prefs": {"palette": "ayu"}}).encode()
        put = lambda h: ("PUT", "/v1/prefs", dict(h, **{"Content-Type": "application/json",     # noqa: E731
                                                         "Content-Length": str(len(body))}), body)
        got = self.one_connection(self.int, [
            put({"X-Machiya-Session": sid}),                      # the owner writes
            ("GET", "/v1/prefs", {"X-Machiya-Session": other}),   # another person: their own, empty
            ("GET", "/v1/prefs", {}),                             # nobody: 400
            ("GET", "/v1/prefs", {"X-Access-Token": "tok-owner"}),
            ("GET", "/v1/prefs", {"X-Access-Token": "nope"}),
        ])
        self.assertEqual([g[0] for g in got], [200, 200, 400, 200, 401])
        self.assertEqual(json.loads(got[1][2])["prefs"], {})
        self.assertEqual(json.loads(got[3][2])["prefs"], {"palette": "ayu"})
        got = self.one_connection(self.pub, [
            ("GET", "/machiya/api/prefs", {"Authorization": "Bearer " + other}),
            ("GET", "/machiya/api/prefs", {"Cookie": "machiya_sso=" + sid}),
            ("GET", "/machiya/api/prefs", {}),
        ])
        self.assertEqual([g[0] for g in got], [200, 200, 401])
        self.assertEqual([json.loads(g[2]).get("prefs") for g in got[:2]], [{}, {"palette": "ayu"}])

    def test_keep_alive_unread_prefs_body_never_becomes_a_request(self):
        _, sid, _ = self.sign_in()
        smuggled = b"GET /v1/nginx HTTP/1.1\r\nHost: x\r\nX-Machiya-Session: %s\r\n\r\n" % sid.encode()
        for port, head in ((self.int, b"PUT /v1/prefs HTTP/1.1\r\nHost: x\r\n"),            # no credential: 400
                           (self.int, b"PUT /v1/nope HTTP/1.1\r\nHost: x\r\n"),
                           (self.int, b"GET /v1/prefs HTTP/1.1\r\nHost: x\r\nX-Access-Token: nope\r\n"),
                           (self.pub, b"PUT /machiya/api/prefs HTTP/1.1\r\nHost: x\r\nAuthorization: Basic x\r\n"),
                           (self.pub, b"PUT /machiya/nope HTTP/1.1\r\nHost: x\r\n"),
                           (self.pub, b"GET /machiya/api/prefs HTTP/1.1\r\nHost: x\r\n")):
            out, closed = self.raw(port, head + b"Content-Length: %d\r\n\r\n" % len(smuggled) + smuggled)
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, head)
            self.assertTrue(closed, head)
            self.assertNotIn(b"X-Hister-Cookie", out)
        # a PUT whose body was read keeps the connection
        body = b'{"prefs": {"theme": "night"}}'
        got = self.one_connection(self.int, [
            ("PUT", "/v1/prefs", {"X-Machiya-Session": sid, "Content-Type": "application/json",
                                  "Content-Length": str(len(body))}, body),
            ("GET", "/v1/check", {"X-Machiya-Session": sid})])
        self.assertEqual([g[0] for g in got], [200, 200])
        self.assertEqual(json.loads(got[1][2])["prefs"], {"theme": "night"})

    def test_values_never_logged(self):
        _, sid, _ = self.sign_in()
        self.put(self.int, "/v1/prefs", {"kura.secretish": "LANTERN-VALUE"}, {"X-Machiya-Session": sid})
        self.assertNotIn("LANTERN-VALUE", self.err.getvalue())


class PrefsCliTest(unittest.TestCase):
    """`hister_login.py prefs import|show|delete`: the migration rule (the newest across the rooms' files wins, the
    account's own newer values kept, a second run changes nothing)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"HISTER_LOGIN_DB": os.path.join(self.tmp, "hister-login.sqlite3")}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def room(self, name, rows):
        path = os.path.join(self.tmp, name + ".sqlite3")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE prefs (principal TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                   "updated INTEGER NOT NULL, PRIMARY KEY (principal, key))")
        db.executemany("INSERT INTO prefs VALUES (?,?,?,?)", rows)
        db.commit()
        db.close()
        return path

    def run_cli(self, *argv):
        out = io.StringIO()
        code = hl.prefs_cli(list(argv), self.env, out)
        return code, out.getvalue()

    def test_import_newest_wins(self):
        from vaultkit.identity import tailscale_uid
        owner = hl.user_key("owner")
        ts = tailscale_uid("me@example.com")
        kura = self.room("kura", [(owner, "theme", "day", 300), (owner, "palette", "nord", 100)])
        konbini = self.room("konbini", [(ts, "theme", "night", 200), (ts, "palette", "dracula", 400),
                                        ("ts:someoneelse", "theme", "day", 999)])
        niwa = self.room("niwa", [("ts:onlyone", "text_size", "large", 50), ("ts:onlyone", "bogus", "x", 1)])
        code, out = self.run_cli("import", "--user", "owner", "--tailscale", "me@example.com", kura, konbini, niwa)
        self.assertEqual(code, 0, out)
        store = hl.vprefs.Store(hl.prefs_path(self.env))
        rev, values, updated = store.snapshot(owner)
        self.assertEqual(values, {"theme": "day", "palette": "dracula", "text_size": "large"})
        self.assertEqual(updated, {"theme": 300, "palette": 400, "text_size": 50})
        self.assertIn("one principal (ts:onlyone), taken as owner", out)
        self.assertIn("skipped (not in the schema)", out)
        self.assertNotIn("someoneelse", out.split("principal")[0])
        # again: nothing changes
        self.assertEqual(self.run_cli("import", "--user", "owner", "--tailscale", "me@example.com", kura, konbini,
                                      niwa)[0], 0)
        self.assertEqual(store.snapshot(owner)[0], rev)
        # a value the account set later is kept
        store.write(owner, {"theme": "night"})
        self.run_cli("import", "--user", "owner", kura)
        self.assertEqual(store.snapshot(owner)[1]["theme"], "night")
        code, out = self.run_cli("show", "--user", "owner")
        self.assertIn("palette", out)
        code, out = self.run_cli("delete", "--user", "owner")
        self.assertEqual(store.snapshot(owner)[1], {})

    def test_ambiguous_file_refused_and_dry_run(self):
        two = self.room("two", [("ts:a", "theme", "day", 1), ("ts:b", "theme", "night", 2)])
        code, out = self.run_cli("import", "--user", "owner", two)
        self.assertEqual(code, 1)
        self.assertIn("name one with --principal", out)
        code, out = self.run_cli("import", "--user", "owner", "--principal", "ts:b", "--dry-run", two)
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertFalse(os.path.exists(hl.prefs_path(self.env)) and
                         hl.vprefs.Store(hl.prefs_path(self.env)).snapshot(hl.user_key("owner"))[1])
        self.assertEqual(self.run_cli("import", "--user", "owner", os.path.join(self.tmp, "missing"))[0], 1)


class CookieNameTest(unittest.TestCase):
    """MACHIYA_SSO_COOKIE: a second stack under the same cookie domain (a dev stack) uses its own sign-in cookie and
    never reads the default one."""
    EXTRA = {"MACHIYA_SSO_COOKIE": "machiya_dev_sso"}
    setUp, tearDown, req, public, internal, check = (HelperTest.setUp, HelperTest.tearDown, HelperTest.req,
                                                     HelperTest.public, HelperTest.internal, HelperTest.check)

    def test_custom_cookie_name(self):
        session = self.fake.signed_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}),
                                         {"Cookie": "hister=" + session})
        self.assertEqual(status, 303)
        self.assertIsNone(cookie_value(headers, "machiya_sso"))
        sid = cookie_value(headers, "machiya_dev_sso")
        self.assertTrue(sid and hl.SID_RE.match(sid))
        # the default-named cookie (another stack's) is ignored: a new id is issued, not reused
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}),
                                         {"Cookie": "hister=%s; machiya_sso=%s" % (session, sid)})
        self.assertTrue(cookie_value(headers, "machiya_dev_sso"))
        # sign-out clears the custom name only
        cookie = "hister=%s; machiya_dev_sso=%s" % (session, sid)
        status, headers, _ = self.public("POST", "/machiya/signout", {"Cookie": cookie, "Content-Length": "0",
                                                                      "Origin": PUBLIC})
        cs = cookies_of(headers)
        self.assertTrue(any(c.startswith("machiya_dev_sso=;") and "Max-Age=0" in c for c in cs))
        self.assertFalse(any(c.startswith("machiya_sso=") for c in cs))
        self.assertEqual(self.check(sid)[0], 401)


class SettingsTest(unittest.TestCase):
    def test_cookie_name_checked(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(hl.Settings({"HISTER_LOGIN_PUBLIC_URL": "https://h.example"}).sso, "machiya_sso")
            for bad in ("a b", "x;y", "é", "a=b"):
                with self.assertRaises(SystemExit):
                    hl.Settings({"HISTER_LOGIN_PUBLIC_URL": "https://h.example", "MACHIYA_SSO_COOKIE": bad})

    def test_public_url_required(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                hl.Settings({})
            with self.assertRaises(SystemExit):
                hl.Settings({"HISTER_LOGIN_PUBLIC_URL": "https://h.example/path"})

    def test_explicit_hosts(self):
        with redirect_stderr(io.StringIO()):
            s = hl.Settings({"HISTER_LOGIN_PUBLIC_URL": "https://h.example:8443",
                             "HISTER_LOGIN_RETURN_HOSTS": "a.example, b.example:8443"})
        self.assertEqual(s.return_hosts, ["a.example", "b.example:8443", "h.example:8443"])

    def test_device_label(self):
        self.assertEqual(hl.device_label({"User-Agent": "Mozilla/5.0 (Macintosh) Firefox/130"}), "Firefox on Mac")
        self.assertEqual(hl.device_label({}), "Browser")


class VendoredTest(unittest.TestCase):
    def test_vaultkit_matches_its_manifest(self):
        from vaultkit import verify
        self.assertEqual(verify.check(os.path.join(HERE, "..", "vaultkit")), [])


if __name__ == "__main__":
    unittest.main()


class JournalTest(unittest.TestCase):
    """WAL everywhere but Haiku, whose SQLite can't share a WAL file between the server and the token CLI."""

    def mode(self, platform):
        d = tempfile.mkdtemp(dir=os.environ.get("TMPDIR", "/var/tmp"))
        self.addCleanup(shutil.rmtree, d, True)
        real = sys.platform
        sys.platform = platform
        try:
            store = hl.Store(os.path.join(d, "h.sqlite3"))
        finally:
            sys.platform = real
        return store.db.execute("PRAGMA journal_mode").fetchone()[0]

    def test_wal_on_linux(self):
        self.assertEqual(self.mode("linux"), "wal")

    def test_rollback_journal_on_haiku(self):
        self.assertEqual(self.mode("haiku1"), "delete")
