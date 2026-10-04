"""Hister reads: collections_list, and the page rules the label and collection tools (hister_write) share. Page search and
page text come from Hister's own MCP (`search` with `@pages`, `get_preview`) since 0.7.0, not from here. Pages only: every
query this server sends carries ` -label:vault -metadata.source:vault -metadata.source:code`, and a document on a room's
own host, a note or a code document (code-import's, docs/contracts/hister.md) is never a page."""
import re
from urllib.parse import urlsplit

from . import tool

ROOM_HOSTS = re.compile(r"^(kura|konbini|niwa)\.")
EXCLUDE = " -label:vault -metadata.source:vault -metadata.source:code"
BUILTIN = ("notes", "pages", "code")        # Hister's own aliases (@notes, @pages, @code): never collections
WORD = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def is_page(doc):
    meta = doc.get("metadata") or {}
    host = urlsplit(doc.get("url") or "").hostname or ""
    return (doc.get("label") != "vault" and meta.get("source") not in ("vault", "code")
            and not ROOM_HOSTS.match(host))


def collections_list(ctx, args):
    aliases = ctx.get("hister", "/api/rules").get("aliases") or {}
    out = {}
    for name, expansion in sorted(aliases.items()):
        bare = name.lstrip("@")
        if bare in BUILTIN or "label:vault" in expansion or "metadata.source:vault" in expansion \
                or "metadata.source:code" in expansion:
            continue
        out[name] = expansion
    return {"collections": out}


TOOLS = [
    tool("collections_list", "The owner's page collections: alias name and the query it expands to (use one in Hister's search "
         "as @name, after @pages).", handler=collections_list),
]
