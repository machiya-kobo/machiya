# Changelog: machiya-landing

## 0.1.0

- The stack's front door: a page that links every room (Shiori, Konbini, Niwa, Kura), the engines (Hister, SearXNG) and the stack's services (machiya-mcp, smallweb, vault-mirror), in the rooms' look (vaultkit 0.17.2's shell, tab bar and Rooms menu; both themes, every palette).
- **Status:** each app's state (up, behind, starting, error, down, or "Not in this stack" when it has no address), its version and vendored vaultkit, polled from its own status endpoint in the background (GETs only, 3 s timeout, every 60 s); `/api/status` is the same as JSON.
- **Sync:** the vault's last pull (Kura, the mirror's `status.json`), the board's commit and LiveSync cycle (Konbini), the garden's unpushed writes (Niwa), the notes' last push into Hister (Kura) and Hister's newest page. Behind is a calm yellow dot; only broken is drawn as an alert.
- **Recent deploys:** version changes the page saw itself, kept in `LANDING_STATE`, with what each version brought from the app's `CHANGELOG.md` (`LANDING_CHANGELOGS`, optional).
- Owner-only: `LANDING_AUTH=tailscale` with `LANDING_USERS`, or `open` on localhost with a Host allow-list.
