"""The settings that follow a person (docs/contracts/prefs.md): vaultkit.prefs (schema, store, the migration rule),
signin.handle_prefs (a room's own store), histerauth.forward_prefs (a room in hister mode), the shell's Shared and
This Device sections, and ui/machiya.js's sync rules, run in Node as rooms sharing one cookie jar (tests/js/).

    python3 -m unittest tests.test_prefs        (the machiya.js part needs `node`; skipped without it)
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from vaultkit import histerauth as ha, identity as idn, palettes, prefs, shell, signin   # noqa: E402
from tests.test_histerauth import FakeHelper, Headers, SID, quiet                     # noqa: E402

HOST = "kura.example.test"


class SchemaTest(unittest.TestCase):
    def test_shared_keys(self):
        self.assertEqual(prefs.validate({"theme": "auto", "palette": "nord", "text_size": "xlarge",
                                         "apps_hidden": "searxng, kura,kura"}),
                         {"theme": "system", "palette": "nord", "text_size": "xlarge", "apps_hidden": "kura,searxng"})
        self.assertEqual(prefs.validate({"theme": None, "apps_hidden": ""}), {"theme": None, "apps_hidden": ""})
        self.assertEqual(list(prefs.SHARED["palette"]["values"]), list(palettes.PALETTES))

    def test_refused(self):
        for bad in ({"theme": "sepia"}, {"palette": "Nord"}, {"text_size": "huge"}, {"apps_hidden": "hister,evil"},
                    {"language": "en"}, {"time_zone": "UTC"}, {"previewPane": "on"}, {"kura": "x"},
                    {"other.key": "x"}, {"kura.Bad": "x"}, {"kura." + "a" * 49: "x"}, {"kura.x": 1},
                    {"kura.x": "a" * 1025}, {"kura.x": "a\nb"}, {"theme": ["night"]}, ["theme"],
                    {"n%d" % i: "v" for i in range(101)}):
            with self.assertRaises(prefs.PrefsError, msg=repr(bad)[:80]):
                prefs.validate(bad)

    def test_app_keys(self):
        ok = {"kura.preview_pane": "on", "niwa.link_previews": "off", "konbini.group": "family",
              "shiori.web_results": "on", "machiya.x_1": "é" * 10}
        self.assertEqual(prefs.validate(ok), ok)

    def test_pills(self):
        """Shiori's pill row is Shared (decided 2026-10-05): the store checks its shape, Shiori its ids."""
        v = prefs.validate({"pills": json.dumps({"hidden": ["images"], "order": ["all", "notes"]}, indent=2)})
        self.assertEqual(v, {"pills": '{"order":["all","notes"],"hidden":["images"]}'})
        for bad in ("x", "[]", '{"order": ["A"]}', '{"order": "all"}', '{"x": []}', json.dumps({"order": ["a"] * 33})):
            with self.assertRaises(prefs.PrefsError, msg=bad):
                prefs.validate({"pills": bad})

    def test_schema_json_file_is_current(self):
        """docs/contracts/prefs.schema.json is what `python3 -m vaultkit.prefs` prints (Shiori's tests read it)."""
        with open(os.path.join(ROOT, "docs", "contracts", "prefs.schema.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), json.loads(json.dumps(prefs.schema())))

    def test_machiya_js_knows_the_same_apps_and_sizes(self):
        with open(os.path.join(ROOT, "ui", "machiya.js"), encoding="utf-8") as f:
            js = f.read()
        apps = re.search(r"const APPS = \[([^\]]*)\]", js).group(1)
        self.assertEqual(re.findall(r'"([a-z]+)"', apps), list(prefs.APPS))
        sizes = re.search(r"const TEXT_SIZES = \[([^\]]*)\]", js).group(1)
        self.assertEqual(re.findall(r'"([a-z]+)"', sizes), list(prefs.TEXT_SIZES))
        self.assertEqual([k for k, _ in shell.TEXT_SIZES], list(prefs.TEXT_SIZES))
        self.assertEqual([k for k, _, _, _ in shell.ROOMS] + [k for k, _, _ in shell.NEIGHBOURS] + [shell.HOUSE[0]],
                         list(prefs.APPS))

    def test_etag(self):
        self.assertEqual(prefs.etag(7), '"7"')
        self.assertTrue(prefs.matches('"7"', 7) and prefs.matches('W/"7", "8"', 7) and prefs.matches("*", 7))
        self.assertFalse(prefs.matches('"70"', 7) or prefs.matches(None, 7) or prefs.matches("7", 7))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "prefs.sqlite3")
        self.store = prefs.Store(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_rev_and_updated(self):
        self.assertEqual(self.store.snapshot("u"), (0, {}, {}))
        rev, values, updated = self.store.write("u", {"theme": "day", "palette": "nord"})
        self.assertEqual((rev, values), (1, {"palette": "nord", "theme": "day"}))
        t = updated["theme"]
        self.assertLessEqual(abs(t - time.time()), 5)
        self.assertEqual(self.store.write("u", {"theme": "day"})[0], 1)        # the same value: nothing changes
        rev, values, updated = self.store.write("u", {"theme": None, "text_size": "large"})
        self.assertEqual((rev, values), (2, {"palette": "nord", "text_size": "large"}))
        self.assertEqual(self.store.snapshot("other"), (0, {}, {}))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_delete_keeps_the_revision_going(self):
        self.store.write("u", {"theme": "day"})
        self.assertEqual(self.store.delete("u"), 1)
        rev, values, _ = self.store.snapshot("u")
        self.assertEqual((rev, values), (2, {}))                                # an old ETag never matches again

    def test_only_newer_and_stamps(self):
        self.store.write("u", {"theme": "day"}, stamps={"theme": 100})
        self.store.write("u", {"theme": "night"}, stamps={"theme": 50}, only_newer=True)
        self.assertEqual(self.store.snapshot("u")[1:], ({"theme": "day"}, {"theme": 100}))
        self.store.write("u", {"theme": "night"}, stamps={"theme": 150}, only_newer=True)
        self.assertEqual(self.store.snapshot("u")[1:], ({"theme": "night"}, {"theme": 150}))

    def test_max_keys_all_or_nothing(self):
        self.store.write("u", {"kura.k%d" % i: "v" for i in range(100)})
        with self.assertRaises(prefs.PrefsError):
            self.store.write("u", {"theme": "day"})
        self.assertNotIn("theme", self.store.snapshot("u")[1])

    def test_an_old_room_file_opens_as_it_is(self):
        """A room's prefs.sqlite3 from before (signin.Prefs: no prefs_rev table) keeps its rows."""
        old = os.path.join(self.dir, "old.sqlite3")
        db = sqlite3.connect(old)
        db.execute("CREATE TABLE prefs (principal TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                   "updated INTEGER NOT NULL, PRIMARY KEY (principal, key))")
        db.execute("INSERT INTO prefs VALUES ('hi:abc', 'theme', 'night', 123)")
        db.commit()
        db.close()
        self.assertEqual(prefs.read_rows(old), [("hi:abc", "theme", "night", 123)])
        self.assertEqual(signin.Prefs(old).snapshot("hi:abc"), (0, {"theme": "night"}, {"theme": 123}))

    def test_merge_newest_wins(self):
        values, stamps, source, refused = prefs.merge([
            ("kura", [("theme", "day", 300), ("palette", "nord", 100), ("text_size", "large", 50)]),
            ("konbini", [("theme", "night", 200), ("palette", "dracula", 400), ("bogus", "x", 999)]),
            ("landing", [("text_size", "small", 60), ("theme", "sepia", 999)]),
        ])
        self.assertEqual(values, {"theme": "day", "palette": "dracula", "text_size": "small"})
        self.assertEqual(stamps, {"theme": 300, "palette": 400, "text_size": 60})
        self.assertEqual(source, {"theme": "kura", "palette": "konbini", "text_size": "landing"})
        self.assertEqual(sorted(refused), ["konbini: bogus", "landing: theme"])


class RoomStoreTest(unittest.TestCase):
    """signin.handle_prefs: a room's own store (identity-file, Tailscale or open mode), the contract's shape."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = signin.Prefs(os.path.join(self.dir, "room.sqlite3"))
        self.token = idn.Principal("vm", "agent", via="token:abcd12")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def call(self, method, data=None, **h):
        hdrs = Headers(("Content-Type", "application/json"), ("Host", HOST), *h.items())
        body = json.dumps(data).encode() if data is not None else b""
        return signin.handle_prefs(self.store, self.token, method, hdrs, body)

    def test_shape_etag_and_304(self):
        status, headers, body = self.call("PUT", {"prefs": {"theme": "auto", "kura.preview_pane": "on"}})
        data = json.loads(body)
        self.assertEqual((status, data["v"], data["rev"], data["prefs"]),
                         (200, 1, 1, {"kura.preview_pane": "on", "theme": "system"}))
        self.assertEqual(set(data["updated"]), {"kura.preview_pane", "theme"})
        self.assertEqual(dict(headers)["ETag"], '"1"')
        status, headers, body = self.call("GET", **{"If-None-Match": '"1"'})
        self.assertEqual((status, body, dict(headers)["ETag"]), (304, b"", '"1"'))
        status, _, body = self.call("GET", **{"If-None-Match": '"0"'})
        self.assertEqual((status, json.loads(body)["rev"]), (200, 1))

    def test_schema_refusals_are_400(self):
        for bad in ({"theme": "sepia"}, {"x": "1"}, {"language": "en"}):
            status, _, body = self.call("PUT", {"prefs": bad})
            self.assertEqual(status, 400, bad)
            self.assertIn("error", json.loads(body))
        self.assertEqual(self.store.snapshot(self.token.uid), (0, {}, {}))


class ForwardTest(unittest.TestCase):
    """histerauth.forward_prefs: a room's /api/prefs in hister mode goes to the helper's /v1/prefs with the caller's
    own credential, never a user id; the fallback has no account; a cookie-borne PUT must be same-origin."""

    class Helper(FakeHelper):
        def __call__(self, method, path, headers, timeout, body=None):
            self.calls.append((method, path, dict(headers), timeout, body))
            if self.down:
                raise OSError("connection refused")
            if path == "/healthz":
                return self.health
            cred = headers.get("X-Machiya-Session") or headers.get("X-Access-Token")
            if path == "/v1/prefs":
                return self.prefs_answer(method, cred, headers, body)
            return self.answers.get((path, cred), self.default)

        def prefs_answer(self, method, cred, headers, body):
            if cred not in self.users:
                return 401, b'{"reason":"signed-out"}'
            if method == "GET" and headers.get("If-None-Match") == '"3"':
                return 304, b""
            p = {"theme": "night"}
            if body:
                p.update(json.loads(body)["prefs"])
            return 200, json.dumps({"v": 1, "rev": 3, "prefs": p, "updated": {"theme": 1}}).encode()

    def setUp(self):
        self.helper = self.Helper()
        self.helper.users = {SID, "tok-owner"}
        self.helper.ok(SID)
        self.helper.answers[("/v1/check", SID)] = (200, json.dumps({
            "username": "owner", "user_id": 1, "via": "session",
            "prefs": {"theme": "night", "palette": "nord", "bogus": "x", "text_size": "huge"}}).encode())
        self.auth = ha.HisterAuth("niwa", "https://hister.example.ts.net/machiya/signin", ("owner",),
                                  "https://niwa.example.ts.net", "http://hl:8081", "tailscale", ("me@passkey",),
                                  "example.ts.net", True, fetch=self.helper)

    def cookie(self, *more):
        return Headers(("Cookie", "machiya_sso=" + SID), *more)

    def test_check_carries_the_shared_prefs(self):
        res = quiet(self.auth.resolve, self.cookie(), True, "/")
        self.assertTrue(res)
        self.assertEqual(res.prefs, {"theme": "night", "palette": "nord"})    # only what the schema accepts
        ctx = shell.prefs("", account=res.prefs)
        self.assertEqual((ctx.theme, ctx.palette), ("night", "nord"))
        ctx = shell.prefs("machiya_theme=day", account=res.prefs)                # a cookie wins over the account
        self.assertEqual((ctx.theme, ctx.palette), ("day", "nord"))
        self.assertEqual(self.auth.prefs_state(res), "account")

    def test_get_with_the_callers_credential(self):
        res = quiet(self.auth.resolve, self.cookie(), True, "/")
        status, headers, body = self.auth.forward_prefs(res, "GET", self.cookie(("If-None-Match", '"2"')))
        self.assertEqual((status, json.loads(body)["prefs"], dict(headers)["ETag"]), (200, {"theme": "night"}, '"3"'))
        method, path, sent, _, _ = self.helper.calls[-1]
        self.assertEqual((method, path, sent["X-Machiya-Session"], sent["If-None-Match"]),
                         ("GET", "/v1/prefs", SID, '"2"'))
        self.assertFalse(any("user" in k.lower() for k in sent))              # never a user id
        status, headers, body = self.auth.forward_prefs(res, "GET", self.cookie(("If-None-Match", '"3"')))
        self.assertEqual((status, body, dict(headers)["ETag"]), (304, b"", '"3"'))

    def test_put_same_origin_for_the_cookie(self):
        res = quiet(self.auth.resolve, self.cookie(), True, "/")
        body = json.dumps({"prefs": {"palette": "ayu"}}).encode()
        h = self.cookie(("Content-Type", "application/json"))
        self.assertEqual(self.auth.forward_prefs(res, "PUT", h, body)[0], 403)            # no Origin
        h = self.cookie(("Content-Type", "application/json"), ("Origin", "https://evil.example"))
        self.assertEqual(self.auth.forward_prefs(res, "PUT", h, body, ["https://niwa.example.ts.net"])[0], 403)
        h = self.cookie(("Content-Type", "application/json"), ("Origin", "https://niwa.example.ts.net"))
        status, _, out = self.auth.forward_prefs(res, "PUT", h, body, ["https://niwa.example.ts.net"])
        self.assertEqual((status, json.loads(out)["prefs"]["palette"]), (200, "ayu"))
        self.assertEqual(self.helper.calls[-1][4], body)
        # the first render's copy follows at once
        self.assertEqual(quiet(self.auth.resolve, self.cookie(), True, "/").prefs["palette"], "ayu")
        # a token is a client's: no Origin needed
        tok = Headers(("X-Access-Token", "tok-owner"), ("Content-Type", "application/json"))
        self.helper.answers[("/v1/check", "tok-owner")] = (200, b'{"username":"owner","user_id":1,"via":"token"}')
        res = quiet(self.auth.resolve, tok, False, "/api/prefs")
        self.assertEqual(self.auth.forward_prefs(res, "PUT", tok, body)[0], 200)
        self.assertEqual(self.helper.calls[-1][2]["X-Access-Token"], "tok-owner")
        self.assertEqual(self.auth.forward_prefs(res, "PUT", Headers(("X-Access-Token", "tok-owner"),
                                                                      ("Content-Type", "text/plain")), body)[0], 415)

    def test_fallback_down_and_signed_out(self):
        quiet(self.auth.resolve, self.cookie(), True, "/")
        self.helper.down = True
        self.auth.health = (None, True)                                           # the health flag is old: asked again
        h = Headers(("Tailscale-User-Login", "me@passkey"))
        res = quiet(self.auth.resolve, h, True, "/")
        self.assertEqual(res.reason, ha.FALLBACK)
        self.assertEqual(self.auth.prefs_state(res), "unavailable")
        self.assertEqual(self.auth.forward_prefs(res, "GET", h)[0], 503)
        self.helper.down = False
        res = quiet(self.auth.resolve, self.cookie(), True, "/")                  # cached: signed in
        self.helper.down = True
        status, _, body = self.auth.forward_prefs(res, "GET", self.cookie())
        self.assertEqual((status, json.loads(body)["error"]), (503, "preferences unavailable"))
        self.helper.down = False
        self.helper.users = set()                                                 # signed out at the helper
        status, _, body = self.auth.forward_prefs(res, "GET", self.cookie())
        self.assertEqual((status, "signin" in json.loads(body)), (401, True))

    def test_standalone_keeps_the_rooms_own_store(self):
        auth = quiet(ha.HisterAuth, "niwa", "https://h/machiya/signin", ("owner",), "https://niwa.example.ts.net", "",
                     "tailscale", ("me@passkey",))
        res = auth.resolve(Headers(("Tailscale-User-Login", "me@passkey")), True, "/")
        self.assertIsNone(auth.forward_prefs(res, "GET", Headers()))
        self.assertEqual(auth.prefs_state(res), "standalone")


class AutoSigninTest(unittest.TestCase):
    """MACHIYA_SIGNIN_PROVIDER (decided 2026-10-05: "Yes, Tailscale automatically"): a room sends a browser with no
    sign-in straight through the helper's provider (provider=…&auto=1); the loop guard's page links to the plain
    page; a deliberate sign-out sets the marker that makes the helper show its page instead."""

    def make(self, provider="oidc"):
        helper = FakeHelper()
        auth = ha.HisterAuth("niwa", "https://hister.example.ts.net/machiya/signin", ("owner",),
                             "https://niwa.example.ts.net", "http://hl:8081", "tailscale", ("me@passkey",),
                             "example.ts.net", True, fetch=helper, provider=provider)
        return auth, helper

    def test_signin_location_carries_the_provider(self):
        auth, _ = self.make()
        self.assertEqual(auth.signin_location("/n/x"), "https://hister.example.ts.net/machiya/signin?return="
                         "https%3A%2F%2Fniwa.example.ts.net%2Fn%2Fx&provider=oidc&auto=1")
        self.assertNotIn("provider", auth.signin_location("/n/x", auto=False))
        plain, _ = self.make("")
        self.assertNotIn("provider", plain.signin_location("/"))

    def test_page_api_and_loop_guard(self):
        auth, _ = self.make()
        res = quiet(auth.resolve, Headers(), True, "/garden")
        self.assertEqual(res.status, 401)
        self.assertIn("provider=oidc&auto=1", res.location)                    # the first trip: automatic
        self.assertTrue(any(c.startswith("__Host-machiya_sso_niwa_try=1") for c in res.cookies))
        api = quiet(auth.resolve, Headers(), False, "/api/garden")
        self.assertIn("provider=oidc", api.json()["signin"])                    # machiya.js takes the page there
        again = quiet(auth.resolve, Headers(("Cookie", "__Host-machiya_sso_niwa_try=1")), True, "/garden")
        self.assertIsNone(again.location)                                       # no loop: a page with a link,
        self.assertNotIn("provider", again.signin)                              # to the plain sign-in page

    def test_signout_sets_the_marker(self):
        """v0.22: the marker is this room's own (host-only); the helper remembers the deliberate sign-out itself."""
        auth, _ = self.make()
        ended, cookies = auth.signout(Headers(("Cookie", "machiya_sso=" + SID)))
        marker = [c for c in cookies if c.startswith("__Host-machiya_sso_niwa_out=1")]
        self.assertEqual(len(marker), 1)
        for attr in ("HttpOnly", "Secure", "Path=/", "Max-Age=%d" % ha.OUT_MAX_AGE):
            self.assertIn(attr, marker[0])
        self.assertNotIn("Domain", marker[0])
        # and the next trip from this room shows the helper's page: no provider, no auto
        res = quiet(auth.resolve, Headers(("Cookie", "__Host-machiya_sso_niwa_out=1")), True, "/")
        self.assertNotIn("provider", res.location)

    def test_load_for(self):
        env = {"NIWA_AUTH": "hister", "NIWA_AUTH_SIGNIN_URL": "https://h/machiya/signin", "NIWA_HISTER_USERS": "owner",
               "NIWA_AUTH_URL": "http://hl:8081", "NIWA_USERS": "me@passkey", "NIWA_PUBLIC_URL": "https://niwa.x",
               "NIWA_BIND_BEHIND_PROXY": "1"}
        self.assertEqual(quiet(ha.load_for, "niwa", env).provider, "")
        self.assertEqual(quiet(ha.load_for, "niwa", dict(env, MACHIYA_SIGNIN_PROVIDER="OIDC")).provider, "oidc")
        with self.assertRaises(idn.IdentityError):
            quiet(ha.load_for, "niwa", dict(env, MACHIYA_SIGNIN_PROVIDER="oidc&x=1"))


class ShellTest(unittest.TestCase):
    LINKS = {"kura": "https://k", "konbini": "https://b", "hister": "https://h", "machiya": "https://m"}

    def test_device_size(self):
        ctx = shell.prefs("machiya_textSize=large; machiya_textSizeDevice=xsmall")
        self.assertEqual((ctx.text, ctx.text_shared, ctx.text_device), ("xsmall", "large", "xsmall"))
        ctx = shell.prefs("textSize=large; textSizeDevice=bogus")
        self.assertEqual((ctx.text, ctx.text_device), ("large", ""))
        self.assertNotIn("textSizeDevice", shell.prefs("textSizeDevice=small").extra)
        self.assertTrue(shell.is_shared("textSizeDevice"))
        page = shell.page(shell.prefs("textSize=large; textSizeDevice=small"), "kura", "T", "<main></main>")
        self.assertIn('data-text="small"', page)

    def test_shared_section_first_rows_and_state(self):
        ctx = shell.prefs("machiya_show_hister=false; textSize=small; textSizeDevice=xlarge")
        sec = shell.shared_section(ctx, "kura", self.LINKS, "account", "owner")
        title, items, note, extra = sec
        html = "".join(items)
        self.assertEqual(title, "Appearance")                                   # v0.23 (was "Shared")
        self.assertLess(html.index('data-set="palette"'), html.index('data-set="theme"'))
        self.assertLess(html.index('data-set="theme"'), html.index('data-set="textSize"'))
        self.assertLess(html.index('data-set="textSize"'), html.index("Use This Device's Size"))   # under its parent
        self.assertIn("data-device-size checked", html)
        self.assertIn('<option value="small" selected>', html)                  # the shared size, not the device's
        self.assertNotIn("show_", html)                                         # the apps are their own section
        rooms = "".join(sec.then[1])
        self.assertEqual(sec.then[0], "Rooms")
        self.assertIn('data-set="show_hister">', rooms)                         # off: no "checked"
        self.assertNotIn("show_kura", rooms)                                    # not the room itself
        self.assertIsNone(shell.shared_section(ctx, "kura", self.LINKS, "account", apps=False).then)
        self.assertIsNone(shell.rooms_section(ctx, "kura", {}))
        self.assertEqual(note, "Follows you on every Machiya app when signed in.")
        self.assertIn(">Saved to your account.</p>", extra)
        self.assertIn('data-prefs-state="account"', extra)
        lines = {s: shell.shared_section(ctx, "kura", self.LINKS, s, "owner", "https://h/machiya/signin")
                 for s in shell.PREFS_STATES}
        self.assertIn('Kept in this browser. <a href="https://h/machiya/signin">Sign In</a>', lines["signed-out"][3])
        self.assertIn(">Kept here until sign-in is back.</p>", lines["unavailable"][3])
        self.assertEqual(lines["standalone"][2], "")                             # nothing to follow
        self.assertIn("Kept in this browser.", lines["standalone"][3])
        self.assertIn("Saved for you in Kura.", lines["room"][3])
        self.assertNotIn("&lt;b&gt;", shell.shared_section(ctx, "kura", self.LINKS, "account", "<b>")[3])
        self.assertIn("&lt;b&gt;", shell.shared_section(ctx, "kura", self.LINKS, "signed-out", "", "/s?<b>")[3])

    def test_device_section_and_page_order(self):
        ctx = shell.prefs("textSizeDevice=large")
        title, items, note = shell.device_section(ctx, [shell.offline_row()])
        html = "".join(items)
        self.assertEqual(title, "This Device")
        self.assertNotIn("Use This Device's Size", html)                        # v0.23: under Text Size
        self.assertIn("offline-copies", html)
        self.assertEqual(note, "Only on this device.")
        self.assertIsNone(shell.device_section(ctx))                             # no rows: no section
        self.assertIsNone(shell.device_section(ctx, [""]))
        off = "".join(shell.shared_section(shell.prefs(""), "kura", self.LINKS)[1])
        self.assertIn('class="item device-size" hidden', off)
        on = "".join(shell.shared_section(ctx, "kura", self.LINKS)[1])
        self.assertIn('<option value="large" selected>', on.split("data-device-size-value")[1])
        account = ("Account", ["<a>"], "")
        # the rooms' call (v0.21 order) and a scrambled one give the same page
        calls = ([shell.shared_section(ctx, "kura", self.LINKS, "account", "owner"), ("Reading", ["<x>"], "Kura only."),
                  ("Sync", ["<y>"], ""), shell.device_section(ctx, [shell.offline_row()]), account,
                  shell.about_section("kura", "1.0")],
                 [shell.about_section("kura", "1.0"), account, shell.device_section(ctx, [shell.offline_row()]), None,
                  ("Reading", ["<x>"], "Kura only."), shell.shared_section(ctx, "kura", self.LINKS, "account", "owner"),
                  ("Sync", ["<y>"], "")])
        pages = [shell.settings_page(c, "kura") for c in calls]
        self.assertEqual(pages[0], pages[1])
        heads = re.findall(r'<h2 id="([a-z-]+)">', pages[0])
        self.assertEqual(heads, ["appearance", "reading", "sync", "rooms", "this-device", "account", "about"])
        self.assertIn('<p class="footnote prefs-state"', pages[0])
        lone = shell.settings_page([shell.shared_section(ctx, "kura", {}), shell.device_section(ctx),
                                    ("Reading", ["<x>", shell.offline_row()], "")], "kura")
        self.assertEqual(re.findall(r'<h2 id="([a-z-]+)">', lone), ["appearance", "reading"])   # folded into its own
        self.assertEqual(shell.kind_of(("Shared", [], "")), "appearance")                       # an older room's

    def test_app_prefs_meta(self):
        old = dict(shell.APP_PREFS)
        try:
            shell.APP_PREFS.clear()
            shell.APP_PREFS.update({"previewPane": {"type": "bool", "cookie": True},
                                    "group": {"type": "choice", "values": ["area", "family"]}, "bad key": {}})
            page = shell.page(shell.Prefs(), "konbini", "T", "<main></main>", prefs_url="/api/prefs")
            meta = re.search(r'<meta name="machiya-app-prefs" content="([^"]+)">', page).group(1)
            import html as h
            spec = json.loads(h.unescape(meta))
            self.assertEqual(spec, {"group": {"key": "konbini.group", "type": "choice", "values": ["area", "family"]},
                                    "previewPane": {"cookie": True, "key": "konbini.preview_pane", "type": "bool"}})
            self.assertNotIn("machiya-app-prefs", shell.page(shell.Prefs(), "konbini", "T", "<main></main>"))
        finally:
            shell.APP_PREFS.clear()
            shell.APP_PREFS.update(old)


@unittest.skipUnless(shutil.which("node"), "needs node")
class MachiyaJsTest(unittest.TestCase):
    """The real ui/machiya.js in Node (tests/js/prefs_sim.mjs), several rooms in one browser and two devices."""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "prefs_sim.mjs")], capture_output=True,
                           text=True, timeout=120)
        if r.returncode:
            raise AssertionError("prefs_sim.mjs failed:\n" + r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_the_revert_is_fixed(self):
        """docs §2.1: pick Light in Kura, open Konbini (whose own store still says Dark): it stays Light, and Konbini's
        store is brought up to date; back in Kura, still Light."""
        r = self.out["revert"]
        self.assertEqual(r["cookies"], ["day", "day", "day"])
        self.assertEqual((r["kura"], r["konbini"]), ("day", "day"))
        self.assertEqual(r["kuraPuts"], [{"theme": "day"}])
        self.assertEqual(r["konbiniPuts"], [{"theme": "day"}])

    def test_one_account_every_room_and_device(self):
        a = self.out["account"]
        self.assertEqual(a["puts"], [{"palette": "nord"}])                        # only the key that changed
        self.assertEqual(a["rooms"], {"konbini": True, "niwa": True, "machiya": True})
        self.assertTrue(a["phone"])
        self.assertEqual(a["phoneCookie"], "nord")

    def test_only_changed_keys_so_a_newer_change_elsewhere_survives(self):
        n = self.out["newer"]
        self.assertEqual(n["puts"], [{"palette": "dracula"}, {"text_size": "xlarge"}])
        self.assertEqual((n["laptopText"], n["laptopPalette"]), ("xlarge", True))
        self.assertEqual(n["account"], {"palette": "dracula", "text_size": "xlarge"})

    def test_offline_change_is_pending_then_sent_first(self):
        o = self.out["offline"]
        self.assertEqual(o["pending"], {"theme": "night"})
        self.assertEqual(o["first"], {"method": "PUT", "body": {"prefs": {"theme": "night"}}, "status": 200})
        self.assertEqual((o["account"], o["cookie"], o["left"]), ("night", "night", "{}"))

    def test_device_size_stays_on_the_device(self):
        d = self.out["device"]
        self.assertEqual(d["phonePuts"], 0)
        self.assertEqual((d["phoneText"], d["phoneShared"], d["deviceCookie"]), ("xlarge", "small", "xlarge"))
        self.assertEqual(d["laptopText"], "small")

    def test_apps_fill_the_blanks_app_keys_and_unknown_values(self):
        a = self.out["apps"]
        self.assertEqual(a["filled"], {"palette": "gruvbox"})                     # this browser's choice, account empty
        self.assertEqual(a["puts"], [{"palette": "gruvbox"}, {"apps_hidden": "searxng"}, {"konbini.group": "family"}])
        self.assertEqual((a["phoneHidden"], a["phoneGroup"]), ("false", "family"))
        self.assertIsNone(a.get("phoneTheme"))                                    # "sepia": never applied
        self.assertIn("palette-gruvbox", a["phoneClasses"])

    def test_account_down_nothing_breaks(self):
        d = self.out["down"]
        self.assertEqual(d["cookie"], "day")
        self.assertIn("theme-day", d["classes"])
        self.assertEqual(json.loads(d["pending"]), {"theme": "day"})


if __name__ == "__main__":
    unittest.main()
