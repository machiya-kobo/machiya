# Reference configuration

Reference configuration for the engines Machiya runs on (Hister and SearXNG), with every hostname and account replaced by an example. Copy the files and adapt them; the rules lists in particular are a generic starting point, not a recommendation for your browsing.

| File | Notes |
|---|---|
| `hister/config.yml` | no secrets. `HISTER__<SECTION>__<KEY>` env vars override any key. `server.base_url` must be the URL browsers use. |
| `hister/skip-rules.txt`, `hister/priority-rules.txt` | load them through Hister's API or its extension popups. Skip rules are Go regexps over the full URL; priority rules are Bleve regexps over the whole URL: no `^`/`$`. |
| `searxng/settings.yml` | `secret_key` comes from `SEARXNG_SECRET`, never this file. `outgoing.proxies` is commented out (direct connections); uncomment it and start the compose `proxy` profile to send engine requests through an optional SOCKS5 proxy (`socks5h://proxy:1080`). |
| `searxng/limiter.toml` | trusted proxy and client ranges for the bot limiter |
| `searxng/plugins/image_proxy_json.py` | signs `/image_proxy` links in `format=json` results, for Shiori |

For your own deployment, change `server.base_url` and `app.search_url` (Hister), and `server.base_url` and `outgoing.proxies` (SearXNG).
