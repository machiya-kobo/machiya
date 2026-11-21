"""HISTER_TOKEN_FILE (docs/contracts/hister.md): the owner's Hister token goes to Hister as X-Access-Token on every call,
to nothing else, never into a log, a repr or an error; unset sends none; a rotated file is picked up; and no write may
change a page's owner (changes.user_id). Run: python3 -m unittest discover -s stack/mcp/tests"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp                                  # noqa: E402
from backend import HISTER_ORIGIN, Backend, SecretFile   # noqa: E402
from test_mcp import call, make_server, rooms              # noqa: E402

TOOLS = (("pages_labels", {}), ("collections_list", {}),
         ("pages_set_label", {"url": "https://example.com/a", "label": "python"}),
         ("machiya_status", {}), ("board_set_next", {"slug": "alpha", "next": "n"}))


class HisterToken(unittest.TestCase):
    TOKEN = "HisterOwnerToken0123456789"

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.file = os.path.join(self.folder, "hister.token")
        with open(self.file, "w") as f:
            f.write(self.TOKEN + "\n")
        self.fakes = rooms()
        self.addCleanup(lambda: [f.close() for f in self.fakes.values()])

    def run_tools(self, server):
        self.addCleanup(os.unlink, server.log_path)
        for name, args in TOOLS:
            self.assertFalse(call(server, name, args).get("isError"), name)

    def test_sent_to_hister_on_every_call_and_nowhere_else(self):
        server = make_server(self.fakes, HISTER_TOKEN_FILE=self.file)
        self.run_tools(server)
        seen = self.fakes["hister"].seen
        self.assertGreaterEqual(len(seen), 3)                  # reads and a label write
        for req in seen:
            self.assertEqual(req["headers"].get("x-access-token"), self.TOKEN)
            self.assertEqual(req["headers"]["origin"], HISTER_ORIGIN)
            self.assertNotIn("authorization", req["headers"])
        for room in ("kura", "konbini", "niwa"):
            self.assertTrue(self.fakes[room].seen, room)
            for req in self.fakes[room].seen:
                self.assertNotIn("x-access-token", req["headers"], room)
        with open(server.log_path) as f:
            self.assertNotIn(self.TOKEN, f.read())
        self.assertNotIn(self.TOKEN, repr(server.backends) + repr(server.config.hister_token))

    def test_unset_sends_none(self):
        server = make_server(self.fakes)
        self.run_tools(server)
        self.assertTrue(self.fakes["hister"].seen)
        for f in self.fakes.values():
            self.assertTrue(all("x-access-token" not in r["headers"] for r in f.seen))
        self.assertIsNone(server.config.hister_token)

    def test_both_tokens_each_to_its_own(self):
        rooms_file = os.path.join(self.folder, "mcp.token")
        with open(rooms_file, "w") as f:
            f.write("mch_abcd_" + "s" * 43 + "\n")
        server = make_server(self.fakes, HISTER_TOKEN_FILE=self.file, MCP_TOKEN_FILE=rooms_file)
        self.run_tools(server)
        for req in self.fakes["hister"].seen:
            self.assertEqual((req["headers"].get("x-access-token"), req["headers"].get("authorization")),
                             (self.TOKEN, None))
        for req in self.fakes["kura"].seen:
            self.assertEqual(req["headers"].get("x-access-token"), None)
            self.assertTrue(req["headers"]["authorization"].startswith("Bearer mch_"))

    def test_bad_file_refuses_to_start_without_echoing(self):
        empty = os.path.join(self.folder, "empty")
        open(empty, "w").close()
        spaced = os.path.join(self.folder, "spaced")
        with open(spaced, "w") as f:
            f.write("has a space " + self.TOKEN + "\n")
        for path in (empty, spaced, os.path.join(self.folder, "missing"), self.folder):
            with self.assertRaises(SystemExit, msg=path) as cm:
                mcp.Config({"MCP_AUTH": "open", "HISTER_TOKEN_FILE": path})
            self.assertNotIn(self.TOKEN, str(cm.exception))
        self.assertIsNone(mcp.Config({"MCP_AUTH": "open", "HISTER_TOKEN_FILE": " "}).hister_token)

    def test_rotation_is_picked_up_and_a_vanished_file_keeps_the_last(self):
        secret = SecretFile(self.file)
        self.assertEqual(secret.get(), self.TOKEN)
        with open(self.file + ".new", "w") as f:
            f.write("RotatedToken-0987654321\n")
        os.replace(self.file + ".new", self.file)
        self.assertEqual(secret.get(), "RotatedToken-0987654321")
        os.unlink(self.file)
        self.assertEqual(secret.get(), "RotatedToken-0987654321")

    def test_only_hister_may_carry_it(self):
        secret = SecretFile(self.file)
        with self.assertRaises(ValueError):
            Backend("kura", "http://127.0.0.1:1", access_token=secret)
        b = Backend("hister", "http://127.0.0.1:1", origin=HISTER_ORIGIN, access_token=secret)
        self.assertEqual(b.headers("mcp")["X-Access-Token"], self.TOKEN)
        self.assertNotIn(self.TOKEN, repr(b))

    def test_status_says_whether_it_is_set(self):
        server = make_server(self.fakes, HISTER_TOKEN_FILE=self.file)
        self.addCleanup(os.unlink, server.log_path)
        self.assertIsNotNone(server.config.hister_token)

    def test_the_token_never_on_argv(self):
        self.assertNotIn(self.TOKEN, " ".join(sys.argv))


class NeverOwnership(unittest.TestCase):
    def test_changes_user_id_is_refused(self):
        for changes in ({"user_id": 0}, {"user_id": 2}, {"label": "a", "user_id": 1}):
            with self.assertRaises(RuntimeError) as cm:
                mcp.check_route("hister", "POST", "/api/update", {"query": "x", "changes": changes})
            self.assertIn("owner", str(cm.exception))
        mcp.check_route("hister", "POST", "/api/update", {"query": "x", "changes": {"label": "a"}})


if __name__ == "__main__":
    unittest.main()
