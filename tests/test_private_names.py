"""No private names in the repository: the hosts, tailnet, accounts and people of the deployment Machiya grew up in
must not reappear in code, comments, tests, docs or config once the repos are public. python3 -m unittest
tests.test_private_names (stdlib only; uses `git ls-files` when it can, else walks the tree).

The list lives outside the repository, so the public repo doesn't publish what it guards: one word per line in
$MACHIYA_PRIVATE_NAMES, else ~/.config/machiya/private-names (`word<TAB># what kind of name`; blank lines and
lines starting with # are ignored). Without the file the scan is skipped and says so; the maintainers keep it.

How it works. Every tracked text file is split into words (lowercase letters and digits, hyphens kept inside a
word: `forgejo.example-host.ts.net` gives `forgejo`, `example-host`, `ts`, `net`); each word, each hyphen part, and
each of those without trailing digits (`host1` -> `host`) is looked up in the list. The test prints the path, line
and word of every finding.

When it fails: replace the name with a neutral example (`example.ts.net`, `<host>`, `owner`, `you`, `your-org`,
the dev seeds' `lantern` and `workshop`), or a neutral fixture in a test. Don't add an ALLOWED entry to silence a
real finding: ALLOWED is only for text that must name a person or place on purpose, each with its reason. Never
write a listed word into this file, a commit message or an issue.
"""
import os
import re
import subprocess
import sys
import unittest
from fnmatch import fnmatch

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def private_list():
    """{word: what kind of name} from $MACHIYA_PRIVATE_NAMES, else ~/.config/machiya/private-names; {} without it."""
    path = os.environ.get("MACHIYA_PRIVATE_NAMES") or os.path.expanduser("~/.config/machiya/private-names")
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                word, _, kind = line.partition("#")
                if word.strip():
                    out[word.strip().lower()] = kind.strip() or "a private name"
    except OSError:
        pass
    return out


PRIVATE = private_list()

# (path glob or "*", the exact text allowed, why). The allowed text is cut out of a line before it is checked, so
# anything else on that line still counts. Keep this list short and give every entry a reason.
ALLOWED = [
    ("*", "Micheal Waltz and Machiya contributors",
     "the copyright holder line (LICENSE notices, file headers, README): deliberate"),
]

WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


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
                kind = private.get(w)
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
        if not PRIVATE:
            self.skipTest("no private-names list (MACHIYA_PRIVATE_NAMES or ~/.config/machiya/private-names)")
        found = scan()
        self.assertEqual(found, [], "private names (see this file's docstring for the fix):\n" + "\n".join(
            "  %s:%d: %s (%s)" % f for f in found))

    def test_the_matcher(self):
        """The splitter and the allow-list, on invented words (a stand-in PRIVATE)."""
        fake = {"quillhost": "host", "pond-heron": "tailnet", "ada": "person"}
        text = ("ssh quillhost3 uptime\nhttps://forgejo.pond-heron.ts.net/x\n/home/ada/git\nquillhosting is fine\n"
                "pond-heronry is fine, so is heron\nCopyright (C) 2026 Ada Q and friends\n")
        got = findings_in("x.md", text, fake, [("*", "Ada Q and friends", "test")])
        self.assertEqual([(n, w) for n, w, _ in got], [(1, "quillhost3"), (2, "pond-heron"), (3, "ada")])

    def test_every_allowance_has_a_reason(self):
        for glob, text, why in ALLOWED:
            self.assertTrue(glob and text.strip() and len(why) > 10, (glob, text))


if __name__ == "__main__":
    unittest.main()
