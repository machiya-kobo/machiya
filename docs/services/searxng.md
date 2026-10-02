# SearXNG (engine: the web)

[SearXNG](https://github.com/searxng/searxng) is the web half of Shiori's searches and Hister's fallback. Machiya uses the upstream image (pinned), with one small local plugin. [Reference config](../../config/searxng/).

- **URL:** `https://searxng.example.ts.net` (for example its own Tailscale Service). This is the one Machiya engine that other users (for example a household) can be granted, SearXNG only. `search.example.ts.net` is Shiori's page, not SearXNG.
- **Egress:** engines are reached directly by default. Optionally, uncomment `outgoing.proxies` in `settings.yml` (`socks5h://<proxy>:1080`, DNS resolved at the proxy) and start the compose `proxy` profile, or point it at a SOCKS5 proxy you already run. With a proxy set, a proxy that is down makes searches fail rather than leak from the server's IP. Some engines (DuckDuckGo, Qwant) may CAPTCHA proxied traffic; disable them under `engines` if so.
- **Formats:** `html` + `json`. Shiori reads JSON.
- **Image proxy in JSON:** stock SearXNG signs `/image_proxy` links only in its HTML. The local plugin [`image_proxy_json.py`](../../config/searxng/plugins/image_proxy_json.py) does the same for `format=json`, so Shiori only ever loads thumbnails through SearXNG and a device never contacts an engine's image host.
- **Limiter:** on, backed by valkey. Tailnet clients are pass-listed in `limiter.toml`.
- **Secrets:** `server.secret_key` comes from the environment (`SEARXNG_SECRET`, from the environment or a secret file), never from `settings.yml`.
- **Deployment:** its own stack (SearXNG, valkey, and optionally a SOCKS5 proxy via the compose `proxy` profile).

## Rules

- SearXNG never queries Hister: SearXNG is shared and Hister is private to the owner.
- Keep the version pinned. Bump it deliberately after reading the release notes, then check that the JSON image-proxy plugin still loads.
