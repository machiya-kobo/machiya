"""The shared page shell of Machiya's web rooms (docs/design.md, docs/ui.md): the document, the header (seal,
wordmark, nav, the Rooms switcher, the settings gear), the phone tab bar (the room's tabs + a Rooms tab), the footer
status line, and the /settings page. Pairs with ui/machiya.css and ui/machiya.js (vendored next to this package, in
app/vaultkit/ui/).

An app builds pages with `page(ctx, room, title, body, ...)`; `ctx` needs `.theme` (and optionally `.text`), which
`prefs(cookie_header)` reads from the cookies machiya.js writes. Room links come from MACHIYA_ROOMS (see rooms()):
a room or neighbour that isn't configured is simply left out, so an app standing alone shows no switcher at all.
"""
import hashlib
import html
import json
import os
from urllib.parse import unquote

from . import palettes

ROOMS = [   # (key, name, seal, what it is) front to back through the house, then the neighbours
    ("shiori", "Shiori", "栞", "search"),
    ("konbini", "Konbini", "店", "board"),
    ("niwa", "Niwa", "庭", "garden"),
    ("kura", "Kura", "蔵", "notes"),
]
NEIGHBOURS = [("hister", "Hister", "pages"), ("searxng", "SearXNG", "the web")]
THEMES = [("system", "System"), ("day", "Light"), ("night", "Dark")]     # the appearance (setting `theme`)
PALETTES = palettes.CHOICES                                              # the theme (setting `palette`, v0.15)
TEXT_SIZES = [("xsmall", "Extra Small"), ("small", "Small"), ("standard", "Standard"), ("large", "Large"),
              ("xlarge", "Extra Large")]

_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true">%s</svg>')
GLYPH = {   # one glyph per room everywhere (Shiori uses SF Symbols for the same shapes)
    "shiori": _SVG % '<path d="M7 3h10v18l-5-4-5 4z"/>',                                           # ribbon
    "konbini": _SVG % '<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="10" rx="1"/>'
                      '<rect x="17" y="4" width="4" height="13" rx="1"/>',                           # columns
    "niwa": _SVG % '<path d="M12 22v-8M12 14c0-4 3-7 8-7-1 5-4 7-8 7zM12 14c0-4-3-7-8-7 1 5 4 7 8 7z"/>',  # sprout
    "kura": _SVG % '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5M8 7h7"/>',     # book
    "hister": _SVG % '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',                         # history
    "searxng": _SVG % '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',  # web
    "person": _SVG % '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',               # who's signed in
    "search": _SVG % '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',                                  # every room's Search tab (v0.16.2)
    "close": _SVG % '<path d="M6 6l12 12M18 6 6 18"/>',                                 # the search pill's clear X (v0.17.1)
    "rooms": _SVG % '<path d="M3 11 12 4l9 7v9H3z"/><path d="M9 20v-5h6v5"/>',                           # the house
    "gear": _SVG % '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1'
                   'a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0'
                   '-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1'
                   'a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9'
                   'a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1'
                   ' 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
}


def e(text):
    return html.escape(str(text or ""), quote=True)


def source_url(env=None):
    """MACHIYA_SOURCE_URL: where this room's source code is published, or "". The AGPL (section 13) says people who use a
    room over a network must be offered its source, so when this is set the footer and About link to it. Unset (the
    default): nothing is shown and every page is byte-identical to one built without this setting. Only a plain http(s)
    address is accepted (no scripts, no whitespace); anything else counts as unset."""
    url = ((env if env is not None else os.environ).get("MACHIYA_SOURCE_URL") or "").strip()
    return url if url.startswith(("http://", "https://")) and not any(c.isspace() or c in '<>"\'' for c in url) else ""


# -- configuration ----------------------------------------------------------------------------------------------

def rooms(env=None):
    """{key: url} from MACHIYA_ROOMS="shiori=https://…,konbini=https://…,niwa=…,kura=…,hister=…,searxng=…"
    (no trailing slashes). Unset or empty = no links; a key may be left out."""
    raw = (env if env is not None else os.environ).get("MACHIYA_ROOMS", "")
    out = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip().lower()] = v.strip().rstrip("/")
    return out


class Prefs:
    """Per-device settings the server needs for the first render (cookies written by machiya.js)."""

    def __init__(self, theme="system", text="standard", extra=None, palette=palettes.DEFAULT):
        self.theme = theme if theme in ("system", "night", "day") else "system"
        self.text = text if text in dict(TEXT_SIZES) else "standard"
        self.palette = palette if palette in palettes.PALETTES else palettes.DEFAULT
        self.extra = extra or {}


COOKIE_DOMAIN = os.environ.get("MACHIYA_COOKIE_DOMAIN", "").strip().lstrip(".")   # e.g. example.ts.net
SHARED_PREFIX = "machiya_"      # shared cookies (Domain=COOKIE_DOMAIN): machiya_theme, machiya_textSize, machiya_show_*


def is_shared(key):
    """Settings one choice of which covers every room on the device (with MACHIYA_COOKIE_DOMAIN set)."""
    return key in ("theme", "palette", "textSize") or key.startswith("show_")


def prefs(cookie_header):
    """The first render's settings. A shared cookie (machiya_<key>, written for every room when MACHIYA_COOKIE_DOMAIN
    is set) wins over the room's own cookie of the same key."""
    jar = {}
    for part in (cookie_header or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            jar[k.strip()] = unquote(v.strip())
    for k in [k for k in jar if k.startswith(SHARED_PREFIX)]:
        jar[k[len(SHARED_PREFIX):]] = jar.pop(k)
    theme = jar.get("theme", "system")
    return Prefs("system" if theme == "auto" else theme, jar.get("textSize", "standard"),
                 {k: v for k, v in jar.items() if k not in ("theme", "textSize", "palette")},
                 jar.get("palette", palettes.DEFAULT))


def search_box(q="", action="/search", placeholder="Search", label="Search"):
    """The room's search field (the header's tools slot, or the top of a search page). machiya.js focuses it on "/"."""
    return ('<form class="search" role="search" action="%s"><input type="search" name="q" value="%s" placeholder="%s" '
            'aria-label="%s" autocomplete="off" spellcheck="false" enterkeyhint="search"></form>'
            % (e(action), e(q), e(placeholder), e(label)))


def search_bar(q="", action="/search", placeholder="Search", label="Search"):
    """The room's search pill (v0.17): a second row of the pinned header at every width, as Shiori's field. Pass it to
    header(search=...). machiya.js shows results as you type (it fetches the room's search page and swaps <main>), and
    "/" focuses it."""
    return ('<form class="search bar" role="search" action="%s"><div class="field"><input type="search" name="q" value="%s" '
            'placeholder="%s" aria-label="%s" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search">'
            '<button type="button" class="clear" aria-label="Clear" title="Clear search">%s</button>'
            '<button type="submit" class="go" aria-label="Search" title="Search">%s</button></div></form>'
            % (e(action), e(q), e(placeholder), e(label), GLYPH["close"], GLYPH["search"]))


def handoff(q, links=None):
    """"Search everything in Shiori ›" for the end of a room's results (a room searches its own things first, then hands
    the query to Shiori). "" without a Shiori address or a query."""
    links = links if links is not None else rooms()
    if not q or "shiori" not in links:
        return ""
    from urllib.parse import quote
    return ('<a class="handoff" href="%s/#/search?q=%s"><span class="seal icon" data-room="shiori" aria-hidden="true"></span>'
            'Search everything in Shiori<span class="arrow" aria-hidden="true">›</span></a>' % (e(links["shiori"]), quote(q, safe="")))


# -- titles, headers, messages (v0.13) ---------------------------------------------------------------------------------

def title(room, what=""):
    """Every page's <title>: "What - Room" ("Lantern - Kura", "Not Found - Konbini"), the room alone for its home."""
    _, name, _, _ = room_info(room)
    return "%s - %s" % (what, name) if what else name


# Every HTML page a room serves: what a note's body may do is decided by the sanitizer (vaultkit.sanitize) and, behind
# it, by this policy. Scripts only from the room itself (no inline script, no handler attributes); images from anywhere
# a note links to; nothing framed except by the room itself; forms post only to the room.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https: http:; "
       "media-src 'self' https: http:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-src 'self'; frame-ancestors 'self'")


def security_headers(csp=CSP):
    """[(header, value)] for every HTML page (v0.13): the CSP above, no MIME sniffing, the path never leaves the
    room in a Referer. A room adds these to its own (Content-Type, Cache-Control). v0.14: also Accept-CH for the
    device's colour scheme, so the browser sends Sec-CH-Prefers-Color-Scheme with later requests (the manifest's)."""
    return [("Content-Security-Policy", csp), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "same-origin"), ("Accept-CH", COLOR_HINT)]


# -- the manifest's colours (v0.14) -------------------------------------------------------------------------------------

COLOR_HINT = "Sec-CH-Prefers-Color-Scheme"
def _colors(palette, mode):
    v = palettes.PALETTES.get(palette, palettes.PALETTES[palettes.DEFAULT])[3][mode]
    return {"background_color": v["bg"], "theme_color": v["dark"]}


NIGHT = _colors(palettes.DEFAULT, "dark")
DAY = _colors(palettes.DEFAULT, "light")
MANIFEST_VARY = "Cookie, " + COLOR_HINT      # the manifest's Vary header: the theme cookie and the hint choose it


def manifest_colors(theme, headers=None, palette=palettes.DEFAULT):
    """The manifest's background_color and theme_color: what an installed app's splash screen and title bar use.
    Night or Day as chosen in Settings; with System, the device's own scheme when the browser says it
    (Sec-CH-Prefers-Color-Scheme, which security_headers' Accept-CH asks for), else Night. With System the answer also
    carries user_preferences.color_scheme_dark (the manifest's per-scheme colours, where a browser supports them), so
    a light install still opens dark when the device is dark. Serve the manifest with Vary: MANIFEST_VARY.
    palette (v0.15): the chosen theme's colours (its bg and its bars' colour), Tokyo Night by default."""
    theme = "system" if theme == "auto" else theme
    night, day = _colors(palette, "dark"), _colors(palette, "light")
    if theme == "night":
        return night
    if theme == "day":
        return day
    hint = ((headers.get(COLOR_HINT) if headers is not None else "") or "").strip().strip('"').lower()
    out = dict(day if hint == "light" else night)
    out["user_preferences"] = {"color_scheme_dark": night}
    return out


def message(heading, text="", actions=()):
    """A short page body (not found, offline, sign in first): a heading, a sentence and a row of buttons
    [(href, label)], centred (machiya.css main.msg, .empty). The first action is the main one."""
    buttons = "".join('<a class="button%s" href="%s">%s</a>' % ("" if i else " primary", e(h), e(l))
                      for i, (h, l) in enumerate(actions))
    return ('<main class="msg"><div class="empty"><h2>%s</h2>%s%s</div></main>'
            % (e(heading), ('<p>%s</p>' % e(text)) if text else "", ('<p class="actions">%s</p>' % buttons) if buttons else ""))


def not_found(room, what=""):
    """The body of a 404: what wasn't found, and the way home."""
    _, name, _, _ = room_info(room)
    return message("Not Found", ("There's nothing at %s." % what) if what else "There's nothing here.",
                   [("/", "Go to %s" % name)])


def offline(room):
    """The body of the precached /offline page: no status line, no claims about the network the person uses."""
    _, name, _, _ = room_info(room)
    return message("Offline", "%s can't be reached right now. Pages you've opened before are still here; "
                              "this one isn't yet." % name, [("/", "Try Again")])


# -- header, tabs, footer ----------------------------------------------------------------------------------------

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")   # vendored: app/vaultkit/ui
if not os.path.isdir(UI_DIR):                                                 # machiya-kobo/machiya itself: ui/ at the root
    UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")


def _ui_hash(name):
    try:
        with open(os.path.join(UI_DIR, name), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        return "0"


UI_VERSION = {n: _ui_hash(n) for n in ("machiya.css", "machiya.js", "machiya-sw.js")}


def ui_url(name):
    """/static/machiya.css?v=<content hash>: a new vaultkit UI gets a new URL, so apps may cache these for good (and a
    service worker can put UI_VERSION into its cache name)."""
    return "/static/%s?v=%s" % (name, UI_VERSION.get(name, "0"))


# -- the service worker (ui/machiya-sw.js; docs/ui.md "Service worker") -----------------------------------------------

def service_worker(version, precache, **options):
    """A room's whole /sw.js: import the shared core and configure it. version: the room's build hash (it goes into
    the static cache's name, with the UI's); precache: the shell URLs, versioned; options (all optional, JSON):
    offline ("/offline"), timeout (ms, 2500), bypass / network / assetMatch (lists of path regexps: never touched /
    navigations never stored / attachments, stale-while-revalidate), notes ({"match": "^/n/", "limit": 200}),
    pages (30), assets (100), pins (a URL answering {"urls": [...]}: notes kept on the device for good).
    Serve it with Cache-Control: no-cache, and /static/machiya-sw.js from app/vaultkit/ui/ like machiya.css."""
    cfg = dict(options, version="%s-%s" % (version, UI_VERSION.get("machiya-sw.js", "0")), precache=list(precache))
    return "importScripts(%s);\nmachiyaSW(%s);\n" % (json.dumps(ui_url("machiya-sw.js")), json.dumps(cfg, indent=1))


OFFLINE_PIN = '<meta name="machiya-offline" content="pin">'   # in a note page's head (page(head=…)): kept for good


def offline_row():
    """The settings row for what this device keeps for offline reading, with "Clear Offline Copies" (machiya.js asks
    the service worker for the counts and sends it CLEAR_OFFLINE)."""
    return ('<div class="item offline-copies"><span>Offline Copies<small class="value" data-offline-count></small></span>'
            '<button type="button" class="quiet" data-clear-offline>Clear Offline Copies</button></div>')


def room_info(key):
    return next((r for r in ROOMS if r[0] == key), (key, key.title(), "", ""))


def mark(room):
    """A room's mark: its own icon (the home-screen icon, drawn by machiya.css), the kanji kept as the tooltip and as a
    fallback for anything that doesn't load the stylesheet."""
    _, name, seal, _ = room_info(room)
    return '<span class="seal icon" data-room="%s" title="%s (%s)" aria-hidden="true">%s</span>' % (room, e(name), seal, seal)


def switcher(room, links, cls="rooms", settings=False, who=""):
    """The Rooms menu: the rooms front to back, then the neighbours; the current room is plain text. With settings,
    a Settings row closes the menu (phones, where the header's gear is hidden), and `who` (the signed-in name) a row
    for the account above it."""
    rows = []
    for key, name, seal, what in ROOMS:
        if key == room:
            rows.append('<b data-room="%s">%s%s<small>here</small></b>' % (key, mark(key), e(name)))
        elif key in links:
            rows.append('<a href="%s/" data-room="%s">%s%s<small>%s</small></a>'
                        % (e(links[key]), key, mark(key), e(name), e(what)))
    nb = ['<a href="%s/" data-room="%s"><span class="seal icon neighbour-icon" data-room="%s" aria-hidden="true"></span>%s<small>%s</small></a>'
          % (e(links[k]), k, k, e(n), e(w)) for k, n, w in NEIGHBOURS if k in links]      # their own logos (machiya.css)
    gear = ('<hr><a href="/settings"><span class="neighbour">%s</span>Settings</a>' % GLYPH["gear"]) if settings else ""
    if settings and who:
        gear = ('<hr><a href="/settings#account" class="who"><span class="neighbour">%s</span>%s<small>signed in</small></a>'
                % (GLYPH["person"], e(who))) + gear[4:]
    if len(rows) <= 1 and not nb and not settings:
        return ""
    return ('<details class="%s"><summary title="Rooms" aria-label="Rooms">%s</summary><nav class="menu" aria-label="Rooms">%s%s%s%s</nav></details>'
            % (cls, GLYPH["rooms"], "".join(rows), "<hr>" if nb else "", "".join(nb), gear))


def header(room, nav, current, links, subtitle="", tools="", settings=True, who="", search=""):
    """nav = [(href, key, label)]; tools = extra HTML before the switcher; who = the signed-in name (v0.13): a person
    button before the gear, to the Account settings; search = search_bar(...) (v0.17): the pill under the top bar."""
    _, name, seal, _ = room_info(room)
    items = "".join(('<b class="here">%s</b>' % e(label)) if key == current else '<a href="%s">%s</a>' % (e(href), e(label))
                    for href, key, label in nav)
    gear = ('<a class="iconbtn gear" href="/settings" title="Settings" aria-label="Settings">%s</a>' % GLYPH["gear"]) if settings else ""
    if who and settings:
        gear = ('<a class="iconbtn who" href="/settings#account" title="Signed in as %s" aria-label="Signed in as %s">%s</a>'
                % (e(who), e(who), GLYPH["person"])) + gear
    return ('<header class="top"><div class="topbar"><a class="brand" href="/">%s'
            '<span class="word">%s</span></a>%s<nav class="nav">%s</nav><div class="tools">%s%s%s</div></div>%s</header>\n'
            % (mark(room), e(name), ('<span class="subtitle">%s</span>' % e(subtitle)) if subtitle else "", items, tools,
               switcher(room, links), gear, ('<div class="searchrow">%s</div>' % search) if search else ""))


def tabbar(tabs, current, room, links, icons=None, who=""):
    """Phones: the room's own tabs (at most four: [(href, key, label)]) plus a Rooms tab when there are rooms."""
    icons = icons or {}
    out = ['<a href="%s"%s>%s<span>%s</span></a>' % (e(href), ' class="here" aria-current="page"' if key == current else "",
                                                     icons.get(key, GLYPH.get(key, "")), e(label))
           for href, key, label in tabs[:4]]
    menu = switcher(room, links, cls="rooms", settings=True, who=who)
    if menu:
        out.append(menu.replace('<summary title="Rooms" aria-label="Rooms">%s</summary>' % GLYPH["rooms"],
                                '<summary title="Rooms" aria-label="Rooms">%s<span>Rooms</span></summary>' % GLYPH["rooms"], 1))
    return '<nav class="tabbar" aria-label="Sections">%s</nav>' % "".join(out)


def footer(room, status=None, links=()):
    """status: {"text": "synced abc1234 3 min ago · 812 notes", "state": "ok|stale|down"}; links: [(href, label)]."""
    _, name, _, _ = room_info(room)
    parts = []
    if status:
        parts.append('<span class="status%s">%s · %s</span>' % (
            "" if status.get("state", "ok") == "ok" else " " + e(status["state"]), e(name), e(status.get("text", ""))))
    parts += ['<a href="%s">%s</a>' % (e(h), e(l)) for h, l in links]
    src = source_url()
    if src:
        parts.append('<a href="%s" rel="noopener">Source code</a>' % e(src))
    parts.append('<span>Part of Machiya</span>')
    return '<footer class="foot">%s</footer>' % "".join(parts)


def prefs_meta(url):
    """<meta name="machiya-prefs" content="/api/prefs">: this page's viewer has server-side preferences there, so
    machiya.js syncs theme and text size with it. Only a local path ("/...", not "//..."); anything else is ""."""
    if not isinstance(url, str) or not url.startswith("/") or url[1:2] in ("/", "\\") \
            or any(c.isspace() or ord(c) < 32 for c in url):
        return ""
    return '<meta name="machiya-prefs" content="%s">\n' % e(url)


def page(ctx, room, title, body, tabs=(), current="", links=None, head="", stylesheets=(), scripts=(), manifest=True,
         icons=None, prefs_url="", who=""):
    """The HTML5 document. stylesheets/scripts: the app's own (machiya.css and machiya.js come first); icons: the SVG
    for each tab key (the room's own glyphs; GLYPH covers the rooms, rooms, gear). prefs_url: the room's /api/prefs
    when this request has a principal with preferences (prefs_meta); machiya.js then syncs theme and text size.
    who (v0.13): the signed-in name, for the phone's Rooms sheet (pass the same to header())."""
    links = links if links is not None else rooms()
    theme = getattr(ctx, "theme", "system")
    theme = "system" if theme == "auto" else theme
    text = getattr(ctx, "text", "standard")
    palette = getattr(ctx, "palette", palettes.DEFAULT)
    palette = palette if palette in palettes.PALETTES else palettes.DEFAULT
    _, name, _, _ = room_info(room)
    bar = {m: _colors(palette, m)["theme_color"] for m in ("dark", "light")}
    if theme == "night":
        scheme, colors = "dark", '<meta name="theme-color" content="%s">' % bar["dark"]
    elif theme == "day":
        scheme, colors = "light", '<meta name="theme-color" content="%s">' % bar["light"]
    else:
        scheme = "dark light"
        colors = ('<meta name="theme-color" content="%s" media="(prefers-color-scheme: dark)">'
                  '<meta name="theme-color" content="%s" media="(prefers-color-scheme: light)">' % (bar["dark"], bar["light"]))
    css = "".join('<link rel="stylesheet" href="%s">\n' % e(h) for h in [ui_url("machiya.css")] + list(stylesheets))
    js = "".join('<script type="module" src="%s"></script>\n' % e(h) for h in [ui_url("machiya.js")] + list(scripts))
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>%s</title>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="color-scheme" content="%s">\n%s\n%s'
        '<link rel="icon" href="/static/icons/%s.svg" type="image/svg+xml">\n'
        '<link rel="apple-touch-icon" href="/static/icons/%s-apple-180.png">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n<meta name="mobile-web-app-capable" content="yes">\n'
        '<meta name="apple-mobile-web-app-status-bar-style" content="%s">\n'
        '<meta name="apple-mobile-web-app-title" content="%s">\n%s%s%s%s'
        '</head>\n<body class="theme-%s%s room-%s" data-room="%s" data-text="%s"%s>\n%s\n%s\n</body>\n</html>\n'
    ) % (e(title), scheme, colors,
         '<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">\n' if manifest else "",
         room, room, "default" if theme == "day" else "black-translucent", e(name), prefs_meta(prefs_url) if prefs_url else "", css, js, head, theme,
         "" if palette == palettes.DEFAULT else " palette-" + palette, room, room, e(text),
         (' data-cookie-domain="%s"' % e(COOKIE_DOMAIN)) if COOKIE_DOMAIN else "", body,
         tabbar(tabs, current, room, links, icons, who) if tabs else "")


# -- /settings ----------------------------------------------------------------------------------------------------

def appearance_section(ctx, synced=False):
    """Display: the theme (palette, v0.15), its appearance (System follows the device; Light, Dark) and the text
    size. synced (v0.13): the page has a prefs_url, so they also follow the person to other devices."""
    theme = getattr(ctx, "theme", "system")
    text = getattr(ctx, "text", "standard")
    palette = getattr(ctx, "palette", palettes.DEFAULT)
    return ("Display", [
        select("Theme", "palette", PALETTES, palette, cookie=True),
        select("Appearance", "theme", THEMES, theme, cookie=True),
        select("Text Size", "textSize", TEXT_SIZES, text, cookie=True),
    ], "Saved to your account, so your other devices follow." if synced else "Kept in this browser only.")


def toggle(label, key, on, cookie=False, disabled=False):
    return ('<label class="item"><span>%s</span><input type="checkbox" role="switch" class="switch" data-set="%s"%s%s%s></label>'
            % (e(label), e(key), " data-cookie" if cookie else "", " checked" if on else "", " disabled" if disabled else ""))


def select(label, key, choices, value, cookie=False):
    opts = "".join('<option value="%s"%s>%s</option>' % (e(v), " selected" if v == value else "", e(t)) for v, t in choices)
    return ('<label class="item"><span>%s</span><select data-set="%s"%s>%s</select></label>'
            % (e(label), e(key), " data-cookie" if cookie else "", opts))


def text_field(label, key, value, placeholder=""):
    return ('<label class="item"><span>%s</span><input type="text" data-set="%s" value="%s" placeholder="%s"></label>'
            % (e(label), e(key), e(value), e(placeholder)))


def row(label, value):
    return '<div class="item"><span>%s</span><span class="value">%s</span></div>' % (e(label), value)


def apps_section(room, links, shown):
    """Which rooms and neighbours appear on this device; addresses stay on the server (MACHIYA_ROOMS)."""
    items = [toggle(name, "show_" + key, shown.get(key, True)) for key, name, _, _ in ROOMS if key != room and key in links]
    items += [toggle(name, "show_" + key, shown.get(key, True)) for key, name, _ in NEIGHBOURS if key in links]
    if not items:
        return None
    return ("Apps", items, "Which rooms appear in the switcher on this device. Their addresses are set on the server.")


def about_section(room, version, status_text="", vaultkit=""):
    items = [row("Version", e(version))]
    if vaultkit:
        items.append(row("vaultkit", e(vaultkit)))
    if status_text:
        items.append(row("Vault", e(status_text)))
    src = source_url()
    if src:
        items.append(row("Source code", '<a href="%s" rel="noopener">%s</a>' % (e(src), e(src))))
        items.append(row("Licence", "GNU AGPL-3.0-or-later"))
    _, name, seal, _ = room_info(room)
    return ("About", items, "%s (%s) is part of Machiya. Install it from the browser's menu (Add to Home Screen)." % (name, seal))


def settings_page(sections, room):
    """sections: [(title, [row html…], footnote or "")]; None entries are skipped. machiya.js saves every change."""
    out = ['<main class="settings" data-settings-room="%s"><h1 class="sechead" style="display:none">Settings</h1>' % e(room)]
    for sec in sections:
        if not sec:
            continue
        title, items, note = sec
        out.append('<h2 id="%s">%s</h2><div class="group">%s</div>%s' % (e(title.lower().replace(" ", "-")), e(title), "".join(items),
                                                                ('<p class="footnote">%s</p>' % e(note)) if note else ""))
    out.append("</main>")
    return "".join(out)


def settings_json(room):
    """The localStorage key the room's settings live under (machiya.js), e.g. kuraSettings."""
    return json.dumps(room + "Settings")
