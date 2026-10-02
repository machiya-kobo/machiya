# machiya-mcp

Machiya's MCP server: read tools for the board, notes and saved pages, board and garden writes, notes write tools, page labels and collections, prompts. Full description, tools, safety rules and configuration: [docs/services/mcp.md](../../docs/services/mcp.md).

```
MCP_AUTH=open MCP_BIND=127.0.0.1 MCP_LOG=/tmp/mcp.log \
  KURA_URL=https://kura.example KONBINI_URL=https://konbini.example HISTER_URL=http://hister:4433 python3 mcp.py
python3 -m unittest discover -s tests      # from this directory; needs markdown and pyyaml (vaultkit's dependencies)
podman build -t machiya-mcp:dev .          # or docker build; run it with --init
```

Settings that describe a particular vault or routine are optional and neutral by default (lists are comma-separated; a path rule is a vault-relative prefix such as `Dir/` or `Dir/File.md`, case-insensitive, or a `*` glob over the whole path):

| Setting | Default | Meaning |
|---|---|---|
| `MCP_TZ` | `UTC` | time zone of the notes' dates and of the backup window (`MCP_NOTES_TZ` overrides the notes' only) |
| `MCP_HISTER_BACKUP_WINDOW` | none | a weekly slot, `Sat 03:10-03:40` (local time, within one day), when Hister is stopped for its backup: Hister writes pause |
| `MCP_NOTES_NEVER_WRITE` | `Templates/,Archive/` | paths the notes write tools never create or change |
| `MCP_NOTES_NEVER_CREATE` | none | paths where a new note may not be made (existing notes there can still be updated) |
| `MCP_NOTES_DEFAULT_FOLDER` | `Inbox/` | the folder `notes_create` uses when given a title only |
| `MCP_NOTES_REFUSED_TAGS` | none | tags never set from here: exact, or `prefix*` |
| `MCP_NOTES_REQUIRED_TAGS` | none | tag prefixes (`kind/`) a note must have at least one tag of |
| `MCP_NOTES_CREATE_TAGS` | none | on a new note, tags in the namespaces named here must be one of these |
| `MCP_NOTES_CARDS` | none | folders (`Dir/`) and tags that mark a note as a board card (its status and tags are changed with the board tools) |
| `MCP_NOTES_STATUS_LOCKED` | none | folders (`Dir/`) and tags whose notes' `status` another tool manages |
| `MCP_NOTES_MANAGED_MARKERS` | none | names of `<!-- name:start -->` / `<!-- name:end -->` blocks another tool rewrites: never edited |
| `MCP_GARDEN_SKIP` | `Templates/,Archive/` | folders `garden_candidates` leaves out |
| `MCP_RESERVED_LABELS` | none | page labels never applied or changed, besides `vault` and `konbini` |
| `MCP_RESERVED_COLLECTIONS` | none | collection (alias) names never created, changed or removed, besides `notes` and `pages`; an alias of labels among them is checked for drift in `collections_audit` |

Layout: `mcp.py` (HTTP, JSON-RPC, gate, limits, audit), `backend.py` (the only place that talks to a room), `envelope.py`
(untrusted wrapper, paging), `rooms/` (one module per room, a list of tools each; `prompts.py` the slash commands), `tests/` (fake rooms on local ports).
