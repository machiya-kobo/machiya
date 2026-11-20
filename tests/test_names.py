"""vaultkit.names: Hister is now Nogon, stage 3 (vaultkit 0.41: the house's names are Nogon's, the old setting names
refuse to start, stored room ids still read): python3 -m unittest tests.test_names"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vaultkit import names, nogonauth, prefs, shell   # noqa: E402
from vaultkit.identity import IdentityError           # noqa: E402


class Setting(unittest.TestCase):
    def setUp(self):
        names._warned.clear()

    def test_new_name_wins_even_empty(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(names.setting("KURA_NOGON_URL", {"KURA_NOGON_URL": "n", "KURA_HISTER_URL": "h"}), "n")
            self.assertEqual(names.setting("KURA_NOGON_URL", {"KURA_NOGON_URL": "", "KURA_HISTER_URL": "h"}), "")
        self.assertEqual(names.setting("KURA_NOGON_URL", {"KURA_NOGON_URL": "n"}), "n")

    def test_old_name_alone_refuses_naming_the_new_one(self):
        for old, new in (("HISTER_LOGIN_DB", "NOGON_LOGIN_DB"), ("KURA_HISTER_URL", "KURA_NOGON_URL")):
            with self.subTest(old=old), self.assertRaises(names.OldName) as cm:
                names.setting(new, {old: "/d"})
            self.assertIn(old, str(cm.exception))
            self.assertIn("rename it " + new, str(cm.exception))
            self.assertIsInstance(cm.exception, ValueError)
        with self.assertRaises(names.OldName):                   # even empty: it was set, so someone meant it
            names.setting("KURA_NOGON_URL", {"KURA_HISTER_URL": ""})

    def test_old_name_beside_the_new_one_is_ignored_and_said_once(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(2):
                self.assertEqual(names.setting("NOGON_LOGIN_DB", {"NOGON_LOGIN_DB": "/n", "HISTER_LOGIN_DB": "/d"}),
                                 "/n")
        self.assertEqual(err.getvalue().count("HISTER_LOGIN_DB"), 1)
        self.assertIn("ignored", err.getvalue())

    def test_default_and_names_without_nogon(self):
        self.assertIsNone(names.setting("KURA_NOGON_URL", {}))
        self.assertEqual(names.setting("KURA_NOGON_URL", {}, "x"), "x")
        self.assertEqual(names.setting("KURA_URL", {"KURA_URL": "u"}), "u")
        self.assertEqual(names.setting("KURA_URL", {"KURA_URL": "u", "KURA_HISTER_URL": "h"}), "u")   # no NOGON in it

    def test_room_key(self):
        self.assertEqual(names.room_key("hister"), "nogon")      # a stored old id
        self.assertEqual(names.room_key("nogon"), "nogon")
        self.assertEqual(names.room_key("kura"), "kura")

    def test_auth_mode(self):
        self.assertEqual(names.auth_mode(" Nogon "), "nogon")
        self.assertEqual(names.auth_mode(None), "")
        self.assertEqual(names.auth_mode("tailscale"), "tailscale")
        with self.assertRaisesRegex(names.OldName, r"KURA_AUTH=hister .*use KURA_AUTH=nogon"):
            names.auth_mode("Hister", "KURA_AUTH")
        with self.assertRaisesRegex(names.OldName, "use AUTH=nogon"):
            names.auth_mode("hister")


class Readers(unittest.TestCase):
    def test_machiya_rooms_takes_hister_as_nogon(self):
        old = shell.rooms({"MACHIYA_ROOMS": "kura=https://k,hister=https://n/"})
        new = shell.rooms({"MACHIYA_ROOMS": "kura=https://k,nogon=https://n/"})
        self.assertEqual(old, new)
        self.assertEqual(new, {"kura": "https://k", "nogon": "https://n"})

    def test_apps_hidden_takes_hister_and_writes_nogon(self):
        self.assertEqual(prefs._apps("hister,kura"), prefs._apps("nogon,kura"))
        self.assertEqual(prefs._apps("hister"), "nogon")
        self.assertEqual(prefs.check("apps_hidden", "searxng,hister"), "nogon,searxng")
        self.assertEqual(prefs.shared_only({"apps_hidden": "hister"}), {"apps_hidden": "nogon"})

    def test_stored_hister_is_read_as_nogon(self):
        """A row written before 0.41 holds `hister`: the store answers `nogon`, and the next write keeps `nogon`."""
        store = prefs.Store(":memory:")
        store.write("hi:x", {"apps_hidden": "kura,hister"})                   # as 0.40 stored it, unvalidated
        self.assertEqual(store.snapshot("hi:x")[1], {"apps_hidden": "kura,nogon"})
        _, values, _ = store.write("hi:x", prefs.validate({"apps_hidden": "kura,hister"}))
        self.assertEqual(values, {"apps_hidden": "kura,nogon"})
        with store.lock, store._conn() as db:
            self.assertEqual(db.execute("SELECT value FROM prefs").fetchone()[0], "kura,nogon")
        store.write("hi:y", {"apps_hidden": "kura,evil"})                     # anything else is left as it is
        self.assertEqual(store.snapshot("hi:y")[1], {"apps_hidden": "kura,evil"})
        merged, _, _, refused = prefs.merge([("old", [("apps_hidden", "hister", 5)])])
        self.assertEqual((merged, refused), ({"apps_hidden": "nogon"}, []))

    def test_stored_hister_still_hides_the_nogon_room(self):
        """An account's apps_hidden and a show_hister cookie saved before 0.41 still hide Nogon."""
        links = shell.rooms({"MACHIYA_ROOMS": "kura=https://k,nogon=https://n"})
        for ctx in (shell.prefs("", account={"apps_hidden": "hister"}), shell.prefs("machiya_show_hister=false"),
                    shell.prefs("show_hister=false")):
            with self.subTest(extra=ctx.extra):
                self.assertEqual(shell.shown_of(ctx), {"nogon": False})
                self.assertNotIn("show_hister", ctx.extra)
                rows = "".join(shell.rooms_section(ctx, "kura", links)[1])
                self.assertIn('data-set="show_nogon">', rows)           # off: no "checked"
                self.assertNotIn("show_hister", rows)
        # a choice under the new id wins over one under the old
        self.assertEqual(shell.shown_of(shell.prefs("machiya_show_hister=false; machiya_show_nogon=true")), {})
        self.assertEqual(shell.shown_of(shell.prefs("machiya_show_hister=true; show_nogon=false")), {"nogon": False})

    def test_a_rooms_own_links_with_hister(self):
        """A room that builds its links itself with the old key still gets the Nogon row, under the new id."""
        menu = shell.switcher("kura", {"kura": "https://k", "hister": "https://n"})
        self.assertIn('href="https://n/" data-room="nogon"', menu)
        self.assertNotIn('data-room="hister"', menu)
        rows = "".join(shell._app_rows("kura", {"hister": "https://n"}, {"hister": False}))
        self.assertIn('data-set="show_nogon">', rows)

    def test_hister_is_not_an_extra_room(self):
        self.assertIn("hister", shell._builtin_keys())
        self.assertIn("nogon", shell._builtin_keys())

    def test_auth_nogon_and_nogon_users(self):
        base = {"NIWA_AUTH_SIGNIN_URL": "https://login.example.ts.net/machiya/signin", "NIWA_USERS": "me@passkey",
                "NIWA_PUBLIC_URL": "https://niwa.example.ts.net", "NIWA_AUTH_URL": "http://login:8081",
                "NIWA_BIND_BEHIND_PROXY": "1"}
        a = nogonauth.load_for("niwa", dict(base, NIWA_AUTH="nogon", NIWA_NOGON_USERS="owner"))
        self.assertEqual(set(a.users), {"owner"})
        for auth, users in (("hister", {"NIWA_NOGON_USERS": "owner"}), ("nogon", {"NIWA_HISTER_USERS": "owner"}),
                            ("hister", {"NIWA_HISTER_USERS": "owner"})):
            with self.subTest(auth=auth, users=users), self.assertRaises(IdentityError) as cm:
                nogonauth.load_for("niwa", dict(base, NIWA_AUTH=auth, **users))
            self.assertIn("NIWA_AUTH=nogon" if auth == "hister" else "NIWA_NOGON_USERS", str(cm.exception))
        self.assertIsNone(nogonauth.load_for("niwa", dict(base, NIWA_AUTH="tailscale")))


if __name__ == "__main__":
    unittest.main()
