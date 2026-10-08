# shiori-feed

Subscribe to any Hister search in your feed reader. **shiori-feed** answers `/shiori/feed?q=…` with an RSS 2.0 feed of the newest matching pages in [Hister](../../docs/services/hister.md), and Shiori's **Subscribe** builds those addresses for you (one search, label or collection at a time, or every collection as OPML).

It is one stdlib Python file. It keeps no state and only ever calls Hister.

## Endpoints

| | |
|---|---|
| `GET /shiori/feed?q=<query>` | 200 `application/rss+xml`: the 50 most recently visited matches, newest first. Hister's sort `date` orders by `updated`; each item's `pubDate` is `added`, when Hister first saw the page. `Cache-Control: private, max-age=300` |
| `&title=<text>` | the channel's title, after "Shiori – " (at most 200 characters; default: the query) |
| `&exclude_label=<label>` | repeatable, at most 20: leaves out pages with exactly that label (case-sensitive), sent to Hister as ` -label:"…"`. Shiori adds `exclude_label=vault`, so your notes stay out |
| `GET /shiori/healthz` | 200 `ok`, with no gate and no Hister call: for probes |

- `q` is anything Hister's search box takes: `label:books`, an alias, words, `*` for everything.
- Code documents (`metadata.source:code`, from [code-import](../../docs/services/code-import.md)) are never feed items.
- Each path also answers without `/shiori` (`/feed`, `/healthz`), for a proxy that strips its mount point.
- Errors: 400 (no `q`, or over a limit above; `q` is at most 500 characters), 403 (the gate), 429 (over `SHIORI_FEED_PER_MINUTE`, with `Retry-After`), 502 (Hister failed or sent more than 16 MB), 404 and 405 for anything else.

Each item carries the page's title, its URL as link and guid, its label as a category, and the first 500 characters of its text as the description.

## Who may read the feeds

shiori-feed has a gate of its own, so it is safe without anything in front. `SHIORI_FEED_AUTH` picks it:

| Mode | Who gets in | Where it may listen |
|---|---|---|
| `tailscale` (default) | a `Tailscale-User-Login` in `SHIORI_FEED_USERS` (`*` = anyone the tailnet lets through). With `SHIORI_FEED_TRUSTED_PROXIES` set, the header counts only from those addresses | `127.0.0.1`, or anywhere with `SHIORI_FEED_TRUSTED_PROXIES` or `SHIORI_FEED_BIND_BEHIND_PROXY=1`. Otherwise it refuses to start: anyone who reaches the port could send the header |
| `proxy` | only connections from `SHIORI_FEED_TRUSTED_PROXIES`: a proxy that signs people in itself, such as nginx with `auth_request` to [hister-login](../../docs/services/hister-login.md) | anywhere; it refuses to start without `SHIORI_FEED_TRUSTED_PROXIES` |
| `open` | everyone | `127.0.0.1`, or anywhere with `SHIORI_FEED_BIND_BEHIND_PROXY=1`: for a container whose port is published on the host's `127.0.0.1` only, as in the reference compose |

`/shiori/healthz` is open in every mode. The proxy in front must drop a `Tailscale-User-Login` a client sends (Tailscale Serve does).

A feed reader on the tailnet can read the feeds straight from a Tailscale Serve address. A reader on a tagged machine (a self-hosted one on a server) sends no login, so it needs `SHIORI_FEED_USERS=*`, which lets in anyone your tailnet policy lets reach the address. A reader on the open internet can't, by design: these feeds are your browsing history.

## Settings

| Env | Default | |
|---|---|---|
| `SHIORI_FEED_HISTER_URL` | — | Hister's address, `http://hister:4433` (required) |
| `SHIORI_FEED_HISTER_TOKEN_FILE` | — | a file holding your Hister token (its first line), sent as `X-Access-Token` ([contracts/hister.md](../../docs/contracts/hister.md)); re-read when the file changes. Set but missing or empty stops the start. Unset: no token (a Hister without users ignores it) |
| `SHIORI_FEED_HISTER_PUBLIC_URL` | `SHIORI_FEED_HISTER_URL` | Hister's address for browsers: the channel's link is its search page |
| `SHIORI_FEED_AUTH` | `tailscale` | `tailscale`, `proxy` or `open` (above) |
| `SHIORI_FEED_USERS` | — | the Tailscale logins allowed in `tailscale` mode; `*` = anyone; unset = nobody |
| `SHIORI_FEED_TRUSTED_PROXIES` | — | addresses or CIDRs of the proxy: `172.31.250.2/32` |
| `SHIORI_FEED_BIND_BEHIND_PROXY` | — | `1`: only the proxy (or the host's own `127.0.0.1` port) reaches the listener's network, so in `tailscale` mode the header may come from any address there. Prefer `SHIORI_FEED_TRUSTED_PROXIES` |
| `SHIORI_FEED_BIND`, `SHIORI_FEED_PORT` | `127.0.0.1`, `8080` | the listener. The image sets `0.0.0.0` |
| `SHIORI_FEED_PER_MINUTE` | `60` | feeds served per minute, for every caller together; `0` = no limit |

## Safety

- **Hister calls** send `Origin: hister://` and the token from the file, never in argv, the environment or a log. A redirect from Hister is never followed, so the token can't go anywhere else. No proxy from the environment is used.
- **Limits:** 20 s and 16 MB for Hister's answer; 30 s for a client to send its request; one request per connection.
- **The log** has the method, the path and the status: never a query, a title or a token. Errors log their code, not the request line.

## Running

```sh
SHIORI_FEED_HISTER_URL=http://127.0.0.1:4433 SHIORI_FEED_AUTH=open python3 shiori_feed.py
curl 'http://127.0.0.1:8080/shiori/feed?q=label:books'
podman build -t shiori-feed:dev stack/shiori-feed
```

## Tests

```sh
cd stack/shiori-feed && python3 -m unittest discover -s tests
```

No network: a fake Hister on `127.0.0.1` records what it is sent.
