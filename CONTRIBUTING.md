# Contributing to Machiya

Thanks for helping. This is the umbrella repository of [Machiya](README.md): the docs and contracts, **vaultkit** (the shared vault core), the shared web UI, **machiya-mcp** and its Claude Code plugin, and the reference compose and config. The services themselves (Kura, Niwa, Konbini, Shiori) have their own repositories with their own `CONTRIBUTING.md`. [CLAUDE.md](CLAUDE.md) is the short guide to the rules that matter here; read it before changing vaultkit, the contracts or the MCP server.

## Tests

Python 3.11 or later, plus `markdown` (3.7 or later) and `pyyaml`. No build step.

```sh
python3 -m venv .venv && .venv/bin/pip install 'markdown>=3.7' pyyaml
.venv/bin/python -m unittest tests.test_vaultkit
( cd stack/mcp && ../../.venv/bin/python -m unittest discover -s tests )
( cd stack/smallweb && ../../.venv/bin/python -m unittest discover -s tests )
```

Add a test with every change. `tests.test_vaultkit` also checks that a vendored copy matches its manifest (it creates a throwaway git repository for that, so it needs `git`).

## Rules

- **vaultkit behaviour is load-bearing.** Three services change with it: a change to wikilink resolution, rendering, `read_notes` order, `description` or dates needs a test and a note in the commit. Wikilink resolution in particular must not change (see CLAUDE.md).
- **Standard library only** in vaultkit, plus `markdown` and `pyyaml`. No service-specific code in it.
- **Vendored copies** (`stack/mcp/vaultkit/`, and in the other repositories `app/vaultkit/`) are never edited in place; tag a release here (`vX.Y.Z`) and re-vendor with `vendor.sh`.
- **Contracts** (`docs/contracts/`, `docs/frontmatter.md`) are shared with other apps: readers change first (accept old and new), writers after. Propose a contract change in an issue before changing it.
- **Docs follow reality.** Change the docs in the same pull request as the behaviour.
- **Keep personal details out of the repository:** hostnames, network names, names, emails, tokens. Use `example.ts.net`, `you@example.com`.
- Match the surrounding code: its naming, comment density and idiom. Commit messages start with `machiya: ` (docs, compose, config) or `vaultkit: ` (the library).

## Sending a change

Open a pull request with what changed and why, and which tests you ran. Keep one change per pull request. By contributing, you agree that your work is licensed under the GNU AGPL-3.0-or-later, as the rest of Machiya.
