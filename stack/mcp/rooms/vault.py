"""Note write tools (notes_create, notes_update): the default vault only, through notes_writer.NotesWriter, which holds the
rules (and the configuration that shapes them). Listed only when a write clone is configured."""
import re

from . import ToolError, arg_list, arg_str, s, tool

WRITE_NOTE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
UPDATE_NOTE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
LIMITS = ("notes_write", "notes_write_day")
BAD_NAME = re.compile(r"[^A-Za-z0-9 _.()&',!-]+")


def file_name(title):
    name = re.sub(r"\s+", " ", BAD_NAME.sub(" ", title)).strip(" .-")[:100].strip()
    if not name:
        raise ToolError("the title has no characters usable in a file name: give a path")
    return name


def notes_create(ctx, args):
    title = arg_str(args, "title", maxlen=300)
    path = arg_str(args, "path", maxlen=300)
    if not path:
        folder = arg_str(args, "folder", "", 120).strip("/") or ctx.server.notes.rules.default_folder
        path = "%s/%s.md" % (folder, file_name(title))
    status = arg_str(args, "status", "draft", 10)
    body = args.get("body", "")
    if not isinstance(body, str):
        raise ToolError("body must be a string")
    out = ctx.server.notes.create(path, title, body, arg_list(args, "tags", 12, 60), arg_str(args, "summary", "", 600), status, ctx.agent)
    kura = ctx.server.config.public.get("kura")
    if kura:
        out["url"] = "%s/n/%s" % (kura, out["path"][:-3].replace(" ", "%20"))
    return out


def notes_update(ctx, args):
    text = args.get("text", "")
    if not isinstance(text, str):
        raise ToolError("text must be a string")
    summary = args.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ToolError("summary must be a string")
    return ctx.server.notes.update(
        arg_str(args, "path", maxlen=300), arg_str(args, "expected_version", maxlen=20), arg_str(args, "mode", maxlen=20), ctx.agent,
        text=text, heading=arg_str(args, "heading", maxlen=200), create_if_missing=args.get("create_if_missing") is True,
        join=arg_str(args, "join", "paragraph", 10), confirm=args.get("confirm") is True,
        tags_add=arg_list(args, "tags_add", 10, 60), tags_remove=arg_list(args, "tags_remove", 10, 60), summary=summary,
        status=arg_str(args, "status", "", 10) or None)


TOOLS = [
    tool("notes_create",
         "Create a note in the owner's default vault, with generated frontmatter: title, today's date, tags, status draft, a "
         "one-line summary. Tags must already exist (a new tag is the owner's). Not for board cards (board_add_backlog) or folders "
         "another tool manages (a refusal says so). Fails if a note with that name exists anywhere. Never put secrets in a note: "
         "refer to the secret by name.",
         {"title": s("The note's title"), "body": s("Markdown body (an H1 with the title is added if missing)"),
          "tags": {"type": "array", "items": {"type": "string"}, "description": "Existing tags, kind/name, e.g. topic/python"},
          "summary": s("One line, up to 200 characters (empty allowed)"), "folder": s("Folder (default: the server's default folder)"),
          "path": s("Or the full path, Folder/Name.md"), "status": s("draft (default) or active", enum=["draft", "active"])},
         ["title", "body", "tags"], WRITE_NOTE, notes_create, LIMITS, ("notes_write",)),
    tool("notes_update",
         "Change an existing note in the default vault. Give the `version` from notes_read as expected_version: if the note changed since, "
         "the call fails and you read it again. Modes: append (text at the end; join=line to continue a list or table), replace_section "
         "(heading + text), replace_body (whole body; needs confirm=true), frontmatter (tags_add, tags_remove, summary, status). A published "
         "note needs confirm=true after the owner agrees. Cards use the board tools, not this.",
         {"path": s("Vault path, Folder/Name.md"), "expected_version": s("The version from notes_read"),
          "mode": s("append, replace_section, replace_body or frontmatter", enum=["append", "replace_section", "replace_body", "frontmatter"]),
          "text": s("The text to add or put in place"), "heading": s("For replace_section: the heading's text"),
          "create_if_missing": {"type": "boolean", "description": "replace_section: add the heading at the end if it is missing"},
          "join": s("append: paragraph (blank line before, default) or line (next line)", enum=["paragraph", "line"]),
          "confirm": {"type": "boolean", "description": "Required for replace_body and for a published note, after the owner agrees"},
          "tags_add": {"type": "array", "items": {"type": "string"}}, "tags_remove": {"type": "array", "items": {"type": "string"}},
          "summary": s("frontmatter: a new one-line summary"), "status": s("frontmatter: draft, active or archive (not for cards)")},
         ["path", "expected_version", "mode"], UPDATE_NOTE, notes_update, LIMITS, ("notes_write",)),
]
