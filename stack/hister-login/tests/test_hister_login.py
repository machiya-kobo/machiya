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
       "HISTER_LOGIN_PROVIDERS": "oidc"}


def cookies_of(headers):
    return [v for k, v in headers if k.lower() == "set-cookie"]


def cookie_value(headers, name):
    for c in cookies_of(headers):
        k, _, rest = c.partition("=")
        if k == name:
            return rest.split(";", 1)[0]
    return None


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake = FakeHister()
        env = dict(ENV, HISTER_LOGIN_HISTER_URL=self.fake.url, HISTER_LOGIN_DB=os.path.join(self.tmp, "d", "hl.db"))
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

    # -- sign-in

    def test_signin_page_when_not_signed_in(self):
        status, headers, body = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA}))
        self.assertEqual(status, 200)
        self.assertIn(b'id="hister-signin"', body)
        self.assertIn(b'href="/api/oauth?provider=oidc"', body)
        self.assertIn(b"/machiya/static/signin.js", body)
        self.assertIn(b"/machiya/static/machiya.css", body)
        self.assertNotIn(b'"/static/', body)                           # Hister owns /static/ on this host
        self.assertNotIn(b"<script>", body)                            # no inline script (CSP script-src 'self')
        ret = cookie_value(headers, "machiya_return")
        self.assertTrue(ret)
        c = [x for x in cookies_of(headers) if x.startswith("machiya_return=")][0]
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
            self.assertIsNone(cookie_value(headers, "machiya_return") or None, bad)
        # the app flow takes the app's scheme only, never a page
        status, _, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": KURA, "app": "1"}),
                                   {"Cookie": "hister=" + session})
        self.assertEqual(status, 400)
        # a forged machiya_return is checked again when used
        forged = hl.b64e(json.dumps({"r": "https://evil.example/", "a": 0}).encode())
        self.assertEqual(self.login.read_return("machiya_return=" + forged), (None, False))

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
        self.assertEqual(self.check(token="tok-owner"), (200, {"username": "owner", "user_id": 1, "via": "token"}))
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
        status, _, body = self.public("GET", "/machiya/healthz")
        self.assertEqual((status, json.loads(body)["hister"]), (503, "user-handling-off"))
        self.assertIn("HISTER USER HANDLING IS OFF", self.err.getvalue())

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

    def test_nginx(self):
        session, sid, _ = self.sign_in()
        status, headers, _ = self.internal("GET", "/v1/nginx", {"X-Machiya-Session": sid})
        self.assertEqual((status, dict(headers)["X-Hister-Cookie"]), (200, "hister=" + session))
        self.assertEqual(self.internal("GET", "/v1/nginx", {"X-Machiya-Session": ""})[0], 401)
        self.assertEqual(self.internal("GET", "/v1/nginx")[0], 401)
        self.age()
        self.fake.fail = True
        self.assertEqual(self.internal("GET", "/v1/nginx", {"X-Machiya-Session": sid})[0], 503)

    def test_internal_paths_not_public(self):
        session, sid, _ = self.sign_in()
        for path in ("/v1/check", "/v1/nginx", "/healthz"):
            self.assertEqual(self.public("GET", path, {"X-Machiya-Session": sid})[0], 404, path)

    # -- the OAuth callback shim

    def test_callback_shim_returns_to_the_room(self):
        anon, state = self.fake.oauth_started()
        ret = hl.b64e(json.dumps({"r": KURA, "a": 0}).encode())
        status, headers, _ = self.public("GET", "/api/oauth/callback?provider=oidc&code=c&state=" + state,
                                         {"Cookie": "hister=%s; machiya_return=%s; machiya_theme=night" % (anon, ret)})
        self.assertEqual(status, 302)
        self.assertEqual(dict(headers)["Location"], KURA)
        histers = [c for c in cookies_of(headers) if c.startswith("hister=")]
        self.assertEqual(len(histers), 2)                                # Hister's own Set-Cookies, passed through
        new = histers[-1].split(";")[0].split("=", 1)[1]
        self.assertNotIn("Domain", histers[-1])
        sid = cookie_value(headers, "machiya_sso")
        self.assertEqual(self.check(sid)[1]["username"], "owner")
        self.assertEqual(self.login.store.q("SELECT hister_session FROM sessions")[0][0], new)
        self.assertTrue(any(c.startswith("machiya_return=;") for c in cookies_of(headers)))

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
                                         {"Cookie": "hister=%s; machiya_return=%s" % (anon, ret)})
        loc = urlsplit(dict(headers)["Location"])
        self.assertEqual((loc.scheme, loc.netloc), ("shiori", "signed-in"))
        frag = {k: v[0] for k, v in parse_qs(loc.fragment).items()}
        self.assertEqual(self.check(frag["sid"])[1]["kind"], "app")
        self.assertIn(frag["hister"], self.fake.sessions)
        self.assertIsNone(cookie_value(headers, "machiya_sso"))          # the app holds the id, not a cookie

    def test_app_flow_from_signin(self):
        session = self.fake.signed_in()
        status, headers, _ = self.public("GET", "/machiya/signin?" + urlencode({"return": "shiori://signed-in",
                                                                                "app": "1"}),
                                         {"Cookie": "hister=" + session})
        self.assertEqual(status, 303)
        self.assertTrue(dict(headers)["Location"].startswith("shiori://signed-in#sid=mhs_"))

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


class SettingsTest(unittest.TestCase):
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
