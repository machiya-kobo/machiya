"""Hister is now Nogon (owner, 2026-11-19: rename everything Machiya calls Hister): Machiya's own names, and what is
left of the old ones (docs/contracts/nogon.md "Names").

Stage 3 (vaultkit 0.41): the house's names are Nogon's.
  - setting(): a setting spelled with NOGON (KURA_NOGON_URL, NOGON_LOGIN_DB, …). Its old HISTER twin is no longer
    read: set on its own, it stops the app (OldName, naming the new variable), so nothing is quietly ignored;
  - room_key(): the house's room id is `nogon`. A stored `hister` (an account's apps_hidden, a machiya_show_hister
    cookie, MACHIYA_ROOMS) is still read as `nogon`, since people's saved settings hold it;
  - auth_mode(): `<P>_AUTH=nogon`. `hister` stops the app (OldName: use AUTH=nogon).
Upstream Nogon's own names (its `hister` session cookie, X-Access-Token, HISTER__* in its config) are not Machiya's
and are never touched here.
"""
import os
import sys

ROOM_ALIASES = {"hister": "nogon"}      # a stored old room id -> the house's id (0.41: what people saved still reads)
OLD_AUTH = {"hister": "nogon"}          # <P>_AUTH values that are refused, with the one to use instead
_warned = set()


class OldName(ValueError):
    """A setting under its old Hister name, which Machiya no longer reads: the app must not start. The message names
    the new variable (or value)."""


def setting(name, env=None, default=None):
    """The value of the setting `name` (spelled with NOGON), else `default`. A present setting wins even when empty, as
    upstream Nogon does with its own variables. Its old HISTER twin is not read (0.41): set without the new name it
    raises OldName, naming the new one; set beside it, it is ignored and said once on stderr."""
    env = os.environ if env is None else env
    old = name.replace("NOGON", "HISTER")
    if name in env:
        if old != name and old in env and old not in _warned:
            _warned.add(old)
            print("%s: an old name, ignored (%s is set; Hister is now Nogon): remove it" % (old, name),
                  file=sys.stderr, flush=True)
        return env[name]
    if old != name and old in env:
        raise OldName("%s is an old name and is no longer read: rename it %s (Hister is now Nogon)" % (old, name))
    return default


def room_key(key):
    """A room id as the house keeps it: `hister` (a stored or old id) -> `nogon`; anything else unchanged."""
    return ROOM_ALIASES.get(key, key)


def auth_mode(value, name="AUTH"):
    """A <P>_AUTH value, lowercased and trimmed. `hister` raises OldName ("use AUTH=nogon"); `name` is the variable,
    for the message (KURA_AUTH)."""
    v = (value or "").strip().lower()
    if v in OLD_AUTH:
        raise OldName("%s=%s is an old name and is no longer read: use %s=%s (Hister is now Nogon)"
                      % (name, v, name, OLD_AUTH[v]))
    return v
