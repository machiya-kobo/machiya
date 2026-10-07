# SearXNG (engine: the web)

[SearXNG](https://github.com/searxng/searxng) is the web half of Shiori's searches and Hister's fallback. Machiya uses the upstream image, pinned, with one small local plugin. [Reference config](../../config/searxng/).

- **URL:** `https://searxng.example.ts.net`, for example its own Tailscale Service. It's the one Machiya engine you can share with other people, a household say. `search.example.ts.net` is Shiori's page, not SearXNG.
- **Formats:** `html` + `json`. Shiori reads JSON.
- **Image proxy in JSON:** stock SearXNG signs `/image_proxy` links only in its HTML. The local plugin [`image_proxy_json.py`](../../config/searxng/plugins/image_proxy_json.py) does the same for `format=json`. So Shiori loads thumbnails only through SearXNG, and a device never contacts an engine's image host.
- **Limiter:** on, backed by valkey. `limiter.toml` pass-lists tailnet clients.
- **Secrets:** `server.secret_key` comes from `SEARXNG_SECRET` (the environment or a secret file), never from `settings.yml`.
- **Deployment:** its own stack: SearXNG, valkey, and optionally a SOCKS5 proxy (the compose `proxy` profile).

## Egress

- By default SearXNG reaches the engines directly.
- To use a proxy, uncomment `outgoing.proxies` in `settings.yml` (`socks5h://<proxy>:1080`, DNS resolved at the proxy). Then start the compose `proxy` profile, or point it at a SOCKS5 proxy you already run.
- With a proxy set, a proxy that's down makes searches fail. They never leak from the server's IP.
- Some engines (DuckDuckGo, Qwant) may CAPTCHA proxied traffic. If so, disable them under `engines`.

## Rules

- SearXNG never queries Hister. SearXNG can be shared, and Hister is only yours.
- Keep the version pinned. Bump it after reading the release notes, then check that the JSON image-proxy plugin still loads.
