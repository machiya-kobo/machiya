# Changelog: smallweb

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
