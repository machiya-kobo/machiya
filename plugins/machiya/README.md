# Machiya plugin for Claude Code

One MCP connection (`.mcp.json`): Machiya's own server (`machiya`: board, notes, saved pages, page labels and collections, garden suggestions). And the skills that work across rooms. Each skill uses the MCP tools when they are connected and falls back to `pm` and the rooms' own APIs when they aren't, so a skill works before the servers are reachable and on a machine without them.

| Skill | For |
|---|---|
| `backlog-add` | capturing an idea as a backlog card, without duplicates (existing areas and topics only) |
| `weekly-review` | the weekly board review, one proposed action per card, approval before any write (user-invoked only) |
| `recall` | finding what you have written or read across notes, saved pages and cards |
| `garden-suggest` | suggesting notes for the garden (suggest only; the owner publishes) |
| `tidy-collections` | tidying saved-page labels and collections: audit, propose, dry run, apply with a token (a new label is the owner's) |

The room-specific how-to lives with each room (`pm` is the board's CLI, in the Konbini repo); these skills cover the work that spans rooms. The server: [docs/services/mcp.md](../../docs/services/mcp.md).

## Pages, and why Hister's own MCP is denied

Saved pages are searched and read with machiya-mcp's `pages_search` and `pages_read` (since plugin 0.3.0 and machiya-mcp 0.8.0). Hister ships its own MCP endpoint (`search`, `get_preview`, `get_history`), but its `search` and `get_preview` return vault notes and the owner's code documents (`metadata.source:code`) to any client, and "code and notes stay out of AI" is the owner's rule (2026-10-05): a rule in a prompt is not enough when a saved page can carry injected instructions. So the plugin no longer connects it, and `install.sh` **denies** every Hister MCP tool under every name it can have (`mcp__plugin_machiya_hister__*` from plugin 0.2.x, `mcp__hister__*` for a server added by hand), which removes them from the model's view; uninstall never takes the denies away. machiya-mcp checks every page twice (the query excludes notes, cards and code, and each result is checked again before the model sees it), and the Hister token stays on the server: an agent's machine needs none.

## Install

One command per machine (idempotent, safe to re-run after an update): `plugins/machiya/install.sh` adds this checkout as a marketplace, installs the plugin at user scope, sets `MACHIYA_AGENT` (default `user@host`; override with `MACHIYA_AGENT_NAME=…`) and, when it is set, `MACHIYA_MCP_URL` in the settings' env, adds the permission rules to `~/.claude/settings.json` (a backup is kept; Hister's MCP tools denied), removes the separate `hister` server that `install.sh hister` added before plugin 0.2.0, and checks the server. It no longer writes `HISTER_MCP_URL` or `HISTER_TOKEN_FILE` (plugin 0.2.x did; they are left alone, and `uninstall` removes them). `install.sh check` tests the connection; `install.sh uninstall` reverses it. All Claude Code sessions for the same user share one `~/.claude`, so one run covers them all; they pick it up when they restart. By hand:

```
claude plugin marketplace add <this repo: a clone's path, or its git URL>     # once per machine
claude plugin install machiya@machiya
```

Set `MACHIYA_MCP_URL` if the server isn't at `https://machiya-mcp.example.ts.net/mcp`, and `MACHIYA_AGENT` (for example `claude@laptop`) so the board's history says who acted. The client has to be on the tailnet. Suggested permissions (reads allowed, most writes ask): see the service doc, which names the tools `mcp__machiya__<tool>` (the server added with `claude mcp add machiya …`). Through this plugin the same tools are `mcp__plugin_machiya_machiya__<tool>`: use that prefix in the permission rules. Use one or the other on a machine, not both.

## `bin/hister-headers`

No longer used by the plugin (it connects no Hister MCP), kept for a person who adds Hister's MCP by hand for their own use (never for an AI client). It prints the header that carries the owner's Hister token, and refuses to send a production token anywhere it shouldn't go (sweep MACH-M-1): with `HISTER_TOKEN_FILE` set it uses that file only (missing or unreadable: no header, never a fallback), and that token goes over plain http only to loopback; the `hpass` fallback (`$HISTER_TOKEN_PASS`, else a per-host entry) applies only when `HISTER_MCP_URL` is an https address that is not loopback and not on a dev-stack port (19200–19226). Anything else prints `{}`: no header.

## The dev stack

Agents work against the [dev stack](../../docs/dev-stack.md) by default (synthetic data; production only for releases). On the machine that runs it:

```
MACHIYA_MCP_URL=http://127.0.0.1:19226/mcp plugins/machiya/install.sh
```

`install.sh check` with the same setting tests the connection and changes nothing.
