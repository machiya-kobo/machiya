# Changelog: smallweb

## 0.3.2

- Shorter copy (the owner's copy-editing pass, 2026-10-05): the robots, client-certificate and changed-certificate notices are a sentence or two; the Open natively tooltip is capitalised.

## 0.3.1

Security (the sweep of 2026-10):
- **No relay to anything** (MACH-F-1): a gopher selector or query, or a Gemini URL, with a control character (CR, LF, NUL, TAB, …) is never sent. A requested gemini:// or gopher:// page is resolved by smallweb, every address must be public (the http(s) rule, `SMALLWEB_FETCH_ALLOW` aside), and the proxy is asked for that address; mail, shell, database and admin ports are refused. The fixed search engines still go to the proxy by name.
- **Another site can't drive it** (MACH-F-1, F-2): by Fetch Metadata, `/page` from another site's page shows "Open This Page?" with a same-origin link and fetches nothing; another site's (or a sibling's) script or image gets 403, as does a cross-site `/api/search`; a cross-site `/?q=` searches nothing.
- **Only pages viewed through smallweb are saved to Hister** (decided 2026-10-05; MACH-F-2): smallweb's own pages, a typed address, a link followed from a sibling site (Shiori's results) or a non-browser client. Never on `HEAD`.
- **The log has no queries** (MACH-F-5): method, path and status only; errors log their code, never the request line.
- **A deadline** (MACH-F-8): one gemini or gopher response may take 30 s in all; a server trickling bytes no longer holds a save worker.
- **The Tailscale header counts only from the sidecar** (MACH-F-9): `SMALLWEB_TRUSTED_PROXIES`; in `tailscale` mode a non-loopback bind without it (or `SMALLWEB_BIND_BEHIND_PROXY=1`) refuses to start. **Deploy note:** set one of them before running 0.3.1 on `0.0.0.0`.
- **The HTML sent to Hister is an allowlist** (MACH-F-7): document and text tags, a few attributes, links and images to http(s), mailto, gemini, gopher or relative addresses only. `srcdoc`, `<script/>`, SVG and MathML, `data:` URLs, forms, `<base>`, `<link>`, `<meta>`, `style` and `class` no longer get through (feed-import uses the same reader).
- `web.decode` uses only text encodings (feed-import 0.1.2's fix; the same file).

## 0.3.0

- **Room tokens beside the Tailscale header** (decided 2026-10-05: tools stop taking Hister's raw token; the agents' VM is a tagged node with no Tailscale login). With `SMALLWEB_AUTH_URL` (hister-login's internal address), `SMALLWEB_PUBLIC_URL` and `SMALLWEB_HISTER_USERS`, a caller may send `Authorization: Bearer mht_…`, a room token hister-login issued for this service, acting as that Hister user (cached 30 s). A bad, revoked or other service's token is refused and never falls back to the header; without one the header decides as before.

## 0.2.3

- **Keep-alive (security):** a request body smallweb didn't read (a refused caller's 403, a GET's) stayed on the
  connection and was parsed as the next request: one smuggled past Tailscale Serve, with a `Tailscale-User-Login`
  Serve never saw, so anyone who could reach smallweb could search, read and save to Hister as an allowed user. Such a
  request now ends with `Connection: close`, and every request starts with no state from the one before
  (`handle_one_request`). A negative or unreadable `Content-Length` is a 400 (it hung, or failed the request). Tests
  send several people's requests down one kept-alive connection, as Serve does.

## 0.2.2

- **`SMALLWEB_HISTER_TOKEN_FILE`** (phase 1 of the Hister sign-in, docs/contracts/hister.md): the owner's Hister token, sent as `X-Access-Token` with every save to Hister (and by `selftest.py --hister`). Unset sends none, as before (a Hister without users ignores it). A set file that is missing, empty or not a token stops the start; the file is re-read when it changes (a file that vanishes keeps the last good value). Never logged or shown; `/api/status` says `hister.token: true|false`. Hister's redirects are no longer followed.

## 0.2.1

- `GET /api/changelog` serves this changelog (open, like `/api/status`) for the Machiya status page's recent deploys.

## 0.2.0

- `POST /api/save` saves `http://` and `https://` pages too (Shiori's Add Page and share target): fetched once in the background, read into a title, text and cleaned HTML, and sent to Hister like a gemini save. `/page` stays gemini and gopher.
- Private addresses are never fetched: every address the name resolves to is checked (loopback, RFC 1918, link-local, CGNAT, `fc00::/7` and the rest), the connection goes to the checked address (through SOCKS as an IP), and each redirect is checked again. `SMALLWEB_FETCH_ALLOW` lists the exceptions. A private Kura vault's address (`/v/<name>/…`) is never saved.
- A refused save is logged as `blocked: private address`, never as a failure.

## 0.1.2

- `SMALLWEB_ORIGINS` accepts the origin of any reverse proxy that serves a web UI in front of smallweb.

## 0.1.1

- An optional egress self-check (it only checks that the trace answers through the proxy), and generic proxy and deployment wording.

## 0.1.0

Gemini and Gopher search (several engines, one result list), pages read through the gateway saved to Hister in the background, `POST /api/save`, the SOCKS5h egress setting, per-host politeness, and `/api/status`.
