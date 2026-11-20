"""Check a vendored copy against the manifest vendor.sh wrote (VENDORED): `python3 -m vaultkit.verify`.

Exits 1 and names the files when the copy was edited in place, a file is missing, or one was added. A service's
tests call check() so drift fails its build; the fix is a change in machiya-kobo/machiya (vaultkit/), a tag, and a re-vendor.
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def check(directory=HERE):
    """[problem, ...] ([] = the copy matches its manifest; a checkout of vaultkit itself has no manifest)."""
    manifest = os.path.join(directory, "VENDORED")
    if not os.path.exists(manifest):
        return []
    want = {}
    with open(manifest, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            digest, name = line.split(None, 1)
            want[name.strip()] = digest
    have = sorted(n for n in os.listdir(directory) if n.endswith(".py"))
    ui = os.path.join(directory, "ui")
    if os.path.isdir(ui):
        have += sorted("ui/" + n for n in os.listdir(ui))
    problems = ["%s: not in the manifest (added by hand?)" % n for n in have if n not in want]
    for name, digest in sorted(want.items()):
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            problems.append("%s: missing" % name)
            continue
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != digest:
                problems.append("%s: edited in place (change it in machiya-kobo/machiya vaultkit/, tag, re-vendor)" % name)
    return problems


def version(directory=HERE):
    try:
        with open(os.path.join(directory, "VENDORED"), encoding="utf-8") as f:
            line = f.readline().strip()
        return line[len("# vaultkit "):] if line.startswith("# vaultkit ") else line
    except OSError:
        return "checkout"


if __name__ == "__main__":
    found = check()
    for p in found:
        print("vaultkit drift:", p)
    print("vaultkit %s: %s" % (version(), "DRIFT" if found else "ok"))
    sys.exit(1 if found else 0)
