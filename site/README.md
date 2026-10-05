# site/: Machiya's web page

One static page for GitHub Pages: plain HTML and CSS, no build step, no scripts, no web fonts, no tracking, nothing loaded from other sites. Every path in it is relative, so it works at the root of a domain and under a sub-path such as `/machiya/`.

| File | What |
|---|---|
| `index.html` | the page |
| `style.css` | its styles: the Tokyo Night and Tokyo Night Day tokens from [`ui/machiya.css`](../ui/machiya.css), light or dark from `prefers-color-scheme` |
| `icons/` | copies of the Machiya and room icons from [`icons/`](../icons/) |
| `img/` | copies of four screenshots from [`docs/screenshots/`](../docs/screenshots/) |
| `favicon.ico` | a copy of `icons/png/favicon.ico` |

The icons and screenshots are copies: when the originals change, copy them again.

## Preview it

```bash
python3 -m http.server -d site 8000
```

then open http://localhost:8000/.

## Rules for changes

- Say only what the READMEs and `docs/` say; link to them rather than copying commands that could drift (the quickstart lives in the [README](../README.md#quickstart)).
- Text is at least 4.5:1 against its background in both themes. In the light theme, text sits on `--bg` or `--surface` only.
- Phone first: no horizontal scroll at 320px wide.
- No deployment hostnames or account names.

## Publishing it on GitHub Pages

[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) deploys `site/` on every push to `main` that changes it. It does nothing until it is enabled. To switch it on, once the repository is public:

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions → Variables → New repository variable:** `PAGES_ENABLED` = `true`.
3. **Actions → pages → Run workflow** (on `main`) for the first deploy. Later pushes to `site/` deploy on their own.
4. Open the address shown in the run's summary (or in Settings → Pages) and check it.

To stop publishing, set `PAGES_ENABLED` to anything else (the site stays up until it is unpublished in Settings → Pages).

**The address.** A Pages site built from this repository is served at `https://machiya-kobo.github.io/machiya/`. To serve it at `https://machiya-kobo.github.io/` itself, the organisation needs a repository named `machiya-kobo.github.io` (its own Pages site, which could hold a copy of `site/` or redirect to `/machiya/`), or the site needs a custom domain (Settings → Pages → Custom domain). The page works at either address.
