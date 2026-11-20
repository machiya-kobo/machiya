"""Hister is now Nogon (owner, 2026-11-19: rename everything Machiya calls Hister): the names Machiya reads in both
spellings while the rename is under way (docs/contracts/hister.md "Names").

Stage 1 (vaultkit 0.38): readers accept the new names next to the old ones, and nothing that is written changes:
  - setting(): a setting spelled with NOGON (KURA_NOGON_URL, NOGON_LOGIN_DB, …), else its HISTER twin;
  - room_key(): the room id `nogon` (MACHIYA_ROOMS, apps_hidden) is read as `hister`, the id the house still keeps;
  - auth_mode(): `<P>_AUTH=nogon` is read as `hister`.
Stage 3 flips the house's own id to `nogon` and reads `hister` as the alias; then the old names go.
Upstream Nogon's own names (its `hister` session cookie, X-Access-Token, HISTER__* in its config) are not Machiya's
and are never touched here.
"""
import os
import sys

ROOM_ALIASES = {"nogon": "hister"}      # the new room id -> the id the house keeps until stage 3
AUTH_ALIASES = {"nogon": "hister"}      # <P>_AUTH=nogon -> the mode's current name
_warned = set()


def setting(name, env=None, default=None):
    """The value of the setting `name` (spelled with NOGON), else of its HISTER twin, else `default`. A NOGON setting
    that is present wins, even when it is empty, as upstream Nogon does with its own variables. Reading the old name
    logs one line per name, so a deployment sees what is left to rename."""
    env = os.environ if env is None else env
    if name in env:
        return env[name]
    old = name.replace("NOGON", "HISTER")
    if old != name and old in env:
        if old not in _warned:
            _warned.add(old)
            print("%s: an old name, read as %s (Hister is now Nogon; rename it before the old names go)" % (old, name),
                  file=sys.stderr, flush=True)
        return env[old]
    return default


def room_key(key):
    """A room id as the house keeps it: `nogon` -> `hister` (stage 1); anything else unchanged."""
    return ROOM_ALIASES.get(key, key)


def auth_mode(value):
    """A <P>_AUTH value as the house keeps it, lowercased and trimmed: `nogon` -> `hister` (stage 1)."""
    v = (value or "").strip().lower()
    return AUTH_ALIASES.get(v, v)
