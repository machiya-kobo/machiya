# Kura 蔵 (the storehouse)

**Every note in the vault, with working links.** Kura is the vault's web home and its read service:
- a reader (three columns on wide screens: folders, notes, preview; one column plus a tab bar on phones)
- full-text search
- a JSON API that Shiori uses for notes
- the push of every note into Hister

- **URL:** `https://kura.example.ts.net` (for example its own Tailscale Service), owner-only
- **Repo:** [machiya-kobo/kura](https://github.com/machiya-kobo/kura)
- **Runs standalone:** its own stack, its own https clone (read-only credentials), FTS5 search, the API, and the Hister push.

## Pages

| Path | What |
|---|---|
| `/` | recently changed, with the preview pane (`?p=<slug>` picks the note) |
| `/n/<path>` | a note: every `[[wikilink]]` links to its Kura page, plus backlinks, tags, and "in the garden" or "not published" |
| `/f/<folder>` | a folder and its sub-folders |
| `/t/`, `/t/<tag>` | every tag with counts, one tag (nested tags included: `topic` lists `topic/docker`) |
| `/recent`, `/search?q=` | recently changed, search (titles, then full text) |
| `/preview/<slug>` | the preview pane on its own (the page script loads it) |
| `/a/<image>` | a vault image |

`Templates/` is never listed or served. The note URL `https://kura…/n/<path without .md>` is **stable**: Hister documents, Shiori's history and marks and other notes all key on it.

## API

See [contracts/kura-api.md](../contracts/kura-api.md): `/api/search`, `/api/notes`, `/api/note`, `/api/recent`, `/api/tags`, `/api/folders`, `/api/status`, `/feed.xml`.

## Settings

| Env | Default | |
|---|---|---|
| `KURA_REPO_URL` | — | vault repo (https/ssh/file). Unset: use `KURA_REPO_DIR` as it is (a mounted checkout) |
| `KURA_REPO_DIR` | `/data/repo` | where the clone lives |
| `KURA_REPO_SUBDIR` | empty (the repository root) | the vault folder in the repo |
| `KURA_REPO_BRANCH` | remote default | |
| `KURA_REPO_TOKEN_FILE`, `KURA_REPO_USER` | — | https token (a file, e.g. a rendered secret) and its user |
| `KURA_POLL` | `60` | seconds between fetches |
| `KURA_AUTH` | `tailscale` | who may read: `tailscale` (the `Tailscale-User-Login` a proxy sets, checked against `KURA_USERS`), `open` (no check: `127.0.0.1` only), `header` (your proxy's login header, `KURA_AUTH_HEADER`, with an identity file), or `hister` (Hister's users through [hister-login](hister-login.md): `KURA_AUTH_URL`, `KURA_AUTH_SIGNIN_URL`, `KURA_HISTER_USERS`, `KURA_AUTH_FALLBACK`; [identity.md](../identity.md#hister-sign-in-authhister)) |
| `KURA_BIND`, `KURA_PORT` | `0.0.0.0`, `8080` | the listener; a header mode (`tailscale`, `header`) refuses a non-loopback bind unless `KURA_BIND_BEHIND_PROXY=1` says the proxy is the only way in |
| `KURA_USERS` | — | allowed `Tailscale-User-Login`s, comma-separated; `*` = anyone; unset = nobody |
| `KURA_PUBLIC_URL` | from `Host` | the base for URLs in API answers: an origin, never a path (`https://kura.example.ts.net`); Kura refuses to start otherwise (0.5.0), since clients tell a work note by `/v/` at the start of its path |
| `KURA_NIWA_URL`, `KURA_KONBINI_URL` | — | sister links ("in the garden", "card on Konbini", the header apps) |
| `KURA_HISTER_URL` | — | push every note into Hister as `label:vault` (needs `KURA_PUBLIC_URL`) |
| `KURA_HISTER_TOKEN_FILE` | — | the owner's Hister token, sent as `X-Access-Token` with the push ([contracts/hister.md](../contracts/hister.md#every-call)) |
| `KURA_VAULTS` | — | more vaults beside the default one, at `/v/<name>/…` (private unless `+shared`) |
| `MACHIYA_IDENTITY_FILE`, `MACHIYA_COOKIE_DOMAIN`, `MACHIYA_SSO_COOKIE`, `MACHIYA_ROOMS` | — | shared with every room: the identity file ([identity.md](../identity.md)), the shared cookie domain, the sign-in cookie's name (`machiya_sso`), the Rooms menu |
| `KURA_DB` | `/data/kura.sqlite3` | the push log (URL + hash per note) |

The full table, with every setting, is in the [Kura README](https://github.com/machiya-kobo/kura#settings).

## Rules

- Read-only: Kura never writes to the vault.
- Kura owns browsing by folder, tag and backlink. Shiori links to `/t/<tag>` rather than building its own.
- Kura has its own full-text search (SQLite FTS5); it does not depend on Hister for finding notes.
