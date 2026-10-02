# Machiya plugin for Claude Code

The MCP server's connection (`.mcp.json`) and the skills that work across rooms. Each skill uses the `mcp__machiya__*` tools when they are connected and falls back to `pm` and the rooms' own APIs when they aren't, so a skill works before the server is reachable and on a machine without it.

| Skill | For |
|---|---|
| `backlog-add` | capturing an idea as a backlog card, without duplicates (existing areas and topics only) |
| `weekly-review` | the weekly board review, one proposed action per card, approval before any write (user-invoked only) |
| `recall` | finding what you have written or read across notes, saved pages and cards |
| `garden-suggest` | suggesting notes for the garden (suggest only; the owner publishes) |
| `tidy-collections` | tidying saved-page labels and collections: audit, propose, dry run, apply with a token (a new label is the owner's) |

The room-specific how-to lives with each room (`pm` is the board's CLI, in the Konbini repo); these skills cover the work that spans rooms. The server: [docs/services/mcp.md](../../docs/services/mcp.md).

## Hister's own MCP (optional, alongside this one)

Hister ships its own MCP endpoint (tested with v0.20.0: `search`, `get_preview`, `get_history`; read-only, no token, owner-only behind your Tailscale Service). `plugins/machiya/install.sh hister` enables it at user scope (`claude mcp add --transport http --scope user hister …`) and adds permission rules: `search` and `get_preview` allowed, **`get_history` denied**, which removes the tool from the model's view (your visit and opened-results history stays out of AI context). `hister-remove` undoes it. It complements machiya-mcp: Hister's `search` returns vault notes mixed with pages unless the query uses `@pages` (or `@notes`), while `pages_search` always excludes notes and `notes_search` comes from Kura.

## Install

One command per machine (idempotent, safe to re-run after an update): `plugins/machiya/install.sh` adds this checkout as a marketplace, installs the plugin at user scope, sets `MACHIYA_AGENT` (default `user@host`; override with `MACHIYA_AGENT_NAME=…`), adds the permission rules to `~/.claude/settings.json` (a backup is kept), and checks the server. `install.sh check` tests the connection; `install.sh uninstall` reverses it. All Claude Code sessions for the same user share one `~/.claude`, so one run covers them all; they pick it up when they restart. By hand:

```
claude plugin marketplace add <this repo: a clone's path, or its git URL>     # once per machine
claude plugin install machiya@machiya
```

Set `MACHIYA_MCP_URL` if the server isn't at `https://machiya-mcp.example.ts.net/mcp`, and `MACHIYA_AGENT` (for example `claude@laptop`) so the board's history says who acted. The client has to be on the tailnet. Suggested permissions (reads allowed, most writes ask): see the service doc, which names the tools `mcp__machiya__<tool>` (the server added with `claude mcp add machiya …`). Through this plugin the same tools are `mcp__plugin_machiya_machiya__<tool>`: use that prefix in the permission rules. Use one or the other on a machine, not both.
