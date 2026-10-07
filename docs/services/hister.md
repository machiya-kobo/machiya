# Hister (engine: your pages)

[Hister](https://github.com/asciimoo/hister) indexes every page you visit or save. Pages come in from:
- the browser extensions and Shiori;
- importers, for saves from other tools;
- Kura, which pushes every note in as `label:vault`.

Machiya runs Hister unchanged. This repo holds its [reference config](../../config/hister/), and [contracts/hister.md](../contracts/hister.md) says how the other services use it.

- **URL:** `https://hister.example.ts.net`, for example a Tailscale Service on its own sidecar. Only you can reach it.
- **Image:** `ghcr.io/asciimoo/hister`, pinned (tested with v0.20.0). It's pre-1.0 and releases break things. When you bump it, bump Konbini's `HISTER_VERSION` too: Konbini ships the `hister` CLI for link rot.
- **Deployment:** its own stack. That stack can also run Shiori's hosted pages and optional helpers. Hister needs none of them.

## Access

- The tailnet grant is the gate, because Hister holds your browsing history.
- With user handling on (`app.user_handling: true`), every call also needs a sign-in or your token. Machiya's callers already send the token ([contracts/hister.md](../contracts/hister.md#every-call)).
- [hister-login](hister-login.md) makes a Hister sign-in count in every Machiya app.

## MCP

Hister has its own MCP endpoint, `POST /mcp` (`search`, `get_preview`, `get_history`). Machiya's AI clients don't use it, because its `search` and `get_preview` return notes and code documents. They search and read pages through [machiya-mcp](mcp.md) instead.

If you connect Hister's MCP to something else, follow [contracts/hister.md](../contracts/hister.md#histers-mcp):
- deny `get_history` on every client;
- send no token while the tailnet grant is the gate, and your Hister token once user handling is on.

## Configuration that matters

- `server.base_url` must equal the URL browsers use. Cookies get `Secure`, and the CSRF check compares `Origin` with it.
- `app.search_url` points at SearXNG for anything Hister doesn't know. This goes one way only: SearXNG must never query Hister.
- The social extractors (twitter, mastodon, bluesky) stay **off**. They split feeds into a document per post and replace a re-added document's text.
- `ytdlp` stays off, so the server never contacts YouTube.
- **Skip rules** (`skip-rules.txt`) keep pages out, including every page on your own `*.ts.net` names. So the extension never captures Konbini, Niwa or Kura. Vault documents carry `metadata.ignore_skip_rules`.
- **Priority rules** (`priority-rules.txt`) are Bleve regexps over the whole URL. Don't use `^` or `$`: a `$` made every search return 0. The boost gets weaker with each extra pattern.
- Both rule files are loaded into Hister's configuration from the reference files.
- **Labels:** your own flat topic labels, `@` aliases, and `vault` for notes.
- **Backups:** if your backup stops Hister (a configurable backup window), API calls hang until it's back. Don't schedule bulk jobs across that window.
