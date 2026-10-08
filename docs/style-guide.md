# Style guide

How a Machiya app looks, taken from Shiori, the front door. Every app shares these pieces and keeps its own character. [design.md](design.md) has the house (themes, room colours, header, tab bar, settings), and [ui.md](ui.md) has the wiring. This page covers the parts you see in a list: pills, chips, tinted items and cards. Since vaultkit/ui v0.26, `ui/machiya.css` has all of them.

## The rules

1. **Colour means where a thing comes from.** Notes are Kura orange, cards Konbini magenta, the published garden Niwa green, your pages and the house blue, and the small web teal. A hue that already has a meaning is never reused for something else. A new kind of thing takes a neutral, or needs a design decision.
2. **Outlined means you can open it, filled means it's a state.** A pill or link chip is drawn in its colour's outline; the selected pill is filled. A state chip (a stage, a status, a count) is a soft fill.
3. **Tint only what's yours, in a mixed list.** The default card is plain. A tinted card says "this one is yours, from that room" among other things, as Shiori's All tab mixes your notes and pages into the web's results.
4. **Every text stays at 4.5:1**, in all ten themes, dark and light, on every surface it's drawn on. The tests check the shared components; anything new gets a check too.
5. **Title Case labels, short words, one field first.** As in [voice.md](voice.md).

## Filter pills

The row of choices above a list: Shiori's All · Pages · Notes · Web tabs, Niwa's all · notes · projects · maps, Konbini's lanes, Kura's vaults, genkan's states.

```html
<nav class="pills" data-fade="end">
  <a class="pill" aria-current="page">All <span class="count">9</span></a>
  <a class="pill" style="--pill: var(--orange)">Notes <span class="count">6</span></a>
</nav>
```

- Outlined 1.5px in `--pill` (default: the room's colour), 600 weight, fully rounded. The current one (`aria-current`, `aria-pressed="true"` or `.on`) is filled, with the page background as its text.
- Counts sit inside, in 400 weight and tabular numbers.
- One row that scrolls sideways, with no scrollbar and a fade at the edge (`data-fade`), never wrapping.
- Each pill's colour is the colour of what it shows (rule 1). When the choices aren't things (dates, states), they all wear the room's colour.

## Chips

- **State:** `.chip` with `--chip` set: a soft fill of its colour, with the colour as text. Use it for a stage (Seedling, Budding, Evergreen), a status, a priority, a count.
- **Link:** `.chip.link`, outlined in `currentColor`; hovering fills it lightly. Use it for anything that opens something: "Kura", "Obsidian", "Konbini", a label. Obsidian, which isn't a room, is neutral (`--fg2`).
- **Tag:** `.tag`, a neutral outline for the vault's tags.

## Tinted items

```html
<li class="card tinted is-note">
  <span class="kind">Your note · Kura</span>
  …
</li>
```

- `.tinted` fills the item with its room's colour at `--tint-mix` and outlines it in the same colour at 55%. Give it `.is-note`, `.is-card` or `.is-garden`, or set `--thing`.
- `--tint-mix` is computed for each theme by `vaultkit/palettes.py`: the most tint each one allows with every text in it still at 4.5:1. It's 9% in most themes and 7% in Ayu dark. In a light theme the tint mixes into the lighter raised shade (`--tint-base`), as Shiori's light cards are lighter than the page. Don't hard-code a percentage.
- `.kind` is the small line above the title that says whose it is, in the tint's colour.
- A tinted item is a raised panel, so accents inside it use their panel shades, as in any card.

## Cards and lists

- `.card` for a raised item (`--dark`, a 1px `--line` border, a 10px radius, the small shadow). `.list` for a plain list with dividers. `.sechead` for an uppercase section heading.
- Empty states: a Title Case heading and one line (`.empty`).

## Each app's own character

The pieces above are shared. These stay each app's own:

| App | Keeps |
|---|---|
| Shiori | one search field first, and the AI Answer and Info beside the results on a wide screen |
| Kura | dense and archival: three columns, tabular numbers, slate as its second colour |
| Niwa | airy: a 68ch reading measure, stages as seasons, the garden's green |
| Konbini | busy: lanes, counts and a status stripe, magenta with the status colours as its second voice |
| genkan | status first: health in green, amber and red. It isn't a Machiya room, so it borrows the pieces and none of the room colours. |

## Icons and favicons

Each app's favicon is its own icon (`icons/<app>.svg`, the header mark and the Rooms menu icon), never a letter, a kanji square or a generic glyph:

- `favicon.ico` (16, 32 and 48 px) from a small variant with fine detail dropped, so it still reads at 16 px (see `machiya-small.svg`);
- `apple-touch-icon` 180 px, and the web manifest's 192 and 512 px, plus a maskable 512;
- the same drawing everywhere: the favicon, the home-screen icon, the header mark and the README.

## Checking a change

Take before and after screenshots of each changed page on desktop and phone, in Tokyo Night dark and light plus one other theme. Run the app's contrast test.
