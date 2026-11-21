"""vaultkit.histerauth: the old name of vaultkit.nogonauth (Hister is now Nogon), kept for one release so a room that
hasn't moved yet still imports. It goes in vaultkit 0.42.0: import nogonauth, NogonAuth and nogon_headers instead.

Everything is nogonauth's own object (the same classes, functions and constants), under its new names and the old
ones (HisterAuth, hister_headers). What it does is nogonauth's, 0.41 included: AUTH=nogon, Origin: nogon://.
"""
from . import nogonauth as _nogonauth
from .nogonauth import *                                    # noqa: F401,F403 (every public name, new spelling)
from .nogonauth import NogonAuth as HisterAuth              # noqa: F401 (the old names)
from .nogonauth import nogon_headers as hister_headers      # noqa: F401


def __getattr__(name):
    """Anything else (the private helpers some rooms reach for, such as _http_fetch) is nogonauth's."""
    try:
        return getattr(_nogonauth, name)
    except AttributeError:
        raise AttributeError("module %r has no attribute %r" % (__name__, name)) from None
