# Nogon (engine: your pages)

[Nogon](https://nogon.eu/) ([source](https://github.com/asciimoo/nogon)), called Hister until its [rename at v0.22](https://nogon.eu/posts/hister-is-now-nogon), indexes every page you visit or save. Pages come in from:
- the browser extensions and [Shiori](https://github.com/machiya-kobo/shiori);
- importers, for saves from other tools;
- [Kura](https://github.com/machiya-kobo/kura), which pushes every note in as `label:vault`.

Machiya runs Nogon unchanged. This repo holds its [reference config](../../config/nogon/), and [contracts/nogon.md](../contracts/nogon.md) says how the other services use it.

- **URL:** `https://nogon.example.ts.net`, for example a Tailscale Service on its own sidecar. Only you can reach it.
- **Image:** `ghcr.io/asciimoo/nogon` (`ghcr.io/asciimoo/hister` up to v0.21), pinned: the reference and dev compose pin v0.22.0. v0.22 opens a Hister's data and config as they are: it prefers `NOGON__*` settings and still reads `HISTER__*`, and `/hister/hister` is kept as a link to the `nogon` binary. It's pre-1.0 and releases break things. When you bump it, bump [Konbini](https://github.com/machiya-kobo/konbini)'s and [Niwa](https://github.com/machiya-kobo/niwa)'s `NOGON_VERSION` too (`HISTER_VERSION` before their stage-1 releases): they ship the `nogon` CLI for link rot.
- **Deployment:** its own stack. That stack can also run Shiori's hosted pages and optional helpers. Nogon needs none of them.

## Access

- The tailnet grant is the gate, because Nogon holds your browsing history.
- With user handling on (`app.user_handling: true`), every call also needs a sign-in or your token. Machiya's callers already send the token ([contracts/nogon.md](../contracts/nogon.md#every-call)).
- [nogon-login](nogon-login.md) makes a Nogon sign-in count in every Machiya app.

## MCP

Nogon has its own MCP endpoint, `POST /mcp` (`search`, `get_preview`, `get_history`). Machiya's AI clients don't use it, because its `search` and `get_preview` return notes and code documents. They search and read pages through [machiya-mcp](mcp.md) instead.

If you connect Nogon's MCP to something else, follow [contracts/nogon.md](../contracts/nogon.md#nogons-mcp):
- deny `get_history` on every client;
- send no token while the tailnet grant is the gate, and your Nogon token once user handling is on.

## Configuration that matters

- `server.base_url` must equal the URL browsers use. Cookies get `Secure`, and the CSRF check compares `Origin` with it.
- `app.search_url` points at SearXNG for anything Nogon doesn't know. This goes one way only: SearXNG must never query Nogon.
- The social extractors (twitter, mastodon, bluesky) stay **off**. They split feeds into a document per post and replace a re-added document's text.
- `ytdlp` stays off, so the server never contacts YouTube.
- **Skip rules** (`skip-rules.txt`) keep pages out, including every page on your own `*.ts.net` names. So the extension never captures Konbini, [Niwa](https://github.com/machiya-kobo/niwa) or Kura. Vault documents carry `metadata.ignore_skip_rules`.
- **Priority rules** (`priority-rules.txt`) are Bleve regexps over the whole URL. Don't use `^` or `$`: a `$` made every search return 0. The boost gets weaker with each extra pattern.
- Both rule files are loaded into Nogon's configuration from the reference files.
- **Labels:** your own flat topic labels, `@` aliases, and `vault` for notes.
- **Backups:** if your backup stops Nogon (a configurable backup window), API calls hang until it's back. Don't schedule bulk jobs across that window.
