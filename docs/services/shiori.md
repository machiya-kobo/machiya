# Shiori 栞 (the bookmark)

**The search front door.** It's a native app for iPhone, iPad and Mac, plus a Safari extension, a hosted search page, a PWA and a Linux app. (For capturing pages in other browsers, use Hister's own extension.) It searches three sources at once: your pages (Hister), your notes (Kura), and the web (SearXNG).

- **Repo:** [machiya-kobo/shiori](https://github.com/machiya-kobo/shiori), built on a Mac
- **Hosted:** `https://search.example.ts.net` (the search page) and `https://shiori.example.ts.net` (the PWA). Both are owner-only, served by a small web server (nginx) next to Hister; the pages are built from the Shiori repo's sources.

## Features, briefly

- **Library:** everything in Hister, newest first, split into All, Pages and Notes.
- **Search scopes:** All (your top pages, your top notes, then the web), Hister, Notes and Web, laid out like SearXNG's results (info box, related searches, images, videos, news).
- **Search from Safari:** keep DuckDuckGo as Safari's engine; address-bar searches open Shiori's combined results.
- **A note** opens in Obsidian to edit (`obsidian://open?vault=personal&file=…`), or in Kura to read. Its #tags link to Kura's tag pages.
- **Remember What You Open:** opened results go into Hister history by URL.
- **Summaries and answers** (Shiori's AI features, Claude on request). Notes are always refused, by host (`kura.`, `konbini.`, `niwa.`) and by label `vault`.
- **Settings are per device.** The hosted pages get the notes' homes when they are built: Kura's address in `SHIORI_NIWA_URL` (the name is older than Kura) and Konbini's in `SHIORI_KONBINI_URL`. RSS for any Hister query: `/shiori/feed` (a small feed service next to Hister; not part of this repository).

## How it talks to the rest

| To | How |
|---|---|
| Hister | apps: directly; hosted pages: same origin. Always `Origin: hister://`. |
| Kura | apps: `kura.*` directly; hosted pages: `/kura/` route on the hosting web server, for Kura's API only (`api/search`, `api/recent`, `api/note`, `api/vaults`, `feed.xml`), never its reader; the extension needs a host permission. |
| SearXNG | apps: directly; hosted pages: `/searx/` route. JSON + the image-proxy plugin. |
| Konbini | `/api/cards` (slug ↔ path) for notes met through Hister. |

## Rules

- No folder, tag or backlink browsing in Shiori. Kura owns that.
- No new network endpoints are added without review.
- The hosting web server never adds or rewrites `Origin`: Hister's `Sec-Fetch-Site` check is the CSRF guard.
- Shiori reads notes from Kura and appends ` -label:vault` to its Hister queries. The notes themselves stay in Hister.
