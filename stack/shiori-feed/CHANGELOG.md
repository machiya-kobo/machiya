# Changelog: shiori-feed

## 0.2.0

- **Room tokens**, as smallweb and the other services take them: with `SHIORI_FEED_AUTH_URL` (hister-login's internal address), `SHIORI_FEED_PUBLIC_URL` (the address the tokens are issued for) and `SHIORI_FEED_HISTER_USERS`, a caller may send `Authorization: Bearer mht_…` (or `X-Machiya-Token`) in `tailscale` or `proxy` mode. hister-login confirms each token (a good answer is kept 30 s, a refusal 5 s). A request with a token is decided by the token alone, and a bad or another room's token is refused. Tokens are never logged.
- **`GET /shiori/api/status`** (and `/api/status`), with no gate, like every Machiya service: `ok`, `ready`, `error`, `version`, `auth`, `room_tokens` and `hister` (`token`, `last_ok`, `last_error`). `error` is set once three feeds in a row failed to reach Hister. Never a query, a title or a token.
- **`GET /shiori/api/changelog`** (and `/api/changelog`): this file, as `text/markdown`, at most 64 KiB, with an `ETag`, for the landing page's Recent Deploys.

## 0.1.0

- **Published** as a Machiya component, out of the single script that served Shiori's Subscribe since 2026-09-28 (and, until 2026-09-30, a settings store Shiori no longer uses: `/shiori/settings` is gone, a 404).
- **A gate of its own** (`SHIORI_FEED_AUTH`): `tailscale` (the default: `SHIORI_FEED_USERS`, the header believed only from `SHIORI_FEED_TRUSTED_PROXIES`), `proxy` (only the proxy that signs people in may connect) or `open` (`127.0.0.1` only, unless `SHIORI_FEED_BIND_BEHIND_PROXY=1` says only the host's own port reaches it). A setting that would let anyone in refuses to start. The script relied on the proxy in front.
- **Settings are prefixed** `SHIORI_FEED_`; `SHIORI_FEED_HISTER_URL` is required, and the token comes only from `SHIORI_FEED_HISTER_TOKEN_FILE` (set but empty or missing stops the start; re-read when it changes). It listens on `127.0.0.1` unless told otherwise; the image sets `0.0.0.0`.
- **Limits:** `SHIORI_FEED_PER_MINUTE` (60), Hister's answer at most 16 MB, a 30 s request timeout, a title at most 200 characters, at most 20 `exclude_label` of at most 100.
- **A redirect from Hister is never followed**, and no proxy from the environment is used: the token goes to Hister only.
- `HEAD /shiori/healthz` answers; the feed is `private`; DEL is dropped from the XML too.
