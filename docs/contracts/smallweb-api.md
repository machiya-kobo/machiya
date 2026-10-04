# smallweb API

The small-web gateway (`stack/smallweb/`): Gemini and Gopher search for Shiori, the pages behind the results, and saving pages (including http(s) ones) to Hister. Contract changes are proposed to the maintainers first, like every contract here.

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

## `POST /api/save {"url": "…", "title": "…"}`

Two callers:
- Shiori's "In a Gemini App" setting: a page opened directly in a Gemini client never passes through `/page`, so Shiori asks smallweb to save it (`gemini://`, `gopher://`).
- Shiori's Add Page action and share target: an `http://` or `https://` page. A browser page can't download other sites (CORS), and Hister's `POST /api/add` with only a URL doesn't fetch the page, so smallweb fetches it and sends Hister the page.

The request:
- Owner-only. The request's `Origin` must be one of: smallweb's own; `hister://` (the native apps); or one listed in `SMALLWEB_ORIGINS` (Shiori's hosted pages, which reach smallweb through a `/smallweb/` route on the web server that hosts them, e.g. `https://shiori.example.ts.net`). With no `Origin`, the request is refused.
- The body is JSON (`Content-Type: application/json`, an object) or a form. `url` is required. `title` is optional and used for http(s) only, when the page has no title of its own.
- Answers `202 {"queued": true, "url": "<canonical>"}` at once; the fetch and the save happen in the background. The canonical URL:
  - gemini and gopher: as in `/api/search`;
  - http(s): lowercase scheme and host (an international name in its `xn--` form), no default port (`80`, `443`), no fragment, non-ASCII in the path and query percent-encoded.
- `400 {"error": "…"}`:
  - a URL that isn't `gemini://`, `gopher://`, `http://` or `https://`;
  - http(s): a user name or password in the URL (`user@`), more than 2048 characters, spaces or control characters, no host, an IP literal in a private range (below, unless `SMALLWEB_FETCH_ALLOW` lists it), or a path that is a private Kura vault's address (below);
  - a body that isn't a JSON object or a form.
- `403` for not the owner or a refused origin. `429` when 20 saves are already waiting.

### gemini:// and gopher://

- smallweb fetches the page through the configured SOCKS proxy and saves it exactly as a `/page` read would: the same `metadata`, no label, the same never-save rules, the unchanged-body dedupe, and the same per-host politeness.
- A Gemini URL with a query is saved only if its path without the query doesn't answer `10`/`11` (checked once a day per path). Otherwise the query is the answer to a prompt, and it's never saved. This applies to `/page` too.

### http:// and https://

This is save-only: `/page` stays gemini and gopher, and smallweb is not a web proxy.

**Private addresses are never fetched.** In the background, smallweb resolves the host itself and refuses the save if ANY of its addresses is:
- loopback, private (RFC 1918), link-local, multicast, reserved or unspecified;
- in `0.0.0.0/8`, CGNAT `100.64.0.0/10` (Tailscale) or the broadcast address;
- IPv6 unique local `fc00::/7`;
- an IPv6 form (IPv4-mapped, 6to4, NAT64) of any of those.

The one exception is `SMALLWEB_FETCH_ALLOW`: a comma list of host names (exact, lowercase) and CIDRs. A listed host name may resolve to anything; otherwise each private address must be in a listed CIDR. Unset (the default) allows nothing private.

The rest of the fetch:
- **The vetted address is the one connected to,** so a second DNS answer can't swap it. With `SMALLWEB_SOCKS` set, the proxy is asked for that IP address, never the name. The request carries the real `Host` header; https sends the name as SNI and checks the certificate against it (the system's trust store).
- **Redirects:** at most 5, each one checked again in full (the URL rules, the resolution, the address rules). A redirect to anything but `http(s)` is refused.
- **Never a private Kura vault:** even for an allowed host, a URL whose path, percent-decoded and with a leading `//` folded, starts with `/v/<name>/` (or is `/v/<name>`) is never fetched or saved. That is a private vault's address in Machiya. It's a `400` when asked for, and a refused save when a redirect leads there.
- **Limits:** 10 s to connect and per read, 30 s for one response, 5 MB (smallweb stops reading and refuses past it).
- **Types:** only a `200` with `text/html`, `application/xhtml+xml` or `text/plain` is saved. Any other type or status is refused and not saved.
- **Charset:** the `Content-Type` header's charset, else the page's `<meta charset>`, else UTF-8 with bad bytes replaced.
- **User-Agent:** `smallweb/<version> (Machiya; saves a page its owner asked for)`.
- **Politeness:** one connection at a time per host, at least 1.5 s apart.
- **No `robots.txt`:** each save is one page its owner asked for, like a browser visit, not a crawl. smallweb never follows the page's links.

What is saved: the same `POST <SMALLWEB_HISTER_URL>/api/add` with `Origin: hister://`, under the final URL (after redirects, canonical), with no `label`:

```json
{"url": "https://example.com/notes/page", "title": "A Page",
 "text": "<visible text>", "html": "<the page, cleaned>",
 "metadata": {"source": "smallweb", "smallweb_scheme": "https", "smallweb_mime": "text/html",
              "smallweb_fetched": 1767225600}}
```

- `title`: the page's `<title>`, else its `og:title`, else its first `<h1>`, else the request's `title`, else the URL. Whitespace is collapsed.
- `text`: the visible text, without `script`, `style`, `noscript`, `template` and `svg`, whitespace collapsed, one line per block.
- `html`: the page with `script`, `style`, `iframe`, `object` and `embed` removed (with their content), every `on…=` attribute and `javascript:` URL removed, and comments dropped. A `text/plain` page is saved as `<pre>`.
- The unchanged-body dedupe holds: a body unchanged since its last save isn't sent again.

Errors, per save:
- They go in `/api/status`'s `hister.last_error` (`<time>: save <url>: <why>`) and the log, like a gemini save's.
- A refused private address is `blocked: private address` (a private vault's: `blocked: private vault address`). It is never a failure, so it doesn't turn `/api/status`'s `error` on.
- Neither does a page that couldn't be fetched or isn't saved (a timeout, `HTTP 404`, `not saved: image/png`, `more than 5 redirects`, `the page is larger than 5 MB`).
- Hister's `406`/`422` (its skip rules or sensitive-content check refuse the page) count as not saved, not as an error: `hister.saved` doesn't grow and `error` stays `null`.

## `GET /api/status` (open, for the probe)

```json
{"ok": true, "ready": true, "error": null, "version": "0.2.0", "auth": "tailscale", "socks": true,
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
- `/page` honours Gemini `robots.txt` for `webproxy` and `*`, and gopher `robots.txt` for `*`. An http(s) save reads no `robots.txt` (see `POST /api/save`).
- Egress goes through `SMALLWEB_SOCKS` (an optional SOCKS proxy), never your own IP when it is set. Some hosts refuse VPN ranges; their pages say so and offer the native link. Gemini and gopher names are resolved by the proxy; an http(s) save resolves the name itself (to check the addresses) and asks the proxy for the vetted address.

## `GET /api/changelog` (open, like `/api/status`)

smallweb's `CHANGELOG.md` as `text/markdown; charset=utf-8` (at most 64 KiB, an `ETag`; 404 without the file), for the [landing page](../services/landing.md)'s Recent Deploys (`vaultkit.changelog`).
