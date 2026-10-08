# Changelog: shiori-ai

## 0.1.0

- **Published** as a Machiya component, out of the single script behind Shiori's Summarize (2026-09-29) and AI Answer on the hosted pages. The contract is unchanged; `status` gains `summarize`, and `answer` is now false when SearXNG isn't set.
- **A gate of its own** (`SHIORI_AI_AUTH`): `tailscale` (the default: `SHIORI_AI_USERS`, the header believed only from `SHIORI_AI_TRUSTED_PROXIES`), `proxy` (only the proxy that signs people in may connect) or `open` (`127.0.0.1` only, unless `SHIORI_AI_BIND_BEHIND_PROXY=1` says only the host's own port reaches it). A setting that would let anyone in refuses to start. The script relied on the proxy in front. `status` and `healthz` stay open.
- **Settings are prefixed** `SHIORI_AI_`, with neutral defaults: no Hister, SearXNG or key unless set (each job is off without its own). `SHIORI_AI_NOTE_HOSTS` names the note hosts. It listens on `127.0.0.1` unless told otherwise; the image sets `0.0.0.0`.
- **No redirect is followed, and no proxy from the environment is used**, so the API key and the Hister token go only where they're meant to. `SHIORI_AI_API_URL` must be https (http only to loopback, for tests). The Hister token file is checked at start and re-read when it changes.
- **Limits:** `SHIORI_AI_PER_MINUTE` (20, a 429 `busy`), the daily caps as before, answers from Hister (16 MB), SearXNG (4 MB) and the engine (1 MB) cut off, a 30 s request timeout, a URL at most 4,096 characters, no control characters in a search. The gate and the same-origin check come before the body is read. One request per connection.
- **Local files are refused** like notes and code (403 `local`): a URL that isn't http(s) before Hister is asked, and a page Hister says is `type: local` after. The script sent them. AI Answer also drops results on a note host.
- The search itself is fenced in the answer's prompt too. Odd answers from Hister, SearXNG or the engine (not an object, a wrong type) are errors, not crashes.
