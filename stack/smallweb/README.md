# smallweb

Machiya's gateway to the small web: Gemini and Gopher search for Shiori, and the pages behind the results, saved to Hister as they're read. It also saves http(s) pages Shiori asks for (`POST /api/save`), which a browser page can't fetch itself; it never becomes a web proxy. Stdlib Python, nonroot, one sqlite file. Contract: [docs/contracts/smallweb-api.md](../../docs/contracts/smallweb-api.md).

## Settings

| Env | Default | |
|---|---|---|
| `SMALLWEB_AUTH` | `tailscale` | `tailscale`: every page and API call needs a `Tailscale-User-Login` in `SMALLWEB_USERS`. `open`: no identity check (a startup warning), for localhost or a trusted LAN only. Anything else refuses to start |
| `SMALLWEB_USERS` | — | allowed logins; `*` = anyone the tailnet lets through; unset = nobody (`/api/status` is open) |
| `SMALLWEB_AUTH_URL` | — | hister-login's internal address (`http://hister-login:8081`): callers may also send a **room token** (`Authorization: Bearer mht_…`) made for smallweb, beside the Tailscale header (0.3.0). A bad one is refused, never passed over. Needs `SMALLWEB_PUBLIC_URL` (the address the token names) and `SMALLWEB_HISTER_USERS` (the Hister usernames it may act as, never `*`) |
| `SMALLWEB_BIND`, `SMALLWEB_PORT` | `0.0.0.0`, `8080` | the listener. A native install behind `tailscale serve` binds `127.0.0.1`. In `tailscale` mode a non-loopback bind needs `SMALLWEB_TRUSTED_PROXIES` or `SMALLWEB_BIND_BEHIND_PROXY=1` (0.3.1), or smallweb refuses to start: anyone who reaches the port could send the header |
| `SMALLWEB_TRUSTED_PROXIES` | — | CIDRs (`172.31.250.2/32`) whose `Tailscale-User-Login` is believed: the Tailscale sidecar's fixed address on smallweb's own network. From anywhere else the header counts for nothing (a room token still works) |
| `SMALLWEB_BIND_BEHIND_PROXY` | — | `1`: the listener's network holds only the proxy, so the header may come from any address there. Prefer `SMALLWEB_TRUSTED_PROXIES` |
| `SMALLWEB_SOCKS` | — | `socks5h://proxy:1080`: every connection goes through it. The search engines' names are resolved by the proxy; a page (gemini, gopher or an http(s) save) is resolved by smallweb itself, every address must be public (`SMALLWEB_FETCH_ALLOW` aside), and the proxy is asked for the checked address (0.3.1). Gemini and gopher pages are never fetched from mail, shell, database or admin ports (`BAD_PORTS`). Unset = direct from the host (the startup line says so) |
| `SMALLWEB_HISTER_URL` | — | `http://hister:4433`: pages read through smallweb are saved there: opened from smallweb's own pages, typed or bookmarked, a link followed from a sibling site (Shiori's results), or a non-browser client. Never a page another site loads, and never on `HEAD` (0.3.1). Unset = no saves |
| `SMALLWEB_HISTER_TOKEN_FILE` | — | the owner's Hister token (the file's first line), sent as `X-Access-Token` with every save ([contracts/hister.md](../../docs/contracts/hister.md)); re-read when the file changes; set but missing or empty stops the start. Unset = no token (a Hister without users ignores it) |
| `SMALLWEB_FETCH_ALLOW` | — | host names (exact, lowercase) and CIDRs an http(s) save may reach although they are private: `wiki.internal,10.1.0.0/16`. Unset = nothing private: loopback, RFC 1918, link-local, CGNAT `100.64.0.0/10` (Tailscale), `fc00::/7` and the like are refused. A private Kura vault's address (`/v/<name>/…`) is never saved, listed or not |
| `SMALLWEB_ORIGINS` | — | origins besides smallweb's own and `hister://` that may `POST /api/save`: `https://shiori.example.ts.net` (Shiori's hosted pages, if they call smallweb from their own origin) |
| `SMALLWEB_PUBLIC_URL` | the request's host | `https://smallweb.example.ts.net`: the base of `proxy_url` in `/api/search`. Set it, since Shiori calls the API from its own origin |
| `SMALLWEB_DATA` | `/data` | `smallweb.sqlite3`: TOFU known hosts, caches, the hourly counts, the save log. All of it can be deleted |
| `SMALLWEB_PER_HOUR` | `30` | searches per engine per hour |

## Deploying

- Build context: `stack/smallweb/`. The image is `python:3.13-alpine`, uid 1000, with a healthcheck on `/api/status` at `127.0.0.1:8080`.
- Networks:
  - **its own network, shared only with the reverse proxy** (the Tailscale sidecar), with a fixed subnet so the sidecar has a fixed address for `SMALLWEB_TRUSTED_PROXIES` (the reference compose, `compose/compose.yml`, shows it);
  - `proxy-net`, to reach `proxy:1080` (optional);
  - Hister's network, for `hister:4433` (optional).
  Never the default network: every container there could reach port 8080.
- A data volume for `/data`. About 64 MB of memory is plenty.
- Serve it on its own name, for example `https://smallweb.example.ts.net`. Shiori calls `/api/search` there; keep the pages on that name too.

## Tests

```sh
docker build -t smallweb-test stack/smallweb
docker run --rm -v "$PWD/stack/smallweb":/s -w /s --entrypoint python3 smallweb-test -m unittest discover -s tests
```

They cover:
- the parsers, on the engines' real formats;
- the renderers;
- the gateway against local fake Gemini (TLS) and Gopher servers, a SOCKS5 proxy (which checks names go to the proxy) and a fake Hister;
- http(s) saves against a local fake web server (http and https): the URL rules, private addresses (by IP, by DNS through a patched resolver, after a redirect), the redirect, size and type limits, charsets, reading the page, the dedupe and Hister's 406. The fake is on `127.0.0.1`, so the tests set `SMALLWEB_FETCH_ALLOW=127.0.0.1/32`.

The tests make throwaway self-signed certificates for those fakes with `openssl` when they start and delete them afterwards (nothing secret is committed; `openssl` must be installed).
