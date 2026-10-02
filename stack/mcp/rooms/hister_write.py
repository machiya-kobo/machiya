"""Page labels and collections (Hister). Reads: pages_labels, collections_audit. Writes: pages_set_label, pages_relabel (a
dry run that mints a token, then an apply), collections_set, collections_remove (same two steps).

The rules: labels are flat, lowercase topics, one per page; vault notes and the reserved (import) labels are never touched; a
NEW label is the owner's; a collection is an `@name` alias whose value is purely `label:a` or `label:(a|b)` and every other
alias is the owner's; at most 200 pages per apply, after a dry run, with a rollback file (a JSON map of url -> old label)
first; no writes inside MCP_HISTER_BACKUP_WINDOW, when set (Hister is stopped for its backup). Every query ends with the
notes exclusion, whatever the caller wrote."""
import collections
import hashlib
import json
import os
import re
import time

from . import IDEMPOTENT, READ, WRITE, ToolError, arg_int, arg_list, arg_str, n, s, tool
from .hister import EXCLUDE, WORD, is_page

EXCL = EXCLUDE + " -label:konbini"
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")
EDITABLE = re.compile(r"^label:(?:\(([A-Za-z0-9_|-]+)\)|([A-Za-z0-9_-]+))$")
CENSUS_TTL = 300
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
WRITE_LIMITS = ("hister_write",)


# -- helpers ---------------------------------------------------------------------------------------------------------

def all_docs(ctx, query, cap=8000):
    """Every page matching `query` (Hister pages 100 at a time with page_key; the last page still carries a key)."""
    docs, key, fetched = [], None, 0
    for _ in range(90):
        params = {"q": query, "format": "json", "limit": 100}
        if key:
            params["page_key"] = key
        d = ctx.get("hister", "/search", params)
        batch = d.get("documents") or []
        fetched += len(batch)
        docs += [x for x in batch if is_page(x) and x.get("label") != "konbini"]
        key, total = d.get("page_key"), d.get("total")
        if not batch or not key or len(docs) >= cap or (total is not None and fetched >= total):
            break
    return docs


def census(ctx):
    """{label: count} over every page (never a note); cached five minutes, dropped after any write."""
    stamp, data = ctx.server.census
    if data is not None and ctx.server.clock() - stamp < CENSUS_TTL:
        return data
    counts = collections.Counter((d.get("label") or "") for d in all_docs(ctx, "*" + EXCL))
    data = dict(counts)
    ctx.server.census = (ctx.server.clock(), data)
    return data


def forget(ctx):
    ctx.server.census = (0.0, None)


def reserved(ctx, label):
    return label.lower() in ctx.server.config.reserved_labels


def topics(ctx):
    """The labels that are topics: existing, non-empty, not reserved."""
    return {l: c for l, c in census(ctx).items() if l and not reserved(ctx, l)}


def check_label(ctx, label, allow_empty=True):
    label = (label or "").strip()
    if not label:
        if allow_empty:
            return ""
        raise ToolError("a label is required")
    if not LABEL.match(label):
        raise ToolError("a label is one flat word like python (letters, digits, - and _)")
    if reserved(ctx, label):
        raise ToolError("%r marks the vault notes or an import, not a topic: never applied here" % label)
    if label not in topics(ctx):
        raise ToolError("%r is not an existing label: a new label is the owner's to create. Existing: %s"
                        % (label, ", ".join(sorted(topics(ctx))[:40])), needs_owner=True)
    return label


def guard_write(ctx):
    if ctx.server.in_backup_window():
        raise ToolError("Hister's backup window (%s, local time) stops it for a short while: writes are paused until it ends. "
                        "Retry after." % ctx.server.config.backup_window_text, code="backup_window")


def url_query(url):
    return 'url:"%s"' % url.replace("\\", "\\\\").replace('"', '\\"')


def aliases(ctx):
    return ctx.get("hister", "/api/rules").get("aliases") or {}


def alias_labels(value):
    m = EDITABLE.match((value or "").strip())
    return sorted(set((m.group(1) or m.group(2)).split("|"))) if m else None


def editable(ctx, name, value):
    """A collection the AI may edit: an @ alias, not notes/pages, whose value is purely labels, none of them reserved."""
    labs = alias_labels(value)
    return (name.startswith("@") and name[1:] not in ("notes", "pages") and labs is not None
            and not any(reserved(ctx, l) for l in labs))


def collection_name(ctx, name):
    name = (name or "").strip().lower().lstrip("@")
    if not WORD.match(name):
        raise ToolError("a collection name is a word like travel (letters, digits, - and _)")
    if name in ("notes", "pages") or name in ctx.server.config.reserved_collections:
        names = ["notes", "pages"] + sorted(ctx.server.config.reserved_collections)
        raise ToolError("%s are reserved: they are the owner's, not collections" % (", ".join(names[:-1]) + " and " + names[-1]))
    return "@" + name


def log_rollback(ctx, kind, data):
    d = ctx.server.config.rollback_dir
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s-%s.json" % (kind, time.strftime("%Y%m%d-%H%M%S", time.gmtime(ctx.server.clock()))))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=0)
    return path


# -- reads -------------------------------------------------------------------------------------------------------------

def pages_labels(ctx, args):
    counts = census(ctx)
    return {"labels": sorted(({"label": l, "count": c} for l, c in counts.items() if l and not reserved(ctx, l)),
                             key=lambda x: (-x["count"], x["label"])),
            "unlabelled": counts.get("", 0), "other": {l: c for l, c in counts.items() if l and reserved(ctx, l)},
            "pages": sum(counts.values())}


def collections_audit(ctx, args):
    counts, al = topics(ctx), aliases(ctx)
    mine = {k: alias_labels(v) for k, v in al.items() if editable(ctx, k, v)}
    in_a_collection = {l for labs in mine.values() for l in labs}
    union = {r: alias_labels(al[r]) for r in sorted(ctx.server.config.reserved_collections) if r in al and alias_labels(al[r]) is not None}
    out = {"collections": {k: {"labels": labs, "pages": sum(counts.get(l, 0) for l in labs)} for k, labs in sorted(mine.items())},
           "labels_in_no_collection": sorted(l for l in counts if l not in in_a_collection),
           "dead_labels_in_collections": {k: [l for l in labs if counts.get(l, 0) == 0] for k, labs in sorted(mine.items())
                                          if any(counts.get(l, 0) == 0 for l in labs)},
           "owners_own": sorted(k for k in al if k not in mine and k.lstrip("@") not in ("notes", "pages")),
           # label-only aliases that name a reserved label (the vault, konbini, an import): not topics, so the alias is left to
           # the owner, but it is worth their look
           "aliases_naming_reserved_labels": {k: sorted(l for l in alias_labels(v) if reserved(ctx, l)) for k, v in sorted(al.items())
                                              if k.startswith("@") and k[1:] not in ("notes", "pages") and alias_labels(v)
                                              and any(reserved(ctx, l) for l in alias_labels(v))}}
    for r, listed in union.items():      # a reserved alias that lists labels: how far it has drifted from the label list
        out["%s_drift" % r] = {"labels_missing_from_%s" % r: sorted(l for l in counts if l not in listed),
                               "labels_in_%s_with_no_pages" % r: sorted(l for l in listed if counts.get(l, 0) == 0 and not reserved(ctx, l)),
                               "note": "%s is the owner's own keyword: reported, never edited here" % r}
    return out


# -- single-page label -------------------------------------------------------------------------------------------------

def page_doc(ctx, url):
    from urllib.parse import urlsplit
    from .hister import ROOM_HOSTS
    u = urlsplit(url)
    if u.scheme not in ("http", "https", "gemini", "gopher") or not u.hostname or ROOM_HOSTS.match(u.hostname):
        raise ToolError("url must be a saved page's URL (vault notes and cards are not pages)")
    d = ctx.get("hister", "/api/document", {"url": url})
    if not is_page(d) or d.get("label") == "konbini":
        raise ToolError("that document is a vault note or a card, not a page")
    return d


def pages_set_label(ctx, args):
    url = arg_str(args, "url", maxlen=1500)
    label = check_label(ctx, args.get("label", ""))
    doc = page_doc(ctx, url)
    old = doc.get("label") or ""
    expected = args.get("expected_label")
    if expected is not None and expected != old:
        raise ToolError("the page's label is now %r, not %r: read it again" % (old, expected), code="label_changed", current=old)
    if reserved(ctx, old):
        raise ToolError("this page's label %r marks an import: it isn't changed here" % old)
    if old == label:
        return {"url": url, "label": label, "changed": False}
    guard_write(ctx)
    ctx.write("hister", "POST", "/api/label", {"url": url, "label": label})
    forget(ctx)
    return {"url": url, "old_label": old, "label": label, "changed": True}


# -- bulk relabel ------------------------------------------------------------------------------------------------------

def pages_relabel(ctx, args):
    token = arg_str(args, "apply_token", maxlen=60)
    cap = ctx.server.config.relabel_max
    if token:
        plan = ctx.server.redeem("relabel", ctx.login, token)
        if plan is None:
            raise ToolError("that apply_token is unknown, used, expired (10 minutes) or not yours: run the dry run again", code="bad_token")
        label, query = plan["label"], plan["query"]
    else:
        label = check_label(ctx, args.get("label", ""))
        query = arg_str(args, "query", maxlen=300)
        if not query:
            raise ToolError("query is required (Hister query language, e.g. label:tech domain:example.com)")
    docs = all_docs(ctx, query + EXCL, cap + 1)
    todo = [d for d in docs if (d.get("label") or "") != label and not reserved(ctx, d.get("label") or "")]
    if len(docs) > cap:
        raise ToolError("the query matches more than %d pages: narrow it (a domain, a label, a date) and run the dry run again" % cap)
    digest = hashlib.sha256("\n".join(sorted(d["url"] for d in todo) + [label]).encode()).hexdigest()
    if not token:
        if not todo:
            return {"dry_run": True, "matched": len(docs), "will_change": 0, "note": "nothing to change"}
        return {"dry_run": True, "matched": len(docs), "will_change": len(todo), "already_labelled": len(docs) - len(todo),
                "label": label or "(cleared)", "from": dict(collections.Counter((d.get("label") or "(none)") for d in todo)),
                "sample": [{"url": d["url"], "title": d.get("title"), "label": d.get("label") or None} for d in todo[:10]],
                "apply_token": ctx.server.mint("relabel", ctx.login, {"label": label, "query": query, "digest": digest}),
                "expires_in_seconds": 600,
                "next": "Show the owner this, and only after they agree call again with the same arguments' apply_token."}
    if digest != plan["digest"]:
        raise ToolError("the matching pages changed since the dry run: run it again", code="stale_plan")
    guard_write(ctx)
    if not ctx.server.allow(("bulk_hour",), ctx.login):
        raise ToolError("at most %d bulk applies an hour" % ctx.server.config.bulk_per_hour, code="rate_limited")
    rollback = log_rollback(ctx, "relabel", {d["url"]: (d.get("label") or "") for d in todo})
    problems, done = [], 0
    for d in todo:
        r = ctx.write("hister", "POST", "/api/update", {"query": url_query(d["url"]), "changes": {"label": label}})
        if isinstance(r, dict) and r.get("matched") == 1:
            done += 1
        else:
            problems.append({"url": d["url"], "answer": r})
    forget(ctx)
    return {"applied": done, "problems": problems[:20], "rollback_file": rollback,
            "rollback": "restore from this rollback file (url -> old label) with a relabel script or the Hister API"}


# -- collections -------------------------------------------------------------------------------------------------------

def collections_set(ctx, args):
    name = collection_name(ctx, args.get("name"))
    labels = sorted(set(l.strip() for l in arg_list(args, "labels", 20, 41)))
    if not labels:
        raise ToolError("labels is a list of one or more existing labels")
    for l in labels:
        check_label(ctx, l, allow_empty=False)
    al = aliases(ctx)
    before = al.get(name)
    if before is not None and not editable(ctx, name, before):
        raise ToolError("%s is the owner's own query, not a label collection: it isn't edited here" % name)
    value = "label:%s" % labels[0] if len(labels) == 1 else "label:(%s)" % "|".join(labels)
    if before == value:
        return {"name": name, "value": value, "changed": False}
    guard_write(ctx)
    ctx.write("hister", "POST", "/api/add_alias", {"alias-keyword": name, "alias-value": value}, form=True)
    log_rollback(ctx, "alias-set", {name: before})
    return {"name": name, "before": before, "after": value, "changed": True,
            "pages": sum(topics(ctx).get(l, 0) for l in labels)}


def collections_remove(ctx, args):
    name = collection_name(ctx, args.get("name"))
    token = arg_str(args, "apply_token", maxlen=60)
    al = aliases(ctx)
    if name not in al:
        raise ToolError("there is no collection %s" % name)
    if not editable(ctx, name, al[name]):
        raise ToolError("%s is the owner's own query, not a label collection: it isn't removed here" % name)
    if not token:
        return {"dry_run": True, "name": name, "value": al[name], "pages": sum(topics(ctx).get(l, 0) for l in alias_labels(al[name])),
                "apply_token": ctx.server.mint("alias-remove", ctx.login, {"name": name, "value": al[name]}),
                "expires_in_seconds": 600, "note": "Removing a collection does not touch any page or label. Ask the owner, then call again with apply_token."}
    plan = ctx.server.redeem("alias-remove", ctx.login, token)
    if plan is None or plan["name"] != name:
        raise ToolError("that apply_token is unknown, used, expired or for another collection: run the dry run again", code="bad_token")
    if plan["value"] != al[name]:
        raise ToolError("the collection changed since the dry run: run it again", code="stale_plan")
    guard_write(ctx)
    rollback = log_rollback(ctx, "alias-remove", {name: al[name]})
    ctx.write("hister", "POST", "/api/delete_alias", {"alias": name}, form=True)
    return {"removed": name, "value": al[name], "rollback_file": rollback,
            "restore": "collections_set with the same labels"}


TOOLS = [
    tool("pages_labels", "Every page label with its page count (vault notes and import labels are not topics and are listed apart), "
         "and how many pages have no label. Cached for five minutes.", handler=pages_labels),
    tool("collections_audit", "Check the owner's collections against the labels: labels in no collection, labels in a collection with no "
         "pages, and how far the owner's reserved aliases (MCP_RESERVED_COLLECTIONS) have drifted (reported, never edited).", handler=collections_audit),
    tool("pages_set_label", "Give one saved page an existing label (empty string clears it). A new label is the owner's; vault notes and "
         "import labels are never touched. Pass expected_label if you read the page's label first.",
         {"url": s("The page's URL, from pages_search"), "label": s("An existing label, or empty to clear"),
          "expected_label": s("The label you saw; the call fails if it changed")}, ["url", "label"], IDEMPOTENT, pages_set_label, WRITE_LIMITS),
    tool("pages_relabel", "Relabel many pages at once, in two steps. Step 1 (no apply_token): a dry run that matches the query (Hister query "
         "language; notes are always excluded), shows how many pages would change, a sample and their current labels, and returns an "
         "apply_token. Step 2: after the owner agrees, call again with the apply_token. At most 200 pages; the old labels are saved to a "
         "rollback file first. The label must already exist.",
         {"query": s("Which pages, e.g. label:tech domain:example.com (dry run only)"), "label": s("The existing label to set, or empty to clear (dry run only)"),
          "apply_token": s("From the dry run, to apply it")}, [], DESTRUCTIVE, pages_relabel, WRITE_LIMITS),
    tool("collections_set", "Create or change a collection: an @name alias for one or more existing labels. Only label collections are "
         "edited; the owner's other aliases, notes and pages are never touched.",
         {"name": s("Collection name, with or without @, e.g. travel"),
          "labels": {"type": "array", "items": {"type": "string"}, "description": "Existing labels"}}, ["name", "labels"], IDEMPOTENT,
         collections_set, WRITE_LIMITS),
    tool("collections_remove", "Remove a label collection, in two steps: without apply_token it shows the collection and returns a token; "
         "after the owner agrees, call again with the token. No page or label changes; the old definition is saved.",
         {"name": s("Collection name"), "apply_token": s("From the first step, to apply")}, ["name"], DESTRUCTIVE, collections_remove, WRITE_LIMITS),
]
for _t in TOOLS[:2]:
    _t["annotations"] = READ
