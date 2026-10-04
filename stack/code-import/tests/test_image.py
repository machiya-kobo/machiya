"""The image carries every module the importer imports, and its version is the one VERSION and CHANGELOG.md name."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def read(*parts):
    with open(os.path.join(HERE, *parts)) as f:
        return f.read()


class ImageTest(unittest.TestCase):
    def test_dockerfile_copies_every_module(self):
        df = read("Dockerfile")
        copies = " ".join(l for l in df.replace("\\\n", " ").splitlines() if l.startswith("COPY"))
        modules = [n for n in sorted(os.listdir(HERE)) if n.endswith(".py")]
        self.assertTrue(modules)
        for name in modules:
            self.assertIn("stack/code-import/" + name, copies, "the Dockerfile doesn't COPY " + name)
        for path in re.findall(r"stack/code-import/(\S+\.py)", copies):
            self.assertTrue(os.path.exists(os.path.join(HERE, path)), "the Dockerfile COPYs a missing " + path)

    def test_local_imports_exist(self):
        local = {n[:-3] for n in os.listdir(HERE) if n.endswith(".py")}
        for name in local:
            src = read(name + ".py")
            for mod in re.findall(r"^(?:import (\w+)|from (\w+) import)", src, re.M):
                mod = mod[0] or mod[1]
                if mod not in local:
                    __import__(mod)                           # a stdlib module; a missing local one fails here

    def test_version(self):
        import codeimport
        version = read("VERSION").strip()
        self.assertEqual(codeimport.VERSION, version)
        changelog = read("CHANGELOG.md")
        self.assertEqual(re.search(r"^## (\S+)", changelog, re.M).group(1), version)


if __name__ == "__main__":
    unittest.main()
