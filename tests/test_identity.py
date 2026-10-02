"""vaultkit.identity (docs/plans/identity.md): python3 -m unittest tests.test_identity (standard library only)."""
import datetime
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from vaultkit import identity as idn   # noqa: E402

KEY = b"k" * 43
FILE = """
version = 1
session_key_file = "session.key"
session_days = 30

[principals.owner]
id = "ownerid00000000a"
kind = "person"
owner = true
tailscale = ["Owner@Example.com"]
proxy = ["owner"]
password = "%(pw)s"

[principals.partner]
id = "partnerid0000000"
kind = "person"
tailscale = ["partner@example.com"]
password = "%(pw2)s"
grants = { niwa = ["read"], kura = { read = true, vaults = ["default"] } }

[principals.newbie]
id = "newbieid00000000"
kind = "person"
proxy = ["newbie"]

[principals.mcp]
kind = "agent"
tailscale_tag = "mcp"
grants = { kura = ["read"], konbini = ["read", "write"], niwa = ["read", "suggest"] }
limits = { writes_per_day = 300 }

[principals.vm]
kind = "agent"
grants = { kura = { read = true, vaults = ["default", "team"] } }
[[principals.vm.tokens]]
id = "abcd12"
hash = "%(th)s"
label = "claude VM"
[[principals.vm.tokens]]
id = "old123"
hash = "%(th_old)s"
expires = 2020-01-01

[principals.niwa]
kind = "service"
grants = { konbini = ["read"] }
"""
SECRET, OLD = "s3cr3t-token-value", "old-token-value"


class H(dict):
    """Case-insensitive headers, like http.server's."""

    def __init__(self, **kw):
        super().__init__({k.replace("_", "-").lower(): v for k, v in kw.items()})

    def get(self, k, d=None):
        return super().get(k.lower(), d)


def headers(**kw):
    return H(**kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "identity.toml")
        with open(os.path.join(self.dir, "session.key"), "wb") as f:
            f.write(KEY)
        self.write(FILE % {"pw": idn.hash_password("correct horse battery"), "pw2": idn.hash_password("partner pass 1"),
                           "th": idn.token_hash(SECRET), "th_old": idn.token_hash(OLD)})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def ident(self, **kw):
        return idn.Identity(self.path, kw.pop("room", "kura"), **kw)


class FileTest(Base):
    def test_valid_file(self):
        config, key = idn.read_file(self.path)
        self.assertEqual(key, KEY)
        self.assertEqual(sorted(config.principals), ["mcp", "newbie", "niwa", "owner", "partner", "vm"])
        self.assertEqual(config.by_login["owner@example.com"], "owner")             # logins match without case

    def test_bad_files_refuse(self):
        with open(self.path) as f:
            good = f.read()
        bad = {
            "version": good.replace("version = 1", "version = 2"),
            "toml": good + "\n[[[",
            "unknown top": good + "\nsurprise = 1\n",
            "unknown room": good.replace('niwa = ["read"], kura', 'hister = ["read"], kura'),
            "unknown action": good.replace('niwa = ["read", "suggest"]', 'niwa = ["read", "publsh"]'),
            "vaults outside kura": good.replace('konbini = ["read"] }', 'konbini = { read = true, vaults = ["x"] } }'),
            "kind": good.replace('kind = "service"', 'kind = "robot"'),
            "agent owner": good.replace('kind = "agent"\ntailscale_tag', 'kind = "agent"\nowner = true\ntailscale_tag'),
            "agent password": good.replace('tailscale_tag = "mcp"', 'tailscale_tag = "mcp"\npassword = "scrypt$x"'),
            "clear password": good.replace('password = "scrypt', 'password = "hunter2', 1),
            "same login twice": good.replace("partner@example.com", "owner@example.com"),
            "token hash": good.replace('hash = "sha256:', 'hash = "md5:', 1),
            "token id twice": good.replace('id = "old123"', 'id = "abcd12"'),
            "bad name": good.replace("[principals.newbie]", "[principals.New_Bie]"),
            "unknown setting": good.replace('proxy = ["newbie"]', 'proxy = ["newbie"]\nadmin = true'),
            "no key file": good.replace('session_key_file = "session.key"', 'session_key_file = "missing.key"'),
        }
        for what, text in bad.items():
            self.write(text)
            with self.assertRaises(idn.IdentityError, msg=what):
                idn.read_file(self.path)
        self.write(good)
        with open(os.path.join(self.dir, "session.key"), "wb") as f:
            f.write(b"short")
        with self.assertRaises(idn.IdentityError):
            idn.read_file(self.path)

    def test_file_holds_no_secret(self):
        with open(self.path) as f:
            text = f.read()
        self.assertNotIn(SECRET, text)
        self.assertNotIn("correct horse", text)


class GrantsTest(Base):
    def test_grants(self):
        config, _ = idn.read_file(self.path)
        owner, partner, mcp, vm, newbie = (config.principals[n] for n in ("owner", "partner", "mcp", "vm", "newbie"))
        self.assertTrue(owner.can("niwa", "publish") and owner.vaults() == "*")
        self.assertTrue(partner.can("niwa", "read") and not partner.can("niwa", "suggest"))
        self.assertEqual(partner.vaults(), ("default",))
        self.assertTrue(mcp.can("konbini", "write") and not mcp.can("konbini", "areas"))
        self.assertFalse(mcp.can("niwa", "publish"))
        self.assertEqual(mcp.vaults(), ("default", "shared"))                        # the default for non-owners
        self.assertEqual(vm.vaults(), ("default", "team"))
        self.assertFalse(any(newbie.can(r, a) for r, acts in idn.ACTIONS.items() for a in acts))   # nothing granted
        self.assertEqual(newbie.vaults(), ())
        self.assertEqual(mcp.limits, {"writes_per_day": 300})


class ResolveTest(Base):
    def test_tailscale_login(self):
        i = self.ident()
        r = i.resolve(headers(Tailscale_User_Login="owner@example.com"))
        self.assertEqual((r.principal.name, r.principal.via), ("owner", "tailscale"))
        r = i.resolve(headers(Tailscale_User_Login="stranger@example.com"))
        self.assertEqual((r.principal, r.status), (None, 403))
        self.assertEqual(i.resolve(headers()).status, 401)

    def test_tagged_node_capability(self):
        cap = json.dumps({idn.CAPABILITY: [{"principal": "mcp"}]})
        self.assertEqual(self.ident().resolve(headers(Tailscale_App_Capabilities=cap)).status, 401)   # opt-in only
        i = self.ident(accept_caps=True)
        cap = json.dumps({idn.CAPABILITY: [{"principal": "mcp"}]})
        self.assertEqual(i.resolve(headers(Tailscale_App_Capabilities=cap)).principal.name, "mcp")
        q = "=?utf-8?q?" + cap.replace(" ", "_").replace("=", "=3D") + "?="        # Serve may Q-encode it
        self.assertEqual(i.resolve(headers(Tailscale_App_Capabilities=q)).principal.name, "mcp")
        for value, status in ((json.dumps({idn.CAPABILITY: [{"principal": "ghost"}]}), 403),
                              (json.dumps({idn.CAPABILITY: [{"principal": "mcp"}, {"principal": "vm"}]}), 403),
                              (json.dumps({"other.example/cap": [{"principal": "mcp"}]}), 401),
                              ("{not json", 401), ("=?utf-8?q?{broken?=", 401)):
            r = i.resolve(headers(Tailscale_App_Capabilities=value))
            self.assertEqual((r.principal, r.status), (None, status), value)
        r = i.resolve(headers(Tailscale_User_Login="owner@example.com", Tailscale_App_Capabilities=cap))
        self.assertEqual(r.principal.name, "owner")                                  # a login decides first

    def test_tailscale_headers_mean_nothing_in_other_modes(self):
        i = self.ident(auth="header", header="Remote-User")
        self.assertIsNone(i.resolve(headers(Tailscale_User_Login="owner@example.com")).principal)
        cap = json.dumps({idn.CAPABILITY: [{"principal": "mcp"}]})
        self.assertIsNone(i.resolve(headers(Tailscale_App_Capabilities=cap)).principal)

    def test_open_mode(self):
        i = self.ident(auth="open")
        r = i.resolve(headers())
        self.assertEqual((r.principal.name, r.principal.owner, r.principal.via), ("local", True, "open"))
        self.assertEqual(i.resolve(headers(Authorization="Bearer mch_abcd12_" + SECRET)).principal.name, "vm")
        self.assertEqual(i.resolve(headers(Authorization="Bearer mch_abcd12_bad")).status, 401)   # still refused
        self.assertEqual(i.resolve(headers(Remote_User="newbie")).principal.name, "local")

    def test_proxy_header(self):
        i = self.ident(auth="header", header="Remote-User")
        self.assertEqual(i.resolve(headers(Remote_User="owner")).principal.name, "owner")
        self.assertEqual(i.resolve(headers(Remote_User="mallory")).status, 403)
        self.assertEqual(i.resolve(headers()).status, 401)
        with self.assertRaises(idn.IdentityError):
            self.ident(auth="header", header="")

    def test_stored_tokens(self):
        i = self.ident()
        r = i.resolve(headers(Authorization="Bearer mch_abcd12_" + SECRET))
        self.assertEqual((r.principal.name, r.principal.via), ("vm", "token:abcd12"))
        for bad in ("Bearer mch_abcd12_wrong", "Bearer mch_zzzz99_" + SECRET, "Bearer mch_abcd12_", "Bearer ",
                    "Basic dXNlcjpwYXNz", "Bearer mch_old123_" + OLD, "Bearer nonsense"):
            r = i.resolve(headers(Authorization=bad))
            self.assertEqual((r.principal, r.status), (None, 401), bad)

    def test_an_invalid_proof_never_falls_through(self):
        i = self.ident()
        r = i.resolve(headers(Authorization="Bearer mch_abcd12_wrong", Tailscale_User_Login="owner@example.com"))
        self.assertEqual((r.principal, r.status), (None, 401))                      # not the owner's login instead
        cap = json.dumps({idn.CAPABILITY: [{"principal": "ghost"}]})
        self.assertIsNone(self.ident(signin=True).resolve(headers(Tailscale_App_Capabilities=cap)).principal)

    def test_bind_check(self):
        for auth in ("tailscale", "header"):
            with self.assertRaises(idn.IdentityError):
                idn.check_bind(auth, "0.0.0.0")
            idn.check_bind(auth, "127.0.0.1")
            idn.check_bind(auth, "::1")
            idn.check_bind(auth, "localhost")
            idn.check_bind(auth, "0.0.0.0", behind_proxy=True)
        idn.check_bind("open", "0.0.0.0")

    def test_load_for(self):
        self.assertIsNone(idn.load_for("kura", {}))                                  # no file: the old *_USERS gate
        env = {"MACHIYA_IDENTITY_FILE": self.path, "KANBAN_AUTH": "header", "KANBAN_AUTH_HEADER": "Remote-User",
               "KANBAN_SIGNIN": "1", "MACHIYA_COOKIE_DOMAIN": "example.net"}
        i = idn.load_for("konbini", env, bind="127.0.0.1")
        self.assertEqual((i.room, i.auth, i.header, i.signin, i.cookie_domain),
                         ("konbini", "header", "Remote-User", True, "example.net"))
        with self.assertRaises(idn.IdentityError):
            idn.load_for("kura", {"MACHIYA_IDENTITY_FILE": self.path}, bind="0.0.0.0")
        self.assertIsNotNone(idn.load_for("kura", {"MACHIYA_IDENTITY_FILE": self.path, "KURA_BIND_BEHIND_PROXY": "1"},
                                          bind="0.0.0.0"))
        with self.assertRaises(idn.IdentityError):
            idn.load_for("kura", {"MACHIYA_IDENTITY_FILE": self.path, "KURA_AUTH": "magic"}, bind="127.0.0.1")


class SessionTest(Base):
    def cookie_of(self, set_cookie):
        return set_cookie.split(";")[0].split("=", 1)[1]

    def test_sign_in_and_session(self):
        i = self.ident(signin=True, cookie_domain="example.net")
        r = i.sign_in("Owner", "correct horse battery", "10.0.0.1")
        self.assertEqual((r.principal.name, r.status), ("owner", 200))
        c = r.cookies[0]
        for attr in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/", "Domain=example.net"):
            self.assertIn(attr, c)
        value = self.cookie_of(c)
        self.assertEqual(i.resolve(headers(Cookie="theme=day; machiya_session=" + value)).principal.name, "owner")
        self.assertEqual(i.resolve(headers(Cookie="machiya_session=" + value)).cookies, [])   # fresh: not renewed
        self.assertIn("Max-Age=0", i.sign_out()[0])

    def test_cookies_only_with_signin_on(self):
        on = self.ident(signin=True)
        value = self.cookie_of(on.sign_in("owner", "correct horse battery").cookies[0])
        self.assertIsNone(self.ident(signin=False).resolve(headers(Cookie="machiya_session=" + value)).principal)

    def test_wrong_passwords_and_throttle(self):
        i = self.ident(signin=True)
        for name, pw in (("owner", "wrong"), ("ghost", "correct horse battery"), ("mcp", "x"), ("newbie", "")):
            r = i.sign_in(name, pw, "10.0.0.2")
            self.assertEqual((r.principal, r.status, r.error), (None, 401, "wrong name or password"), name)
        for _ in range(4):
            i.sign_in("partner", "nope", "10.0.0.%d" % (_ + 10))
        self.assertEqual(i.sign_in("partner", "nope", "10.0.0.20").status, 401)       # the 5th failure
        self.assertEqual(i.sign_in("partner", "partner pass 1", "10.0.0.21").status, 429)  # name blocked, right pw
        for n in range(20):
            i.sign_in("someone%d" % n, "x", "10.9.9.9")
        self.assertEqual(i.sign_in("owner", "correct horse battery", "10.9.9.9").status, 429)  # address blocked
        self.assertEqual(i.sign_in("owner", "correct horse battery", "10.0.0.99").status, 200)

    def test_tampered_expired_and_signed_out(self):
        i = self.ident(signin=True)
        value = self.cookie_of(i.sign_in("owner", "correct horse battery").cookies[0])
        body, mac = value.split(".")
        payload = json.loads(idn.b64d(body))
        forged = idn.b64e(json.dumps(dict(payload, p="partner")).encode()) + "." + mac
        for v in (forged, body + ".AAAA", "garbage", value + "x"):
            r = i.resolve(headers(Cookie="machiya_session=" + v))
            self.assertEqual((r.principal, r.status), (None, 401), v)
            self.assertIn("Max-Age=0", r.cookies[0])                                  # and it's cleared
        expired = idn.sign(KEY, "session", dict(payload, exp=int(time.time()) - 1))
        self.assertIsNone(i.resolve(headers(Cookie="machiya_session=" + expired)).principal)
        device = idn.sign(KEY, "device", {"p": "owner", "d": "x", "e": 1, "iat": 1})
        self.assertIsNone(i.resolve(headers(Cookie="machiya_session=" + device)).principal)   # wrong purpose
        with open(self.path) as f:
            text = f.read()
        self.write(text.replace('proxy = ["owner"]', 'proxy = ["owner"]\nsession_epoch = 2'))
        i.checked = 0
        self.assertIsNone(i.resolve(headers(Cookie="machiya_session=" + value)).principal)    # epoch bumped

    def test_renewed_on_use_with_a_hard_cap(self):
        i = self.ident(signin=True)
        t0 = int(time.time())
        with mock.patch.object(idn, "now", return_value=t0 - 2 * 86400):
            old = self.cookie_of(i.sign_in("owner", "correct horse battery").cookies[0])
        r = i.resolve(headers(Cookie="machiya_session=" + old))
        self.assertEqual(r.principal.name, "owner")
        renewed = json.loads(idn.b64d(self.cookie_of(r.cookies[0]).split(".")[0]))
        self.assertEqual(renewed["auth"], t0 - 2 * 86400)                            # the sign-in time is kept
        self.assertGreaterEqual(renewed["exp"], t0 + 30 * 86400 - 5)
        first = t0 - 179 * 86400                                                     # signed in 179 days ago
        late = idn.sign(KEY, "session", {"p": "owner", "u": "ownerid00000000a", "e": 1, "iat": t0 - 2 * 86400,
                                         "auth": first,
                                         "exp": t0 + 86400})
        capped = json.loads(idn.b64d(self.cookie_of(i.resolve(headers(Cookie="machiya_session=" + late)).cookies[0])
                                     .split(".")[0]))
        self.assertEqual(capped["exp"], first + 180 * 86400)


class PairingTest(Base):
    def test_pair_device_and_revoke(self):
        with open(self.path, "rb") as f:
            data = idn.tomllib.load(f)
        code, device = idn.new_pairing(data, "owner", "iPhone")
        idn.write_file(self.path, data)
        i = self.ident()
        self.assertEqual(i.pair("WRONGCOD", "10.0.0.5")[0].status, 401)
        r, token = i.pair(code.lower().replace("-", " "), "10.0.0.6")               # typed loosely
        self.assertEqual((r.principal.name, token[:4]), ("owner", "mcd_"))
        got = i.resolve(headers(Authorization="Bearer " + token))
        self.assertEqual((got.principal.name, got.principal.via), ("owner", "device:" + device))
        self.assertIsNone(i.resolve(headers(Cookie="machiya_session=" + token[4:])).principal)   # not a session
        data["principals"]["owner"]["revoked_devices"] = [device]
        idn.write_file(self.path, data)
        i.checked = 0
        self.assertEqual(i.resolve(headers(Authorization="Bearer " + token)).status, 401)

    def test_expired_code_and_throttle(self):
        with open(self.path, "rb") as f:
            data = idn.tomllib.load(f)
        code, _ = idn.new_pairing(data, "owner", "old", minutes=1)
        data["pairing"][0]["expires"] = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        idn.write_file(self.path, data)
        i = self.ident()
        self.assertEqual(i.pair(code, "10.0.0.7")[0].status, 401)
        for _ in range(5):
            self.assertEqual(i.pair("XXXXXXXX", "10.0.0.8")[0].status, 401)
        self.assertEqual(i.pair("XXXXXXXX", "10.0.0.8")[0].status, 429)              # the 6th try waits
        with self.assertRaises(idn.IdentityError):                                    # only a person pairs
            idn.new_pairing(data, "mcp", "x")
            idn.write_file(self.path, data)


class ReloadTest(Base):
    def test_reload_and_keep_last_good(self):
        i = self.ident()
        self.assertEqual(i.resolve(headers(Tailscale_User_Login="partner@example.com")).principal.name, "partner")
        with open(self.path) as f:
            text = f.read()
        self.write(text.replace("partner@example.com", "partner2@example.com"))
        i.checked = 0
        self.assertEqual(i.resolve(headers(Tailscale_User_Login="partner@example.com")).status, 403)
        self.assertEqual(i.resolve(headers(Tailscale_User_Login="partner2@example.com")).principal.name, "partner")
        self.write(text + "\n[[[ broken")
        i.checked = 0
        with redirect_stderr(io.StringIO()) as err:
            r = i.resolve(headers(Tailscale_User_Login="partner2@example.com"))
        self.assertEqual(r.principal.name, "partner")                                # the last good file still rules
        self.assertIn("keeping the last good identity file", err.getvalue())


class ReviewTest(Base):
    """Regressions for the security review of phase 2 (each named after its finding)."""

    def test_h1_parallel_guesses_are_counted_before_hashing(self):
        import threading
        i = self.ident(signin=True)
        results = []
        go = threading.Event()

        def guess():
            go.wait()
            results.append(i.sign_in("owner", "wrong guess", "10.1.1.1").status)
        threads = [threading.Thread(target=guess) for _ in range(60)]
        for t in threads:
            t.start()
        go.set()
        for t in threads:
            t.join()
        self.assertLessEqual(results.count(401), 5)                                  # at most the limit got hashed
        self.assertEqual(i.sign_in("owner", "correct horse battery", "10.1.1.2").status, 429)   # name locked
        ok = self.ident(signin=True)
        self.assertEqual(ok.sign_in("owner", "correct horse battery", "10.1.1.3").status, 200)
        for _ in range(4):
            ok.sign_in("owner", "wrong", "10.1.1.3")
        self.assertEqual(ok.sign_in("owner", "correct horse battery", "10.1.1.3").status, 200)  # success forgives

    def test_m1_rotating_or_removing_the_key_signs_everyone_out(self):
        i = self.ident(signin=True)
        value = i.sign_in("owner", "correct horse battery").cookies[0].split(";")[0].split("=", 1)[1]
        with open(os.path.join(self.dir, "session.key"), "wb") as f:
            f.write(b"n" * 43)
        i.checked = 0
        self.assertIsNone(i.resolve(headers(Cookie="machiya_session=" + value)).principal)
        value = i.sign_in("owner", "correct horse battery").cookies[0].split(";")[0].split("=", 1)[1]
        os.unlink(os.path.join(self.dir, "session.key"))
        i.checked = 0
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(i.resolve(headers(Cookie="machiya_session=" + value)).principal)
            self.assertEqual(i.sign_in("owner", "correct horse battery").status, 503)
        self.assertEqual(i.resolve(headers(Tailscale_User_Login="owner@example.com")).principal.name, "owner")

    def test_m2_hostile_input_never_raises(self):
        i = self.ident(signin=True, accept_caps=True)
        for caps in ("=?utf-8?b?Q===?=", '"[' * 60000, json.dumps({idn.CAPABILITY: [{"principal": ["mcp"]}]}),
                     json.dumps({idn.CAPABILITY: [{"principal": None}]}), "\x00", "=?x?q?y?="):
            with redirect_stderr(io.StringIO()) as err:
                self.assertIsNone(i.resolve(headers(Tailscale_App_Capabilities=caps)).principal, caps[:20])
            self.assertEqual(err.getvalue(), "", caps[:20])                          # handled, not the safety net
        for bad in (5, ["owner"], None, b"owner"):
            self.assertEqual(i.sign_in(bad, "x").status, 401)
            self.assertEqual(i.sign_in("owner", bad).status, 401)
            self.assertEqual(i.pair(bad)[0].status, 401)
        self.assertEqual(i.sign_in("o" * 10000, "x").status, 401)
        for cookie in ("machiya_session=" + "a" * 100000, "machiya_session=..", "machiya_session=\x00.\x00"):
            self.assertIsNone(i.resolve(headers(Cookie=cookie)).principal)
        with open(self.path) as f:
            good = f.read()
        for text in ("principals = [1]\n", good + "\npairing = 5\n", good.replace("[[principals.vm.tokens]]",
                     "tokens = 5\n[[principals.vm.tokens]]", 1), good + '\n[[pairing]]\nprincipal = ["a"]\n'):
            self.write(text if text.startswith(("version", "\n")) or "version" in text else 'version = 1\n'
                       'session_key_file = "session.key"\n' + text)
            with self.assertRaises(idn.IdentityError, msg=text[:30]):
                idn.read_file(self.path)
        with open(self.path, "wb") as f:
            f.write(b"version = 1\n\xff\xfe")
        with self.assertRaises(idn.IdentityError):
            idn.read_file(self.path)
        i.checked = 0
        with redirect_stderr(io.StringIO()):
            self.assertEqual(i.resolve(headers(Tailscale_User_Login="owner@example.com")).principal.name, "owner")

    def test_m3_write_file_never_follows_a_symlink(self):
        with open(self.path, "rb") as f:
            data = idn.tomllib.load(f)
        os.chmod(self.path, 0o640)
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("keep me")
        os.symlink(victim, self.path + ".bak")
        idn.write_file(self.path, data)
        with open(victim) as f:
            self.assertEqual(f.read(), "keep me")                                    # the .bak symlink was replaced
        self.assertFalse(os.path.islink(self.path + ".bak"))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(os.stat(self.path + ".bak").st_mode), 0o640)
        link = os.path.join(self.dir, "link.toml")
        os.symlink(self.path, link)
        with self.assertRaises(idn.IdentityError):
            idn.write_file(link, data)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])

    def test_m4_a_reused_name_starts_afresh(self):
        i = self.ident(signin=True)
        value = i.sign_in("partner", "partner pass 1").cookies[0].split(";")[0].split("=", 1)[1]
        with open(self.path) as f:
            text = f.read()
        self.write(text.replace('id = "partnerid0000000"', 'id = "a1b2c3d4e5f6a7b8"'))
        i.checked = 0
        self.assertIsNone(i.resolve(headers(Cookie="machiya_session=" + value)).principal)

    def test_l1_a_signature_has_one_spelling(self):
        i = self.ident(signin=True)
        value = i.sign_in("owner", "correct horse battery").cookies[0].split(";")[0].split("=", 1)[1]
        body, mac = value.split(".")
        for v in (body + "." + mac[:10] + "!*~" + mac[10:], body + "." + mac + "=", body + "." + mac + "==",
                  body + "." + mac.replace("-", "+").replace("_", "/"), body + "." + " ".join(mac)):
            if v != value:
                self.assertIsNone(i.resolve(headers(Cookie="machiya_session=" + v)).principal, v)

    def test_l2_l3_names_and_keys_are_exact(self):
        with open(self.path) as f:
            good = f.read()
        for bad in (good.replace("[principals.newbie]", '[principals."newbie\\n"]'),
                    good.replace('tailscale_tag = "mcp"', 'tailscale_tag = "mcp\\n"'),
                    good.replace('id = "abcd12"', "id = 123456"),
                    good.replace('label = "claude VM"', 'label = "claude VM"\nowner = true')):
            self.write(bad)
            with self.assertRaises(idn.IdentityError):
                idn.read_file(self.path)
        with open(self.path, "w") as f:
            f.write(good)
        with open(self.path, "rb") as f:
            data = idn.tomllib.load(f)
        data["principals"]["mcp"]["limits"] = {"a = 1 }\nowner = true\n#": 1}
        with self.assertRaises(idn.IdentityError):                                    # quoted, then refused
            idn.write_file(self.path, data)
        self.assertNotIn("owner = true\n#", idn.dump(data))

    def test_l4_duplicates_and_exact_proxy_logins(self):
        import email.message
        m = email.message.Message()
        m["Tailscale-User-Login"] = "partner@example.com"
        m["Tailscale-User-Login"] = "owner@example.com"
        self.assertEqual(self.ident().resolve(m).status, 401)
        m = email.message.Message()
        m["Authorization"] = "Bearer mch_abcd12_" + SECRET
        m["Authorization"] = "Bearer junk"
        self.assertEqual(self.ident().resolve(m).status, 401)
        i = self.ident(auth="header", header="Remote-User")
        self.assertEqual(i.resolve(headers(Remote_User="Owner")).status, 403)        # proxies compare exactly

    def test_n1_a_write_during_a_reload_is_not_lost(self):
        i = self.ident()
        with open(self.path) as f:
            text = f.read()
        real = idn.read_file

        def racing(path):
            got = real(path)                                                         # read the old file, then...
            self.write(text.replace("partner@example.com", "partner3@example.com"))   # ...the CLI writes
            st = os.stat(self.path)
            os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
            return got
        self.write(text + "\n")
        i.checked = 0
        with mock.patch.object(idn, "read_file", racing):
            i.current()
        i.checked = 0
        self.assertEqual(i.resolve(headers(Tailscale_User_Login="partner3@example.com")).principal.name, "partner")

    def test_n2_junk_names_cant_free_a_locked_name(self):
        t = idn.Throttle(5, 900)
        for _ in range(5):
            t.take("owner")
        self.assertFalse(t.take("owner"))
        for n in range(t.MAX_KEYS + 300):
            t.take("junk%d" % n)
        self.assertFalse(t.take("owner"))                                           # still locked
        self.assertLessEqual(len(t.hits), t.MAX_KEYS)

    def test_m4_a_person_needs_an_id(self):
        with open(self.path) as f:
            text = f.read()
        self.write(text.replace('id = "newbieid00000000"\n', ""))
        with self.assertRaises(idn.IdentityError):
            idn.read_file(self.path)

    def test_l5_the_throttle_stays_small(self):
        t = idn.Throttle(5, 900)
        for n in range(t.MAX_KEYS + 500):
            t.take("10.%d" % n)
        self.assertLessEqual(len(t.hits), t.MAX_KEYS)

    def test_l6_only_the_clis_scrypt_parameters(self):
        slow = "scrypt$131072$8$16$" + idn.b64e(b"s" * 16) + "$" + idn.b64e(b"d" * 32)
        self.assertFalse(idn.check_password("x", slow))
        with open(self.path) as f:
            text = f.read()
        self.write(text.replace(idn.hash_password("correct horse battery").split("$")[0] + "$16384$", "scrypt$131072$")
                   .replace('password = "scrypt$16384$', 'password = "scrypt$131072$', 1))
        with self.assertRaises(idn.IdentityError):
            idn.read_file(self.path)                                                 # refused when the file loads

    def test_l7_every_session_cookie_is_tried(self):
        i = self.ident(signin=True)
        value = i.sign_in("owner", "correct horse battery").cookies[0].split(";")[0].split("=", 1)[1]
        r = i.resolve(headers(Cookie="machiya_session=junk; machiya_session=" + value))
        self.assertEqual(r.principal.name, "owner")

    def test_l8_an_empty_authorization_is_a_bad_proof(self):
        self.assertEqual(self.ident().resolve(headers(Authorization="", Tailscale_User_Login="owner@example.com"))
                         .status, 401)
        o = self.ident(auth="open", signin=True)
        r = o.resolve(headers(Cookie="machiya_session=junk"))
        self.assertEqual(r.principal.name, "local")
        self.assertIn("Max-Age=0", r.cookies[0])                                     # the bad cookie is cleared


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "identity.toml")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = idn.main(["--file", self.path] + list(args))
        return code, out.getvalue().strip()

    def test_round_trip(self):
        self.assertEqual(self.cli("init")[0], 0)
        key = os.path.join(self.dir, "session.key")
        self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.cli("add", "owner", "--kind", "person", "--owner")
        self.cli("login", "owner", "tailscale", "owner@example.com")
        self.cli("add", "mcp", "--kind", "agent")
        self.cli("tag", "mcp", "mcp")
        self.cli("grant", "mcp", "konbini", "read", "write")
        self.cli("grant", "mcp", "kura", "read", "--vaults", "default", "shared")
        code, token = self.cli("token", "mint", "mcp", "--label", "test", "--days", "30")
        self.assertTrue(token.startswith("mch_"))
        with open(self.path) as f:
            self.assertNotIn(token.split("_", 2)[2], f.read())             # only the hash is kept
        with mock.patch("getpass.getpass", side_effect=["a long passphrase", "a long passphrase"]):
            self.assertEqual(self.cli("passwd", "owner")[0], 0)
        code, pair = self.cli("pair", "owner", "--label", "phone")
        self.assertRegex(pair, r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual(self.cli("epoch", "bump", "owner")[0], 0)
        code, out = self.cli("check")
        self.assertIn("ok: 2 principals, 1 tokens, 1 pairing codes", out)
        self.assertTrue(os.path.exists(self.path + ".bak"))

        i = idn.Identity(self.path, "konbini", accept_caps=True)
        r = i.resolve(headers(Authorization="Bearer " + token))
        self.assertTrue(r.principal.can("konbini", "write") and not r.principal.can("konbini", "areas"))
        self.assertEqual(r.principal.vaults(), ("default", "shared"))
        self.assertEqual(i.sign_in("owner", "a long passphrase").status, 404)        # sign-in is a room's choice
        self.assertEqual(idn.Identity(self.path, "konbini", signin=True).sign_in("owner", "a long passphrase").status,
                         200)
        self.assertEqual(i.resolve(headers(Tailscale_App_Capabilities=json.dumps(
            {idn.CAPABILITY: [{"principal": "mcp"}]}))).principal.name, "mcp")

        tid = token.split("_")[1]
        self.cli("token", "revoke", tid)
        i.checked = 0
        os.utime(self.path, ns=(time.time_ns(), time.time_ns() + 10 ** 9))
        self.assertEqual(i.resolve(headers(Authorization="Bearer " + token)).status, 401)

    def test_refusals(self):
        self.cli("init")
        with self.assertRaises(SystemExit):
            self.cli("init")                                                         # never over an existing file
        self.cli("add", "a", "--kind", "agent")
        with self.assertRaises(SystemExit):
            self.cli("grant", "a", "konbini", "fly")                                 # not written: a room would refuse
        with self.assertRaises(SystemExit):
            self.cli("add", "b", "--kind", "agent", "--owner")
        self.cli("add", "p", "--kind", "person")
        with mock.patch("getpass.getpass", side_effect=["short", "short"]):
            with self.assertRaises(SystemExit):
                self.cli("passwd", "p")                                              # under 12 characters
        with mock.patch("getpass.getpass", side_effect=["a long passphrase", "another passphrase"]):
            with self.assertRaises(SystemExit):
                self.cli("passwd", "p")                                              # typed differently
        self.assertIn("ok:", self.cli("check")[1])


if __name__ == "__main__":
    unittest.main()
