# Changelog: shiori-feed

## 0.5.0

- **The rename's stage 3c (owner, 2026-11-21):** `/shiori/api/status` has `nogon` only; its `hister` twin is gone. Shiori's apps read `nogon` since 1.30.0.

## 0.4.0

- The rename's stage 3 (Hister is now Nogon):
  - **The old settings stop the start.** `SHIORI_FEED_HISTER_URL`, `_PUBLIC_URL`, `_TOKEN_FILE` and `_USERS` are no longer read; one set without its `SHIORI_FEED_NOGON_*` twin stops shiori-feed with a message naming the new one. Beside its twin it is ignored, and said once.
  - **Searches send `Origin: nogon://`** (Nogon v0.22 and later), not `hister://`.
  - **`/shiori/api/status` has `nogon`** (`token`, `last_ok`, `last_error`). `hister`, the same object, stays until the Shiori app on every device reads `nogon` (the rename's stage 3c).
  - The code's own names say Nogon (`nogon_search`, `cfg.nogon`, …), as does the log line (`nogon: HTTP …`).

## 0.3.1

- 0.3.0 on today's Python base. 0.3.0 was tagged but never built: its base pin was stale (its note above wrongly says it was re-pinned).

## 0.3.0

- Says Nogon, upstream Hister's new name (owner, 2026-11-19): the feed's title ("Nogon search: …"), its errors ("Nogon failed") and the status error.
- The rename's stage 1: `SHIORI_FEED_NOGON_URL`, `SHIORI_FEED_NOGON_PUBLIC_URL`, `SHIORI_FEED_NOGON_TOKEN_FILE` and `SHIORI_FEED_NOGON_USERS` are read first. The `SHIORI_FEED_HISTER_*` names still work until stage 3, each logged once. A NOGON name that is set wins, even when empty.
- The Python base re-pinned to today's digest.

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
