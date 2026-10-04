# Changelog: machiya-landing

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
