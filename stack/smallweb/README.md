# smallweb

Machiya's gateway to the small web: Gemini and Gopher search for Shiori, and the pages behind the results, saved to Hister as they're read. Stdlib Python, nonroot, one sqlite file. Contract: [docs/contracts/smallweb-api.md](../../docs/contracts/smallweb-api.md).

## Settings

| Env | Default | |
|---|---|---|
| `SMALLWEB_AUTH` | `tailscale` | `tailscale`: every page and API call needs a `Tailscale-User-Login` in `SMALLWEB_USERS`. `open`: no identity check (a startup warning), for localhost or a trusted LAN only. Anything else refuses to start |
| `SMALLWEB_USERS` | — | allowed logins; `*` = anyone the tailnet lets through; unset = nobody (`/api/status` is open) |
| `SMALLWEB_BIND`, `SMALLWEB_PORT` | `0.0.0.0`, `8080` | the listener. A native install behind `tailscale serve` binds `127.0.0.1` |
| `SMALLWEB_SOCKS` | — | `socks5h://proxy:1080`: every gemini/gopher connection goes through it, names resolved by the proxy. Unset = direct from the host (the startup line says so) |
| `SMALLWEB_HISTER_URL` | — | `http://hister:4433`: pages read are saved there. Unset = no saves |
| `SMALLWEB_ORIGINS` | — | origins besides smallweb's own and `hister://` that may `POST /api/save`: `https://shiori.example.ts.net` (Shiori's hosted pages, if they call smallweb from their own origin) |
| `SMALLWEB_PUBLIC_URL` | the request's host | `https://smallweb.example.ts.net`: the base of `proxy_url` in `/api/search`. Set it, since Shiori calls the API from its own origin |
| `SMALLWEB_DATA` | `/data` | `smallweb.sqlite3`: TOFU known hosts, caches, the hourly counts, the save log. All of it can be deleted |
| `SMALLWEB_PER_HOUR` | `30` | searches per engine per hour |

## Deploying

- Build context: `stack/smallweb/`. The image is `python:3.13-alpine`, uid 1000, with a healthcheck on `/api/status` at `127.0.0.1:8080`.
- Networks:
  - `proxy-net`, to reach `proxy:1080` (optional);
  - Hister's network, for `hister:4433` (optional);
  - the reverse proxy's network (for example a Tailscale sidecar's), if one fronts it.
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
- the gateway against local fake Gemini (TLS) and Gopher servers, a SOCKS5 proxy (which checks names go to the proxy) and a fake Hister.

The tests make throwaway self-signed certificates for those fakes with `openssl` when they start and delete them afterwards (nothing secret is committed; `openssl` must be installed).
