# Using SearXNG

- `GET /search?q=<query>&format=json` (plus the usual `categories`, `time_range`, `pageno`, `language`). Shiori's combined results are built from this.
- Thumbnails: use only the `/image_proxy?url=…&h=…` links in the JSON, signed by the local `image_proxy_json` plugin. Never load an image from anywhere else, so a device never contacts an engine's image host.
- Tailnet clients are pass-listed in the limiter. A burst from Shiori is fine; a scraper isn't.
- Hosted Shiori pages call it through a `/searx/` route on the web server that hosts them (Host `searxng.example.ts.net`).
- Hister's `app.search_url` sends a "no results" search to SearXNG. The reverse never happens.
