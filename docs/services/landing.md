# machiya-landing: the front door and status page

The whole stack's front door, in two pages:
- **`/`**, a launcher that searches everything and shows what's going on today;
- **`/status`**, which links every app and shows how each is doing: the rooms (Shiori, Konbini, Niwa, Kura), the engines (Hister, SearXNG) and the stack's own services (machiya-mcp, smallweb, vault-mirror, feed-import, code-import).

Code: [stack/landing/](../../stack/landing/) (settings in its README). It shares the other apps' look (vaultkit's shell, phone tab bar, Rooms menu, Settings, themes and palettes), with the Machiya icon as its mark.

## The launcher (`/`)

| Part | From |
|---|---|
| the day and a **status pill** ("All up", "1 needs a look", "2 behind"), linking `/status` | the status poll |
| **Search everything** | a plain form to Shiori's search page (`LANDING_SEARCH_URL`, `<url>?q=…`). It's another origin, so there are no live results; the page's CSP allows that one form target. Unset: no field |
| the four **rooms**, one count each | Hister's page count (Shiori), Konbini's cards in WIP, Niwa's published notes, Kura's notes across every vault |
| **Working On** | Konbini `GET /api/cards`: WIP cards in its order (rank, priority), then the most recently updated, with each next step. Top 5; "Now ›" links Konbini's `/now` |
| **Due Soon** | the same cards: a `due` date in the next 14 days or past, not done or archived |
| **Notes Changed** | Kura `GET /api/recent?limit=5` |
| **Saved & Read** | Hister's newest pages (without vault notes and code documents), with a feed reader's reads and stars marked ("Read in NewsBlur"). Needs your token once Hister has users |
| **Garden** | Niwa `GET /feed.xml`: published notes tended in the last 30 days |

- A Today section is left out when its app is missing, down or refuses. Nothing on the launcher is an error.
- `GET /api/today` is the same data as JSON.

## The status page (`/status`)

| Section | What it shows |
|---|---|
| **Rooms**, **Engines**, **Stack Services** | each app's state (up, behind, starting, error, down, or *Not in this stack*), its version and vendored vaultkit, and a fact or two (notes, cards, pages, tools) |
| **Sync** | the vault's last pull (Kura; the mirror), the board's commit against the vault's head and its LiveSync cycle (Konbini), unpushed garden writes (Niwa), the notes' last push into Hister (Kura), Hister's newest page, **Feeds read** (feed-import: its last good run, pages added in the last day from the page's own samples, failed runs) and **Repos indexed** (code-import: the same, for code documents) |
| **Recent Deploys** | version changes the page saw itself ([below](#recent-deploys-where-they-come-from)), with the changelog lines that came with them |

Where each app's state comes from:

| App | Asks | Also shows |
|---|---|---|
| Kura | `/api/status` | from `/api/vaults`: the number of vaults and their notes (counts only, never a private vault's name) |
| Konbini | `/api/health` | cards in WIP: its per-board counts when it reports them, else from `/api/cards` |
| Niwa | `/api/status` | its published count |
| Shiori | its hosted page (`/`) | the version and build from `/_shiori/status.json`, else the assets' build hash; "AI on · N left today" from `/shiori/ai/status` ("AI down" when that isn't JSON); "feed ok" from `/shiori/healthz`; "Searches 112 today · 3.4k mo · 41.2k yr" from the deployment's counts file (`LANDING_SEARCH_COUNTS`) |
| Hister | `/health` | its version from its MCP `initialize`, its page count and newest page, and "N repos" (below) |
| SearXNG | `/healthz` and `/config` | |
| machiya-mcp, smallweb | `/api/status` | |
| vault-mirror, feed-import, code-import | their `status.json` (read-only mounts) | code-import's comes from `LANDING_CODE_STATUS`, and is quiet when missing |

- **Shiori:** none of its extras makes Shiori down. Its version shows only on its card. A rebuild of the same version is still a deploy ("0.1.0 (build a → b)"), and what changed comes from `/_shiori/CHANGELOG.md`.
- **Hister's "N repos"** is the total of `metadata.source:code metadata.code_kind:repo`, code-import's repo cards. Hidden when 0. Needs `LANDING_HISTER_TOKEN_FILE` once Hister has users.

**Freshness.** A green dot is fine. A yellow dot is *behind* and stays calm. Only *error* and *down* get red text and a red edge.

| Check | Behind after | Broken after |
|---|---|---|
| the vault's pull | 15 minutes | 6 hours |
| Konbini's LiveSync cycle | 15 minutes | 2 hours |
| the notes' push into Hister | an hour | a day |
| Konbini's commit differs from Kura's | 15 minutes | |
| a writer has commits it hasn't pushed | at once | |
| Hister's newest page | three days (red only when Hister itself is down) | |
| feed-import without a good run | an hour | when it says `ok: false` |

An app that doesn't answer within 3 seconds is down.

**Counts** read short: below 1,000 as they are, then one decimal and k, M or B (1,150 → 1.1k, 12,340 → 12.3k). They're cut to one decimal, not rounded.

## Standalone and polite

- **Every app is optional.** Only apps with an address are polled. The others show *Not in this stack*. The page needs no app, and no app needs the page (principle 4).
- **Server-side, cached.** A background thread polls every app in parallel every minute, Today's reads included: a 3 second timeout, no redirects followed, at most 4 MB read. Both pages are served from the last answers.
- The browser refreshes `<main>` every minute while a page is visible, never under a half-typed search.
- **GETs, and one POST.** Hister's version comes from its MCP `initialize` (`POST /mcp`, which changes nothing; [contracts/hister.md](../contracts/hister.md)), at most every 15 minutes. Everything else is a GET.
- **Hister** gets `Origin: hister://` on every GET (the MCP needs none). Its newest-page search leaves the vault notes out.

### Tokens

**Your Hister token** (`LANDING_HISTER_TOKEN_FILE`, re-read when it changes) goes to Hister. When the rooms run `AUTH=hister`, it also goes to their reads that need you:
- Konbini's `/api/health` (the full view, with sync, LiveSync and per-board counts) and its cards;
- Kura's vaults and recent notes;
- Niwa's feed.

It goes only to Kura, Konbini and Niwa at their configured https address, and never across a redirect.

- The open reads (Kura's and Niwa's `/api/status`, every `/api/changelog`) are sent without it.
- Konbini's `/api/health` is asked again without it when refused.
- Without the token, or when refused, the WIP and vault counts and Today's room sections are left out quietly. Kura's open `vault_count` still shows.

**Other tokens.** None by default.
- `LANDING_TOKEN_FILE` goes to Kura, Niwa and Konbini over https only. Use a room token from hister-login (`mht_…`, [identity.md](../identity.md#room-tokens)) for your reads, and Hister's token then goes to Hister only. Or, for rooms with an identity file, that file's token.
- `LANDING_CHANGELOG_TOKEN_FILE` goes only to the changelog URLs.

## Recent deploys: where they come from

The page records a deploy when an app answers with a version (or a vendored vaultkit) it hasn't seen from that app before. It keeps the time it noticed in one small JSON file (`LANDING_STATE`).

- It shows what is really running, works with any mix of apps, and needs no forge, registry or deployment access.
- Losing the file loses only the list of past deploys.
- The time is when the page noticed, within a minute of the restart. A deploy made while the page was down shows up at its next poll.
- A redeploy of the same version is not a deploy.

What a version brought comes from the app itself. Every app serves its `CHANGELOG.md` at **`GET /api/changelog`** ([principle 7](../principles.md), `vaultkit.changelog`, [ui.md](../ui.md#the-changelog-endpoint-vaultkitchangelog-v018)), behind the same gate as its status.

- The page asks Kura, Konbini, Niwa, machiya-mcp and smallweb every 15 minutes, with the `ETag`, so an unchanged file is a 304.
- It also asks at once when an app answers with a version its copy doesn't have.
- A jump of several versions lists each one's first line.
- An app that answers 404, or anything but markdown, shows its versions only. `LANDING_CHANGELOGS` can point one elsewhere.
- The engines (Hister, SearXNG) and Shiori's hosted page have no endpoint. vault-mirror, feed-import and code-import have no HTTP at all.

## Auth

Only you can open it, as with the rooms.

| `LANDING_AUTH` | Who gets in |
|---|---|
| `tailscale` (default) | a `Tailscale-User-Login` in `LANDING_USERS` |
| `open` | anyone; for localhost, with a `Host` allow-list |
| `hister` | Hister's users, exactly as for Konbini and Niwa ([hister-login](hister-login.md), vaultkit's `histerauth`) |
| `header` | with the identity file: a proxy's login header |

With `hister`:
- The Tailscale fallback applies, so the status page still opens (with the banner) when Hister or the helper is down.
- A signed-out page goes to sign in, and `POST /signout` ends the session.
- Settings shows Shared first, then This Device and the Account.
- The Shared settings follow you: `/api/prefs` is the account's, forwarded to the helper ([contracts/prefs.md](../contracts/prefs.md)).
- With `MACHIYA_SIGNIN_PROVIDER=oidc`, a signed-out page signs itself in through tsidp.

With the identity file ([identity.md](../identity.md)), callers are principals and need `landing` `read` (you have it).

`/healthz`, `/api/changelog` and the static files are open. `/api/status`, `/api/today` and `/api/prefs` need you.

## Deploying it beside the rooms

Add a `landing` service to the stack's compose:
- build context `stack/landing/` (or a published image), a non-root user, a small memory limit, a `/data` volume;
- the default network only. Like machiya-mcp, its calls to the rooms must leave through the host's own tailscaled, so Konbini's `/api/health` sees your login;
- vault-mirror's volume mounted read-only, for `LANDING_MIRROR_STATUS`;
- feed-import's data volume mounted read-only, for `LANDING_FEED_STATUS`.

Environment:
- `MACHIYA_ROOMS`, as the rooms have it, plus `machiya=https://machiya.example.ts.net`
- `LANDING_APPS=machiya-mcp=https://…,smallweb=https://…`
- `LANDING_SEARCH_URL=<Shiori's search page>`
- `LANDING_USERS=<your Tailscale login>`
- `LANDING_BIND_BEHIND_PROXY=1`

Then add a Tailscale Service for it (the owner-only grant first, then `serve.json` → `http://landing:8080`), and probe `/healthz`.

**Links to it.** Add `machiya=https://machiya.example.ts.net` to every room's `MACHIYA_ROOMS` (vaultkit 0.18 or later).
- Each Rooms menu then ends with **Machiya · home**, before Settings.
- The footer's "Part of Machiya" links here.
- Status links point at `/status`. Shiori's Status link is a build setting of its hosted pages.
