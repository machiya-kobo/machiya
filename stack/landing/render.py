"""The landing page's HTML, built on vaultkit's shell (the rooms' header, tab bar, Rooms menu, footer and settings).
Everything an app said is escaped here; nothing from an answer is ever markup."""
import datetime
import os

import deploys
import probes
from vaultkit import shell as house
from vaultkit import verify as vk_verify

e = house.e
ROOM = "machiya"
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")


def _hash(name):
    import hashlib
    try:
        with open(os.path.join(STATIC_DIR, name), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        return "0"


STATIC_V = {n: _hash(n) for n in ("landing.css", "landing.js")}


def static_url(name):
    return "/static/%s?v=%s" % (name, STATIC_V.get(name, "0"))


def vaultkit_version():
    """'0.17.2' from the vendored manifest's first line."""
    return probes.vk(vk_verify.version().split(" - ")[0])


_SVG = house._SVG
ICONS = {   # the tab bar's own glyphs (the Rooms tab and the gear are the shell's)
    "home": _SVG % '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
                   'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',                                                  # today (a sun)
    "status": _SVG % '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "sync": _SVG % '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4"/>',
    "deploys": _SVG % '<path d="M12 3 3 7.5 12 12l9-4.5zM3 12l9 4.5 9-4.5M3 16.5 12 21l9-4.5"/>',
    "service": _SVG % '<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/>'
                      '<path d="M8 7.5h.01M8 16.5h.01"/>',
}
TABS = [("/", "home", "Home"), ("/status", "status", "Status")]          # + the shell's Rooms tab
NAV = [("/", "home", "Home"), ("/status", "status", "Status"), ("/status#sync", "sync", "Sync"),
       ("/status#deploys", "deploys", "Deploys")]
WORD = {"up": "Up", "behind": "Behind", "starting": "Starting", "error": "Error", "down": "Down", "absent": "Not in this stack"}
FOOT = {"up": "ok", "behind": "stale", "error": "down"}


# -- time ------------------------------------------------------------------------------------------------------------

def ago(at, now):
    """'just now', '3 min ago', '2 h ago', '4 days ago' (a time in the future reads 'just now')."""
    s = max(0, int(now - at))
    if s < 45:
        return "just now"
    if s < 3600:
        return "%d min ago" % max(1, round(s / 60))
    if s < 48 * 3600:
        return "%d h ago" % (s // 3600)
    return "%d days ago" % (s // 86400)


def when(at, now, tz=None):
    """<time> with the relative age and the exact local time as its tooltip."""
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return ""
    dt = datetime.datetime.fromtimestamp(at, tz or datetime.timezone.utc)
    return '<time datetime="%s" title="%s">%s</time>' % (
        e(dt.isoformat(timespec="seconds")), e(dt.strftime("%Y-%m-%d %H:%M %Z").strip()), e(ago(at, now)))


# -- pieces ----------------------------------------------------------------------------------------------------------

def dot(state):
    return '<i class="dot" data-state="%s" aria-hidden="true"></i>' % e(state)


def mark(key):
    if key in dict((k, 1) for k, _, _, _ in house.ROOMS):
        return '<span class="seal icon" data-room="%s" aria-hidden="true"></span>' % e(key)
    if key in ("hister", "searxng"):
        return '<span class="seal icon neighbour-icon" data-room="%s" aria-hidden="true"></span>' % e(key)
    return '<span class="glyph" aria-hidden="true">%s</span>' % ICONS["service"]


def version_line(a):
    bits = []
    if a.get("version"):
        bits.append(e(a["version"]))
    if a.get("build"):
        bits.append("build %s" % e(a["build"]))
    if a.get("vaultkit"):
        bits.append("vaultkit %s" % e(a["vaultkit"]))
    return " · ".join(bits)


def state_line(a):
    word = WORD.get(a["state"], a["state"].title())
    if a["state"] in ("down", "error", "starting") and a.get("error"):
        return "%s%s · %s" % (dot(a["state"]), e(word), e(a["error"]))
    return dot(a["state"]) + e(word)


def tile(key, name, what, a, href):
    """A room: its icon, name and what it is, the state, the version and a fact or two. The whole tile is the link."""
    facts = list(a.get("facts") or [])
    inner = ('<span class="thead">%s<span class="tname">%s</span><span class="twhat">%s</span></span>'
             '<span class="tstate">%s</span>%s%s%s'
             % (mark(key), e(name), e(what), state_line(a),
                ('<span class="tver">%s</span>' % version_line(a)) if version_line(a) else "",
                ('<span class="tfacts">%s</span>' % e(" · ".join(facts))) if facts else "",
                ('<span class="tfacts">%s</span>' % e(a["more"])) if a.get("more") else ""))
    if a["state"] == "absent" or not href:
        return '<div class="tile" data-app="%s" data-state="%s">%s</div>' % (e(key), e(a["state"]), inner)
    return '<a class="tile" data-app="%s" data-state="%s" href="%s/">%s</a>' % (e(key), e(a["state"]), e(href), inner)


def row(key, name, what, a, href):
    """An engine or a stack service: one line in a list."""
    title = ('<a href="%s">%s</a>' % (e(href), e(name))) if href and a["state"] != "absent" else e(name)
    meta = [version_line(a)] + [e(f) for f in a.get("facts") or []]
    return ('<li class="app" data-app="%s" data-state="%s">%s<span class="aname">%s<small>%s</small></span>'
            '<span class="ameta">%s</span><span class="astate">%s</span></li>'
            % (e(key), e(a["state"]), mark(key), title, e(what), " · ".join(m for m in meta if m), state_line(a)))


def sync_row(r, now, tz):
    detail = e(r.get("text") or "")
    if r["state"] == "error" and r.get("error"):
        detail = (detail + " · " if detail else "") + '<span class="err">%s</span>' % e(r["error"])
    return ('<li class="sync" data-state="%s"%s>%s<span class="slabel">%s<small>%s</small></span>'
            '<span class="sdetail">%s</span><span class="sage">%s</span></li>'
            % (e(r["state"]), ' data-quiet' if r.get("quiet") else "", dot(r["state"]), e(r["label"]), e(r["source"]),
               detail, when(r.get("at"), now, tz) or '<span class="muted">—</span>'))


def changes(items, more_versions=0, per_version=1, width=150):
    """A changelog excerpt: [(version, [items])] -> a short list."""
    out = []
    for version, lines in items:
        shown = lines[:per_version]
        for i, line in enumerate(shown):
            text = line if len(line) <= width else line[:width - 1].rsplit(" ", 1)[0] + "…"
            extra = len(lines) - len(shown) if i == len(shown) - 1 else 0
            out.append('<li>%s%s%s</li>' % (('<b>%s</b> ' % e(version)) if i == 0 else "", e(text),
                                            (' <span class="more">+%d more</span>' % extra) if extra else ""))
    if more_versions:
        out.append('<li class="more">%s</li>' % e("and %d earlier version%s" % (more_versions, "" if more_versions == 1 else "s")))
    return ('<ul class="changes">%s</ul>' % "".join(out)) if out else ""


def deploy_item(ev, logs, now, tz):
    key = ev.get("app", "")
    name = probes.NAMES.get(key, key)
    ver = "%s → %s" % (ev["from"], ev["to"]) if ev.get("from") else ev.get("to", "")
    kit = ""
    if ev.get("vaultkit_from") and ev.get("vaultkit_to") and ev["vaultkit_from"] != ev["vaultkit_to"]:
        kit = '<span class="chip">vaultkit %s → %s</span>' % (e(ev["vaultkit_from"]), e(ev["vaultkit_to"]))
    picked = deploys.between(logs.get(key) or {}, ev.get("from"), ev.get("to"))
    return ('<li class="deploy">%s<div class="dbody"><div class="dhead"><b>%s</b><span class="dver">%s</span>%s%s</div>%s</div></li>'
            % (mark(key), e(name), e(ver), kit, when(ev.get("at"), now, tz), changes(picked[:2], max(0, len(picked) - 2))))


def running_item(key, a, logs):
    name = probes.NAMES.get(key, key)
    sections = logs.get(key) or {}
    picked = [(a["version"], sections[a["version"]])] if a.get("version") in sections else []
    return ('<li class="deploy">%s<div class="dbody"><div class="dhead"><b>%s</b><span class="dver">%s</span></div>%s</div></li>'
            % (mark(key), e(name), version_line(a), changes(picked, per_version=1)))


# -- the page --------------------------------------------------------------------------------------------------------

def main_html(snap, history, logs, links, targets, now, tz=None):
    """The <main> of the landing page (landing.js swaps it in place on its refresh)."""
    apps = snap["apps"]
    ov = snap["overall"]
    present = sum(1 for a in apps.values() if a["state"] != "absent")
    out = ['<main class="landing" data-checked="%d">' % int(snap["checked"])]
    out.append('<section class="overview" data-state="%s"><p class="overall">%s%s</p><p class="checked">Checked %s · '
               '%s</p></section>' % (e(ov["state"]), dot(ov["state"]), e(ov["text"]), when(snap["checked"], now, tz),
                                     e(probes.plural(present, "app"))))
    out.append('<h2 class="sechead" id="rooms">Rooms</h2><div class="tiles">')
    for key, name, group, what in probes.APPS:
        if group == "rooms":
            out.append(tile(key, name, what, apps[key], links.get(key) or targets.get(key)))
    out.append('</div>')
    for group, heading in (("engines", "Engines"), ("services", "Stack Services")):
        out.append('<h2 class="sechead">%s</h2><ul class="list card apps">' % heading)
        for key, name, g, what in probes.APPS:
            if g != group:
                continue
            href = links.get(key) or (targets.get(key) if group == "engines" else "")
            if group == "services" and key != "vault-mirror" and targets.get(key):
                href = targets[key].rstrip("/") + "/api/status"
            out.append(row(key, name, what, apps[key], href))
        out.append('</ul>')
    out.append('<h2 class="sechead" id="sync">Sync</h2>')
    if snap["sync"]:
        out.append('<ul class="list card syncs">%s</ul>' % "".join(sync_row(r, now, tz) for r in snap["sync"]))
    else:
        out.append('<div class="empty"><h2>Nothing to Show</h2><p>Sync shows up when Kura, Konbini, Niwa or Hister answer.</p></div>')
    out.append('<h2 class="sechead" id="deploys">Recent Deploys</h2>')
    recent = history.recent(now)
    if recent:
        out.append('<ol class="list card deploys">%s</ol>' % "".join(deploy_item(ev, logs, now, tz) for ev in recent))
    else:
        since = ("since %s" % when(history.since, now, tz)) if history.since else "yet"
        out.append('<p class="none">No deploys seen %s. A deploy is noted when an app answers with a new version.</p>' % since)
    if len(recent) < 3:
        running = [running_item(k, apps[k], logs) for k, _, _, _ in probes.APPS if apps[k].get("version")]
        if running:
            out.append('<h3 class="subhead">Running Now</h3><ul class="list card deploys running">%s</ul>' % "".join(running))
    if history.error:
        out.append('<p class="none">%s</p>' % e(history.error))
    out.append('</main>')
    return "".join(out)


def footer(snap, now):
    apps = snap["apps"]
    present = [a for a in apps.values() if a["state"] != "absent"]
    up = sum(1 for a in present if a["state"] not in probes.BAD)
    text = "checked %s · %d of %d up" % (ago(snap["checked"], now), up, len(present))
    return house.footer(ROOM, {"text": text, "state": FOOT.get(snap["overall"]["state"], "ok")})


def page(ctx, snap, history, logs, links, targets, now, tz=None):
    body = (house.header(ROOM, NAV, "status", links) + main_html(snap, history, logs, links, targets, now, tz)
            + footer(snap, now))
    return house.page(ctx, ROOM, house.title(ROOM, "Status"), body, TABS, "status", links=links, stylesheets=[static_url("landing.css")],
                      scripts=[static_url("landing.js")], icons=ICONS)


def settings(ctx, links, version):
    _, rows, _ = house.about_section(ROOM, version, "", vaultkit_version())
    about = ("About", rows, "Machiya's front door and status page. Install it from the browser's menu (Add to Home Screen).")
    sections = [house.appearance_section(ctx), house.apps_section(ROOM, links, {}), about]
    body = house.header(ROOM, NAV, "", links) + house.settings_page(sections, ROOM)
    return house.page(ctx, ROOM, house.title(ROOM, "Settings"), body, TABS, "", links=links,
                      stylesheets=[static_url("landing.css")], icons=ICONS)


def message(ctx, links, heading, text, status_title=""):
    body = house.header(ROOM, NAV, "", links) + house.message(heading, text, [("/", "Go to Machiya")])
    return house.page(ctx, ROOM, house.title(ROOM, status_title or heading), body, TABS, "", links=links,
                      stylesheets=[static_url("landing.css")], icons=ICONS)


# -- the launcher (/) -------------------------------------------------------------------------------------------------

READ_WORD = {True: "Starred in %s", False: "Read in %s"}


def pill(ov):
    n, st = ov.get("count") or 0, ov["state"]
    if st == "up":
        word = "All up"
    elif not n:
        word = "Status"
    elif st == "error":
        word = "1 needs a look" if n == 1 else "%d need a look" % n
    else:
        word = "%d behind" % n
    return '<a class="pill" href="/status" data-state="%s" title="%s">%s%s</a>' % (
        e(st), e(ov.get("text", "")), dot(st), e(word))


def search_pill(action):
    """Search everything: a GET form to Shiori's search page (another origin, so no live results), Shiori's field."""
    if not action:
        return ""
    return ('<form class="search launch" role="search" action="%s" method="get"><div class="field">'
            '<input type="search" name="q" placeholder="Search everything" aria-label="Search everything" '
            'autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search">'
            '<button type="button" class="clear" aria-label="Clear" title="Clear search">%s</button>'
            '<button type="submit" class="go" aria-label="Search" title="Search">%s</button></div></form>'
            % (e(action), house.GLYPH["close"], house.GLYPH["search"]))


def mini(key, name, a, count, href):
    """A room on the launcher: its icon, name and one count; a dot only when it isn't up (calm)."""
    flag = dot(a["state"]) if a["state"] not in ("up",) else ""
    return ('<a class="mini" data-app="%s" data-state="%s" href="%s/">%s<span class="mname">%s</span>'
            '<span class="mcount">%s%s</span></a>' % (e(key), e(a["state"]), e(href), mark(key), e(name), flag, e(count)))


def fact(a, word):
    return next((f for f in a.get("facts") or [] if f.endswith(word)), "")


def section(title, items, more=("", ""), empty=""):
    if items is None:
        return ""
    href, label = more
    head = '<h2 class="sechead">%s%s</h2>' % (e(title), ('<a class="more" href="%s">%s ›</a>' % (e(href), e(label))) if href else "")
    body = ('<ul class="list">%s</ul>' % "".join(items)) if items else '<p class="none">%s</p>' % e(empty)
    return '<section class="card tsec">%s%s</section>' % (head, body)


def due_word(days):
    if days < 0:
        return ("%d day%s overdue" % (-days, "" if days == -1 else "s"), "late")
    if days == 0:
        return ("due today", "soon")
    if days == 1:
        return ("due tomorrow", "soon")
    return ("in %d days" % days, "soon" if days <= 3 else "")


def home_html(snap, links, targets, search, now, tz=None):
    apps, t = snap["apps"], snap.get("today") or {}

    def link(key):
        return (links.get(key) or (targets.get(key) if str(targets.get(key, "")).startswith("http") else "") or "").rstrip("/")

    date = datetime.datetime.fromtimestamp(now, tz or datetime.timezone.utc)
    out = ['<main class="landing home" data-checked="%d">' % int(snap["checked"])]
    out.append('<section class="hero"><h1>%s</h1>%s</section>' % (e(date.strftime("%A %-d %B")), pill(snap["overall"])))
    out.append(search_pill(search))
    hd = (apps.get("hister") or {}).get("data") or {}
    counts = {
        "shiori": plural_or(hd.get("docs"), "page", "search"),
        "konbini": ("%s in WIP" % "{:,}".format(t["wip"])) if t.get("wip") is not None else (fact(apps["konbini"], "WIP") or fact(apps["konbini"], "cards")),
        "niwa": fact(apps["niwa"], "published"),
        "kura": plural_or(((apps["kura"].get("data") or {}).get("total_notes")), "note", fact(apps["kura"], "notes")),
    }
    tiles = [mini(k, n, apps[k], counts[k], link(k)) for k, n, g, _ in probes.APPS
             if g == "rooms" and apps[k]["state"] != "absent" and link(k)]
    if tiles:
        out.append('<nav class="minis" aria-label="Rooms">%s</nav>' % "".join(tiles))
    secs = []
    kb, ku, ni, sh = link("konbini"), link("kura"), link("niwa"), link("shiori")
    if t.get("working") is not None:
        secs.append(section("Working On", ['<li><a href="%s">%s</a>%s%s</li>' % (
            e(c["url"]), e(c["title"]), ('<small>%s</small>' % e(c["area"])) if c.get("area") else "",
            ('<p class="next">%s</p>' % e(c["next"])) if c.get("next") else "") for c in t["working"]],
            (kb + "/now", "Now") if kb else ("", ""), "Nothing in progress."))
    if t.get("due") is not None:
        rows = []
        for c in t["due"]:
            word, cls = due_word(c["days"])
            rows.append('<li><a href="%s">%s</a><span class="due%s" title="%s">%s</span></li>' % (
                e(c["url"]), e(c["title"]), (" " + cls) if cls else "", e(c["due"]), e(word)))
        secs.append(section("Due Soon", rows, ("", ""), "Nothing due in the next two weeks."))
    if t.get("notes") is not None:
        secs.append(section("Notes Changed", ['<li><a href="%s">%s</a><small>%s</small>%s</li>' % (
            e(n["url"]), e(n["title"]), e(n.get("folder") or ""), when(n.get("at"), now, tz)) for n in t["notes"]],
            (ku + "/", "Kura") if ku else ("", ""), "No notes yet."))
    if t.get("pages"):
        secs.append(section("Saved & Read", ['<li><a href="%s" rel="noopener noreferrer">%s</a><small>%s</small>%s%s</li>' % (
            e(p["url"]), e(p["title"]), e(p.get("domain") or ""),
            ('<span class="chip read%s">%s</span>' % (" starred" if p.get("starred") else "",
                                                     e(READ_WORD[bool(p.get("starred"))] % p["reader"]))) if p.get("reader") else "",
            when(p.get("at"), now, tz)) for p in t["pages"]], (sh + "/", "Shiori") if sh else ("", "")))
    if t.get("garden"):
        secs.append(section("Garden", ['<li><a href="%s">%s</a>%s</li>' % (
            e(g["url"]), e(g["title"]), ('<small>tended %s</small>' % e(ago(g["at"], now))) if g.get("at") else "")
            for g in t["garden"]], (ni + "/", "Niwa") if ni else ("", "")))
    if secs:
        out.append('<h2 class="sechead today-head">Today</h2><div class="today">%s</div>' % "".join(secs))
    elif not tiles:
        out.append('<div class="empty"><h2>Nothing Here Yet</h2><p>Rooms show up here when MACHIYA_ROOMS names them.</p></div>')
    out.append('</main>')
    return "".join(out)


def plural_or(n, word, fallback):
    return probes.plural(int(n), word) if isinstance(n, (int, float)) and not isinstance(n, bool) else fallback


def home(ctx, snap, links, targets, search, now, tz=None):
    body = house.header(ROOM, NAV, "home", links) + home_html(snap, links, targets, search, now, tz) + footer(snap, now)
    return house.page(ctx, ROOM, "Machiya", body, TABS, "home", links=links, stylesheets=[static_url("landing.css")],
                      scripts=[static_url("landing.js")], icons=ICONS)
