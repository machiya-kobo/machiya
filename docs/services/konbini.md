# Konbini コンビニ (the shop)

**The project board, built from the vault's project notes, open all hours.** A card is a note in Konbini's index: cards live in `Projects/` or carry a board `status:` value (`backlog` to `archived`). Notes tagged `type/project` without one land in the Unsorted tray. The notes are the source of truth. SQLite is a cache that a rebuild recreates from the clone, and git is the backup.

- **URL:** `https://konbini.example.ts.net` (board)
- **Repo:** [machiya-kobo/konbini](https://github.com/machiya-kobo/konbini) (app in `app/`, vendors vaultkit)
- **CLI:** `pm` (`pm ls`, `pm show`, `pm move`, `pm next`, `pm claim`, `pm log`, `pm new`)

## What it does

- Board columns: backlog, ready, WIP (limit 3), blocked, done, archived.
- Per card: priority, rank, effort, area lanes, topics, machines, checklists, next action, blockers, and agent claims (15 min).
- Board edits go into frontmatter lines only. Konbini commits them under a configurable author in batches after about 2 minutes idle, then pushes. Events go to `.board/events/*.jsonl` in the vault repo.
- Calendar, roundups (day, week, month, year), the stream, writing kits and post tracking for the blog.
- Link rot for links in card notes: Wayback, an optional cold archive, and Hister copies (owner-only, modern pages only). Niwa checks published notes itself.
- The health of an optional vault sync bridge appears in "Needs a look".

## API

See [contracts/konbini-api.md](../contracts/konbini-api.md). `/api/cards` is the one other services read: Shiori reads it, and so can a dashboard.

## Scope

Konbini is the board: the reader and search belong to [Kura](kura.md) and the published garden to [Niwa](niwa.md). `/garden/*` redirects to Niwa, writing kits link Niwa directly, and card pages show the project and link the note to Kura (read) and Obsidian (edit) instead of rendering it.
