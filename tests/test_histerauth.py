"""vaultkit.histerauth (AUTH=hister): python3 -m unittest tests.test_histerauth (standard library only; no network:
the helper is a fake fetch)."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from vaultkit import histerauth as ha             # noqa: E402
from vaultkit.identity import IdentityError, tailscale_uid   # noqa: E402

SID = "mhs_" + "A" * 43
SID2 = "mhs_" + "B" * 43
SIGNIN = "https://hister.example.ts.net/machiya/signin"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeHelper:
    """fetch(method, path, headers, timeout): answers from `self.answers[(path, credential)]` or `self.default`;
    `down` raises OSError like a refused connection; records every call."""

    def __init__(self):
        self.calls, self.answers, self.down = [], {}, False
        self.default = (401, b'{"reason":"signed-out"}')
        self.health = (200, b'{"ok":true}')

    def __call__(self, method, path, headers, timeout):
        self.calls.append((method, path, dict(headers), timeout))
        if self.down:
            raise OSError("connection refused")
        if path == "/healthz":
            return self.health
        cred = headers.get("X-Machiya-Session") or headers.get("X-Access-Token")
        if path == "/v1/signout":
            return 204, b""
        return self.answers.get((path, cred), self.default)

    def ok(self, cred, username="owner", uid=1):
        self.answers[("/v1/check", cred)] = (200, json.dumps({"username": username, "user_id": uid,
                                                               "via": "session"}).encode())


class Headers(dict):
    """A mapping with get_all, like http.server's message (case-sensitive keys are fine for these tests)."""

    def __init__(self, *pairs):
        super().__init__()
        self.pairs = list(pairs)
        for k, v in pairs:
            self.setdefault(k, v)

    def get_all(self, name):
        return [v for k, v in self.pairs if k == name] or None


def make(fallback="tailscale", users=("owner",), fallback_users=("me@passkey",), auth_url="http://hl:8081",
         domain="example.ts.net"):
    helper, clock = FakeHelper(), Clock()
    auth = ha.HisterAuth("niwa", SIGNIN, users, "https://niwa.example.ts.net", auth_url, fallback, fallback_users,
                         domain, True, fetch=helper, clock=clock)
    return auth, helper, clock


def cookie_value(cookies, name):
    """The value a Set-Cookie list gives `name` (None when it isn't set)."""
    for c in cookies:
        k, _, rest = c.partition("=")
        if k == name:
            return rest.split(";", 1)[0]
    return None


def quiet(fn, *a, **kw):
    with redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


class SafeReturnTest(unittest.TestCase):
    HOSTS = ["kura.example.ts.net", "niwa.example.ts.net", "dev.example.test:8443"]

    def test_accepts_exact_hosts(self):
        for url in ("https://kura.example.ts.net/n/Note?x=1#top", "https://KURA.example.ts.net/",
                    "https://kura.example.ts.net:443/a", "https://dev.example.test:8443/x"):
            self.assertEqual(ha.safe_return(url, self.HOSTS), url, url)

    def test_refuses(self):
        bad = [
            "", None, 5, "/local/path", "//kura.example.ts.net/", "http://kura.example.ts.net/",
            "https://evil.example/", "https://kura.example.ts.net.evil.example/", "https://evil.kura.example.ts.net/",
            "https://user@kura.example.ts.net/", "https://user:pw@kura.example.ts.net/",
            "https://evil.example\\@kura.example.ts.net/", "https://kura.example.ts.net\\@evil.example/",
            "https://kura.example.ts.net/a b", "https://kura.example.ts.net/\n", "https://kura.example.ts.net/\x00",
            "https://kura.example.ts.net:8443/", "https://dev.example.test/", "https://dev.example.test:9999/",
            "https://kura.example.ts.net:bad/", "javascript:alert(1)", "data:text/html,x",
            "https://kura.example.ts.net/" + "a" * 2048, "https://kura.example.ts.net/\u0085",
            "shiori://signed-in",                                     # no app schemes allowed here
        ]
        for url in bad:
            self.assertIsNone(ha.safe_return(url, self.HOSTS), repr(url))

    def test_app_schemes(self):
        self.assertEqual(ha.safe_return("shiori://signed-in", self.HOSTS, ("shiori",)), "shiori://signed-in")
        self.assertEqual(ha.safe_return("SHIORI://signed-in", self.HOSTS, ("shiori",)), "SHIORI://signed-in")
        for url in ("shiori://signed-in#x", "shiori://a@b/", "other://signed-in", "javascript:alert(1)"):
            self.assertIsNone(ha.safe_return(url, self.HOSTS, ("shiori", "javascript")), url)
        # the https rules still hold in the app flow
        self.assertIsNone(ha.safe_return("https://evil.example/", self.HOSTS, ("shiori",)))

    def test_host_entries_are_strict(self):
        self.assertIsNone(ha.safe_return("https://kura.example.ts.net/", ["kura.example.ts.net/x", "", "@a"]))
        self.assertIsNone(ha.safe_return("https://kura.example.ts.net/", []))

    def test_signin_url(self):
        self.assertEqual(ha.signin_url(SIGNIN, ""), SIGNIN)
        self.assertEqual(ha.signin_url(SIGNIN, "https://k.t/a?b=c&d"),
                         SIGNIN + "?return=https%3A%2F%2Fk.t%2Fa%3Fb%3Dc%26d")
        self.assertEqual(ha.signin_url(SIGNIN + "?app=1", "x"), SIGNIN + "?app=1&return=x")


class HisterHeadersTest(unittest.TestCase):
    def test_token_file_reread(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "token")
            self.assertEqual(ha.hister_headers(""), {"Origin": "hister://"})
            self.assertEqual(quiet(ha.hister_headers, path), {"Origin": "hister://"})        # missing: no token
            with open(path, "w") as f:
                f.write("s3cret-one\n")
            self.assertEqual(ha.hister_headers(path), {"Origin": "hister://", "X-Access-Token": "s3cret-one"})
            with open(path + ".new", "w") as f:
                f.write("s3cret-two-longer")
            os.replace(path + ".new", path)                                                   # rotated: re-read
            self.assertEqual(ha.hister_headers(path)["X-Access-Token"], "s3cret-two-longer")
            with open(path, "w") as f:
                f.write("bad token with spaces")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertNotIn("X-Access-Token", ha.hister_headers(path))
            self.assertNotIn("s3cret", err.getvalue())
        finally:
            shutil.rmtree(tmp)


class LoadForTest(unittest.TestCase):
    BASE = {"NIWA_AUTH": "hister", "NIWA_AUTH_URL": "http://hister-login:8081", "NIWA_AUTH_SIGNIN_URL": SIGNIN,
            "NIWA_HISTER_USERS": "owner", "NIWA_USERS": "me@passkey", "NIWA_PUBLIC_URL": "https://niwa.example.ts.net",
            "NIWA_BIND_BEHIND_PROXY": "1", "MACHIYA_COOKIE_DOMAIN": ".example.ts.net"}

    def test_not_hister(self):
        self.assertIsNone(ha.load_for("niwa", {}))
        self.assertIsNone(ha.load_for("niwa", {"NIWA_AUTH": "tailscale"}))

    def test_loads(self):
        a = ha.load_for("niwa", dict(self.BASE))
        self.assertEqual((a.fallback, a.users, a.cookie_domain, a.auth_url), ("tailscale", {"owner"}, "example.ts.net",
                                                                              "http://hister-login:8081"))
        self.assertEqual(a.fallback_users, {"me@passkey"})
        self.assertFalse(a.standalone)

    def test_konbini_names(self):
        env = {k.replace("NIWA_", "KANBAN_"): v for k, v in self.BASE.items()}
        env.pop("KANBAN_USERS")
        env.pop("KANBAN_PUBLIC_URL")
        env.update({"KANBAN_TAILNET_USERS": "me@passkey", "KANBAN_BOARD_URL": "https://konbini.example.ts.net"})
        a = ha.load_for("konbini", env)
        self.assertEqual(a.public_url, "https://konbini.example.ts.net")
        self.assertEqual(a.fallback_users, {"me@passkey"})

    def test_kura_none(self):
        env = {k.replace("NIWA_", "KURA_"): v for k, v in self.BASE.items()}
        env.pop("KURA_USERS")
        env.pop("KURA_BIND_BEHIND_PROXY")
        env["KURA_AUTH_FALLBACK"] = "none"
        a = ha.load_for("kura", env)                      # no bind check: nothing reads Tailscale-User-Login
        self.assertEqual(a.fallback, "none")

    def test_refusals(self):
        cases = [
            {"NIWA_AUTH_SIGNIN_URL": ""}, {"NIWA_AUTH_SIGNIN_URL": "javascript:x"}, {"NIWA_HISTER_USERS": ""},
            {"NIWA_HISTER_USERS": "owner,*"}, {"NIWA_USERS": "*"}, {"NIWA_AUTH_FALLBACK": "open"},
            {"NIWA_AUTH_FALLBACK": "none", "NIWA_AUTH_URL": ""}, {"NIWA_PUBLIC_URL": ""},
            {"MACHIYA_IDENTITY_FILE": "/etc/machiya/identity.toml"}, {"NIWA_BIND_BEHIND_PROXY": ""},
            {"MACHIYA_COOKIE_DOMAIN": "bad domain"},
        ]
        for change in cases:
            env = dict(self.BASE, **change)
            with self.assertRaises(IdentityError, msg=repr(change)):
                ha.load_for("niwa", env)

    def test_loopback_bind_needs_no_proxy_flag(self):
        env = dict(self.BASE, NIWA_BIND_BEHIND_PROXY="")
        self.assertIsNotNone(ha.load_for("niwa", env, bind="127.0.0.1"))

    def test_no_helper_runs_on_tailscale_with_a_warning(self):
        env = dict(self.BASE, NIWA_AUTH_URL="")
        err = io.StringIO()
        with redirect_stderr(err):
            a = ha.load_for("niwa", env)
        self.assertIn("hister mode, but no sign-in service: Tailscale identity only", err.getvalue())
        self.assertTrue(a.standalone)
        r = a.resolve(Headers(("Tailscale-User-Login", "me@passkey")))
        self.assertEqual((r.status, r.reason, r.banner), (200, "ok", False))
        self.assertEqual(r.principal.uid, tailscale_uid("me@passkey"))
        self.assertEqual(a.resolve(Headers()).status, 503)
        self.assertEqual(a.resolve(Headers(("Tailscale-User-Login", "other@x"))).status, 403)


class CredentialTest(unittest.TestCase):
    def setUp(self):
        self.a, _, _ = make()

    def test_kinds(self):
        c = self.a.credential
        self.assertEqual(c(Headers(("X-Access-Token", "tok"))), ("token", "tok", False))
        self.assertEqual(c(Headers(("Authorization", "Bearer tok"))), ("token", "tok", False))
        self.assertEqual(c(Headers(("Authorization", "bearer " + SID))), ("sid", SID, False))
        self.assertEqual(c(Headers(("Cookie", "a=b; machiya_sso=%s" % SID))), ("sid", SID, True))
        self.assertEqual(c(Headers(("Cookie", "machiya_sso=junk; machiya_sso=%s" % SID))), ("sid", SID, True))
        self.assertEqual(c(Headers()), (None, "", False))
        # the token wins over the cookie (first match decides)
        self.assertEqual(c(Headers(("X-Access-Token", "tok"), ("Cookie", "machiya_sso=" + SID)))[0], "token")

    def test_bad(self):
        c = self.a.credential
        for h in (Headers(("Authorization", "Basic abc")), Headers(("Authorization", "Bearer ")),
                  Headers(("Authorization", "Bearer mhs_short")), Headers(("Authorization", "Bearer mch_ab_cd")),
                  Headers(("Authorization", "Bearer mcd_x.y")), Headers(("X-Access-Token", "a b")),
                  Headers(("X-Access-Token", "")), Headers(("Authorization", "Bearer a"), ("Authorization", "Bearer b")),
                  Headers(("X-Access-Token", "a"), ("X-Access-Token", "b"))):
            self.assertEqual(c(h)[0], "bad", h.pairs)
        self.assertEqual(c(Headers(("Cookie", "machiya_sso=junk"))), ("bad", "", True))


class CookieNameTest(unittest.TestCase):
    """MACHIYA_SSO_COOKIE: a second stack on the same cookie domain (a dev stack) reads, clears and guards with its
    own names, and never reads the default machiya_sso (another stack's)."""

    def test_default_and_setting(self):
        self.assertEqual(ha.sso_cookie_name({}), "machiya_sso")
        self.assertEqual(ha.sso_cookie_name({"MACHIYA_SSO_COOKIE": " machiya_dev_sso "}), "machiya_dev_sso")
        for bad in ("a b", "x;y", "a=b", "é", "x" * 61):
            with self.assertRaises(ha.IdentityError):
                ha.sso_cookie_name({"MACHIYA_SSO_COOKIE": bad})
        a = ha.load_for("niwa", dict(LoadForTest.BASE, MACHIYA_SSO_COOKIE="machiya_dev_sso"))
        self.assertEqual((a.sso_cookie, a.room_cookie, a.try_cookie, a.state_cookie, a.out_cookie),
                         ("machiya_dev_sso", "__Host-machiya_dev_sso_niwa", "__Host-machiya_dev_sso_niwa_try",
                          "__Host-machiya_dev_sso_niwa_state", "__Host-machiya_dev_sso_niwa_out"))
        self.assertEqual(ha.load_for("niwa", dict(LoadForTest.BASE)).sso_cookie, "machiya_sso")

    def test_own_name_only(self):
        helper, clock = FakeHelper(), Clock()
        a = ha.HisterAuth("niwa", SIGNIN, ("owner",), "https://niwa.example.ts.net", "http://hl:8081", "tailscale",
                          ("me@passkey",), "example.ts.net", True, fetch=helper, clock=clock,
                          sso_cookie="machiya_dev_sso")
        helper.ok(SID)
        other = "mhs_" + "B" * 43
        self.assertEqual(a.credential(Headers(("Cookie", "machiya_sso=%s; machiya_dev_sso=%s" % (other, SID)))),
                         ("sid", SID, True))
        self.assertEqual(a.credential(Headers(("Cookie", "machiya_sso=" + other))), (None, "", False))
        self.assertEqual(a.resolve(Headers(("Cookie", "machiya_sso=%s; machiya_dev_sso=%s" % (other, SID)))).status,
                         200)
        r = a.resolve(Headers(("Cookie", "machiya_dev_sso=" + other)), path="/")      # signed out: its own cleared
        self.assertTrue(any(c.startswith("machiya_dev_sso=;") and "Max-Age=0" in c for c in r.cookies))
        self.assertTrue(any(c.startswith("__Host-machiya_dev_sso_niwa_try=1;") for c in r.cookies))
        self.assertFalse(any(c.startswith(("machiya_sso=", "machiya_sso_try=", "__Host-machiya_sso"))
                             for c in r.cookies))
        with self.assertRaises(ha.IdentityError):
            ha.HisterAuth("niwa", SIGNIN, ("owner",), "https://n.example", "http://hl:8081", sso_cookie="bad name")


class ResolveTest(unittest.TestCase):
    def cookie(self, sid=SID):
        return Headers(("Cookie", "machiya_sso=" + sid))

    def test_signed_in_session(self):
        a, h, _ = make()
        h.ok(SID)
        r = a.resolve(self.cookie())
        self.assertEqual((r.status, r.reason, r.actor, r.banner, r.cookies), (200, "ok", "hister:owner", False, []))
        self.assertTrue(r.principal.owner)
        self.assertEqual(r.principal.uid, tailscale_uid("me@passkey"))   # the one fallback login keys preferences
        self.assertEqual(h.calls[0][1:], ("/v1/check", {"X-Machiya-Session": SID, "Accept": "application/json",
                                                         "X-Machiya-Room": "https://niwa.example.ts.net"}, 2.0))

    def test_app_and_token(self):
        a, h, _ = make()
        h.ok(SID)
        h.ok("htok")
        self.assertEqual(a.resolve(Headers(("Authorization", "Bearer " + SID))).actor, "app:owner")
        r = a.resolve(Headers(("X-Access-Token", "htok")), is_page=False)
        self.assertEqual((r.status, r.actor), (200, "token:owner"))
        self.assertEqual(h.calls[-1][2]["X-Access-Token"], "htok")
        self.assertEqual(a.resolve(Headers(("Authorization", "Bearer htok"))).actor, "token:owner")

    def test_cache_30s_when_signed_in(self):
        a, h, clock = make()
        h.ok(SID)
        a.resolve(self.cookie())
        clock.t += 29
        a.resolve(self.cookie())
        self.assertEqual(len(h.calls), 1)
        clock.t += 2
        a.resolve(self.cookie())
        self.assertEqual(len(h.calls), 2)

    def test_other_hister_user_is_403_no_fallback(self):
        a, h, _ = make()
        h.ok(SID, username="someone-else")
        r = a.resolve(Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey")))
        self.assertEqual((r.status, r.reason, r.location, r.principal), (403, "not-allowed", None, None))

    def test_signed_out_page_redirects_once_and_clears(self):
        a, h, clock = make()
        r = a.resolve(Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey")),
                      path="/n/Note?x=1")
        self.assertEqual((r.status, r.reason, r.principal), (401, "signed-out", None))
        nonce = cookie_value(r.cookies, "__Host-machiya_sso_niwa_state")
        self.assertEqual(r.location, SIGNIN + "?return=https%3A%2F%2Fniwa.example.ts.net%2Fn%2FNote%3Fx%3D1&state="
                         + ha.state_hash(nonce))
        self.assertIn("machiya_sso=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; Secure; Domain=example.ts.net",
                      r.cookies)
        self.assertTrue(any(c.startswith("__Host-machiya_sso_niwa_try=1;") and "Max-Age=30" in c
                            and "Domain" not in c for c in r.cookies))
        # the guard is set: the next one is a page with a link, not another redirect
        r2 = a.resolve(Headers(("Cookie", "__Host-machiya_sso_niwa_try=1")))
        self.assertEqual((r2.status, r2.location), (401, None))
        self.assertTrue(r2.signin.startswith(SIGNIN + "?return="))
        self.assertIn("&state=", r2.signin)                                       # the link comes back with a code
        self.assertFalse(any(c.startswith("__Host-machiya_sso_niwa_try=1") for c in r2.cookies))

    def test_signed_out_cached_5s(self):
        a, h, clock = make()
        a.resolve(self.cookie())
        clock.t += 4
        a.resolve(self.cookie())
        self.assertEqual(len(h.calls), 1)
        clock.t += 2
        a.resolve(self.cookie())
        self.assertEqual(len(h.calls), 2)

    def test_signed_out_never_falls_back(self):
        for status in (401, 403, 400):
            a, h, _ = make()
            h.default = (status, b"")
            r = a.resolve(Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey")))
            self.assertEqual((r.status, r.principal, r.banner), (401, None, False), status)
            self.assertEqual(a.fallback_total, 0)

    def test_api_gets_json_never_a_redirect(self):
        a, h, _ = make()
        r = a.resolve(self.cookie(), is_page=False, path="/api/x")
        self.assertEqual((r.status, r.location), (401, None))
        self.assertFalse(any(c.startswith("machiya_sso_try") for c in r.cookies))
        status, headers, body = a.respond(r, is_page=False)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body), {"error": "sign in", "signin": r.signin})
        self.assertIn(("Content-Type", "application/json"), headers)

    def test_no_credential(self):
        a, h, _ = make()
        r = a.resolve(Headers(), path="/")
        self.assertEqual((r.status, r.reason), (401, "signed-out"))
        self.assertTrue(r.location)
        self.assertEqual([c[1] for c in h.calls], ["/healthz"])          # it asked whether sign-in is up

    def test_unavailable_falls_back_with_banner(self):
        for setup in ("down", "5xx", "off", "junk200", "timeout"):
            a, h, _ = make()
            if setup == "down":
                h.down = True
            elif setup == "5xx":
                h.default = (502, b"bad gateway")
            elif setup == "off":
                h.default = (503, b'{"reason":"user-handling-off"}')
            elif setup == "junk200":
                h.default = (200, b"")
            else:
                def slow(*args):
                    raise TimeoutError("timed out")
                a.fetch = slow
            r = quiet(a.resolve, Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "Me@Passkey")))
            self.assertEqual((r.status, r.reason, r.banner, r.actor), (200, "fallback", True, "fallback:Me@Passkey"),
                             setup)
            self.assertEqual(r.principal.uid, tailscale_uid("me@passkey"))
            self.assertEqual(a.fallback_total, 1)

    def test_unavailable_fallback_refusals(self):
        a, h, _ = make()
        h.down = True
        self.assertEqual(quiet(a.resolve, self.cookie()).status, 503)                        # no Tailscale login
        r = quiet(a.resolve, Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "other@x")))
        self.assertEqual(r.status, 403)
        r = quiet(a.resolve, Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey"),
                                     ("Tailscale-User-Login", "me@passkey")))
        self.assertEqual(r.status, 503)                                                         # sent twice
        self.assertEqual(a.fallback_total, 0)

    def test_fallback_is_not_cached(self):
        a, h, clock = make()
        h.down = True
        quiet(a.resolve, Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey")))
        h.down = False
        h.ok(SID)
        clock.t += 6                                               # the "down" answer was cached 5 s only
        r = quiet(a.resolve, Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey")))
        self.assertEqual((r.reason, r.banner), ("ok", False))

    def test_user_handling_off_cached_10s(self):
        a, h, clock = make()
        h.default = (503, b'{"reason":"user-handling-off"}')
        hdr = Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey"))
        quiet(a.resolve, hdr)
        clock.t += 9
        quiet(a.resolve, hdr)
        self.assertEqual(len(h.calls), 1)
        clock.t += 2
        quiet(a.resolve, hdr)
        self.assertEqual(len(h.calls), 2)

    def test_none_fallback_is_503(self):
        a, h, clock = make(fallback="none", fallback_users=())
        h.down = True
        r = quiet(a.resolve, Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey")))
        self.assertEqual((r.status, r.reason, r.principal), (503, "unavailable", None))
        r = quiet(a.resolve, Headers(("Tailscale-User-Login", "me@passkey")))            # no credential either
        self.assertEqual(r.status, 503)
        status, _, body = a.respond(r, is_page=False)
        self.assertEqual((status, json.loads(body)["error"]), (503, "sign-in is unavailable"))
        status, _, body = a.respond(r, is_page=True)
        self.assertEqual(status, 503)
        self.assertIn(b"Sign-In Is Unavailable", body)
        # and a signed-in owner still gets in: Kura's uid is the Hister username's hash
        h.down = False
        h.ok(SID)
        clock.t += 6                                               # the cached "down" lasts 5 s
        r = a.resolve(self.cookie())
        self.assertEqual(r.status, 200)
        self.assertTrue(r.principal.uid.startswith("hi:") and len(r.principal.uid) == 35)

    def test_health_flag(self):
        a, h, clock = make()
        h.health = (503, b'{"ok":false}')
        r = quiet(a.resolve, Headers(("Tailscale-User-Login", "me@passkey")))          # never signed in: fallback
        self.assertEqual((r.status, r.reason), (200, "fallback"))
        quiet(a.resolve, Headers(("Tailscale-User-Login", "me@passkey")))
        self.assertEqual([c[1] for c in h.calls], ["/healthz"])                    # the flag is kept 10 s
        h.health = (200, b'{"ok":true}')
        clock.t += 11
        r = quiet(a.resolve, Headers(("Tailscale-User-Login", "me@passkey")))
        self.assertEqual((r.status, r.reason), (401, "signed-out"))
        self.assertEqual(h.calls[-1][3], 1.0)                                      # /healthz: 1 s timeout

    def test_check_outcome_sets_health(self):
        a, h, clock = make()
        h.down = True
        quiet(a.resolve, Headers(("Cookie", "machiya_sso=" + SID), ("Tailscale-User-Login", "me@passkey")))
        h.down = False
        r = a.resolve(Headers(("Tailscale-User-Login", "me@passkey")))            # flag says down: no /healthz
        self.assertEqual(r.reason, "fallback")
        self.assertNotIn("/healthz", [c[1] for c in h.calls])

    def test_bad_credentials_are_401_never_passed_over(self):
        a, h, _ = make()
        for hdr in (Headers(("Authorization", "Bearer mch_ab_cd"), ("Tailscale-User-Login", "me@passkey")),
                    Headers(("Cookie", "machiya_sso=junk"), ("Tailscale-User-Login", "me@passkey"))):
            r = a.resolve(hdr)
            self.assertEqual(r.status, 401)
        self.assertEqual(h.calls, [])

    def test_guard_cleared_on_success(self):
        a, h, _ = make()
        h.ok(SID)
        r = a.resolve(Headers(("Cookie", "machiya_sso=%s; __Host-machiya_sso_niwa_try=1" % SID)))
        self.assertEqual(r.status, 200)
        self.assertTrue(any(c.startswith("__Host-machiya_sso_niwa_try=;") and "Max-Age=0" in c for c in r.cookies))

    def test_lru_bound(self):
        a, h, _ = make()
        for i in range(ha.CACHE_MAX + 10):
            a.check("token", "t%d" % i)
        self.assertEqual(len(a.cache), ha.CACHE_MAX)

    def test_never_raises(self):
        a, h, _ = make()

        class Broken:
            def get(self, name):
                raise RuntimeError("boom")
        r = quiet(a.resolve, Broken())
        self.assertEqual((r.status, r.principal), (503, None))

    def test_path_is_kept_local(self):
        a, _, _ = make()
        for p in ("//evil.example/x", "http://evil", "/\\evil", "/a b"):
            self.assertEqual(a.signin_location(p), SIGNIN + "?return=https%3A%2F%2Fniwa.example.ts.net%2F")


class SignoutTest(unittest.TestCase):
    def test_signout(self):
        a, h, clock = make()
        h.ok(SID)
        hdr = Headers(("Cookie", "machiya_sso=" + SID))
        self.assertEqual(a.resolve(hdr).status, 200)
        ended, cookies = a.signout(hdr)
        self.assertTrue(ended)
        self.assertEqual(h.calls[-1][:2], ("POST", "/v1/signout"))
        self.assertEqual(h.calls[-1][2]["X-Machiya-Session"], SID)
        self.assertIn("machiya_sso=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; Secure; Domain=example.ts.net", cookies)
        # this room's cached "signed in" is gone at once
        self.assertEqual(a.resolve(hdr).status, 401)

    def test_signout_helper_down(self):
        a, h, _ = make()
        h.down = True
        ended, cookies = a.signout(Headers(("Authorization", "Bearer " + SID)))
        self.assertFalse(ended)
        self.assertTrue(cookies)

    def test_signout_without_session(self):
        a, h, _ = make()
        self.assertTrue(a.signout(Headers())[0])
        self.assertEqual(h.calls, [])


class PiecesTest(unittest.TestCase):
    def test_banner_and_meta(self):
        self.assertEqual(ha.banner_html(), '<div class="machiya-banner" role="status">%s</div>' % ha.BANNER_TEXT)
        self.assertEqual(ha.signin_meta(), '<meta name="machiya-signin" content="/signout">\n')
        for bad in ("//x", "https://x", "/a b", "", None):
            self.assertEqual(ha.signin_meta(bad), "")

    def test_respond_pages(self):
        a, h, _ = make()
        r = a.resolve(Headers(), path="/x")
        status, headers, body = a.respond(r)
        self.assertEqual(status, 302)
        self.assertIn(("Location", r.location), headers)
        r2 = a.resolve(Headers(("Cookie", "__Host-machiya_sso_niwa_try=1")))
        status, headers, body = a.respond(r2)
        self.assertEqual(status, 401)
        self.assertIn(b"Sign In", body)
        self.assertIn(r2.signin.replace("&", "&amp;").encode(), body)

    def test_settings_refused_in_constructor(self):
        with self.assertRaises(IdentityError):
            ha.HisterAuth("kura", SIGNIN, ["owner"], "https://k", "", "none")
        with self.assertRaises(IdentityError):
            ha.HisterAuth("kura", SIGNIN, ["*"], "https://k", "http://h", "none")
        with self.assertRaises(IdentityError):
            ha.HisterAuth("kura", "", ["o"], "https://k", "http://h", "none")

    def test_ui_hooks_are_opt_in(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui")
        with open(os.path.join(root, "machiya.js")) as f:
            js = f.read()
        self.assertIn('document.querySelector(\'meta[name="machiya-signin"]\')', js)
        self.assertIn("if (signinMeta && window.fetch)", js)
        with open(os.path.join(root, "machiya.css")) as f:
            self.assertIn(".machiya-banner", f.read())


RSID = "mhr_" + "R" * 43
RSID2 = "mhr_" + "S" * 43
RTOK = "mht_" + "T" * 43
CODE = "mhc_" + "C" * 43
NIWA = "https://niwa.example.ts.net"


class RoomSessionTest(unittest.TestCase):
    """v0.22: a host-only cookie per room (__Host-<base>_<room>) from a one-time code; room tokens; X-Machiya-Room."""

    def room_ok(self, h, cred=RSID, room=NIWA, kind="room"):
        h.answers[("/v1/check", cred)] = (200, json.dumps({"username": "owner", "user_id": 1, "via": "session",
                                                           "kind": kind, "room": room}).encode())

    def test_origin_helpers(self):
        self.assertEqual(ha.origin_of("https://Kura.Example:443/x?y"), "https://kura.example")
        self.assertEqual(ha.origin_of("http://h:8080/"), "http://h:8080")
        self.assertEqual(ha.origin_of("https://127.0.0.3:19201"), "https://127.0.0.3:19201")
        for bad in ("", "ftp://x", "https://", "https://u@x", "https://x\\y", "https://x y", None, "javascript:x"):
            self.assertIsNone(ha.origin_of(bad), bad)
        self.assertEqual(ha.origins_header("https://a.example, https://b.example:8443"),
                         ["https://a.example", "https://b.example:8443"])
        self.assertEqual(ha.origins_header("https://a.example, junk"), [])
        n = ha.new_nonce()
        self.assertTrue(ha.NONCE_RE.match(n) and ha.NONCE_RE.match(ha.state_hash(n)))
        self.assertNotEqual(ha.state_hash(n), n)

    def test_cookie_names(self):
        a, _, _ = make()
        self.assertEqual((a.room_cookie, a.state_cookie, a.try_cookie, a.out_cookie),
                         ("__Host-machiya_sso_niwa", "__Host-machiya_sso_niwa_state", "__Host-machiya_sso_niwa_try",
                          "__Host-machiya_sso_niwa_out"))
        plain = ha.HisterAuth("kura", SIGNIN, ("owner",), "http://localhost:8080", "http://hl:8081", "none",
                              secure=False, fetch=FakeHelper())
        self.assertEqual(plain.room_cookie, "machiya_sso_kura")      # no __Host- without https
        self.assertTrue(all("Secure" not in c for c in plain.clear_cookies()))

    def test_credentials(self):
        a, _, _ = make()
        c = a.credential
        self.assertEqual(c(Headers(("Cookie", "__Host-machiya_sso_niwa=" + RSID))), ("room", RSID, True))
        self.assertEqual(c(Headers(("Authorization", "Bearer " + RSID))), ("room", RSID, False))
        self.assertEqual(c(Headers(("Authorization", "Bearer " + RTOK))), ("rtoken", RTOK, False))
        # the room's own cookie first, the legacy shared-domain one after it
        self.assertEqual(c(Headers(("Cookie", "machiya_sso=%s; __Host-machiya_sso_niwa=%s" % (SID, RSID)))),
                         ("room", RSID, True))
        self.assertEqual(c(Headers(("Cookie", "machiya_sso=" + SID))), ("sid", SID, True))
        # another room's cookie (a dev stack: one host, rooms on ports) is not this room's
        self.assertEqual(c(Headers(("Cookie", "__Host-machiya_sso_kura=" + RSID))), (None, "", False))
        for bad in (Headers(("Cookie", "__Host-machiya_sso_niwa=" + SID)),          # an mhs_ in the room cookie
                    Headers(("Cookie", "__Host-machiya_sso_niwa=junk"))):
            self.assertEqual(c(bad), ("bad", "", True))
        for bad in ("Bearer mhr_short", "Bearer mht_short", "Bearer " + CODE):
            self.assertEqual(c(Headers(("Authorization", bad)))[0], "bad", bad)

    def test_room_session_names_the_room(self):
        a, h, _ = make()
        self.room_ok(h)
        r = a.resolve(Headers(("Cookie", "__Host-machiya_sso_niwa=" + RSID)))
        self.assertEqual((r.status, r.actor, r.principal.via), (200, "hister:owner", "hister"))
        self.assertEqual(h.calls[-1][2], {"X-Machiya-Session": RSID, "X-Machiya-Room": NIWA,
                                          "Accept": "application/json"})

    def test_room_session_of_another_room_is_refused(self):
        a, h, _ = make()
        self.room_ok(h, room="https://kura.example.ts.net")          # a helper that didn't check (or a mix-up)
        r = a.resolve(Headers(("Cookie", "__Host-machiya_sso_niwa=" + RSID)), is_page=False)
        self.assertEqual((r.status, r.json()["reason"]), (401, "wrong-room"))
        self.assertTrue(any(c.startswith("__Host-machiya_sso_niwa=;") and "Max-Age=0" in c for c in r.cookies))

    def test_room_token(self):
        a, h, _ = make()
        self.room_ok(h, RTOK, kind="token")
        h.answers[("/v1/check", RTOK)] = (200, json.dumps({"username": "owner", "user_id": 1, "via": "token",
                                                           "kind": "token"}).encode())
        r = a.resolve(Headers(("Authorization", "Bearer " + RTOK)), is_page=False)
        self.assertEqual((r.status, r.actor, r.principal.via), (200, "token:owner", "token"))
        self.assertEqual(h.calls[-1][2]["X-Machiya-Session"], RTOK)

    def test_legacy_refusal_says_why(self):
        a, h, _ = make()
        h.answers[("/v1/check", "htok")] = (401, b'{"reason":"legacy-off"}')
        r = a.resolve(Headers(("X-Access-Token", "htok")), is_page=False)
        body = r.json()
        self.assertEqual((r.status, body["reason"]), (401, "legacy-off"))
        self.assertIn("room token", body["detail"])

    def test_accept_origins(self):
        env = dict(LoadForTest.BASE, NIWA_AUTH_ACCEPT_ORIGINS="https://search.example.ts.net, https://shiori.example.ts.net/")
        a = quiet(ha.load_for, "niwa", env)
        self.assertEqual(a.audiences, [NIWA, "https://search.example.ts.net", "https://shiori.example.ts.net"])
        for bad in ("search.example", "https://x/path", "ftp://x"):
            with self.assertRaises(IdentityError):
                quiet(ha.load_for, "niwa", dict(env, NIWA_AUTH_ACCEPT_ORIGINS=bad))
        h = FakeHelper()
        a.fetch = h
        self.room_ok(h, room="https://search.example.ts.net")       # the hosted pages' cookie, passed on by nginx
        r = a.resolve(Headers(("Cookie", "__Host-machiya_sso_niwa=" + RSID)))
        self.assertEqual(r.status, 200)
        self.assertEqual(h.calls[-1][2]["X-Machiya-Room"],
                         NIWA + ", https://search.example.ts.net, https://shiori.example.ts.net")

    def test_page_trip_carries_state(self):
        a, h, _ = make()
        r = a.resolve(Headers(), path="/n/Note")
        nonce = cookie_value(r.cookies, "__Host-machiya_sso_niwa_state")
        self.assertTrue(ha.NONCE_RE.match(nonce))
        state = [c for c in r.cookies if c.startswith("__Host-machiya_sso_niwa_state=")][0]
        for attr in ("Path=/", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=600"):
            self.assertIn(attr, state)
        self.assertNotIn("Domain", state)
        self.assertTrue(r.location.endswith("&state=" + ha.state_hash(nonce)))
        self.assertNotIn(nonce, r.location)                             # only its hash travels
        api = a.resolve(Headers(), is_page=False, path="/api/x")        # an API's address: no state (no cookie
        self.assertNotIn("state=", api.signin)                          # behind it); the page makes its own trip
        self.assertIsNone(cookie_value(api.cookies, "__Host-machiya_sso_niwa_state"))
        self.assertEqual(a.signin_location("/machiya/callback?code=x"),
                         SIGNIN + "?return=https%3A%2F%2Fniwa.example.ts.net%2F")    # never back to a used code

    def callback(self, a, code=CODE, nonce="N" * 43, extra=()):
        cookie = "__Host-machiya_sso_niwa_state=%s; __Host-machiya_sso_niwa_try=1" % nonce if nonce else ""
        return a.resolve(Headers(("Cookie", cookie), *extra), True, "/machiya/callback?code=" + code)

    def test_callback_trades_the_code(self):
        a, h, _ = make()
        h.answers[("/v1/redeem", None)] = (200, json.dumps({
            "session": RSID, "return": NIWA + "/n/Note?x=1", "username": "owner", "user_id": 1,
            "max_age": 86400 * 10, "prefs": {"theme": "night"}}).encode())
        r = self.callback(a)
        self.assertEqual((r.status, r.reason, r.location, r.principal), (302, "callback", NIWA + "/n/Note?x=1", None))
        method, path, sent, timeout = h.calls[-1]
        self.assertEqual((method, path), ("POST", "/v1/redeem"))
        self.assertEqual((sent["X-Machiya-Code"], sent["X-Machiya-Room"], sent["X-Machiya-State"]),
                         (CODE, NIWA, "N" * 43))
        room = [c for c in r.cookies if c.startswith("__Host-machiya_sso_niwa=" + RSID)][0]
        for attr in ("Path=/", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=864000"):
            self.assertIn(attr, room)
        self.assertNotIn("Domain", room)
        for gone in ("__Host-machiya_sso_niwa_state", "__Host-machiya_sso_niwa_try", "__Host-machiya_sso_niwa_out"):
            self.assertEqual(cookie_value(r.cookies, gone), "", gone)
        status, headers, body = a.respond(r, is_page=False)            # always a redirect, even for odd clients
        self.assertEqual((status, dict(headers)["Location"], dict(headers)["Referrer-Policy"]),
                         (302, NIWA + "/n/Note?x=1", "no-referrer"))
        self.assertIn(("Set-Cookie", room), headers)
        # the next request with the new cookie is answered from the cache the redeem filled
        n = len(h.calls)
        ok = a.resolve(Headers(("Cookie", "__Host-machiya_sso_niwa=" + RSID)))
        self.assertEqual((ok.status, ok.prefs, len(h.calls)), (200, {"theme": "night"}, n))

    def test_callback_return_stays_in_the_room(self):
        a, h, _ = make()
        for ret in ("https://evil.example/", NIWA + ".evil.example/", "https://kura.example.ts.net/x", None):
            h.answers[("/v1/redeem", None)] = (200, json.dumps({"session": RSID, "return": ret,
                                                               "username": "owner"}).encode())
            self.assertEqual(self.callback(a).location, NIWA + "/", ret)

    def test_callback_refused_is_a_page_not_a_loop(self):
        a, h, _ = make()
        h.answers[("/v1/redeem", None)] = (401, b'{"reason":"bad-code"}')
        for r in (self.callback(a), self.callback(a, nonce=""), self.callback(a, code="mhc_short")):
            self.assertEqual((r.status, r.location, r.principal), (401, None, None))
            nonce = cookie_value(r.cookies, "__Host-machiya_sso_niwa_state")
            self.assertTrue(nonce and r.signin.endswith("&state=" + ha.state_hash(nonce)))    # a fresh trip, by hand
            status, _, body = a.respond(r)
            self.assertEqual(status, 401)
            self.assertIn(b"Sign In", body)
        self.assertEqual(sum(1 for c in h.calls if c[1] == "/v1/redeem"), 1)    # no nonce, a bad code: not asked

    def test_callback_helper_down(self):
        a, h, _ = make()
        h.down = True
        r = quiet(self.callback, a)
        self.assertEqual((r.status, r.reason), (503, "unavailable"))

    def test_signout_room_session(self):
        a, h, _ = make()
        self.room_ok(h)
        hdr = Headers(("Cookie", "__Host-machiya_sso_niwa=" + RSID))
        self.assertEqual(a.resolve(hdr).status, 200)
        ended, cookies = a.signout(hdr)
        self.assertTrue(ended)
        method, path, sent, _ = h.calls[-1]
        self.assertEqual((method, path, sent["X-Machiya-Session"], sent["X-Machiya-Room"]),
                         ("POST", "/v1/signout", RSID, NIWA))
        self.assertEqual(cookie_value(cookies, "__Host-machiya_sso_niwa"), "")
        marker = [c for c in cookies if c.startswith("__Host-machiya_sso_niwa_out=1")][0]
        self.assertNotIn("Domain", marker)
        self.assertEqual(a.resolve(hdr).status, 401)                    # this room's cached answer is gone at once

    def test_forward_prefs_with_a_room_session(self):
        a, h, _ = make()
        self.room_ok(h)
        hdr = Headers(("Cookie", "__Host-machiya_sso_niwa=" + RSID))
        res = a.resolve(hdr)
        h.answers[("/v1/prefs", RSID)] = (200, b'{"v":1,"rev":2,"prefs":{"theme":"day"},"updated":{"theme":1}}')
        status, _, body = a.forward_prefs(res, "GET", hdr)
        self.assertEqual((status, json.loads(body)["prefs"]), (200, {"theme": "day"}))
        self.assertEqual((h.calls[-1][2]["X-Machiya-Session"], h.calls[-1][2]["X-Machiya-Room"]), (RSID, NIWA))


class TokenGateTest(unittest.TestCase):
    """v0.22: machiya-mcp and smallweb (their own Tailscale-header gate) also take a room token scoped to them."""

    def gate(self):
        h, clock = FakeHelper(), Clock()
        return ha.TokenGate("mcp", "http://hl:8081", "https://mcp.example.ts.net/mcp", ["owner"], fetch=h,
                            clock=clock), h, clock

    def test_no_token_is_the_services_own_gate(self):
        g, h, _ = self.gate()
        for hdr in (Headers(), Headers(("Authorization", "Bearer mch_ab_cd")), Headers(("Tailscale-User-Login", "x"))):
            self.assertIsNone(g.resolve(hdr))
        self.assertEqual(h.calls, [])

    def test_token(self):
        g, h, clock = self.gate()
        h.answers[("/v1/check", RTOK)] = (200, b'{"username": "owner", "kind": "token"}')
        r = g.resolve(Headers(("Authorization", "Bearer " + RTOK)))
        self.assertEqual((r.status, r.principal.name, r.principal.via, r.actor), (200, "owner", "token", "token:owner"))
        self.assertEqual(h.calls[-1][2]["X-Machiya-Room"], "https://mcp.example.ts.net")
        clock.t += 29
        g.resolve(Headers(("Authorization", "Bearer " + RTOK)))
        self.assertEqual(len(h.calls), 1)                                     # cached 30 s
        r = g.resolve(Headers(("X-Machiya-Token", RTOK)))                      # the plugin's fixed header
        self.assertEqual((r.status, r.principal.name), (200, "owner"))
        self.assertIsNone(g.resolve(Headers(("X-Machiya-Token", ""))))         # empty (no token set): none
        for bad in (Headers(("X-Machiya-Token", "junk")), Headers(("X-Machiya-Token", RTOK), ("Authorization", "x")),
                    Headers(("X-Machiya-Token", RTOK), ("X-Machiya-Token", RTOK))):
            self.assertEqual(g.resolve(bad).status, 401)

    def test_refusals(self):
        g, h, _ = self.gate()
        h.answers[("/v1/check", RTOK)] = (401, b'{"reason": "wrong-room"}')
        r = g.resolve(Headers(("Authorization", "Bearer " + RTOK)))
        self.assertEqual((r.status, r.json()["reason"]), (401, "wrong-room"))
        self.assertEqual(g.resolve(Headers(("Authorization", "Bearer mht_short"))).status, 401)
        self.assertEqual(g.resolve(Headers(("Authorization", "Bearer " + RTOK), ("Authorization", "x"))).status, 401)
        g2, h2, _ = self.gate()
        h2.answers[("/v1/check", RTOK)] = (200, b'{"username": "other", "kind": "token"}')
        self.assertEqual(g2.resolve(Headers(("Authorization", "Bearer " + RTOK))).status, 403)
        h2.answers[("/v1/check", RSID)] = (200, b'{"username": "owner", "kind": "room"}')
        g3, h3, _ = self.gate()
        h3.answers[("/v1/check", RTOK)] = (200, b'{"username": "owner", "kind": "room"}')   # only a token counts
        self.assertEqual(g3.resolve(Headers(("Authorization", "Bearer " + RTOK))).status, 401)
        g4, h4, _ = self.gate()
        h4.down = True
        self.assertEqual(g4.resolve(Headers(("Authorization", "Bearer " + RTOK))).status, 503)

    def test_settings(self):
        self.assertIsNone(ha.token_gate_for("mcp", "MCP", {}))
        g = ha.token_gate_for("mcp", "MCP", {"MCP_AUTH_URL": "http://hister-login:8081", "MCP_PUBLIC_URL":
                                             "https://mcp.example.ts.net", "MCP_HISTER_USERS": "owner"})
        self.assertEqual(g.origin, "https://mcp.example.ts.net")
        for env in ({"MCP_AUTH_URL": "http://h:8081", "MCP_HISTER_USERS": "owner"},
                    {"MCP_AUTH_URL": "http://h:8081", "MCP_PUBLIC_URL": "https://m"},
                    {"MCP_AUTH_URL": "http://h:8081", "MCP_PUBLIC_URL": "https://m", "MCP_HISTER_USERS": "*"}):
            with self.assertRaises(IdentityError):
                ha.token_gate_for("mcp", "MCP", env)


if __name__ == "__main__":
    unittest.main()


class TrustedFallbackTest(unittest.TestCase):
    """v0.29: with the room's trusted proxies and the peer's address, the Tailscale fallback believes the login header
    only from those proxies; without them (or without the peer), as before."""

    def make(self, trusted, auth_url="http://hl:8081"):
        helper, clock = FakeHelper(), Clock()
        helper.down = True                       # the helper is down, so the fallback decides
        a = ha.HisterAuth("niwa", SIGNIN, ("owner",), "https://niwa.example.ts.net", auth_url, "tailscale",
                          ("me@passkey",), "example.ts.net", True, fetch=helper, clock=clock, trusted=trusted)
        return a

    def test_untrusted_peer_gets_no_fallback(self):
        nets = ha.trusted_proxies("10.210.4.2/32")
        a = self.make(nets)
        h = Headers(("Tailscale-User-Login", "me@passkey"))
        self.assertEqual(quiet(a.resolve, h, client="10.210.4.2").status, 200)
        self.assertEqual(quiet(a.resolve, h, client="10.210.4.9").status, 503)   # as with no login at all
        self.assertEqual(quiet(a.resolve, h).status, 200)                          # no peer given: as before
        self.assertEqual(a.fallback_total, 2)

    def test_standalone_too(self):
        a = self.make(ha.trusted_proxies("10.210.4.2/32"), auth_url="")
        h = Headers(("Tailscale-User-Login", "me@passkey"))
        self.assertEqual(quiet(a.resolve, h, client="10.210.4.2").status, 200)
        self.assertEqual(quiet(a.resolve, h, client="192.0.2.1").status, 503)

    def test_none_and_empty_are_unchanged(self):
        h = Headers(("Tailscale-User-Login", "me@passkey"))
        self.assertEqual(quiet(self.make(None).resolve, h, client="192.0.2.1").status, 200)
        self.assertEqual(quiet(self.make(()).resolve, h, client="192.0.2.1").status, 200)

    def test_load_for_reads_the_rooms_trusted_proxies(self):
        env = {"NIWA_AUTH": "hister", "NIWA_AUTH_SIGNIN_URL": SIGNIN, "NIWA_HISTER_USERS": "owner",
               "NIWA_USERS": "me@passkey", "NIWA_PUBLIC_URL": "https://niwa.example.ts.net",
               "NIWA_AUTH_URL": "http://hl:8081", "NIWA_TRUSTED_PROXIES": "10.210.4.2/32"}
        a = quiet(ha.load_for, "niwa", env, bind="127.0.0.1")
        self.assertEqual(a.trusted, ha.trusted_proxies("10.210.4.2/32"))
        env["NIWA_TRUSTED_PROXIES"] = "proxy"
        with self.assertRaisesRegex(IdentityError, "NIWA_TRUSTED_PROXIES"):
            quiet(ha.load_for, "niwa", env, bind="127.0.0.1")
