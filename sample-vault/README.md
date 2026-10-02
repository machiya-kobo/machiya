# Sample vault

A small, fictional vault for running Machiya without real notes: a paper-lantern workshop and a planned trip to Kyoto. It is what the Quickstarts and the screenshots use, and it exercises every feature of the stack.

The vault folder is `personal/`, so set `*_REPO_SUBDIR=personal` (the reference compose does, through `VAULT_SUBDIR`); the services' own default is the repository root. To serve it, make it a git repository: `tools/demo-vault <dir>` copies it into `<dir>` and commits it (a bare clone of that is what `VAULT_REPO_URL=file:///…` points at); by hand: `git init -b main && git add -A && git commit -m sample`.

| Folder | What's in it |
|---|---|
| `Notes/` | 12 notes: published and unpublished, three growth stages (`seedling`, `budding`, `evergreen`), tables, callouts, task lists, code, backlinks. `Paper lanterns` is unpublished (Niwa shows links to it as plain text, Kura links it); search terms "bamboo" and "chochin" |
| `Journal/` | two dated entries |
| `MOC/` | `Crafts` and `Travel`, published maps of content |
| `Projects/` | 10 cards: every board column (`backlog`, `ready`, `wip`, `blocked`, `done`, `archived`), `priority`, `next`, `waiting`, `dependsOn`, `due`, three streams (Lanterns, Kyoto 2027, Workshop) and two goals; `Lantern` is published in Niwa |
| `Inbox/` | one fresh capture |
| `Templates/` | never indexed or served |

Everything in it is invented. Keep it that way: no real names, places you live, hosts or accounts.
