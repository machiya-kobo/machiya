# Shiori (the bookmark)

**The search front door.** Shiori searches three sources at once: your pages (Nogon), your notes ([Kura](https://github.com/machiya-kobo/kura)) and the web (SearXNG). It comes as:
- a native app for iPhone, iPad and Mac;
- a Safari extension;
- a hosted search page and a PWA;
- a Linux app.

To capture pages in other browsers, use Nogon's own extension.

- **Repo:** [machiya-kobo/shiori](https://github.com/machiya-kobo/shiori), built on a Mac
- **Hosted:** `https://search.example.ts.net` (the search page) and `https://shiori.example.ts.net` (the PWA). Only you can reach them. A small web server (nginx) next to Nogon serves them, built from the Shiori repo's sources.

## Features, briefly

- **Library:** everything in Nogon, newest first, split into All, Pages and Notes.
- **Search scopes:** All (your top pages, your top notes, then the web), Nogon, Notes and Web. Laid out like SearXNG's results: info box, related searches, images, videos, news.
- **Search from Safari:** keep DuckDuckGo as Safari's engine, and address-bar searches open Shiori's combined results.
- **A note** opens in Obsidian to edit (`obsidian://open?vault=personal&file=…`), or in Kura to read. Its #tags link to Kura's tag pages.
- **Remember What You Open:** opened results go into Nogon history by URL.
- **Summaries and answers:** Shiori's AI features, Claude on request; on the hosted pages through [shiori-ai](shiori-ai.md). Notes are always refused, by host (`kura.`, `konbini.`, `niwa.`) and by label `vault`.
- **Settings are per device.**
- **The notes' homes** are set when the hosted pages are built: Kura's address in `SHIORI_NIWA_URL` (the name is older than Kura), [Konbini](https://github.com/machiya-kobo/konbini)'s in `SHIORI_KONBINI_URL`.
- **RSS for any Nogon query:** `/shiori/feed`, from [shiori-feed](shiori-feed.md), next to Nogon.

## How it talks to the rest

| To | How |
|---|---|
| Nogon | apps: directly; hosted pages: same origin. Always `Origin: nogon://` (1.30.0; `hister://` before, and still to smallweb until 0.6.0 is live). |
| Kura | apps: `kura.*` directly; hosted pages: `/kura/` route on the hosting web server, for Kura's API only (`api/search`, `api/recent`, `api/note`, `api/vaults`, `feed.xml`), never its reader; the extension needs a host permission. |
| SearXNG | apps: directly; hosted pages: `/searx/` route. JSON + the image-proxy plugin. |
| Konbini | `/api/cards` (slug ↔ path) for notes met through Nogon. |

## Rules

- No folder, tag or backlink browsing in Shiori. Kura owns that.
- No new network endpoints without review.
- The hosting web server never adds or rewrites `Origin`. Nogon's `Sec-Fetch-Site` check is the CSRF guard.
- Shiori reads notes from Kura and appends ` -label:vault` to its Nogon queries. The notes themselves stay in Nogon.
