# CLAUDE.md: Machiya (the umbrella repo)

Machiya 町家 is a set of small self-hosted apps around an Obsidian vault kept in git: **Shiori** (search: apps, Safari extension, web), **Kura** (notes: reader, search, JSON API), **Niwa** (the published garden: web, Gemini, Gopher) and **Konbini** (the project board), with **Hister** (your pages) and **SearXNG** (the web) as engines. Each app has its own repository and runs on its own. This one holds what they share.

## Where things are

| Path | What |
|---|---|
| `vaultkit/` | the shared vault core (Python): reading notes, wikilinks, rendering, sign-in, the page shell and the palettes |
| `ui/` | the shared stylesheet and script (`machiya.css`, `machiya.js`); the style guide is `docs/style-guide.md` |
| `stack/` | small services with their own tests: `mcp` (the MCP server), `landing`, `hister-login`, `smallweb`, `feed-import`, `code-import`, `vault-mirror`, `shiori-feed`, `shiori-ai` |
| `plugins/machiya/` | the Claude Code plugin for the MCP server |
| `docs/` | architecture, principles, services, API contracts (`docs/contracts/`), the frontmatter schema, install guides |
| `compose/` | the reference stack (`compose.yml`) and a dev stack with dummy data (`compose/dev/`) |
| `sample-vault/` | the demo vault every test and screenshot uses |
| `site/`, `org/` | the website and the GitHub organization's pages |
| `tools/` | test and release helpers (`dev-test`, `quickstart-test`, `demo-vault`) |

## Tests

```
python3 -m venv .venv && .venv/bin/pip install 'markdown>=3.7' pyyaml
.venv/bin/python -m unittest tests.test_vaultkit
for d in stack/*/; do (cd "$d" && ../../.venv/bin/python -m unittest discover -s tests) || echo "FAILED: $d"; done
```

Python 3.11 or later. `tests/test_private_names.py` is skipped unless you have the maintainers' list of names; that's expected.

## Rules

- **vaultkit's output is load-bearing.** Kura, Niwa and Konbini vendor it, so a change to wikilink resolution, rendering, `read_notes` order, `description` or dates needs a test and a note in the commit.
- **Wikilink resolution must not change.** The lowercased target, without `.md`, is looked up among lowercased basenames and full slugs, then by its last segment; the first note in `os.walk` order wins a name.
- vaultkit uses the standard library, `markdown` and `pyyaml` only. App-specific code (board columns, garden checks, page layouts) stays in the apps.
- **The palettes are generated.** Edit the table in `vaultkit/palettes.py`, never the block between the `palettes: begin`/`end` markers in `ui/machiya.css`; `python3 -m vaultkit.palettes` prints it. Every text must stay at 4.5:1 contrast in all ten themes, dark and light; the tests check.
- **Docs follow the code.** When a service, contract or name changes, update `docs/` in the same change. Diagrams are mermaid in markdown.
- **Metadata goes in frontmatter** (`docs/frontmatter.md`), never in separate notes or a database.
- **Secrets are read from files** (`*_FILE` settings, `read_secret(path)`). Never put a token in a URL, a command line, a log line or `.git/config`.
- **No personal details in the repository**: no hostnames, network or tailnet names, people's names, emails, vault names, tokens or anyone's own settings. Code, tests, docs and commit messages use `example.com`, `example.ts.net` and the sample vault.
- **Copy and docs** are American English, in the voice of `docs/voice.md`.
- **Config here is reference.** `compose/` and `config/` are examples to copy and adapt; nothing here deploys itself.

## Commits and releases

Commit messages start with the area: `machiya: …` (docs, compose, config, site), `vaultkit: …` (the library and `ui/`), or the service's name (`mcp: …`, `landing: …`). vaultkit and the umbrella share one version line (`vX.Y.Z`); an app picks up a new vaultkit by vendoring a tag (`tools/vendor-vaultkit vX.Y.Z` in that app). Never edit a vendored copy in an app; its tests fail on purpose. Contributions are under AGPL-3.0-or-later (`LICENSE`); see `CONTRIBUTING.md`.
