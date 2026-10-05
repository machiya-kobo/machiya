"""No private names in the repository: the hosts, tailnet, accounts and people of the deployment Machiya grew up in
must not reappear in code, comments, tests, docs or config once the repos are public. python3 -m unittest
tests.test_private_names (stdlib only; uses `git ls-files` when it can, else walks the tree).

How it works. Every tracked text file is split into words (lowercase letters and digits, hyphens kept inside a
word: `forgejo.example-host.ts.net` gives `forgejo`, `example-host`, `ts`, `net`); each word, each hyphen part, and
each of those without trailing digits (`host1` -> `host`) is hashed and looked up in PRIVATE. The list holds salted
hashes, not the names, so this file doesn't publish what it guards. The test prints the path, line and word of
every finding.

When it fails: replace the name with a neutral example (`example.ts.net`, `<host>`, `owner`, `you`, `your-org`,
the dev seeds' `lantern` and `workshop`), or a neutral fixture in a test. Don't add an ALLOWED entry to silence a
real finding: ALLOWED is only for text that must name a person or place on purpose, each with its reason.

Add a name: `python3 tests/test_private_names.py --hash <word>` prints the line for PRIVATE (a word as the splitter
sees it: lowercase, one hyphenated run, no trailing digits). Never write the word itself into this file, a commit
message or an issue.
"""
import hashlib
import os
import re
import subprocess
import sys
import unittest
from fnmatch import fnmatch

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SALT = "machiya-private-name:"

# Salted sha256 (first 20 hex digits) of each private word, with what kind of name it is.
PRIVATE = {
    "00000000000000000000": "a server's hostname",
    "00000000000000000000": "a server's hostname",
    "00000000000000000000": "a hostname (any trailing digits)",
    "00000000000000000000": "the tailnet's name",
    "00000000000000000000": "an account, domain and forge owner",
    "00000000000000000000": "a person's first name and a login",
    "00000000000000000000": "a person's surname",
}

# (path glob or "*", the exact text allowed, why). The allowed text is cut out of a line before it is checked, so
# anything else on that line still counts. Keep this list short and give every entry a reason.
ALLOWED = [
    ("*", "Micheal Waltz and Machiya contributors",
     "the copyright holder line (LICENSE notices, file headers, README): deliberate"),
]

WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def digest(word):
    return hashlib.sha256((SALT + word).encode("utf-8")).hexdigest()[:20]


def candidates(token):
    out = set()
    for w in [token] + token.split("-"):
        if w:
            out.add(w)
            bare = w.rstrip("0123456789")
            if bare:
                out.add(bare)
    return out


def tracked_files(root=ROOT):
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, timeout=60)
        if r.returncode == 0 and r.stdout:
            return [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".git", "__pycache__", ".venv", "node_modules")]
        out += [os.path.relpath(os.path.join(d, n), root) for n in names]
    return out


def allowed_for(path, allowed=None):
    return [text for glob, text, _ in (ALLOWED if allowed is None else allowed) if glob == "*" or fnmatch(path, glob)]


def findings_in(path, text, private=None, allowed=None):
    """[(line number, word, kind)] of every private word in text."""
    private = PRIVATE if private is None else private
    allowed = allowed_for(path, allowed)
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for a in allowed:
            line = line.replace(a, " ")
        for token in WORD.findall(line.lower()):
            for w in candidates(token):
                kind = private.get(digest(w))
                if kind:
                    found.append((n, token, kind))
                    break
    return found


def scan(root=ROOT):
    found = []
    for rel in tracked_files(root):
        try:
            with open(os.path.join(root, rel), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue                                    # binary (screenshots, icons): not checked here
        text = raw.decode("utf-8", "replace")
        found += [(rel, n, w, kind) for n, w, kind in findings_in(rel, text)]
    return found


class PrivateNames(unittest.TestCase):
    def test_no_private_names(self):
        found = scan()
        self.assertEqual(found, [], "private names (see this file's docstring for the fix):\n" + "\n".join(
            "  %s:%d: %s (%s)" % f for f in found))

    def test_the_matcher(self):
        """The splitter and the allow-list, on invented words (hashed here as a stand-in PRIVATE)."""
        fake = {digest("quillhost"): "host", digest("pond-heron"): "tailnet", digest("ada"): "person"}
        text = ("ssh quillhost3 uptime\nhttps://forgejo.pond-heron.ts.net/x\n/home/ada/git\nquillhosting is fine\n"
                "pond-heronry is fine, so is heron\nCopyright (C) 2026 Ada Q and friends\n")
        got = findings_in("x.md", text, fake, [("*", "Ada Q and friends", "test")])
        self.assertEqual([(n, w) for n, w, _ in got], [(1, "quillhost3"), (2, "pond-heron"), (3, "ada")])

    def test_every_allowance_has_a_reason(self):
        for glob, text, why in ALLOWED:
            self.assertTrue(glob and text.strip() and len(why) > 10, (glob, text))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--hash":
        w = sys.argv[2].strip().lower()
        print('    "%s": "<what kind of name>",' % digest(w))
    else:
        unittest.main()
