"""Garden tools: suggest a note for the garden (Niwa's own POST /api/suggest) and find candidates. There is no publish
tool and never will be: only the owner publishes, in Niwa's queue."""
from . import IDEMPOTENT, WRITE, ToolError, arg_int, arg_str, n, s, tool
from . import kura


def garden_suggest(ctx, args):
    note = arg_str(args, "note", maxlen=400)
    reason = arg_str(args, "reason", maxlen=600)
    if not note or not reason:
        raise ToolError("note and reason are required: say why this note belongs in the garden")
    if len(reason) > 300 or "\n\n" in reason:
        raise ToolError("reason is a short paragraph of at most 300 characters")
    if "/" not in note and not note.endswith(".md"):       # a card slug: its note is the card's path
        if not ctx.has("konbini"):
            raise ToolError("give the note's vault path (Projects/Foo.md); card slugs need the board")
        note = ctx.get("konbini", "/api/cards/" + note)["path"]
    path = kura.note_path({"path": note}, ctx)
    if ctx.has("kura"):                                     # only a default-vault note that exists
        d = ctx.get("kura", "/api/note", {"path": path})
        if not kura.default_only(ctx, [d]):
            raise ToolError("that note is not in the default vault")
    try:                                  # newer Niwa: a note with an open suggestion isn't suggested again
        open_now = {x["path"]: x for x in ctx.get("niwa", "/api/suggestions").get("suggestions", []) if isinstance(x, dict)}
    except ToolError:
        open_now = {}                     # an older Niwa, or it can't say: the daily cap is the brake
    if path in open_now:
        return {"suggested": False, "path": path, "already_open": open_now[path],
                "note": "This note already has an open suggestion in Niwa's queue; the owner decides there."}
    out = ctx.write("niwa", "POST", "/api/suggest", {"path": path, "reason": reason})
    return {"suggested": True, "path": path, "result": out,
            "note": "The owner decides in Niwa's queue; nothing is published."}


def garden_candidates(ctx, args):
    folder = arg_str(args, "folder", maxlen=120).strip("/")
    skip = ctx.server.config.garden_skip           # MCP_GARDEN_SKIP: folders that never go in the garden
    limit = arg_int(args, "limit", 15, 1, 50)
    out, seen, offset = [], 0, 0
    while len(out) < limit and offset < 400:
        d = ctx.get("kura", "/api/recent", {"limit": 100, "offset": offset})
        rows = kura.default_only(ctx, d.get("results", []))
        seen += len(d.get("results", []))
        for note in rows:
            path = note.get("path", "")
            if note.get("published") or path.startswith(skip) or (folder and not path.startswith(folder + "/")):
                continue
            out.append(kura.compact(note))
        if not d.get("results") or seen >= (d.get("total") or 0):
            break
        offset += 100
    return {"candidates": out[:limit], "skipped_folders": list(skip),
            "hint": "Read a note with notes_read before suggesting it; say why it belongs. Only the owner publishes."}


TOOLS = [
    tool("garden_suggest",
         "Suggest a default-vault note for the owner's garden, with a short reason. It lands in Niwa's queue; the owner decides. "
         "There is no way to publish from here. At most 10 suggestions a day. A note already in the garden, or with an open suggestion, is reported, not repeated.",
         {"note": s("Vault path (Projects/Foo.md) or a card slug"), "reason": s("Why it belongs in the garden, up to 300 characters")},
         ["note", "reason"], WRITE, garden_suggest, ("garden_day",)),
    tool("garden_candidates",
         "Recently changed default-vault notes that are not in the garden yet (the server's skipped folders are left out and listed): "
         "a starting list to read before suggesting.",
         {"folder": s("Only this folder"), "limit": n("Results (default 15, max 50)")},
         annotations=None, handler=garden_candidates, requires=("kura", "niwa")),
]
