"""vaultkit.signin (docs/plans/identity.md, phase 6): python3 -m unittest tests.test_signin (standard library only)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from http.client import HTTPMessage
from io import BytesIO
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from vaultkit import identity as idn, signin   # noqa: E402

FILE = """
version = 1
session_key_file = "session.key"

[principals.owner]
id = "ownerid00000000a"
kind = "person"
owner = true
password = "%(pw)s"

[principals.partner]
id = "partnerid0000000"
kind = "person"
password = "%(pw2)s"

[principals.vm]
kind = "agent"
[[principals.vm.tokens]]
id = "abcd12"
hash = "%(th)s"
"""
PASSWORD, SECRET = "correct horse battery", "s3cr3t-token-value"
HOST = "kura.example"


def headers(*pairs, **kw):
    """http.server's own header class (case-insensitive, get_all), from pairs and keywords."""
    m = HTTPMessage()
    for k, v in list(pairs) + [(k.replace("_", "-"), v) for k, v in kw.items()]:
        m[k] = v
    return m


def form_headers(origin="https://" + HOST, **kw):
    kw.setdefault("Host", HOST)
    kw.setdefault("Content_Type", "application/x-www-form-urlencoded")
    if origin is not None:
        kw["Origin"] = origin
    return headers(**kw)


def form(**fields):
    return urlencode(fields).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "identity.toml")
        with open(os.path.join(self.dir, "session.key"), "wb") as f:
            f.write(b"k" * 43)
        with open(self.path, "w") as f:
            f.write(FILE % {"pw": idn.hash_password(PASSWORD), "pw2": idn.hash_password("partner pass 1"),
                            "th": idn.token_hash(SECRET)})
        self.ident = idn.Identity(self.path, "kura", signin=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def post(self, next="/", password=PASSWORD, name="owner", client="10.0.0.1", **kw):
        return signin.handle_post(self.ident, form_headers(**kw), form(name=name, password=password, next=next), client)

    @staticmethod
    def cookies(hdrs):
        return [v for k, v in hdrs if k == "Set-Cookie"]


class SafeNextTest(unittest.TestCase):
    def test_local_paths_pass(self):
        for good in ("/", "/n/Projects/Kura", "/search?q=a&b=c#top", "/v/work/n/x"):
            self.assertEqual(signin.safe_next(good), good)
        self.assertEqual(signin.safe_next("/n/Café"), "/n/Caf%C3%A9")             # fits a Location header

    def test_hostile_values_fall_back(self):
        for bad in ("//evil.example", "/\\evil.example", "https://evil.example/", "evil", "", None, 5,
                    "/x\r\nSet-Cookie: a=b", "/\tx", "/x\x00", "/x\x7f", "/x\u0085", "/a b", " /x", "/x" * 2000,
                    "javascript:alert(1)", "/signin", "/signout/"):
            self.assertEqual(signin.safe_next(bad), "/", msg=repr(bad))


class SameOriginTest(unittest.TestCase):
    def test_rules(self):
        ok = signin.same_origin
        self.assertTrue(ok(headers(Host=HOST, Origin="https://" + HOST)))
        self.assertTrue(ok(headers(Host=HOST, Referer="https://%s/signin?next=/" % HOST)))   # no Origin: Referer
        # plain http: Host and Origin both come from the page (DNS rebinding), so only the room's own origins count
        self.assertFalse(ok(headers(Host="localhost:8080", Origin="http://localhost:8080"), secure=False))
        self.assertFalse(ok(headers(Host="evil.example:8080", Origin="http://evil.example:8080"), secure=False))
        mine = ("http://localhost:8080",)
        self.assertTrue(ok(headers(Host="localhost:8080", Origin="http://localhost:8080"), False, mine))
        self.assertTrue(ok(headers(Host="localhost:8080", Referer="http://LOCALHOST:8080/x"), False, mine))
        self.assertFalse(ok(headers(Host="evil.example:8080", Origin="http://evil.example:8080"), False, mine))
        self.assertFalse(ok(headers(Host="localhost:8080", Origin="null"), False, mine))
        self.assertFalse(ok(headers(Host=HOST, Origin="http://" + HOST), True, ("http://" + HOST,)))   # secure: https
        self.assertFalse(ok(headers(Host="localhost:8080", Origin="http://localhost:8080")))  # https room, http page
        self.assertFalse(ok(headers(Host=HOST)))                                            # neither
        self.assertFalse(ok(headers(Host=HOST, Origin="null")))
        self.assertFalse(ok(headers(Host=HOST, Origin="https://evil.example")))
        self.assertFalse(ok(headers(Host=HOST, Origin="https://evil.example", Referer="https://%s/" % HOST)))
        self.assertFalse(ok(headers(Host=HOST, Origin="https://%s.evil.example" % HOST)))
        self.assertFalse(ok(headers(Host=HOST, Origin="https://user@" + HOST)))
        self.assertFalse(ok(headers(Origin="https://" + HOST)))                            # no Host
        self.assertFalse(ok(headers(("Host", HOST), ("Origin", "https://" + HOST), ("Origin", "https://evil.example"))))
        self.assertFalse(ok(headers(Host=HOST, Origin="https://[" + HOST)))                # unreadable


class ReviewTest(Base):
    """Regressions for the security review of the sign-in module."""

    def test_prefs_db_errors_are_503(self):
        import sqlite3
        prefs = signin.Prefs(os.path.join(self.dir, "p.sqlite3"))
        self.assertEqual(os.stat(prefs.path).st_mode & 0o777, 0o600)
        token = idn.Principal("vm", "agent", via="token:abcd12")
        hold = sqlite3.connect(prefs.path, isolation_level=None)
        hold.execute("BEGIN EXCLUSIVE")
        try:
            for method in ("GET", "PUT"):
                status, _, out = signin.handle_prefs(prefs, token, method, headers(Content_Type="application/json"),
                                                     b'{"prefs": {"a": "1"}}')
                self.assertEqual((status, json.loads(out)["error"]), (503, "preferences unavailable"), method)
        finally:
            hold.execute("ROLLBACK")
            hold.close()

    def test_content_length_is_digits_once(self):
        import io
        for bad in ("5_0", "+5", "\u0665", "-1", "1e1", " ", "9999999999"):
            self.assertIsNone(signin.read_body(headers(Content_Length=bad), io.BytesIO(b"x" * 50), 100), bad)
        self.assertIsNone(signin.read_body(headers(("Content-Length", "1"), ("Content-Length", "2")),
                                           io.BytesIO(b"xx"), 100))
        self.assertEqual(signin.read_body(headers(Content_Length="2"), io.BytesIO(b"xy"), 100), b"xy")

    def test_ids_are_unique_and_open_has_its_own(self):
        with open(self.path) as f:
            text = f.read()
        import re
        uid = re.search(r'id = "([a-z0-9]{16})"', text).group(1)
        def write(t):
            with open(self.path, "w") as f:
                f.write(t)
        write(text + '\n[principals.bot]\nid = "%s"\nkind = "agent"\n' % uid)
        with self.assertRaises(idn.IdentityError):
            idn.read_file(self.path)
        write(text + '\n[principals.%s]\nkind = "agent"\n' % uid)        # a name that is someone's id
        with self.assertRaises(idn.IdentityError):
            idn.read_file(self.path)
        self.assertEqual(idn.OPEN_OWNER.uid, ":open")


class SignInTest(Base):
    def test_success_sets_cookie_and_redirects(self):
        status, hdrs, body = self.post(next="/n/Projects/Kura?x=1")
        self.assertEqual(status, 303)
        self.assertIn(("Location", "/n/Projects/Kura?x=1"), hdrs)
        (cookie,) = self.cookies(hdrs)
        self.assertTrue(cookie.startswith(idn.SESSION_COOKIE + "="))
        for attr in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
            self.assertIn(attr, cookie)
        who = self.ident.resolve(headers(Cookie=cookie.split(";")[0]))
        self.assertEqual((who.principal.name, who.principal.via), ("owner", "session"))

    def test_hostile_next_goes_home(self):
        for bad in ("//evil.example", "/\\evil.example", "https://evil.example", "/x\r\nLocation: //evil"):
            status, hdrs, _ = self.post(next=bad, client="10.0.1.%d" % len(bad))
            self.assertEqual((status, dict(hdrs)["Location"]), (303, "/"), msg=repr(bad))

    def test_cross_site_post_is_refused(self):
        for kw in ({"origin": "https://evil.example"}, {"origin": None}, {"origin": "null"},
                   {"origin": None, "Referer": "https://evil.example/x"}, {"origin": "http://" + HOST}):
            status, hdrs, _ = self.post(**kw)
            self.assertEqual(status, 403, msg=kw)
            self.assertEqual(self.cookies(hdrs), [])
        self.assertEqual(self.post(origin=None, Referer="https://%s/signin" % HOST)[0], 303)

    def test_wrong_password_is_401_with_the_page(self):
        status, hdrs, body = self.post(password="wrong", name="Owner", next="/n/x")
        self.assertEqual(status, 401)
        self.assertEqual(self.cookies(hdrs), [])
        page = body.decode()
        self.assertIn("Wrong name or password.", page)
        self.assertIn('name="next" value="/n/x"', page)
        self.assertIn('value="Owner"', page)                                   # the name is kept, the password never
        self.assertNotIn("wrong", page.replace("Wrong", ""))
        self.assertIn(("X-Frame-Options", "DENY"), hdrs)
        self.assertIn(("Cache-Control", "no-store"), hdrs)
        # an unknown name answers the same
        status2, _, body2 = self.post(password="wrong", name="Owner", client="10.0.0.2")
        status3, _, body3 = self.post(password="wrong", name="nobody", client="10.0.0.3")
        self.assertEqual((status2, status3), (401, 401))
        self.assertIn("Wrong name or password.", body3.decode())

    def test_throttle_is_429(self):
        for i in range(5):
            self.assertEqual(self.post(password="wrong %d" % i, client="10.0.2.%d" % i)[0], 401)
        status, hdrs, body = self.post(client="10.0.2.9")                      # even the right password waits now
        self.assertEqual(status, 429)
        self.assertIn("Too many tries", body.decode())
        self.assertEqual(self.cookies(hdrs), [])

    def test_body_limits_and_shape(self):
        big = form(name="owner", password="x" * (signin.MAX_FORM + 1))
        self.assertEqual(signin.handle_post(self.ident, form_headers(), big, "c")[0], 413)
        self.assertEqual(self.post(Content_Type="application/json")[0], 415)
        twice = b"name=owner&name=partner&password=x"
        self.assertEqual(signin.handle_post(self.ident, form_headers(), twice, "c")[0], 400)
        self.assertEqual(signin.handle_post(self.ident, form_headers(), b"name=\xff", "c")[0], 400)

    def test_off_without_signin(self):
        off = idn.Identity(self.path, "kura", signin=False)
        self.assertEqual(signin.handle_post(off, form_headers(), form(name="owner", password=PASSWORD), "c")[0], 404)
        self.assertEqual(signin.handle_get(off, headers(), "")[0], 404)
        self.assertEqual(signin.handle_post(None, form_headers(), b"", "c")[0], 404)

    def test_get_page(self):
        status, hdrs, body = signin.handle_get(self.ident, headers(Cookie="theme=day"), "next=//evil.example")
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('name="next" value="/"', page)
        self.assertIn('type="password"', page)
        self.assertIn("theme-day", page)
        self.assertIn('action="/signin"', page)
        status, _, body = signin.handle_get(self.ident, headers(), "next=/n/%22%3E%3Cscript%3E")
        self.assertNotIn("<script>", body.decode())                               # escaped (and percent-encoded)

    def test_signout_clears_the_cookie(self):
        status, hdrs, _ = signin.handle_signout(self.ident, headers(Host=HOST, Origin="https://" + HOST))
        self.assertEqual((status, dict(hdrs)["Location"]), (303, "/"))
        (cookie,) = self.cookies(hdrs)
        self.assertTrue(cookie.startswith(idn.SESSION_COOKIE + "=;"))
        self.assertIn("Max-Age=0", cookie)
        status, hdrs, _ = signin.handle_signout(self.ident, headers(Host=HOST, Origin="https://evil.example"))
        self.assertEqual((status, self.cookies(hdrs)), (403, []))
        self.assertEqual(signin.handle_signout(self.ident, headers(Host=HOST))[0], 403)


class PairTest(Base):
    def pair(self, body, client="10.0.3.1", ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        status, hdrs, out = signin.handle_pair(self.ident, headers(Content_Type=ctype), raw, client)
        return status, json.loads(out)

    def test_pair_success_and_failure(self):
        with open(self.path, "rb") as f:
            data = idn.tomllib.load(f)
        code, device = idn.new_pairing(data, "owner", "iPhone")
        idn.write_file(self.path, data)
        self.ident.checked = 0
        self.assertEqual(self.pair({"code": "WRONGCOD", "device": "iPhone"}),
                         (401, {"error": "unknown or expired code"}))
        status, out = self.pair({"code": code, "device": "iPhone"})
        self.assertEqual((status, out["principal"], out["token"][:4]), (200, "owner", "mcd_"))
        who = self.ident.resolve(headers(Authorization="Bearer " + out["token"]))
        self.assertEqual(who.principal.via, "device:" + device)

    def test_bad_requests(self):
        self.assertEqual(self.pair({"device": "x"})[0], 400)
        self.assertEqual(self.pair({"code": 5})[0], 400)
        self.assertEqual(self.pair(["code"])[0], 400)
        self.assertEqual(self.pair(b"{nope")[0], 400)
        self.assertEqual(self.pair({"code": "ABCD", "device": "x\ny"})[0], 400)
        self.assertEqual(self.pair({"code": "ABCD"}, ctype="text/plain")[0], 415)
        self.assertEqual(self.pair(b"{" + b" " * signin.MAX_PAIR + b"}")[0], 413)

    def test_throttle(self):
        for _ in range(5):
            self.assertEqual(self.pair({"code": "XXXXXXXX", "device": "d"}, client="10.0.3.9")[0], 401)
        self.assertEqual(self.pair({"code": "XXXXXXXX", "device": "d"}, client="10.0.3.9")[0], 429)


class PrefsTest(Base):
    def setUp(self):
        super().setUp()
        self.prefs = signin.Prefs(os.path.join(self.dir, "room.sqlite3"))

    def test_get_put_and_isolation(self):
        self.assertEqual(self.prefs.put("owner", {"theme": "night", "kura.sort": "changed"}),
                         {"kura.sort": "changed", "theme": "night"})
        self.assertEqual(self.prefs.put("partner", {"theme": "day"}), {"theme": "day"})
        self.assertEqual(self.prefs.get_all("owner"), {"kura.sort": "changed", "theme": "night"})
        self.assertEqual(self.prefs.put("owner", {"theme": None, "text_size": "large"}),
                         {"kura.sort": "changed", "text_size": "large"})
        self.assertEqual(self.prefs.get_all("partner"), {"theme": "day"})
        self.assertEqual(self.prefs.get_all("nobody"), {})
        again = signin.Prefs(self.prefs.path)                                         # kept in the file
        self.assertEqual(again.get_all("partner"), {"theme": "day"})

    def test_limits(self):
        for bad in ({"Theme": "x"}, {"": "x"}, {"a" * 65: "x"}, {"a b": "x"}, {"a/b": "x"}, {"x\n": "x"},
                    {"k": 5}, {"k": ["x"]}, {"k": "x" * 4097}, {"k": "é" * 2049}, {"k": "\ud800"}, ["k"]):
            with self.assertRaises(signin.PrefsError, msg=repr(bad)):
                self.prefs.put("owner", bad)
        self.prefs.put("owner", {"k": "x" * 4096, "a" * 64: "", "a.b_c-1": "v"})
        self.prefs.put("owner", {"n%d" % i: "v" for i in range(97)})                  # 100 keys in all
        with self.assertRaises(signin.PrefsError):
            self.prefs.put("owner", {"one-more": "v"})
        with self.assertRaises(signin.PrefsError):                                   # all or nothing
            self.prefs.put("owner", {"n0": None, "x1": "v", "x2": "v"})
        self.assertEqual(len(self.prefs.get_all("owner")), 100)
        self.assertIn("n0", self.prefs.get_all("owner"))
        self.assertEqual(len(self.prefs.put("owner", {"n0": None, "x1": "v"})), 100)  # one out, one in
        with self.assertRaises(signin.PrefsError):
            self.prefs.put("owner", {"n%d" % i: "v" for i in range(101)})

    def call(self, principal, method="PUT", data=None, **kw):
        kw.setdefault("Host", HOST)
        kw.setdefault("Content_Type", "application/json")
        body = json.dumps(data if data is not None else {"prefs": {"theme": "night"}}).encode()
        status, hdrs, out = signin.handle_prefs(self.prefs, principal, method, headers(**kw), body)
        return status, json.loads(out)

    def test_api_same_origin_for_cookies(self):
        session = idn.Principal("owner", "person", True, via="session")
        token = self.ident.resolve(headers(Authorization="Bearer mch_abcd12_" + SECRET)).principal
        self.assertEqual(token.via, "token:abcd12")
        self.assertEqual(self.call(session)[0], 403)                                  # a cookie, no Origin
        self.assertEqual(self.call(session, Origin="https://evil.example")[0], 403)
        for via in ("tailscale", "proxy", "open", ""):                               # ambient logins are cookies too
            self.assertEqual(self.call(idn.Principal("owner", "person", True, via=via))[0], 403, msg=via)
        self.assertEqual(self.call(session, Origin="https://" + HOST), (200, {"prefs": {"theme": "night"}}))
        self.assertEqual(self.call(token, data={"prefs": {"x": "1"}}), (200, {"prefs": {"x": "1"}}))   # no Origin
        self.assertEqual(self.call(session, "GET"), (200, {"prefs": {"theme": "night"}}))          # GET: no rule
        self.assertEqual(self.call(token, "GET"), (200, {"prefs": {"x": "1"}}))                    # its own prefs
        self.assertEqual(self.call(None, "GET")[0], 401)
        self.assertEqual(self.call(token, "DELETE")[0], 405)

    def test_a_reused_name_starts_with_no_prefs(self):
        old = idn.Principal("guest", "person", via="token:aaaa11", uid="guestold00000001")
        self.assertEqual(self.call(old, data={"prefs": {"theme": "day"}})[0], 200)
        new = idn.Principal("guest", "person", via="token:bbbb22", uid="guestnew00000002")
        self.assertEqual(self.call(new, "GET"), (200, {"prefs": {}}))                # keyed by id, not by name
        self.assertEqual(self.call(old, "GET"), (200, {"prefs": {"theme": "day"}}))

    def test_api_bad_bodies(self):
        token = idn.Principal("vm", "agent", via="token:abcd12")
        self.assertEqual(self.call(token, data={"theme": "night"})[0], 400)           # not wrapped in "prefs"
        self.assertEqual(self.call(token, data={"prefs": {"Bad Key": "x"}})[0], 400)
        self.assertEqual(self.call(token, Content_Type="text/plain")[0], 415)
        big = json.dumps({"prefs": {"k": "x" * signin.MAX_PREFS}}).encode()
        self.assertEqual(signin.handle_prefs(self.prefs, token, "PUT", headers(Content_Type="application/json"),
                                             big)[0], 413)


class ReadBodyTest(unittest.TestCase):
    def test_read_body(self):
        self.assertEqual(signin.read_body(headers(Content_Length="3"), BytesIO(b"abcdef"), 10), b"abc")
        self.assertEqual(signin.read_body(headers(), BytesIO(b"abc"), 10), b"")
        for h in (headers(Content_Length="11"), headers(Content_Length="-1"), headers(Content_Length="x"),
                  headers(Content_Length="5"), headers(Transfer_Encoding="chunked")):
            self.assertIsNone(signin.read_body(h, BytesIO(b"abc"), 10))


if __name__ == "__main__":
    unittest.main()
