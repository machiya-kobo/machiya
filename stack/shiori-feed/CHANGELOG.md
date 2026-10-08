# Changelog: shiori-feed

## 0.1.0

- **Published** as a Machiya component, out of the single script that served Shiori's Subscribe since 2026-09-28 (and, until 2026-09-30, a settings store Shiori no longer uses: `/shiori/settings` is gone, a 404).
- **A gate of its own** (`SHIORI_FEED_AUTH`): `tailscale` (the default: `SHIORI_FEED_USERS`, the header believed only from `SHIORI_FEED_TRUSTED_PROXIES`), `proxy` (only the proxy that signs people in may connect) or `open` (`127.0.0.1` only, unless `SHIORI_FEED_BIND_BEHIND_PROXY=1` says only the host's own port reaches it). A setting that would let anyone in refuses to start. The script relied on the proxy in front.
- **Settings are prefixed** `SHIORI_FEED_`; `SHIORI_FEED_HISTER_URL` is required, and the token comes only from `SHIORI_FEED_HISTER_TOKEN_FILE` (set but empty or missing stops the start; re-read when it changes). It listens on `127.0.0.1` unless told otherwise; the image sets `0.0.0.0`.
- **Limits:** `SHIORI_FEED_PER_MINUTE` (60), Hister's answer at most 16 MB, a 30 s request timeout, a title at most 200 characters, at most 20 `exclude_label` of at most 100.
- **A redirect from Hister is never followed**, and no proxy from the environment is used: the token goes to Hister only.
- `HEAD /shiori/healthz` answers; the feed is `private`; DEL is dropped from the XML too.
