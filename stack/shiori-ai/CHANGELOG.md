# Changelog: shiori-ai

## 0.2.0

- **Room tokens**, as smallweb and the other services take them: with `SHIORI_AI_AUTH_URL` (hister-login's internal address), `SHIORI_AI_PUBLIC_URL` (the address the tokens are issued for) and `SHIORI_AI_HISTER_USERS`, a caller may send `Authorization: Bearer mht_…` (or `X-Machiya-Token`) in `tailscale` or `proxy` mode. hister-login confirms each token (a good answer is kept 30 s, a refusal 5 s). A request with a token is decided by the token alone, and a bad or another room's token is refused. A request a token lets in skips the same-origin check: a bearer token isn't a cookie another site's page can ride on. Notes, code and local files are still refused, whoever asks. Tokens are never logged.
- **`GET /api/status`** (and `/shiori/ai/api/status`), with no gate, like every Machiya service: `ok`, `ready`, `error`, `version`, `auth`, `room_tokens`, `last_ok`, `last_error`, and the AI's `enabled`, `answer`, `summarize`, `model` and `remaining`. `error` is set when `SHIORI_AI_KEY_FILE` holds no key, or once three requests in a row failed at the engine or SearXNG. Never a URL, a query or text. `/shiori/ai/status`, the pages' own, is unchanged.
- **`GET /api/changelog`** (and `/shiori/ai/api/changelog`): this file, as `text/markdown`, at most 64 KiB, with an `ETag`, for the landing page's Recent Deploys.

## 0.1.0

- **Published** as a Machiya component, out of the single script behind Shiori's Summarize (2026-09-29) and AI Answer on the hosted pages. The contract is unchanged; `status` gains `summarize`, and `answer` is now false when SearXNG isn't set.
- **A gate of its own** (`SHIORI_AI_AUTH`): `tailscale` (the default: `SHIORI_AI_USERS`, the header believed only from `SHIORI_AI_TRUSTED_PROXIES`), `proxy` (only the proxy that signs people in may connect) or `open` (`127.0.0.1` only, unless `SHIORI_AI_BIND_BEHIND_PROXY=1` says only the host's own port reaches it). A setting that would let anyone in refuses to start. The script relied on the proxy in front. `status` and `healthz` stay open.
- **Settings are prefixed** `SHIORI_AI_`, with neutral defaults: no Hister, SearXNG or key unless set (each job is off without its own). `SHIORI_AI_NOTE_HOSTS` names the note hosts. It listens on `127.0.0.1` unless told otherwise; the image sets `0.0.0.0`.
- **No redirect is followed, and no proxy from the environment is used**, so the API key and the Hister token go only where they're meant to. `SHIORI_AI_API_URL` must be https (http only to loopback, for tests). The Hister token file is checked at start and re-read when it changes.
- **Limits:** `SHIORI_AI_PER_MINUTE` (20, a 429 `busy`), the daily caps as before, answers from Hister (16 MB), SearXNG (4 MB) and the engine (1 MB) cut off, a 30 s request timeout, a URL at most 4,096 characters, no control characters in a search. The gate and the same-origin check come before the body is read. One request per connection.
- **Local files are refused** like notes and code (403 `local`): a URL that isn't http(s) before Hister is asked, and a page Hister says is `type: local` after. The script sent them. AI Answer also drops results on a note host.
- The search itself is fenced in the answer's prompt too. Odd answers from Hister, SearXNG or the engine (not an object, a wrong type) are errors, not crashes.
