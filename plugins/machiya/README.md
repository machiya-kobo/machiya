# Machiya plugin for Claude Code

Two MCP connections (`.mcp.json`): Machiya's own server (`machiya`: board, notes, page labels and collections, garden suggestions) and Hister's built-in MCP (`hister`: page search and preview). And the skills that work across rooms. Each skill uses the MCP tools when they are connected and falls back to `pm` and the rooms' own APIs when they aren't, so a skill works before the servers are reachable and on a machine without them.

| Skill | For |
|---|---|
| `backlog-add` | capturing an idea as a backlog card, without duplicates (existing areas and topics only) |
| `weekly-review` | the weekly board review, one proposed action per card, approval before any write (user-invoked only) |
| `recall` | finding what you have written or read across notes, saved pages and cards |
| `garden-suggest` | suggesting notes for the garden (suggest only; the owner publishes) |
| `tidy-collections` | tidying saved-page labels and collections: audit, propose, dry run, apply with a token (a new label is the owner's) |

The room-specific how-to lives with each room (`pm` is the board's CLI, in the Konbini repo); these skills cover the work that spans rooms. The server: [docs/services/mcp.md](../../docs/services/mcp.md).

## Hister's MCP (pages)

Hister ships its own MCP endpoint, `POST /mcp` (tested with v0.20.0): `search`, `get_preview` and `get_history`, all read-only. The plugin connects it as `hister` (tools `mcp__plugin_machiya_hister__<tool>`), and it is how the model reads saved pages: machiya-mcp has no page reads since 0.7.0, only the label and collection tools Hister's MCP lacks. Two rules go with it, in the skills and the [Hister contract](../../docs/contracts/hister.md#histers-mcp):

- **Page queries start with `@pages`**, the alias for everything except the vault notes (Hister's `search` returns notes mixed with pages otherwise); notes come from `notes_search` and `notes_read`.
- **`get_history` is denied** (visits and opened results stay out of AI context). `install.sh` writes the deny rule for every name the tool can have (`mcp__plugin_machiya_hister__get_history`, and `mcp__hister__get_history` for a server added by hand), which removes it from the model's view, and never takes it away.

`get_preview` returns a page's whole text and HTML with no paging: read pages sparingly. Hister needs no token while its tailnet grant is the gate; once Hister's user handling is on, the connection needs the owner's Hister token as an `Authorization: Bearer` header.

## Install

One command per machine (idempotent, safe to re-run after an update): `plugins/machiya/install.sh` adds this checkout as a marketplace, installs the plugin at user scope, sets `MACHIYA_AGENT` (default `user@host`; override with `MACHIYA_AGENT_NAME=…`) and, when they are set, `MACHIYA_MCP_URL` and `HISTER_MCP_URL` in the settings' env, adds the permission rules to `~/.claude/settings.json` (a backup is kept), moves the machine off the separate `hister` server that `install.sh hister` added before plugin 0.2.0, and checks both servers. `install.sh check` tests the connections; `install.sh uninstall` reverses it. All Claude Code sessions for the same user share one `~/.claude`, so one run covers them all; they pick it up when they restart. By hand:

```
claude plugin marketplace add <this repo: a clone's path, or its git URL>     # once per machine
claude plugin install machiya@machiya
```

Set `MACHIYA_MCP_URL` if the server isn't at `https://machiya-mcp.example.ts.net/mcp`, `HISTER_MCP_URL` if Hister's endpoint isn't at `https://hister.example.ts.net/mcp`, and `MACHIYA_AGENT` (for example `claude@laptop`) so the board's history says who acted. The client has to be on the tailnet. Suggested permissions (reads allowed, most writes ask): see the service doc, which names the tools `mcp__machiya__<tool>` (the server added with `claude mcp add machiya …`). Through this plugin the same tools are `mcp__plugin_machiya_machiya__<tool>`: use that prefix in the permission rules. Use one or the other on a machine, not both.
