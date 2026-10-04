# Hister (engine: your pages)

[Hister](https://github.com/asciimoo/hister) indexes every page you visit or save: the browser extensions and Shiori add them, importers can bring in saves from other tools, and Kura pushes every note in as `label:vault`. Machiya uses it as-is (upstream image, pinned); this repo holds its [reference config](../../config/hister/) and how the services use it ([contracts/hister.md](../contracts/hister.md)).

- **URL:** `https://hister.example.ts.net` (for example a Tailscale Service on its own sidecar), owner-only. The tailnet grant is the gate, because it holds browsing history. With user handling on (`app.user_handling: true`) Hister also needs a sign-in or the owner's token on every call, and [hister-login](hister-login.md) makes that sign-in count in every room; callers send the token already ([contracts/hister.md](../contracts/hister.md#every-call)).
- **Image:** `ghcr.io/asciimoo/hister`, pinned (tested with v0.20.0). It's pre-1.0 and releases break things. When bumping it, bump Konbini's `HISTER_VERSION` too (it ships the `hister` CLI for link rot).
- **MCP:** Hister's own `POST /mcp` (`search`, `get_preview`; `get_history` denied on every client) is how AI clients search and read saved pages; the machiya plugin connects it beside machiya-mcp. Rules: [contracts/hister.md](../contracts/hister.md#histers-mcp). It needs no token while the tailnet grant is the gate; with user handling on, a client sends the owner's Hister token.
- **Deployment:** its own stack. A deployment can also run Shiori's hosted pages and optional helpers in that stack; none of them is needed to run Hister.

## Configuration that matters

- `server.base_url` must equal the URL browsers use: cookies get `Secure`, and the CSRF check compares `Origin` with it.
- `app.search_url` → SearXNG for anything Hister doesn't know. This goes one way only: SearXNG must never query Hister.
- The social extractors (twitter, mastodon, bluesky) are **off**: they split feeds into a document per post and replace a re-added document's text. `ytdlp` stays off, so the server never contacts YouTube.
- **Skip rules** (`skip-rules.txt`) keep pages out, including every page on your own `*.ts.net` names, so the extension never captures Konbini, Niwa or Kura. Vault documents carry `metadata.ignore_skip_rules`. **Priority rules** (`priority-rules.txt`) are Bleve regexps over the whole URL: no `^` or `$` (a `$` made every search return 0), and the boost dilutes with each extra pattern. Both are loaded into Hister's configuration from the reference files.
- **Labels:** your own flat topic labels plus `@` aliases, and `vault` for notes.
- **Backups:** if your backup stops Hister (a configurable backup window), API calls hang meanwhile, so don't schedule bulk jobs across it.
