"""vaultkit.names: Hister is now Nogon, stage 1 (readers accept both spellings): python3 -m unittest tests.test_names"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vaultkit import histerauth, names, prefs, shell   # noqa: E402


class Setting(unittest.TestCase):
    def setUp(self):
        names._warned.clear()

    def test_new_name_wins_even_empty(self):
        self.assertEqual(names.setting("KURA_NOGON_URL", {"KURA_NOGON_URL": "n", "KURA_HISTER_URL": "h"}), "n")
        self.assertEqual(names.setting("KURA_NOGON_URL", {"KURA_NOGON_URL": "", "KURA_HISTER_URL": "h"}), "")

    def test_old_name_read_and_logged_once(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(names.setting("NOGON_LOGIN_DB", {"HISTER_LOGIN_DB": "/d"}), "/d")
            self.assertEqual(names.setting("NOGON_LOGIN_DB", {"HISTER_LOGIN_DB": "/d"}), "/d")
        self.assertEqual(err.getvalue().count("HISTER_LOGIN_DB"), 1)
        self.assertIn("NOGON_LOGIN_DB", err.getvalue())

    def test_default_and_names_without_nogon(self):
        self.assertIsNone(names.setting("KURA_NOGON_URL", {}))
        self.assertEqual(names.setting("KURA_NOGON_URL", {}, "x"), "x")
        self.assertEqual(names.setting("KURA_URL", {"KURA_URL": "u"}), "u")

    def test_room_and_auth(self):
        self.assertEqual(names.room_key("nogon"), "hister")
        self.assertEqual(names.room_key("kura"), "kura")
        self.assertEqual(names.auth_mode(" Nogon "), "hister")
        self.assertEqual(names.auth_mode("hister"), "hister")
        self.assertEqual(names.auth_mode(None), "")


class Readers(unittest.TestCase):
    def test_machiya_rooms_takes_nogon(self):
        old = shell.rooms({"MACHIYA_ROOMS": "kura=https://k,hister=https://n/"})
        new = shell.rooms({"MACHIYA_ROOMS": "kura=https://k,nogon=https://n/"})
        self.assertEqual(old, new)
        self.assertEqual(new, {"kura": "https://k", "hister": "https://n"})

    def test_apps_hidden_takes_nogon(self):
        self.assertEqual(prefs._apps("nogon,kura"), prefs._apps("hister,kura"))
        self.assertEqual(prefs._apps("nogon"), "hister")

    def test_auth_nogon_and_nogon_users(self):
        base = {"NIWA_AUTH_SIGNIN_URL": "https://login.example.ts.net/machiya/signin", "NIWA_USERS": "me@passkey",
                "NIWA_PUBLIC_URL": "https://niwa.example.ts.net", "NIWA_AUTH_URL": "http://login:8081",
                "NIWA_BIND_BEHIND_PROXY": "1"}
        for auth, users in (("hister", {"NIWA_HISTER_USERS": "owner"}), ("nogon", {"NIWA_NOGON_USERS": "owner"}),
                            ("nogon", {"NIWA_HISTER_USERS": "owner"}), ("hister", {"NIWA_NOGON_USERS": "owner"})):
            with self.subTest(auth=auth, users=users), contextlib.redirect_stderr(io.StringIO()):
                a = histerauth.load_for("niwa", dict(base, NIWA_AUTH=auth, **users))
                self.assertIsNotNone(a)
                self.assertEqual(set(a.users), {"owner"})
        self.assertIsNone(histerauth.load_for("niwa", dict(base, NIWA_AUTH="tailscale")))


if __name__ == "__main__":
    unittest.main()
