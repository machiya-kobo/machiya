"""Room modules: one per backend, each a list of tools. A tool is a dict the server turns into an MCP tool:
name, description, inputSchema, annotations and a handler(ctx, args) returning plain data (the server wraps it).
A room's tools are listed only when its URL is configured."""
import re

READ = {"readOnlyHint": True, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
IDEMPOTENT = dict(WRITE, idempotentHint=True)
BOARD_WRITE = ("board_write", "board_write_day")

_SLUG = re.compile(r"^[\w][\w.\- ]{0,120}$")


class ToolError(Exception):
    """A refusal the model should see as a tool error (bad argument, refused target, backend said no)."""

    def __init__(self, message, **extra):
        super().__init__(message)
        self.message, self.extra = message, extra


def tool(name, description, properties=None, required=(), annotations=None, handler=None, limits=("read",), requires=None):
    """`limits` names the rate buckets a call takes; `requires` the rooms that must be configured (default: its module's)."""
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required),
                            "additionalProperties": False},
            "annotations": annotations or READ, "handler": handler, "limits": tuple(limits), "requires": requires}


def arg_list(args, key, maxitems=20, maxlen=80):
    v = args.get(key) or []
    if not isinstance(v, list) or len(v) > maxitems or not all(isinstance(x, str) for x in v):
        raise ToolError("%s must be a list of at most %d strings" % (key, maxitems))
    return [x.strip()[:maxlen] for x in v if x.strip()]


def s(description, **kw):
    return {"type": "string", "description": description, **kw}


def n(description, **kw):
    return {"type": "integer", "description": description, **kw}


def arg_str(args, key, default="", maxlen=500):
    v = args.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        raise ToolError("%s must be a string" % key)
    return v.strip()[:maxlen]


def arg_int(args, key, default, lo=0, hi=100):
    v = args.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v)
        else:
            raise ToolError("%s must be an integer" % key)
    return max(lo, min(hi, v))


def arg_slug(args, key):
    v = arg_str(args, key)
    if not _SLUG.match(v) or v.startswith(".") or "/" in v:
        raise ToolError("%s must be a card slug" % key)
    return v
