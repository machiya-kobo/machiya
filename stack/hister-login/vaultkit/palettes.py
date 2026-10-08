"""The rooms' colour themes (v0.15): ten palettes, each with a dark and a light variant (docs/design.md).

A theme is two choices: the palette (Tokyo Night, Solarized, …) and the appearance (System follows the device, Dark,
Light). The appearance is the old `theme` setting (`system` / `night` / `day`, so a cookie or a stored preference
from before means what it did); the palette is the new `palette` setting, `tokyo-night` when unset.

Each variant names Machiya's tokens (ui/machiya.css): surfaces (bg, dark: bars and cards, hl: raised), lines, text
(fg, fg2, muted, comment) and the nine accents the rooms and things wear. The values are each theme's own published
colours; where one would be hard to read as text, `readable()` moves its lightness (never its hue) until it is:

- every variant: fg, fg2, muted and the accents >= 4.5:1 on bg (WCAG AA);
- the Rooms menu's text (menu-fg: fg moved the same way) >= 4.5:1 on dark, the menu's panel (v0.16.6), and its role
  words (menu-muted: muted moved the same way) >= 4.5:1 on dark and on hl, the current row (v0.16.7);
- each accent's panel shade (<accent>-panel: the accent moved the same way, v0.25) >= its minimum on dark and on hl,
  the raised panels (cards, settings groups, menus): machiya.css swaps the accents for these inside a panel, and the
  page keeps the accents themselves;
- comment (faint text) >= 4.4:1 in a light variant, >= 2.75:1 in a dark one, and a dark variant's slate >= 4:1:
  what Tokyo Night has always had.

`css()` writes the stylesheet's palette section from this table (python3 -m vaultkit.palettes > the block between
the markers in ui/machiya.css); a test checks the two never drift apart.
"""
import colorsys

TEXT = ("fg", "fg2", "muted", "blue", "orange", "red", "yellow", "green", "teal", "magenta", "cyan", "slate", "comment")
TOKENS = ("bg", "dark", "hl", "line", "line2") + TEXT
ACCENTS = ("blue", "orange", "red", "yellow", "green", "teal", "magenta", "cyan", "slate")   # each has an <accent>-panel

PALETTES = {   # key: (name, dark variant's name, light variant's name, {"dark": {...}, "light": {...}})
    "tokyo-night": ("Tokyo Night", "Night", "Day", {
        "dark": dict(bg="#1a1b26", dark="#16161e", hl="#292e42", line="#232433", line2="#33395a", fg="#c0caf5",
                     fg2="#a9b1d6", comment="#565f89", muted="#8890b5", blue="#7aa2f7", orange="#ff9e64",
                     red="#f7768e", yellow="#e0af68", green="#9ece6a", teal="#73daca", magenta="#bb9af7",
                     cyan="#7dcfff", slate="#737aa2"),
        "light": dict(bg="#e1e2e7", dark="#d0d5e3", hl="#eef0f6", line="#c4c8da", line2="#aab1cb", fg="#3760bf",
                      fg2="#4c5a8f", comment="#5a6391", muted="#4c5a8f", blue="#155fc5", orange="#9a5000",
                      red="#bd204c", yellow="#7a5e36", green="#506b34", teal="#0d6e5c", magenta="#802cee",
                      cyan="#006b8f", slate="#4c5a8f")}),
    "solarized": ("Solarized", "Dark", "Light", {
        "dark": dict(bg="#002b36", dark="#00212b", hl="#073642", line="#0a3542", line2="#2b5662", fg="#93a1a1",
                     fg2="#839496", comment="#586e75", muted="#839496", blue="#268bd2", orange="#cb4b16",
                     red="#dc322f", yellow="#b58900", green="#859900", teal="#2aa198", magenta="#d33682",
                     cyan="#2aa198", slate="#6c71c4"),
        "light": dict(bg="#fdf6e3", dark="#eee8d5", hl="#fffbf0", line="#e6dfc8", line2="#d3cbb0", fg="#586e75",
                      fg2="#657b83", comment="#93a1a1", muted="#657b83", blue="#268bd2", orange="#cb4b16",
                      red="#dc322f", yellow="#b58900", green="#859900", teal="#2aa198", magenta="#d33682",
                      cyan="#2aa198", slate="#6c71c4")}),
    "nord": ("Nord", "Dark", "Light", {
        "dark": dict(bg="#2e3440", dark="#272c36", hl="#3b4252", line="#373e4c", line2="#4c566a", fg="#eceff4",
                     fg2="#d8dee9", comment="#616e88", muted="#aeb6c4", blue="#81a1c1", orange="#d08770",
                     red="#bf616a", yellow="#ebcb8b", green="#a3be8c", teal="#8fbcbb", magenta="#b48ead",
                     cyan="#88c0d0", slate="#7b88a1"),
        "light": dict(bg="#eceff4", dark="#e5e9f0", hl="#f6f8fb", line="#d8dee9", line2="#c2cad6", fg="#2e3440",
                      fg2="#3b4252", comment="#4c566a", muted="#4c566a", blue="#5e81ac", orange="#d08770",
                      red="#bf616a", yellow="#ebcb8b", green="#a3be8c", teal="#8fbcbb", magenta="#b48ead",
                      cyan="#88c0d0", slate="#4c566a")}),
    "dracula": ("Dracula", "Dracula", "Alucard", {
        "dark": dict(bg="#282a36", dark="#21222c", hl="#44475a", line="#343746", line2="#4d5066", fg="#f8f8f2",
                     fg2="#e2e2dc", comment="#6272a4", muted="#b6b9cc", blue="#bd93f9", orange="#ffb86c",
                     red="#ff5555", yellow="#f1fa8c", green="#50fa7b", teal="#8be9fd", magenta="#ff79c6",
                     cyan="#8be9fd", slate="#6272a4"),
        "light": dict(bg="#fffbeb", dark="#f3efdd", hl="#ffffff", line="#e8e3cc", line2="#cfcfde", fg="#1f1f1f",
                      fg2="#3a3a3a", comment="#6c664b", muted="#4f4b38", blue="#644ac9", orange="#a34d14",
                      red="#cb3a2a", yellow="#846e15", green="#14710a", teal="#036a96", magenta="#a3144d",
                      cyan="#036a96", slate="#635d43")}),
    "catppuccin": ("Catppuccin", "Mocha", "Latte", {
        "dark": dict(bg="#1e1e2e", dark="#181825", hl="#313244", line="#2a2a3c", line2="#45475a", fg="#cdd6f4",
                     fg2="#bac2de", comment="#6c7086", muted="#a6adc8", blue="#89b4fa", orange="#fab387",
                     red="#f38ba8", yellow="#f9e2af", green="#a6e3a1", teal="#94e2d5", magenta="#cba6f7",
                     cyan="#89dceb", slate="#7f849c"),
        "light": dict(bg="#eff1f5", dark="#e6e9ef", hl="#f8f9fb", line="#dce0e8", line2="#bcc0cc", fg="#4c4f69",
                      fg2="#5c5f77", comment="#7c7f93", muted="#6c6f85", blue="#1e66f5", orange="#fe640b",
                      red="#d20f39", yellow="#df8e1d", green="#40a02b", teal="#179299", magenta="#8839ef",
                      cyan="#04a5e5", slate="#7c7f93")}),
    "gruvbox": ("Gruvbox", "Dark", "Light", {
        "dark": dict(bg="#282828", dark="#1d2021", hl="#3c3836", line="#32302f", line2="#504945", fg="#ebdbb2",
                     fg2="#d5c4a1", comment="#928374", muted="#bdae93", blue="#83a598", orange="#fe8019",
                     red="#fb4934", yellow="#fabd2f", green="#b8bb26", teal="#8ec07c", magenta="#d3869b",
                     cyan="#83a598", slate="#a89984"),
        "light": dict(bg="#fbf1c7", dark="#ebdbb2", hl="#f9f5d7", line="#e5d5a8", line2="#d5c4a1", fg="#3c3836",
                      fg2="#504945", comment="#7c6f64", muted="#665c54", blue="#076678", orange="#af3a03",
                      red="#9d0006", yellow="#b57614", green="#79740e", teal="#427b58", magenta="#8f3f71",
                      cyan="#076678", slate="#7c6f64")}),
    "rose-pine": ("Rosé Pine", "Rosé Pine", "Dawn", {
        "dark": dict(bg="#191724", dark="#14121d", hl="#26233a", line="#21202e", line2="#403d52", fg="#e0def4",
                     fg2="#c8c5e0", comment="#6e6a86", muted="#908caa", blue="#9ccfd8", orange="#ebbcba",
                     red="#eb6f92", yellow="#f6c177", green="#3e8fb0", teal="#9ccfd8", magenta="#c4a7e7",
                     cyan="#9ccfd8", slate="#908caa"),
        "light": dict(bg="#faf4ed", dark="#f2e9e1", hl="#fffaf3", line="#dfdad9", line2="#cecacd", fg="#575279",
                      fg2="#6e6a8f", comment="#9893a5", muted="#797593", blue="#286983", orange="#d7827e",
                      red="#b4637a", yellow="#ea9d34", green="#56949f", teal="#56949f", magenta="#907aa9",
                      cyan="#56949f", slate="#797593")}),
    "kanagawa": ("Kanagawa", "Wave", "Lotus", {
        "dark": dict(bg="#1f1f28", dark="#16161d", hl="#2a2a37", line="#252530", line2="#363646", fg="#dcd7ba",
                     fg2="#c8c093", comment="#727169", muted="#a6a69c", blue="#7e9cd8", orange="#ffa066",
                     red="#e46876", yellow="#e6c384", green="#98bb6c", teal="#7aa89f", magenta="#957fb8",
                     cyan="#7fb4ca", slate="#938aa9"),
        "light": dict(bg="#f2ecbc", dark="#e5ddb0", hl="#f7f3d6", line="#dcd5ac", line2="#d5cea3", fg="#545464",
                      fg2="#43436c", comment="#8a8980", muted="#716e61", blue="#4d699b", orange="#cc6d00",
                      red="#c84053", yellow="#77713f", green="#6f894e", teal="#597b75", magenta="#624c83",
                      cyan="#4e8ca2", slate="#716e61")}),
    "everforest": ("Everforest", "Dark", "Light", {
        "dark": dict(bg="#2d353b", dark="#232a2e", hl="#343f44", line="#323c41", line2="#475258", fg="#d3c6aa",
                     fg2="#c0b597", comment="#7a8478", muted="#9da9a0", blue="#7fbbb3", orange="#e69875",
                     red="#e67e80", yellow="#dbbc7f", green="#a7c080", teal="#83c092", magenta="#d699b6",
                     cyan="#7fbbb3", slate="#859289"),
        "light": dict(bg="#fdf6e3", dark="#efebd4", hl="#fffbef", line="#e6e2cc", line2="#e0dcc7", fg="#5c6a72",
                      fg2="#5c6a72", comment="#829181", muted="#829181", blue="#3a94c5", orange="#f57d26",
                      red="#f85552", yellow="#dfa000", green="#8da101", teal="#35a77c", magenta="#df69ba",
                      cyan="#3a94c5", slate="#829181")}),
    "ayu": ("Ayu", "Dark", "Light", {
        "dark": dict(bg="#0b0e14", dark="#07090d", hl="#131721", line="#11151c", line2="#1e232b", fg="#bfbdb6",
                     fg2="#acaaa2", comment="#565b66", muted="#8a8986", blue="#59c2ff", orange="#ff8f40",
                     red="#f07178", yellow="#ffb454", green="#aad94c", teal="#95e6cb", magenta="#d2a6ff",
                     cyan="#39bae6", slate="#6c7380"),
        "light": dict(bg="#fcfcfc", dark="#f0f0f0", hl="#ffffff", line="#e7e8e9", line2="#d4d6d8", fg="#5c6166",
                      fg2="#5c6166", comment="#8a9199", muted="#787b80", blue="#399ee6", orange="#fa8d3e",
                      red="#f07171", yellow="#f2ae49", green="#86b300", teal="#4cbf99", magenta="#a37acc",
                      cyan="#55b4d4", slate="#787b80")}),
}
DEFAULT = "tokyo-night"
# what an installed app's splash screen and title bar use: the variant's bg and dark (shell.manifest_colors)
CHOICES = [(k, v[0]) for k, v in PALETTES.items()]


# -- contrast ------------------------------------------------------------------------------------------------------

def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb)


def luminance(h):
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def minimum(mode, token):
    if token == "comment":
        return 2.75 if mode == "dark" else 4.4
    if mode == "dark" and token == "slate":
        return 4.0
    return 4.5


def readable(colour, bg, need, mode):
    """colour with its lightness moved (darker on a light background, lighter on a dark one) until it has `need`
    contrast on bg; the hue and saturation stay."""
    if contrast(colour, bg) >= need:
        return colour
    h, l, s = colorsys.rgb_to_hls(*_rgb(colour))
    step = -0.005 if mode == "light" else 0.005
    while 0 <= l <= 1:
        l += step
        out = _hex(colorsys.hls_to_rgb(h, min(1, max(0, l)), s))
        if contrast(out, bg) >= need:
            return out
    return "#000000" if mode == "light" else "#ffffff"


def variant(key, mode):
    """The variant's tokens, made readable."""
    raw = PALETTES[key][3][mode]
    return {t: readable(raw[t], raw["bg"], minimum(mode, t), mode) if t in TEXT else raw[t] for t in TOKENS}


# Tinted items (Shiori's design language, docs/style-guide.md, v0.26): something that's yours wears its room's colour, a
# fill of that colour mixed into the card at --tint-mix with an outline in it. The mix is the most each variant allows
# with every text a card draws (menu-fg, menu-muted, each accent's panel shade) still at its minimum, under every room
# colour; Shiori found 9% (Tokyo Night) and 8% (Day) the same way. Never more than TINT_MAX. Since v0.27.5 every accent
# can tint (Konbini tints its cards by status: slate, blue, orange, red, green), which costs a point or two in a few themes.
TINTS = ACCENTS
TINT_MAX = 9


def mix(a, b, pct):
    """CSS color-mix(in srgb, a pct%, b): the channels mixed as written (gamma-encoded), as the browser does."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x * pct / 100 + y * (1 - pct / 100) for x, y in zip(ra, rb)))


HOVER_MIX = 24   # a hovered filter pill: its colour at 24% over --hl (Shiori 0.18.0, the owner: the plain lift hardly showed)


def hover_shade(colour, hl, mode):
    """A hovered pill's colour (v0.27.4): colour with its lightness moved until it reads at 4.5:1 on its own fill,
    color-mix(in srgb, itself HOVER_MIX%, hl). The stylesheet mixes the fill from this same shade, so the browser draws
    exactly the pair checked here."""
    h, l, s = colorsys.rgb_to_hls(*_rgb(colour))
    step = -0.005 if mode == "light" else 0.005
    out = colour
    while contrast(out, mix(out, hl, HOVER_MIX)) < 4.5:
        l += step
        if not 0 <= l <= 1:
            return "#000000" if mode == "light" else "#ffffff"
        out = _hex(colorsys.hls_to_rgb(h, l, s))
    return out


def card_colour(v, mode):
    """Shiori's result card (v0.27): a surface a step above the page. In a dark variant it's --bg moved 40% toward --hl
    (Tokyo Night #202331, Shiori's #24283b; at 50% Solarized dark's fg2 fell to 4.45:1); in a light one it's --hl,
    lighter than the page, as Shiori's light cards are. The tests check every text on it."""
    return mix(v["hl"], v["bg"], 40) if mode == "dark" else v["hl"]


def tint_base(mode):
    """What a tinted item's fill is mixed into: the card (v0.27; before, --dark in a dark variant and --hl in a light
    one: a light variant's --dark leaves its text no room for any tint)."""
    return "card"


def tint_mix(v, mode):
    texts = [(v["menu-fg"], 4.5), (v["menu-muted"], 4.5)] + [(v[t + "-panel"], minimum(mode, t)) for t in ACCENTS]
    base = v[tint_base(mode)]
    for pct in range(TINT_MAX, -1, -1):
        if all(contrast(fg, mix(v[t + "-panel"], base, pct)) >= need for t in TINTS for fg, need in texts):
            return pct
    return 0


def tokens(key, mode):
    """variant() plus the derived tokens the stylesheet writes: menu-fg (fg readable on dark, the Rooms menu's panel),
    menu-muted (muted readable on dark and on hl) and <accent>-panel (each accent readable on dark and on hl, its
    lightness moved only where it isn't). Text on --dark or --hl uses these (docs/design.md, docs/ui.md)."""
    v = variant(key, mode)
    v["menu-fg"] = readable(v["fg"], v["dark"], 4.5, mode)
    v["menu-muted"] = readable(readable(v["muted"], v["dark"], 4.5, mode), v["hl"], 4.5, mode)
    for t in ACCENTS:
        need = minimum(mode, t)
        v[t + "-panel"] = readable(readable(v[t], v["dark"], need, mode), v["hl"], need, mode)
    for t in ACCENTS:
        v[t + "-hover"] = hover_shade(v[t], v["hl"], mode)
    v["card"] = card_colour(v, mode)
    v["tint-mix"] = tint_mix(v, mode)
    return v


# -- the stylesheet -----------------------------------------------------------------------------------------------

def _shadow(mode, fg):
    if mode == "dark":
        return "--shadow-1: 0 1px 2px rgba(0,0,0,.35); --shadow-2: 0 1px 2px rgba(0,0,0,.35), 0 6px 18px rgba(0,0,0,.25);"
    r, g, b = (round(c * 255) for c in _rgb(fg))
    a = "rgba(%d,%d,%d,.12)" % (r, g, b)
    return "--shadow-1: 0 1px 2px %s; --shadow-2: 0 1px 2px %s, 0 6px 18px %s;" % (a, a, a)


def _block(selectors, key, mode, indent=""):
    v = tokens(key, mode)
    rows = [" ".join("--%s: %s;" % (t, v[t]) for t in ("bg", "dark", "hl", "line", "line2")),
            " ".join("--%s: %s;" % (t, v[t]) for t in ("fg", "fg2", "comment", "muted", "menu-fg", "menu-muted")),
            " ".join("--%s: %s;" % (t, v[t]) for t in ("blue", "orange", "red", "yellow", "green")),
            " ".join("--%s: %s;" % (t, v[t]) for t in ("teal", "magenta", "cyan", "slate")),
            " ".join("--%s-panel: %s;" % (t, v[t + "-panel"]) for t in ACCENTS[:5]),
            " ".join("--%s-panel: %s;" % (t, v[t + "-panel"]) for t in ACCENTS[5:]),
            " ".join("--%s-hover: %s;" % (t, v[t + "-hover"]) for t in ACCENTS[:5]),
            " ".join("--%s-hover: %s;" % (t, v[t + "-hover"]) for t in ACCENTS[5:]),
            "--card: %s; --tint-mix: %d%%; --tint-base: var(--%s);" % (v["card"], v["tint-mix"], tint_base(mode)),
            _shadow(mode, v["fg"])]
    return "%s%s {\n%s\n%s}\n" % (indent, ", ".join(selectors), "\n".join(indent + "  " + r for r in rows), indent)


def css():
    """The palette section of ui/machiya.css. body.theme-night / theme-day / theme-system (alias theme-auto) is the
    appearance; body.palette-<key> the palette (none = Tokyo Night)."""
    out = ["/* generated by vaultkit/palettes.py (python3 -m vaultkit.palettes); edit the table there, not here */\n"]
    for key, (name, dark_name, light_name, _) in PALETTES.items():
        p = "" if key == DEFAULT else ".palette-%s" % key
        first = [] if key != DEFAULT else ["body.theme-night", "body.theme-system", "body.theme-auto"]
        out.append("/* %s: %s (dark) and %s (light) */\n" % (name, dark_name, light_name))
        out.append(_block(first or ["body%s.theme-night" % p, "body%s.theme-system" % p, "body%s.theme-auto" % p],
                          key, "dark"))
        out.append(_block(["body%s.theme-day" % p], key, "light"))
        out.append("@media (prefers-color-scheme: light) {\n")
        out.append(_block(["body%s.theme-system" % p, "body%s.theme-auto" % p], key, "light", "  "))
        out.append("}\n")
    return "".join(out)


BEGIN, END = "/* -- palettes: begin -- */\n", "/* -- palettes: end -- */\n"


if __name__ == "__main__":
    print(css(), end="")
