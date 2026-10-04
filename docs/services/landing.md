# machiya-landing: the front door and status page

The whole stack's front door, in two pages (0.2.0; the owner's "Launcher + Today"): **`/`**, a launcher that searches everything and shows what the rooms say is going on today, and **`/status`**, which links every room (Shiori, Konbini, Niwa, Kura), the engines (Hister, SearXNG) and the stack's own services (machiya-mcp, smallweb, vault-mirror, feed-import) and shows how each is doing. Code: [stack/landing/](../../stack/landing/) (settings in its README). It wears the rooms' look: vaultkit's shell, the phone tab bar, the Rooms menu, Settings, both themes and every palette, with the Machiya icon as its mark.

## The launcher (`/`)

| Part | From |
|---|---|
| the day and a **status pill** ("All up", "1 needs a look", "2 behind"), linking `/status` | the status poll |
| **Search everything** | a plain form to Shiori's search page (`LANDING_SEARCH_URL`, `<url>?q=…`; another origin, so no live results; the page's CSP allows that one form target). Unset: no field |
| the four **rooms**, one count each | Hister's page count (Shiori), Konbini's cards in WIP, Niwa's published notes, Kura's notes |
| **Working On** | Konbini `GET /api/cards`: WIP cards in its order (rank, priority), then the most recently updated, with each next step; top 5, "Now ›" links Konbini's `/now` |
| **Due Soon** | the same cards: a `due` date in the next 14 days or past, not done or archived |
| **Notes Changed** | Kura `GET /api/recent?limit=5` |
| **Saved & Read** | Hister's newest pages (vault notes left out), a feed reader's reads and stars marked ("Read in NewsBlur"); the owner's token once Hister has users |
| **Garden** | Niwa `GET /feed.xml`: published notes tended in the last 30 days |

Each Today section is left out when its room is missing, down or refuses; nothing on the launcher is an error. `GET /api/today` is the same data as JSON.

## The status page (`/status`)

| Section | From |
|---|---|
| **Rooms** and **Engines**, **Stack Services**: up, behind, starting, error, down, or *Not in this stack*; version and vendored vaultkit; a fact or two (notes, cards, pages, tools) | each app's own status: Kura `/api/status`, Konbini `/api/health`, Niwa `/api/status` (its published count), Shiori's hosted page (`/`; its version and build from `/_shiori/status.json`, else the assets' build hash; "AI on · N left today" from `/shiori/ai/status`, "AI down" when that isn't JSON; "feed ok" from `/shiori/healthz`; none of those makes Shiori down), Hister `/health` (+ its version from its MCP `initialize`, its page count and newest page, with `LANDING_HISTER_TOKEN_FILE` once Hister has users), SearXNG `/healthz` and `/config`, machiya-mcp and smallweb `/api/status`, vault-mirror's and feed-import's `status.json` (read-only mounts) |
| **Sync** | the vault's last pull (Kura; the mirror), the board's commit against the vault's head and its LiveSync cycle (Konbini), unpushed garden writes (Niwa), the notes' last push into Hister (Kura), Hister's newest page, and **Feeds read** (feed-import: its last good run, pages added in the last day from the page's own samples, failed runs) |
| **Recent Deploys** | version changes the page saw itself (below), with the changelog lines that came with them |

**Freshness.** A green dot is fine; a yellow dot is *behind* and stays calm; only *error* and *down* get red text and a red edge. Thresholds: the vault's pull is behind after 15 minutes and broken after 6 hours; Konbini's LiveSync cycle behind after 15 minutes, broken after 2 hours; the notes' push into Hister behind after an hour, broken after a day; Konbini's commit differing from Kura's for more than 15 minutes is behind; a writer with commits it hasn't pushed is behind; Hister's newest page is fine under three days old and behind after that, red only when Hister itself is down; feed-import is behind after an hour without a good run and broken when it says `ok: false`. An app that doesn't answer within 3 seconds is down.

## Standalone and polite

- **Every app is optional.** Only apps with an address are polled; the others show *Not in this stack*. The page needs no app, and no app needs the page (principle 4).
- **Server-side, cached.** A background thread polls every app in parallel every minute (a 3 second timeout, no redirects followed, at most 4 MB read), Today's reads included, and both pages are served from the last answers. The browser refreshes `<main>` every minute while a page is visible (never under a half-typed search).
- **GETs, and one POST.** Hister's version comes from its MCP `initialize` (`POST /mcp`, which changes nothing; [contracts/hister.md](../contracts/hister.md)), at most every 15 minutes. Everything else is a GET.
- **Hister** gets `Origin: hister://` on every GET (the MCP needs none); its newest-page search leaves the vault notes out.
- **Tokens.** None by default. `LANDING_TOKEN_FILE` (for rooms with an identity file) goes to Kura, Niwa and Konbini over https only; `LANDING_CHANGELOG_TOKEN_FILE` only to the changelog URLs.

## Recent deploys: where they come from

The page records a deploy when an app answers with a version it hasn't seen from that app before (and a changed vendored vaultkit), with the time it noticed, in one small JSON file (`LANDING_STATE`). That is what is really running, it works with any mix of apps, and it needs no forge, registry or deployment access. Losing the file loses only the list of past deploys. The trade-offs: the time is when the page noticed (within a minute of the restart), a deploy made while the page was down shows up at its next poll, and a redeploy of the same version is not a deploy.

What a version brought comes from the app itself (owner, 2026-10-04): every app serves its `CHANGELOG.md` at **`GET /api/changelog`** ([principle 7](../principles.md), `vaultkit.changelog`, [ui.md](../ui.md#the-changelog-endpoint-vaultkitchangelog-v018)), behind the same gate as its status, so no forge, registry or token is involved. The page asks Kura, Konbini, Niwa, machiya-mcp and smallweb every 15 minutes (with the `ETag`, so an unchanged file is a 304) and at once when an app answers with a version its copy doesn't have. A jump of several versions lists each one's first line. An app that answers 404, or anything but markdown, shows its versions only; `LANDING_CHANGELOGS` can point one elsewhere. The engines (Hister, SearXNG) and Shiori's hosted page have no endpoint, and vault-mirror and feed-import have no HTTP at all.

## Auth

Owner-only, like the rooms: `LANDING_AUTH=tailscale` (the default) admits only a `Tailscale-User-Login` in `LANDING_USERS`; `open` is for localhost (with a `Host` allow-list). With the identity file ([identity.md](../identity.md)) callers are principals and need `landing` `read` (the owner has it); `LANDING_AUTH=header` then trusts a proxy's login header. `/healthz` is open and carries no data; `/api/status` and `/api/changelog` are the owner's. `AUTH=hister` comes through vaultkit, as for the rooms.

## Deploying it beside the rooms

Add a `landing` service to the stack's compose: build context `stack/landing/` (or a published image), a non-root user, a small memory limit, a `/data` volume, and the default network only (like machiya-mcp: its calls to the rooms must leave through the host's own tailscaled, so Konbini's owner-only `/api/health` sees your login). Mount vault-mirror's volume read-only for `LANDING_MIRROR_STATUS` and feed-import's data volume read-only for `LANDING_FEED_STATUS`. Environment: `MACHIYA_ROOMS` (as the rooms, plus `machiya=https://machiya.example.ts.net`), `LANDING_APPS=machiya-mcp=https://…,smallweb=https://…`, `LANDING_SEARCH_URL=<Shiori's search page>`, `LANDING_USERS=<your Tailscale login>`, `LANDING_BIND_BEHIND_PROXY=1`. Then a Tailscale Service for it (owner-only grant first, then `serve.json` → `http://landing:8080`), and the probe on `/healthz`.

**Links to it.** Add `machiya=https://machiya.example.ts.net` to every room's `MACHIYA_ROOMS` (vaultkit 0.18; the row reads **Machiya · home** from 0.19.1): each Rooms menu then ends with it before Settings, and the footer's "Part of Machiya" links here. Status links (Shiori's) point at `/status`. Shiori's Status link is a build setting of its hosted pages.
