"""vault-mirror's tests: status.json after a good and a failed update. Run from stack/vault-mirror:
python3 -m unittest discover -s tests (needs git; uses vaultkit/git.py from this checkout, as the image does)."""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))          # the checkout's vaultkit/
sys.path.insert(0, os.path.join(HERE, ".."))


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


class MirrorStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "a.md"), "w") as f:
            f.write("a\n")
        git(self.src, "init", "-q", "-b", "main")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-qm", "a")
        os.environ.update(MIRROR_URL="file://" + self.src, MIRROR_DIR=os.path.join(self.tmp, "data", "vault"))
        os.makedirs(os.path.join(self.tmp, "data"))
        import mirror
        self.mirror = importlib.reload(mirror)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def status(self):
        with open(self.mirror.STATUS) as f:
            return json.load(f)

    def test_a_failed_fetch_is_not_synced(self):
        state = {}
        self.mirror.once(state)
        good = self.status()
        self.assertTrue(good["head"])
        self.assertIsNone(good["error"])
        os.rename(self.src, self.src + ".gone")                          # the forge is down, or the token revoked
        self.mirror.once(state)
        bad = self.status()
        self.assertEqual(bad["head"], good["head"])
        self.assertEqual(bad["synced_at"], good["synced_at"])           # not a fresh "synced"
        self.assertIn("fetch", bad["error"])
        os.rename(self.src + ".gone", self.src)
        self.mirror.once(state)
        self.assertIsNone(self.status()["error"])

    def test_credentials_in_a_url_never_reach_the_status(self):
        self.mirror.m.url = "https://user:s3cret@forge.invalid/x.git"
        self.mirror.m.run("remote", "set-url", "origin", self.mirror.m.url)
        self.mirror.once({})                                             # the first run clones from file://
        self.mirror.once({})
        self.assertNotIn("s3cret", json.dumps(self.status()))


if __name__ == "__main__":
    unittest.main()
