# smallweb API

The small-web gateway (`stack/smallweb/`): Gemini and Gopher search for Shiori, and the pages behind the results. Contract changes are proposed to the maintainers first, like every contract here.

Served on its own name, `https://smallweb.example.ts.net` (its own Tailscale Service, owner-only), never same-origin with Hister or Shiori.

## `GET /api/search?q=<text>&source=tlgs,kennedy,veronica&page=<n>`

- `q`: required, 1–300 characters (whitespace collapsed). Queried only on this call: callers must not call it from autocomplete or prefetch.
- `source`: optional comma list, default all three. The order sets the interleave.
- `page`: optional, 1–50, default 1. It is each engine's own page n: TLGS 10, Kennedy 15, Veronica-2 30 per page.

`200`:

```json
{
  "query": "example query", "page": 1,
  "results": [
    { "title": "An Example Page",
      "url": "gemini://example.org/notes/example-page",
      "proxy_url": "https://smallweb.example.ts.net/page?url=gemini%3A%2F%2Fexample.org%2Fnotes%2Fexample-page",
      "snippet": "a sample line from an example capsule",
      "marks": [[22, 29]],
      "source": "tlgs",
      "sources": ["tlgs", "kennedy"],
      "scheme": "gemini",
      "kind": "text/gemini",
      "size": "9KB",
      "archive_url": "gemini://tardis.example.org/archive/…" } ],
  "sources": {
    "tlgs":     { "ok": true,  "ms": 1043, "total": 937, "next": true,  "cached": false },
    "kennedy":  { "ok": true,  "ms": 212,  "total": 121, "next": true,  "cached": true },
    "veronica": { "ok": false, "ms": 8000, "total": null, "next": false, "cached": false } },
  "errors": { "veronica": "timeout" }
}
```

| Field | Meaning |
|---|---|
| `url` | The canonical URL: lowercase host, no default port, `#` in a gopher selector as `%23`. Hister stores this URL, and Shiori's `recordOpened` gets it too. |
| `proxy_url` | Where to open it in a browser: `/page` on smallweb. |
| `snippet`, `marks` | Plain text with the engines' `[brackets]` removed. `marks` are `[start, end)` offsets of the matched words; they may be `[]`. Veronica-2 has no snippets: it gives `kind · host › selector`. |
| `source`, `sources` | The engine that ranked the hit first, and every engine that returned it (results are deduped on the normalised URL). |
| `scheme`, `kind` | `gemini` or `gopher`. `kind` is the MIME type for Gemini, and `menu`, `text`, `search`, `html` or `binary` for Gopher. |
| `size`, `archive_url` | As the engine reports them, or `null`. `archive_url` is TLGS's TARDIS copy. |
| `sources.*` | Per engine: `ok`, `ms`, `total` (or `null`), `next` (a next page exists), `cached`. |
| `errors` | Only the engines that failed: `timeout`, `rate-limited`, `slow-down`, `unreachable`, `certificate changed`, or the engine's own status text. |

- Results interleave by per-engine rank, in `source` order.
- One engine failing never fails the search. A stale cached answer is served on error if there is one, with `cached: true`.
- Errors: `400 {"error": "…"}` (missing or too-long `q`, unknown source, bad page), `403` (not the owner).

## Pages (browser, not API)

- `GET /page?url=gemini://…|gopher://…`: the page as HTML.
  - CSP `default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`, no script.
  - `gemini://` and `gopher://` links go back through `/page`. `http(s)` links go direct (`rel=noreferrer`). Other schemes are shown as text.
  - Each page links its canonical URL as "native" (opens a native Gemini client when one is installed).
- `GET /page?url=…&q=…`: the answer to a Gemini `10`/`11` prompt or a gopher type-7 search.
- `GET /?q=`: a plain search page over the same data.
- Images are passed through. Other binaries download with `Content-Security-Policy: sandbox`.

## Saved to Hister

A page read through `/page` is saved (in the background) with `POST http://hister:4433/api/add` and `Origin: hister://`:

```json
{"url": "gemini://example.org/notes/example-page", "title": "An Example Page",
 "text": "<plain text>", "html": "<rendered page>",
 "metadata": {"source": "smallweb", "smallweb_scheme": "gemini", "smallweb_mime": "text/gemini",
              "smallweb_cert_sha256": "<64 hex, Gemini only>", "smallweb_fetched": 1767225600}}
```

- No `label`: a visited page carries none, the house convention.
- **Saved:** Gemini `20` with `text/gemini` or `text/plain`; gopher type `0`; gopher type `1` menus with at least 300 characters of text.
- **Never saved:** the engines' own search pages, anything answered after a prompt or type-7 search (including a direct link to a prompt's answer), `6x`, binaries, pages behind a changed certificate (TOFU mismatch), and a body unchanged since its last save.
- Find them in Hister with `metadata.source:smallweb`. `gemini://` and `gopher://` URLs are accepted by Hister (`201`), and `%23` in a selector is kept.
- The skip rule for your own `*.ts.net` pages (`^https?://([^/]*\.)?example\.ts\.net…`, see `config/hister/skip-rules.txt`) keeps the extension from saving the proxy URLs.

## `POST /api/save {"url": "gemini://…|gopher://…"}`

For Shiori's "In a Gemini App" setting: a page opened directly in a Gemini client never passes through `/page`, so Shiori asks smallweb to save it.

- Owner-only. The request's `Origin` must be one of: smallweb's own; `hister://` (the native apps); or one listed in `SMALLWEB_ORIGINS` (Shiori's hosted pages, which reach smallweb through a `/smallweb/` route on the web server that hosts them, e.g. `https://shiori.example.ts.net`). With no `Origin`, the request is refused.
- The body is JSON (`Content-Type: application/json`) or a form.
- Answers `202 {"queued": true, "url": "<canonical>"}` at once. Then, in the background, smallweb fetches the page through the configured SOCKS proxy and saves it exactly as a `/page` read would: the same `metadata`, no label, the same never-save rules, the unchanged-body dedupe, and the same per-host politeness.
- `400` for a URL that isn't `gemini://` or `gopher://`. `403` for not the owner or a refused origin. `429` when 20 saves are already waiting.
- A Gemini URL with a query is saved only if its path without the query doesn't answer `10`/`11` (checked once a day per path). Otherwise the query is the answer to a prompt, and it's never saved. This applies to `/page` too.

## `GET /api/status` (open, for the probe)

```json
{"ok": true, "ready": true, "error": null, "version": "0.1.0", "auth": "tailscale", "socks": true,
 "sources": {"tlgs": {"last_ok": 1767225600, "last_error": null}, "kennedy": {…}, "veronica": {…}},
 "hister": {"enabled": true, "saved": 12, "queued": 0, "last_error": null}, "known_hosts": 7}
```

- `ready` is always `true` once smallweb answers. `error` is `null` unless smallweb itself is broken: the data dir isn't writable, the SOCKS proxy failed twice in a row, or Hister saves failed three times in a row (Hister's own 406/422 answers don't count). A monitoring probe on this endpoint fails on it. An engine or a capsule being down is not an error: see `sources.*.last_error`.

## Politeness (part of the contract: callers rely on it, engines are owed it)

- Each engine host gets one connection at a time, at least 1.5 s apart.
- Each engine gets at most `SMALLWEB_PER_HOUR` searches an hour (default 30). Past that it answers `rate-limited`.
- Searches are cached for 1 h and pages for 10 min.
- `44 SLOW DOWN` is honoured (doubling on repeats). At most 5 redirects; 10 s timeouts; 5 MB cap.
- smallweb never follows links on its own.
- `/page` honours Gemini `robots.txt` for `webproxy` and `*`, and gopher `robots.txt` for `*`.
- Egress goes through `SMALLWEB_SOCKS` (an optional SOCKS proxy), never your own IP when it is set. Some hosts refuse VPN ranges; their pages say so and offer the native link.
