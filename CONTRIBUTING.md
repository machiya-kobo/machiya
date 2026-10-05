# Contributing to Machiya

Thanks for helping. This is the umbrella repository of [Machiya](README.md): the docs and contracts, **vaultkit** (the shared vault core), the shared web UI, **machiya-mcp** and its Claude Code plugin, and the reference compose and config. The services themselves (Kura, Niwa, Konbini, Shiori) have their own repositories with their own `CONTRIBUTING.md`. [CLAUDE.md](CLAUDE.md) is the short guide to the rules that matter here; read it before changing vaultkit, the contracts or the MCP server.

## Tests

Python 3.11 or later, plus `markdown` (3.11 or later: older versions can run out of memory on one note) and `pyyaml`. No build step.

```sh
python3 -m venv .venv && .venv/bin/pip install 'markdown>=3.11' pyyaml
.venv/bin/python -m unittest discover -s tests          # vaultkit, identity, sign-in, the dev stack's pieces
for s in mcp landing hister-login smallweb feed-import code-import; do
  ( cd stack/$s && ../../.venv/bin/python -m unittest discover -s tests ) || echo "FAILED: $s"
done
```

Add a test with every change. smallweb's tests also need `openssl`. `tests.test_vaultkit` also checks that a vendored copy matches its manifest (it creates a throwaway git repository for that, so it needs `git`).

`tests.test_private_names` fails when a name from the deployment Machiya grew up in (its hosts, tailnet, accounts, people) appears in any tracked text file. Use a neutral example instead (`example.ts.net`, `<host>`, `owner`, `you`, `your-org`, the dev seeds' `lantern`); its docstring explains the hashed list, the allow-list (only the copyright line today) and how to add a name. Never widen the allow-list to silence a real finding.

## The dev stack

To try a change against everything at once, use the dev stack ([docs/dev-stack.md](docs/dev-stack.md)): every service, Hister's users as the sign-in, synthetic data only. With the kura, niwa and konbini checkouts next to this one:

```sh
cd compose/dev && ./dev init && ./dev up && ./dev check       # ./dev reset --yes goes back to the seed
```

Never load real notes, pages or feeds into it; extend `compose/dev/seed/` instead. `tools/dev-test HOST` runs the same stack and checks on another machine; `tools/fleet-nightly` runs them and the Quickstarts on every test VM each night ([docs/dev-stack.md](docs/dev-stack.md#nightly)).

## Rules

- **vaultkit behaviour is load-bearing.** Three services change with it: a change to wikilink resolution, rendering, `read_notes` order, `description` or dates needs a test and a note in the commit. Wikilink resolution in particular must not change (see CLAUDE.md).
- **Standard library only** in vaultkit, plus `markdown` and `pyyaml`. No service-specific code in it.
- **Vendored copies** (`stack/mcp/vaultkit/`, `stack/landing/vaultkit/`, `stack/hister-login/vaultkit/`, and in the other repositories `app/vaultkit/`) are never edited in place; tag a release here (`vX.Y.Z`) and re-vendor with `./vendor.sh <consumer> vX.Y.Z`.
- **Contracts** (`docs/contracts/`, `docs/frontmatter.md`) are shared with other apps: readers change first (accept old and new), writers after. Propose a contract change in an issue before changing it.
- **Docs follow reality.** Change the docs in the same pull request as the behaviour.
- **Keep personal details out of the repository:** hostnames, network names, names, emails, tokens. Use `example.ts.net`, `you@example.com`.
- Match the surrounding code: its naming, comment density and idiom. Commit messages start with `machiya: ` (docs, compose, config) or `vaultkit: ` (the library).

## Sending a change

Open a pull request with what changed and why, and which tests you ran. Keep one change per pull request. By contributing, you agree that your work is licensed under the GNU AGPL-3.0-or-later, as the rest of Machiya.
