# Style guide

How a Machiya app looks, taken from Shiori, the front door. Every app shares these pieces and keeps its own character. [design.md](design.md) has the house (themes, room colours, header, tab bar, settings), and [ui.md](ui.md) has the wiring. This page covers the parts you see in a list: pills, chips, tinted items and cards. Since vaultkit/ui v0.26, `ui/machiya.css` has all of them.

## The rules

1. **Colour means where a thing comes from.** Notes are Kura orange, cards Konbini magenta, the published garden Niwa green, your pages and the house blue, and the small web teal. A hue that already has a meaning is never reused for something else. A new kind of thing takes a neutral, or needs a design decision.
2. **Outlined, as Shiori draws them.** Pills and chips are drawn in their colour's outline on no fill. Only the selected pill is filled. Under the pointer, a filter pill that isn't selected fills with 24% of its colour over the raised colour (`--hl`), with a small shadow, and its text and outline take its hover shade (`--<accent>-hover`), which `palettes.py` makes readable at 4.5:1 on that fill in every theme (v0.27.4, as Shiori 0.18.0; the owner, 2026-10-07: an outline change hardly showed, and a plain lift onto `--hl` was still hard to see). A chip that opens something still draws a heavier outline on hover. A fill of a colour behind text in that same colour always pulls it under 4.5:1, so there are no filled chips (v0.26.2).
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
- One row that scrolls sideways, with no scrollbar, never wrapping. `machiya.js` fades the edge it can still scroll toward (`data-fade`, v0.26.1).
- Each pill's colour is the colour of what it shows (rule 1). When the choices aren't things (dates, states), they all wear the room's colour.

## View switches

How a list is shown (Group By, Sort, a layout), as opposed to what it holds, is a `.segmented` control: neutral, a sunken track with the chosen option as a raised pill, like Shiori's Anytime and Best Match settings. It never takes a colour, so it can't be mistaken for a filter row of pills (v0.27.1).

```html
<nav class="segmented" aria-label="Group By"><a aria-current="true">Area</a><a>Stream</a><a>Family</a></nav>
```

## Chips

- **State:** `.chip` with `--chip` set: outlined in its colour, the colour as text, no fill. Use it for a stage (Seedling, Budding, Evergreen), a status, a priority, a count.
- **Link:** `.chip.link`: the same outline, a heavier one on hover, and a pointer. Use it for anything that opens something: "Kura", "Obsidian", "Konbini", a label. Obsidian, which isn't a room, is neutral (`--fg2`).
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

- **A result card** (`.card`, v0.27) is Shiori's: a surface a step above the page (`--card`, computed per theme), 14px corners, no visible border, the small shadow. Its parts: `.title` (16px, 500, in the thing's or room's colour), `.snippet` (the secondary colour), `.meta` (a row of state and link chips plus small text).
- **A list of cards** is `<ul class="cards">`, one `<li class="card">` each, 10px apart, as Shiori's results. Use it for lists of things a person reads one by one (notes in a garden, search results, cards in a stream). Dense, scannable indexes stay `.list` rows with dividers (Kura's columns, a tag index).
- **Section headings** (`.sechead`) are written in Title Case and drawn as written ("Notes", "Topic Maps"), 14px, 600, the secondary colour, as Shiori's. No uppercase, no letter-spacing. In a list with its own tabs (Shiori's search page), the heading on a tab wears that tab's colour, as its pill does (Your Pages blue, Your Notes orange, Your Code red).
- **Links inside a card** (a result's title, which opens the original) underline under the pointer and show the link cursor, as a web link does. A click anywhere else on the card does what the card's click does, so the link has to read apart from it. On macOS, draw it as a plain SwiftUI button: AppKit draws a borderless one and keeps the hover from its label.
- **Rows that select** (a sidebar's rows): under the pointer, a light fill of the accent (14%) across the row, past the label's edges like the selection, never on the selected row. Nothing moves.
- **Sidebar section headings** (Shiori's Collections and Labels, Kura's Folders): `.sidehead`, the rows' size, semibold, in teal, in every app (the owner, 2026-10-07: in the rows' colour they read as one more row, and the accent read as purple beside the selection). This is the one place teal doesn't mean the Small Web.
- **Selectable rows** in a sidebar or a dense column: `.rows`, whose links fill with 14% of the room's colour under the pointer (a passing state, like a pill's hover), never the current row (`.here` or `aria-current`).
- **The header's current page** is a raised pill (`--hl`), as Shiori's tab bar draws it, with no underline.
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

- the browser tab: `<room>-small.svg`, a small variant with fine detail dropped so it still reads at 16 px (see `machiya-small.svg`), and `<room>.ico` (16, 32 and 48 px) from it. `shell.page` links both (v0.26.1);
- `apple-touch-icon` 180 px, and the web manifest's 192 and 512 px, plus a maskable 512;
- the same drawing everywhere: the favicon, the home-screen icon, the header mark and the README.

## Checking a change

Take before and after screenshots of each changed page on desktop and phone, in Tokyo Night dark and light plus one other theme. Run the app's contrast test.
