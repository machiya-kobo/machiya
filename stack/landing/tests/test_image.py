"""The image carries every module the app imports (0.2.1 shipped without today.py and crashed at start)."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ImageTest(unittest.TestCase):
    def test_dockerfile_copies_every_module(self):
        df = open(os.path.join(HERE, "Dockerfile")).read()
        copies = " ".join(l for l in df.splitlines() if l.startswith("COPY"))
        if "*.py" in copies:                                 # a glob takes every module beside landing.py
            return
        for name in sorted(os.listdir(HERE)):
            if name.endswith(".py"):
                self.assertIn(name, copies, "the Dockerfile doesn't COPY " + name)

    def test_local_imports_exist(self):
        src = open(os.path.join(HERE, "landing.py")).read()
        for mod in re.findall(r"^import (\w+)", src, re.M):
            if os.path.exists(os.path.join(HERE, mod + ".py")) or mod in ("json", "os", "sys", "threading", "time"):
                continue
            __import__(mod)                                  # a stdlib module; a missing local one fails here


if __name__ == "__main__":
    unittest.main()
