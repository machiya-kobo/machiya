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
| `KURA_USERS` | — | allowed `Tailscale-User-Login`s, comma-separated; `*` = anyone; unset = nobody |
| `KURA_PUBLIC_URL` | from `Host` | the base for URLs in API answers: an origin, never a path (`https://kura.example.ts.net`); Kura refuses to start otherwise (0.5.0), since clients tell a work note by `/v/` at the start of its path |
| `KURA_NIWA_URL`, `KURA_KONBINI_URL` | — | sister links ("in the garden", "card on Konbini", the header apps) |
| `KURA_HISTER_URL` | — | push every note into Hister as `label:vault` (needs `KURA_PUBLIC_URL`) |
| `KURA_DB` | `/data/kura.sqlite3` | the push log (URL + hash per note) |

## Rules

- Read-only: Kura never writes to the vault.
- Kura owns browsing by folder, tag and backlink. Shiori links to `/t/<tag>` rather than building its own.
- Kura has its own full-text search (SQLite FTS5); it does not depend on Hister for finding notes.
