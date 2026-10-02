# vaultkit

The shared core of Machiya's vault services, in `vaultkit/` of this repo. Each service is standalone (its own repo, stack, copy of the vault and state), and all of them need the same vault code:

| Module | What |
|---|---|
| `front.py` | frontmatter (`note_front`, `tags_of`), git conflict markers, conflict copies written by a sync tool (Obsidian LiveSync), `_str`, `_unlink` |
| `notes.py` | `read_notes(root)`, `Note` (title, tags, published, stage, type, confidence, summary), `relative()` dates |
| `vault.py` | `Vault`: the index (notes, wikilink names, images, links, backlinks, last-changed dates from git) and the Markdown renderer (`garden` and `all`/`kura` link modes; a `retro` flag for HTML 3.2 tables, unused) |
| `frontmatter.py` | `edit_front` (set/remove top-level keys or the tags block, touching nothing else), `merge_note` (three-way frontmatter merge), `version_of`, `EditError` |
| `gitsync.py` | `GitSync`: a read-write clone a service commits to in batches (author, paths, an events log with union merge), pull with rebase and a file-by-file replay on conflict (never commits conflict markers), push |
| `git.py` | `Git` (a runner) and `Mirror` (a read-only clone of an https/ssh/file remote kept up to date; a token travels as a header in git's environment, never in argv or `.git/config`) |
| `verify.py` | drift check for a vendored copy |

Consumers: **Konbini** (the board), **Kura** (every note, search, API) and **Niwa** (the published garden). The principles are in [principles.md](principles.md).

## Vendored, not installed

Services copy the package in at a tag, so their builds need no network and no private-repo access:

```sh
./vendor.sh <service>/app v0.1.0     # from this repo's root     # -> <service>/app/vaultkit/ + VENDORED (version + sha256 per file)
python3 -m vaultkit.verify           # in the service: fails if the copy was edited in place
```

Each service's Dockerfile runs `python3 -m vaultkit.verify`, so an edited copy fails the build. To fix something, change it here, add a test, tag it, and re-vendor it into every consumer.

## Using it

```python
from vaultkit import Vault, Mirror, read_secret

m = Mirror("https://forgejo.example/owner/obsidian.git", "/data/repo", token=read_secret("/secrets/token"))
head, changed = m.update()          # clone, or fetch + hard reset
v = Vault("/data/repo", "personal")
v.revision = head                   # the index rebuilds when this changes
v.index()
html = v.render(v.get("Projects/Kura"), "", mode="all")
```

Konbini subclasses `Vault` to plug in its own cache: `key()` returns the board's HEAD + revision, and `source()` returns its timeline's note list.

## Tests

`python3 -m unittest discover -s tests`, run in any service image (it needs `markdown` and `pyyaml`, which a bare host may lack):

```sh
docker run --rm --user 1000:1000 -e HOME=/tmp -v "$PWD":/v -w /v --entrypoint python3 <registry>/konbini:<tag> -m unittest discover -s tests
```
