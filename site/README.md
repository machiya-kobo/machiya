# site/: Machiya's web page

One static page for GitHub Pages: plain HTML and CSS, no build step, no scripts, no web fonts, no tracking, nothing loaded from other sites. Every path in it is relative, so it works at the root of a domain and under a sub-path such as `/machiya/`.

| File | What |
|---|---|
| `index.html` | the page |
| `style.css` | its styles: the Tokyo Night and Tokyo Night Day tokens from [`ui/machiya.css`](../ui/machiya.css), light or dark from `prefers-color-scheme` |
| `icons/` | copies of the Machiya and room icons from [`icons/`](../icons/) |
| `img/` | the page's screenshots, WebP, each in a light and a dark version (`<name>-light.webp`, `<name>-dark.webp`), picked by `prefers-color-scheme`; the hero has a phone version for screens up to 640px wide |
| `favicon.ico` | a copy of `icons/png/favicon.ico` |

The icons are copies: when the originals change, copy them again. The screenshots were taken from the [dev stack](../docs/dev-stack.md) (synthetic data only) with Playwright and Chromium: Shiori's web page and web app (built with Shiori's `scripts/build-web.sh` and `build-pwa.sh`, served by its `web/dev-server.py`) against the dev stack's Hister, Kura and code results, with invented web results from the stand-in SearXNG in Shiori's `tools/screenshots`; Kura and Niwa directly. Desktop shots are 1280px wide at 2x (hero) or 1.5x, phone shots 390px at 2x. Before adding one, check it shows no real host, account or note.

## Preview it

```bash
python3 -m http.server -d site 8000
```

then open http://localhost:8000/.

## Rules for changes

- Say only what the READMEs and `docs/` say; link to them rather than copying commands that could drift (the quickstart lives in the [README](../README.md#quickstart)).
- Text is at least 4.5:1 against its background in both themes. In the light theme, text sits on `--bg` or `--surface` only.
- Phone first: no horizontal scroll at 320px wide.
- Write like a person: short concrete sentences about what it does, no internals above the footer. Docs and repositories go in the footer; the page has one Install button.
- No deployment hostnames or account names.

## Publishing it on GitHub Pages

[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) deploys `site/` on every push to `main` that changes it. It does nothing until it is enabled. To switch it on, once the repository is public:

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions → Variables → New repository variable:** `PAGES_ENABLED` = `true`.
3. **Actions → pages → Run workflow** (on `main`) for the first deploy. Later pushes to `site/` deploy on their own.
4. Open the address shown in the run's summary (or in Settings → Pages) and check it.

To stop publishing, set `PAGES_ENABLED` to anything else (the site stays up until it is unpublished in Settings → Pages).

**The address.** A Pages site built from this repository is served at `https://machiya-kobo.github.io/machiya/`. To serve it at `https://machiya-kobo.github.io/` itself, the organisation needs a repository named `machiya-kobo.github.io` (its own Pages site, which could hold a copy of `site/` or redirect to `/machiya/`), or the site needs a custom domain (Settings → Pages → Custom domain). The page works at either address.
