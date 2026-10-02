# Frontmatter

**Metadata goes in frontmatter** ([principles](principles.md) #10). This page is the schema every Machiya service reads and writes.

The conventions follow what Obsidian's own tools use (TaskNotes, Tasks, Bases, Dataview), so the vault stays usable inside Obsidian through **Bases** (including its Kanban view), **Dataview** and **TaskNotes**. Readers still accept the legacy names as a fallback (see "Legacy names").

## Conventions

- Single lowercase words where possible, camelCase for compound names (`dependsOn`, `completedDate`), never snake_case in new fields.
- Dates are bare `YYYY-MM-DD`. Links are quoted `"[[Note]]"`, in lists where there can be several.
- Obsidian's core properties keep their meaning: `tags`, `aliases`, `cssclasses`, plus `publish` and `description` for Publish-style previews.

## Fields

| Field | Values | Written by | Read by | Legacy name |
|---|---|---|---|---|
| `title` | text | you | all | |
| `created` | YYYY-MM-DD | you, templates, Konbini (stubs) | Kura, Niwa ("planted"), Konbini | `date` |
| `tags` | list | you, Konbini (board-managed `status/*` removed) | all | |
| `summary` | text | you, Konbini | all | |
| `status` | `backlog` · `ready` · `wip` · `blocked` · `done` · `archived` | Konbini (drag, pm) and Bases' Kanban view | Konbini, Niwa (stage rule), Kura, Bases, TaskNotes (custom statuses; `done`/`archived` count as completed) | `board` + `status/*` tags |
| `priority` | `high` · `normal` · `low` | Konbini, pm | Konbini, Bases, TaskNotes | `1` · `2` · `3` |
| `next` | text: the next concrete action | Konbini, pm | Konbini | |
| `waiting` | text: waiting on something outside the vault (hardware, a person) | Konbini, pm | Konbini (counts as blocked) | `blocked_by` |
| `dependsOn` | list of `"[[Project]]"` | you, Konbini | Konbini (derives "waiting on" / "unblocks"; a card is blocked while any dependency isn't done), Dataview, Bases | |
| `stream` | `"[[Example Stream]]"` or text, one per card | you, Konbini | Konbini (stream views) | new |
| `goal` | text or `"[[…]]"`, one per card | you, Konbini | Konbini (goal roll-up) | |
| `due` | YYYY-MM-DD | you, Konbini | Konbini (timeline; a goal's target is its cards' latest `due`), Bases, TaskNotes, Dataview | |
| `started` | YYYY-MM-DD (actually started) | Konbini (first move to WIP), you | Konbini | |
| `completedDate` | YYYY-MM-DD | Konbini (move to done) | Konbini, TaskNotes, Bases | |
| `rank` | number (order within a column) | Konbini | Konbini | |
| `project` | slug (the card's id) | you, Konbini | Konbini, pm, Shiori (card links) | |
| `publish`, `growth`, `confidence`, `garden_pin` | garden fields | Niwa (owner only) | Niwa, Kura | |

`garden_pin` is the one snake_case field.

**Dependencies:** `dependsOn` is a flat list of links (the Tasks name, in frontmatter). TaskNotes' own `blockedBy` uses objects (`{uid, reltype}`), so TaskNotes won't read these dependencies. That trade-off is deliberate, for readability and Bases.

## Notes that aren't cards

Every note has a `status:` field, and `status/*` tags are not used. Cards use `backlog` · `ready` · `wip` · `blocked` · `done` · `archived`; other notes use `draft` · `active` · `archive`. Niwa's stage rule (draft → seedling) reads `status: draft`. Konbini tells cards from other notes: a card is a note in its index (cards live in `Projects/` or carry a board status value).

## Legacy names

Readers (Kura, Niwa and Konbini through vaultkit) accept both the current and the legacy names, so older notes, templates and sync tools keep working. Writers use the current names:

- `board:` → `status:` on cards; `status/*` tags → `status:` on every other note
- `date:` → `created:`
- `priority: 1/2/3` → `high/normal/low`
- `blocked_by:` → `waiting:`
