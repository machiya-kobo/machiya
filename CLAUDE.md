# CLAUDE.md: Machiya (the umbrella repo), a contributor guide

Machiya 町家 is a stack of small services around an Obsidian vault kept in git: **Kura** (the reader, search and JSON API), **Shiori** (the search front door: apps, Safari extension, web), **Niwa** (the published garden: web, gemini, gopher) and **Konbini** (the project board), with **Hister** (your pages and bookmarks) and **SearXNG** (the web) as engines. Each service lives in its own repository and runs on its own. This repo holds:

- the docs (`docs/`: architecture with mermaid diagrams, principles, services, API contracts, the frontmatter schema, install guides)
- **vaultkit** (`vaultkit/`, the shared vault core), the shared UI (`ui/`), and **machiya-mcp** (`stack/mcp/`, the MCP server) with its Claude Code plugin (`plugins/machiya/`)
- a reference compose (`compose/`), reference Hister and SearXNG config (`config/`), and a sample vault

## Rules

- **Docs follow reality.** When a service, contract or name changes, update `docs/` in the same change. The diagrams are mermaid in markdown.
- **Config here is reference.** Copy it and adapt it; nothing here deploys itself.
- **vaultkit behaviour is load-bearing.** Three services change with it, so anything that changes its output needs a test and a note in the commit. That covers wikilink resolution, rendering, `read_notes` order, `description` and dates.
- **Wikilink resolution must not change.** The lowercased target, without `.md`, is looked up among lowercased basenames and full slugs, then by its last segment; the first note in `os.walk` order wins a name.
- vaultkit uses the standard library, `markdown` and `pyyaml` only. No service-specific code goes in it: board columns, garden checks and HTML shells stay in the services.
- **Secrets.** `Mirror` takes a token value (read it with `read_secret(path)` from a file). Never put a token in a URL, argv, a log line or `.git/config`.
- **Releases.** Tag `vX.Y.Z` (the umbrella and vaultkit share one version line), then re-vendor into each consumer (`tools/vendor-vaultkit vX.Y.Z` there). Never edit a vendored copy in a consumer; its build fails on purpose.
- **Shared rules** (`docs/principles.md`, `docs/design.md`): each service runs on its own unless a dependency is truly hard; metadata goes in frontmatter; modern HTML only; the owner-only gates; Hister's rules (`Origin: hister://`, never `hister index --force` a known URL).
- **Metadata goes in frontmatter**, never in separate notes or a service database (`docs/frontmatter.md`).

## Tests

```
python3 -m venv .venv && .venv/bin/pip install 'markdown>=3.7' pyyaml
.venv/bin/python -m unittest tests.test_vaultkit
(cd stack/mcp && ../../.venv/bin/python -m unittest discover -s tests)
```

## Commits

`machiya: …` for docs, compose and config; `vaultkit: …` for the library. Contributions are under AGPL-3.0-or-later (see `LICENSE`).
