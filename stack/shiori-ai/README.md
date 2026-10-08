# shiori-ai

Summarize a saved page, or answer a web search, from Shiori on the web. **shiori-ai** is the server side of Shiori's **Summarize** and **AI Answer** on its hosted pages. It asks Claude through the Anthropic API, only when you ask, and within daily caps you set.

- **Summarize** reads the copy of a page that [Hister](../../docs/services/hister.md) already stored. It never fetches the page.
- **AI Answer** answers from [SearXNG](../../docs/services/searxng.md)'s result snippets: the first 8 web results. It never fetches a page either, and the client sends only the search, so it is no general chatbot proxy.
- **Your notes and code never leave.** A note's address is refused before Hister is asked, and a page labeled `vault` or from code-import is refused after.

It is one stdlib Python file with one SQLite file for its caches and counters. Shiori's apps run their AI on the device and don't use it.

## Endpoints

Shiori's pages call these on their own origin (`/shiori/ai/` on the Hister host's address). The client's side is `docs/ai.md` in Shiori's repository.

| | |
|---|---|
| `GET /shiori/ai/status` | `{enabled, answer, summarize, engine, model, remaining}`: `remaining` is today's engine calls left. No gate (a status page can read it) |
| `POST /shiori/ai/summarize` | `{"url": <a URL Hister has>, "refresh": false}` → `{summary, engine, model, partial, cached, updated}`. `partial`: the page was cut to 60,000 characters first |
| `POST /shiori/ai/answer` | `{"q": <1-300 characters>, "refresh": false}` → `{answer, sources: [{n, title, url}], engine, model, cached}`. The answer cites sources as `[1]`, `[2]` |
| `GET /healthz`, `/shiori/ai/healthz` | `{"ok": true}`. No gate |

Errors are JSON `{error, message}`:

| Status | `error` | When |
|---|---|---|
| 400 | `bad_request` | not JSON, over 8 KB, no `url`/`q`, a search over 300 characters or with Hister syntax (`label:`, `url:`, `metadata.`) |
| 403 | `forbidden` | the gate, or not Shiori's own page (below) |
| 403 | `note`, `code` | a note or a code document |
| 404 | `not_indexed`, `not_found` | Hister has no copy; no such endpoint |
| 422 | `empty`, `no_results` | no readable text; the web found nothing |
| 429 | `cap`, `busy` | today's cap is used up (`Retry-After`: midnight UTC); over `SHIORI_AI_PER_MINUTE` |
| 502 | `engine`, `declined` | the engine refused the request, or declined to answer |
| 503 | `unavailable` | no key, Hister or the engine down, or the job not set up |
| 504 | `searx` | SearXNG took over 6 s |

**Caches.** A summary is kept per page and Hister capture (the newest `SHIORI_AI_CACHE_MAX`), so a page captured again gets a fresh one. An answer is kept 24 hours per search, lowercased with spaces collapsed. `refresh: true` skips the cache. Cached replies don't count against the caps.

## Who may use it

shiori-ai has a gate of its own, so it is safe without anything in front. `SHIORI_AI_AUTH` picks it, as for [shiori-feed](../shiori-feed/README.md):

| Mode | Who gets in | Where it may listen |
|---|---|---|
| `tailscale` (default) | a `Tailscale-User-Login` in `SHIORI_AI_USERS` (`*` = anyone the tailnet lets through). With `SHIORI_AI_TRUSTED_PROXIES` set, the header counts only from those addresses | `127.0.0.1`, or anywhere with `SHIORI_AI_TRUSTED_PROXIES` or `SHIORI_AI_BIND_BEHIND_PROXY=1`. Otherwise it refuses to start |
| `proxy` | only connections from `SHIORI_AI_TRUSTED_PROXIES`: a proxy that signs people in itself, such as the nginx serving Shiori's pages with `auth_request` to [hister-login](../../docs/services/hister-login.md) | anywhere; it refuses to start without `SHIORI_AI_TRUSTED_PROXIES` |
| `open` | everyone | `127.0.0.1`, or anywhere with `SHIORI_AI_BIND_BEHIND_PROXY=1`: for a container whose port is published on the host's `127.0.0.1` only |

On top of the gate, `summarize` and `answer` take only requests from Shiori's own page: `Sec-Fetch-Site: same-origin`, or, from a client without Fetch Metadata, `Origin: https://<Host>`. So the proxy must pass `Host` as the page's own host. `status` and `healthz` are open in every mode.

## Settings

| Env | Default | |
|---|---|---|
| `SHIORI_AI_KEY_FILE` | — | a file holding an Anthropic API key (its first line), read on every request, so a new key needs no restart. Unset or empty: AI is off (`enabled: false`). Use a key of its own with a spend limit |
| `SHIORI_AI_MODEL` | `claude-sonnet-5-5` | |
| `SHIORI_AI_DAILY_REQUESTS` | `100` | engine calls per UTC day |
| `SHIORI_AI_DAILY_INPUT_TOKENS` | `2000000` | input tokens per UTC day |
| `SHIORI_AI_PER_MINUTE` | `20` | summarize and answer requests per minute, for every caller together; `0` = no limit |
| `SHIORI_AI_HISTER_URL` | — | Hister's address, `http://hister:4433`. Unset: no summaries |
| `SHIORI_AI_HISTER_TOKEN_FILE` | — | a file holding your Hister token, sent as `X-Access-Token` ([contracts/hister.md](../../docs/contracts/hister.md)); re-read when it changes. Set but missing or empty stops the start |
| `SHIORI_AI_SEARXNG_URL` | — | SearXNG's address, `http://searxng:8080`, with the `json` format on in its `search.formats`. Unset: no answers |
| `SHIORI_AI_NOTE_HOSTS` | `kura,konbini,niwa` | hosts whose pages are notes: a full host name, or a first label (`kura` matches `kura.example.ts.net`) |
| `SHIORI_AI_AUTH` | `tailscale` | `tailscale`, `proxy` or `open` (above) |
| `SHIORI_AI_USERS` | — | the Tailscale logins allowed in `tailscale` mode; `*` = anyone; unset = nobody |
| `SHIORI_AI_TRUSTED_PROXIES` | — | addresses or CIDRs of the proxy |
| `SHIORI_AI_BIND_BEHIND_PROXY` | — | `1`: only the proxy (or the host's own `127.0.0.1` port) reaches the listener's network. Prefer `SHIORI_AI_TRUSTED_PROXIES` |
| `SHIORI_AI_BIND`, `SHIORI_AI_PORT` | `127.0.0.1`, `8080` | the listener. The image sets `0.0.0.0` |
| `SHIORI_AI_DATA` | `/data` | `shiori-ai.db`: the caches and the daily counts. It can be deleted (the day's counts go with it) |
| `SHIORI_AI_API_URL` | `https://api.anthropic.com/v1/messages` | for tests: https, or http to a loopback address only |

## Safety

- **Secrets come from files**, never from argv, the environment or a log. The key goes only to `SHIORI_AI_API_URL`, the token only to Hister: no redirect is ever followed, and no proxy from the environment is used.
- **Prompt injection.** The page or the results sit between `<page>` or `<results>` tags, and the system prompt says they are data. Any such tag inside them loses its angle brackets, so they can't close the block. SearXNG's titles and snippets lose all tags.
- **Limits:** a request body of 8 KB and 30 s to send it; Hister's answer 16 MB and 20 s; SearXNG's 4 MB and 6 s; the engine's 1 MB and 90 s, with 300 output tokens. One request per connection.
- **The caps are counted before the engine call**, since a failed call may still be billed.
- **The log** carries the method, the path, the status and counts (characters, results, tokens). Never a URL, a search, a title, page text or a secret.

## Running

```sh
SHIORI_AI_AUTH=open SHIORI_AI_DATA=/tmp/shiori-ai SHIORI_AI_KEY_FILE=$HOME/.config/anthropic-key \
  SHIORI_AI_HISTER_URL=http://127.0.0.1:4433 SHIORI_AI_SEARXNG_URL=http://127.0.0.1:8888 python3 shiori_ai.py
podman build -t shiori-ai:dev stack/shiori-ai
```

## Tests

```sh
cd stack/shiori-ai && python3 -m unittest discover -s tests
```

No network: fakes of Hister, SearXNG and the Messages API on `127.0.0.1` record what they are sent.
