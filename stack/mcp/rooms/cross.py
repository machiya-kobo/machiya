"""Cross-room tools: one search over notes and cards, and which rooms are on. Pages are searched with pages_search."""
import concurrent.futures
import time

from . import arg_int, arg_str, n, s, tool, ToolError
from . import konbini, kura

STATUS_PATHS = {"konbini": "/api/status", "kura": "/api/status", "niwa": "/api/status", "hister": "/api/stats"}


def machiya_search(ctx, args):
    q = arg_str(args, "q", maxlen=300)
    if not q:
        raise ToolError("q is required")
    limit = arg_int(args, "limit", 8, 1, 25)
    jobs = {}
    if ctx.has("kura"):
        jobs["notes"] = lambda: kura.notes_search(ctx, {"q": q, "limit": limit})
    if ctx.has("konbini"):
        jobs["cards"] = lambda: konbini.board_list_cards(ctx, {"text": q, "limit": limit})
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {k: pool.submit(f) for k, f in jobs.items()}
        for k, f in futures.items():
            try:
                out[k] = f.result()
            except (ToolError, Exception) as e:   # one room down must not hide the others
                out[k] = {"error": getattr(e, "message", str(e))}
    return out


def machiya_status(ctx, args):
    rooms = {}
    for room in ctx.rooms():
        t0 = time.time()
        try:
            d = ctx.get(room, STATUS_PATHS[room])
            rooms[room] = {"ok": True, "ms": int((time.time() - t0) * 1000), "head": d.get("head"), "version": d.get("version")}
        except Exception as e:
            rooms[room] = {"ok": False, "error": getattr(e, "message", str(e))}
    return {"rooms": rooms}


TOOLS = [
    tool("machiya_search", "Search notes and board cards at once, grouped. Use notes_search or board_list_cards for more of "
         "one kind, and pages_search for saved pages.", {"q": s("Query"), "limit": n("Results per kind (default 8)")}, ["q"],
         handler=machiya_search),
    tool("machiya_status", "Which Machiya rooms this server can reach right now, with their latest commit.",
         handler=machiya_status),
]
