# Design language

Machiya's apps are rooms in one house: each room is distinct, and they share a design language.

The pieces you see in a list (filter pills, chips, tinted items, cards) and how each app keeps its character are in the [style guide](style-guide.md).

## The house

- **Ten themes, each dark and light** (v0.15, `vaultkit/palettes.py`): Tokyo Night (the default), Solarized, Nord, Dracula (Alucard for light), Catppuccin (Mocha / Latte), Gruvbox, Rosé Pine (Dawn for light), Kanagawa (Wave / Lotus), Everforest and Ayu. Each uses its own published colours, with any that would be hard to read as text moved in lightness only (never hue) until it is: text and accents ≥ 4.5:1 on the background (Tokyo Night Day's rule from Shiori), checked by a contrast test. The shared components are checked as drawn too (2026-10-06): text on `--dark` or `--hl` (settings and sign-in groups, the Rooms menu, the phone's current tab) uses `--menu-fg` / `--menu-muted`, which are readable there; the footer and footnotes use `--muted`; `--comment` is faint (2.75:1 in a dark variant) and not for text in the shell. Accents on a raised panel use their panel shade, `--<accent>-panel` (v0.25, docs/ui.md), which the panels swap in themselves.
- **Blue is the house colour:** links, focus rings, primary buttons, and Shiori's native tint.
- **One shared stylesheet, `ui/machiya.css`** in this repo, vendored into each web app at a tag with the same drift check as vaultkit (principle 8). It holds the tokens and the shared components; each app keeps only its own `<app>.css` for its room.
- **Scales:**
  - Type: 12 / 13 / 14 / 16 / 20 / 26. Reading rooms (Kura, Niwa) use a 16px note body at line-height 1.65; UI chrome stays at 13–14px.
  - Spacing: 4 / 8 / 12 / 16 / 24 / 32.
  - Radii: 6 / 10 / 16 / 999.
  - Shadows: 2 levels.
- **Motion:** short fades only, always behind `prefers-reduced-motion`.
- **Wording:** Title Case labels and empty states, as in Shiori ("No Note Selected" plus one line). Links read "View in Kura", "View Card in Konbini".
- **No textures.** Seals and accents only.

## The rooms

| Room | Seal | Accent | Character |
|---|---|---|---|
| Shiori, the front door | 栞 | blue, the house colour (yellow lens as its second hue) | quick and precise: one field first |
| Konbini, the shop front | 店 | **magenta** (the status spectrum is its second voice) | busy, at-a-glance: lanes, counts, a status stripe |
| Niwa, the inner garden | 庭 | green (teal second) | airy: a 68ch reading measure, stages as seasons |
| Kura, the storehouse | 蔵 | orange (slate second) | dense, archival: three columns, tabular numbers |

- **Things wear the colour of their room.** Notes are orange (Kura), cards are magenta (Konbini), published-garden links are green (Niwa). That applies in Shiori too: its Notes pill and chips become Kura orange.
- **In Shiori:** notes are Kura orange everywhere (the Notes pill, cards, headings, the Kura swipe), Konbini links magenta, Niwa links green. To keep the colours distinct, Web and Images use Shiori's lens yellow, **Small Web (Gemini/Gopher) owns teal** (and the sidebars' section headings, the owner's call, 2026-10-07), and Obsidian chips use the neutral secondary text colour (Obsidian isn't a room). Tokens: `--notes`, `--konbini`, `--niwa`, `--web`, `--obsidian`, all 4.5:1-tested. Every other hue already has a meaning (blue house/pages, cyan All, orange notes, magenta Konbini/Opened, green Niwa, yellow Web, red Videos), so a new source takes a neutral or needs a design decision. The switcher is a house button beside the gear on the search page, the extension and the web app, and a Rooms tab on phones; the native apps carry the colours and glyphs only.
- **The header mark** is the room's own icon (its home-screen icon, from `icons/`) and its wordmark; the Rooms menu shows the same icons. The kanji (栞 店 庭 蔵) stay as the icons' tooltip and in About; they replace the older kanji squares.

## Moving between rooms

- **One top menu** (owner, 2026-10-10): the rooms and genkan match in icon type, placement and heading: the room's mark and wordmark in its accent, the tabs, then the tools right-aligned (an app's own chip, the Rooms house, the person, the gear). **The house is the Rooms menu everywhere**, never a link home. From **1100px** the search pill sits in the same row, centred between the tabs and the tools (below that, and on phones, it is the second row). Genkan keeps its status pill in the tools slot and its host label in the subtitle slot, moves Edit Layout into Settings, and puts its tabs on the header row from 1100px. Shiori keeps its own style, but its search page follows the same header.
- **One switcher in every header**, and a **Rooms** tab on phones, the fifth tab, opening a sheet. It lists the rooms front to back, **Shiori · Konbini · Niwa · Kura**, then the neighbours, **Hister · SearXNG**, then the house itself, **Machiya · home** (the [landing page](services/landing.md): the launcher, with the status page at `/status`, when the stack has one), then Settings. The footer's "Part of Machiya" links there too.
- **One tab bar on phones, the same in every app** (v0.16): a floating pill like Shiori's, 8px from each edge and 10px above the home indicator, frosted glass (70% opaque over a 20px blur, v0.16.1), the current tab on a raised pill; its tabs share the width, so five never run off the screen. The bar sits on `--dark` with the current tab on `--hl` in a dark variant, and the reverse in a light one, which is how Shiori draws it. **Search is the third tab in every room** (Kura: Home · Recent · Search · Tags; Niwa: Garden · Stream · Search · Queue; Konbini: Board · Now · Search · Roundup; then Rooms), and no room has a search field in its header (v0.16.4): on wide screens Search is a nav link in the same third place, and "/" opens the search page. Shiori, which is all search, keeps its own tabs.
- **A footer status line** in every app, from its `/api/status`: e.g. "Kura · synced abc1234 3 min ago · 812 notes".
- Each room's **`/settings` page** follows Shiori's Settings, in the same order everywhere (v0.21, decided 2026-10-05; v0.23 regrouped it so it flows from everyday choices to rare ones, [ui.md](ui.md#settings-appearance-per-app-this-device)):
  1. **Appearance**: Theme (the ten palettes, stored as `palette`), Appearance (System / Light / Dark, stored as `theme` = `system` / `day` / `night`), Text Size and, under it, Use This Device's Size, with "Follows you on every Machiya app when signed in." and a few words on where the choices are kept now. Shiori adds its Pills.
  2. The room's own sections.
  3. **Rooms**: which apps the Rooms menu shows (the native apps have none).
  4. **This Device**: offline copies, addresses (only when the room has such rows).
  5. Account, then About.

  Settings come in three kinds:
  - **Shared** settings follow the signed-in person to every app and device.
  - **Per-app** settings follow the person too, kept as `<app>.<key>`.
  - **This Device** settings never leave the device.

  Only Text Size may differ per device. The contract is [contracts/prefs.md](contracts/prefs.md).

## Who builds what

`ui/` holds the shared tokens, components, seals, the switcher, the settings component and the footer, with a shared page shell in vaultkit, tagged with it. Each web room vendors them and moves its pages over. Shiori's native apps carry over what fits: the accent, glyph family, room colours and Title Case.

## The Machiya icon

`icons/machiya.svg`: a machiya front, a lattice upper floor under a low tiled eave, a koshi-lattice ground floor, and a three-panel noren on the Tokyo Night background. In the default **rooms** palette the eaves are Shiori's blue and the three noren panels carry the other rooms' colours: Kura's orange, Niwa's green and Konbini's magenta. Files in `icons/`:

| File | For |
|---|---|
| `machiya.svg` | the source (rounded square) |
| `machiya-maskable.svg` | the same house inside the central 80% of a full-bleed square, for Android and PWA masks and the iOS touch icon |
| `machiya-avatar.svg` | a full-bleed square (no transparent corners) with the house enlarged to about 92% of the width, for avatars such as the GitHub organisation's (`png/machiya-avatar-512.png`) |
| `machiya-small.svg` | the favicon sizes (16 to 48 px): the lattice dropped and the parts bold, so the eaves and the noren survive at 16 px |
| `png/machiya-{16,32,48,64,96,128,180,192,256,512}.png` | 16 to 48 from the small variant, the rest from the source; 180 is the Apple touch size |
| `png/machiya-maskable-{180,192,512}.png` | the maskable renders |
| `png/favicon.ico` | 16, 32 and 48 px, from the small variant |

The icons are generated: `python3 icons/build-icons.py [--palette rooms|blue|sunset|indigo|garden|sakura]` writes the three SVG forms and the PNG set (Inkscape and ImageMagick) from the templates in `icons/alt/_templates/`; the drawing never changes, only the colours. The other palettes are kept as alternatives, pre-rendered as `icons/alt/machiya-<palette>.svg` (`blue` is the original colouring). **The switcher keeps its plain house glyph:** eight line-art variants of the machiya front were drawn at 18 px and every one read as something else (a pagoda, a shop: Konbini's 店, a torii); the plain house is the clearest "home" and matches the other line glyphs. The full-colour icon is for installs, the README and the docs, not the header.
