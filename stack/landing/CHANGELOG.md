# Changelog: machiya-landing

## 0.2.2

- The image carries `today.py`: 0.2.1's Dockerfile listed the modules by name and missed it, so the container crashed at start. It now copies every module, and a test checks the Dockerfile against the files.

## 0.2.1

- **More stats on `/status`** (the owner, through shiori): Konbini shows **N in WIP** beside its card count (from Konbini's per-board counts in `/api/health` when it has them, else counted from `/api/cards`); Kura shows **N vaults · N notes** across every vault from `GET /api/vaults` (counts only: a private vault's name never leaves the probe), and the launcher's Kura tile counts all of them too; Shiori shows **web searches** today, this month and this year ("312 searches today · 4,210 this month · 51k this year") from a counts file the deployment writes (`LANDING_SEARCH_COUNTS`; every SearXNG search; left out quietly when the file is missing). Shiori's `status.json` `hister` field (the Hister it was built against) isn't shown: Hister's own row shows the running one.

## 0.2.0

- **`/` is a launcher now** (the owner's pick, "Launcher + Today"): the day and a status pill ("All up", "1 needs a look"; it links the status page), a **Search everything** pill that sends the query to Shiori's search page (`LANDING_SEARCH_URL`, another origin: a plain form, no live results; the page's CSP allows that one form target), the four rooms with one count each (pages, cards in WIP, published, notes), and **Today**: Working On (Konbini's WIP cards with their next step), Due Soon (cards due in the next 14 days, or overdue), Notes Changed (Kura's newest), Saved & Read (Hister's newest pages, a feed reader's reads and stars marked), Garden (Niwa's tended notes of the last 30 days). Each section leaves itself out when its room is missing or refuses. `GET /api/today` is the same as JSON.
- **The status page moves to `/status`.** Niwa shows its published count only; Hister its version (from its MCP `initialize`, the one POST this page sends, with the owner's token when set, at most every 15 minutes); vault-mirror its version (0.1.1's `status.json`); Shiori its version, build, AI ("AI on · N left today", "AI down") and feed health from `/_shiori/status.json`, `/shiori/ai/status` and `/shiori/healthz` (the build stamp when the file isn't there yet), with no vaultkit line.
- **feed-import** joins the stack services, from its `status.json` (`LANDING_FEED_STATUS`), and Sync gets **Feeds read**: the last good run, pages added in the last day (from the page's own samples), failed runs. **Pages indexed** is judged now: fine while Hister is up and its newest page is under three days old, behind after that, red only when Hister is down.
- vaultkit after v0.19.0 (the Rooms menu's house row reads "Machiya · home"); re-vendor at the release tag.

## 0.1.2

- Hister shows Up again: its `/health` answers 200 with an empty body, which 0.1.1 read as "not healthy". Any 2xx from `/health` now means up; the test's fake Hister answers the same way.

## 0.1.1

- **Hister with users** (phase 1 of the Hister sign-in): Hister is up or down by its open `GET /health` now, not `/api/stats`, which answers 403 once Hister's user handling is on. The page count and the newest page are best effort: with **`LANDING_HISTER_TOKEN_FILE`** (the owner's token, `X-Access-Token`, to Hister only; re-read when the file changes; a set file that is missing or empty stops the start) they keep showing; without it (or with a refused token) Hister is still up and the card says the count needs the token. Every Hister call, `/health` included, sends `Origin: hister://`; the token is never logged or in the page's JSON.

## 0.1.0

- The stack's front door: a page that links every room (Shiori, Konbini, Niwa, Kura), the engines (Hister, SearXNG) and the stack's services (machiya-mcp, smallweb, vault-mirror), in the rooms' look (vaultkit 0.17.2's shell, tab bar and Rooms menu; both themes, every palette).
- **Status:** each app's state (up, behind, starting, error, down, or "Not in this stack" when it has no address), its version and vendored vaultkit, polled from its own status endpoint in the background (GETs only, 3 s timeout, every 60 s); `/api/status` is the same as JSON.
- **Sync:** the vault's last pull (Kura, the mirror's `status.json`), the board's commit and LiveSync cycle (Konbini), the garden's unpushed writes (Niwa), the notes' last push into Hister (Kura) and Hister's newest page. Behind is a calm yellow dot; only broken is drawn as an alert.
- **Recent deploys:** version changes the page saw itself, kept in `LANDING_STATE`, with what each version brought from the app's own `GET /api/changelog` (its `CHANGELOG.md`; `LANDING_CHANGELOGS` overrides an address). An app that doesn't serve one shows its versions only.
- Owner-only: `LANDING_AUTH=tailscale` with `LANDING_USERS`, or `open` on localhost with a Host allow-list; with `MACHIYA_IDENTITY_FILE`, the `landing` `read` grant.
- `GET /api/changelog`: this page's own changelog. vaultkit after v0.17.2, untagged (the house row and footer link, the `landing` room, `vaultkit.changelog`); re-vendor at the release tag.
