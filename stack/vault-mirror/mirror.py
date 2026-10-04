"""vault-mirror: the Machiya stack's one shared copy of the vault repo (docs/principles.md).

Keeps a full clone at MIRROR_DIR up to date (vaultkit.Mirror: clone once, then fetch + hard reset every
MIRROR_POLL seconds) and writes MIRROR_DIR/../status.json ({head, synced_at, error, version}). Kura reads the clone
directly (mounted read-only); Niwa and Konbini borrow its objects for their small read-write clones
(`git clone --reference`). Nothing writes to the mirror except this process. An app brought up standalone
doesn't use it: it owns its own copy.
"""
import json
import os
import sys
import time

VERSION = "0.1.1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vaultkit.git import Mirror, read_secret   # noqa: E402

URL = os.environ["MIRROR_URL"]
DIR = os.environ.get("MIRROR_DIR", "/data/vault")
POLL = max(10, int(os.environ.get("MIRROR_POLL", "30")))
STATUS = os.path.join(os.path.dirname(DIR.rstrip("/")), "status.json")
m = Mirror(URL, DIR, os.environ.get("MIRROR_BRANCH", ""), read_secret(os.environ.get("MIRROR_TOKEN_FILE", "")),
           os.environ.get("MIRROR_USER", "") or "token")


def write_status(**kw):
    """status.json for the readers: head, synced_at, error, and (0.1.1) this mirror's version."""
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dict(kw, version=VERSION), f)
    os.replace(tmp, STATUS)




def keep_objects():
    """Niwa's and Konbini's clones borrow this repo's objects (alternates), so it must never prune any: an upstream
    force-push would otherwise let `git gc` drop objects a borrower still needs. Idempotent, runs after each clone."""
    m.run("config", "gc.auto", "0")
    m.run("config", "gc.pruneExpire", "never")
    m.run("config", "gc.reflogExpireUnreachable", "never")


print("vault-mirror: %s -> %s every %ds" % (URL.split("@")[-1], DIR, POLL), flush=True)
kept = False
while True:
    try:
        head, changed = m.update()
        if not head:
            raise RuntimeError("no commit (clone or fetch failed; see above)")
        if changed or not kept:
            keep_objects()
            kept = True
        if changed:
            print("vault-mirror: at %s" % head[:10], flush=True)
        write_status(head=head, synced_at=int(time.time()), error=None)
    except Exception as err:                       # keep serving the last good copy
        print("vault-mirror: %s" % err, flush=True)
        try:
            with open(STATUS) as f:
                last = json.load(f)
        except (OSError, ValueError):
            last = {}
        write_status(head=last.get("head"), synced_at=last.get("synced_at"), error=str(err))
    time.sleep(POLL)
