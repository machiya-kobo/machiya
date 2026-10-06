"""Gemtext and gophermaps as HTML. Remote text is always escaped and there is no script at all (the pages are served
with a no-script CSP). gemini:// and gopher:// links go through /page; http(s) links go direct (rel=noreferrer);
other schemes (spartan, finger, mailto, …) are shown as text.
"""
import html
import re
from urllib.parse import quote, urljoin

import smolnet

e = lambda s: html.escape(s or "", quote=True)   # noqa: E731


def proxied(url):
    return "/page?url=" + quote(url, safe="")


def link(target, label):
    """An <a> for a resolved URL, or plain text for a scheme we don't follow."""
    scheme = target.split(":", 1)[0].lower() if ":" in target else ""
    if scheme in ("gemini", "gopher"):
        try:
            return '<a href="%s">%s</a>' % (e(proxied(smolnet.canonical(target))), e(label))
        except ValueError:
            return e(label)
    if scheme in ("http", "https"):
        return '<a class="web" href="%s" rel="noreferrer noopener" target="_blank">%s</a>' % (e(target), e(label))
    return '<span class="other" title="%s">%s</span>' % (e(target), e(label))


# -- gemtext ---------------------------------------------------------------------------------------------------------

def gemtext(text, base):
    """-> (title, html, plain). `base` resolves relative links."""
    out, plain, title = [], [], ""
    pre, pre_lines, in_list = False, [], False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in text.splitlines():
        if line.startswith("```"):
            if pre:
                out.append("<pre>%s</pre>" % e("\n".join(pre_lines)))
                plain.append("\n".join(pre_lines))
                pre, pre_lines = False, []
            else:
                close_list()
                pre = True
            continue
        if pre:
            pre_lines.append(line)
            continue
        if line.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>%s</li>" % e(line[2:]))
            plain.append(line[2:])
            continue
        close_list()
        if line.startswith("=>"):
            m = re.match(r"^=>\s*(\S+)(?:\s+(.*))?$", line)
            if m:
                target = urljoin(base, m.group(1))
                label = (m.group(2) or "").strip() or m.group(1)
                out.append('<p class="link">%s</p>' % link(target, label))
                plain.append(label)
            continue
        m = re.match(r"^(#{1,3})\s*(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (level, e(m.group(2)), level))
            plain.append(m.group(2))
            title = title or m.group(2).strip()
            continue
        if line.startswith(">"):
            out.append("<blockquote>%s</blockquote>" % e(line[1:].strip()))
            plain.append(line[1:].strip())
            continue
        if line.strip():
            out.append("<p>%s</p>" % e(line))
            plain.append(line)
        else:
            out.append('<p class="gap"></p>')
    close_list()
    if pre:                                                   # an unterminated block still shows
        out.append("<pre>%s</pre>" % e("\n".join(pre_lines)))
        plain.append("\n".join(pre_lines))
    return title, "\n".join(out), "\n".join(plain)


# -- gopher ----------------------------------------------------------------------------------------------------------

LINK_TYPES = set("0145679gIhdsp;:<P")      # everything that points somewhere; binaries are passed through by /page


def gophermap(text, host, port):
    """-> (title, html, plain): a menu keeps its ASCII layout (<pre>), with links and search forms inline."""
    out, plain, title = [], [], ""
    for line in text.splitlines():
        if line == ".":
            break
        if not line:
            out.append("")
            continue
        t, rest = line[0], line[1:]
        f = rest.split("\t")
        disp = f[0]
        sel = f[1] if len(f) > 1 else ""
        h = f[2] if len(f) > 2 and f[2] else host
        p = (f[3].strip() if len(f) > 3 else "") or str(port)
        plain.append(disp)
        if t in ("i", "3") or len(f) < 2:
            out.append('<span class="%s">%s</span>' % ("err" if t == "3" else "info", e(disp)))
            title = title or disp.strip()
            continue
        if t == "h" and sel.startswith("URL:"):
            out.append('<span class="t">web</span> ' + link(sel[4:], disp))
            continue
        if t in "8T+":
            out.append('<span class="t">tel</span> %s' % e(disp))
            continue
        url = smolnet.gopher_url(h, p, t, sel)
        if t == "7":
            out.append('<span class="t">find</span> <form class="gsearch" method="get" action="/page">'
                       '<input type="hidden" name="url" value="%s"><label>%s <input type="search" name="q" required>'
                       '</label> <button>Search</button></form>' % (e(url), e(disp)))
            continue
        tag = {"0": "text", "1": "menu"}.get(t, "file")
        out.append('<span class="t">%s</span> %s' % (tag, link(url, disp)))
    return title, '<pre class="menu">%s</pre>' % "\n".join(out), "\n".join(plain)


def plaintext(text):
    return '<pre class="text">%s</pre>' % e(text)


# -- the page --------------------------------------------------------------------------------------------------------

ENGINES = [("tlgs", "TLGS"), ("kennedy", "Kennedy"), ("veronica", "Veronica")]


def open_address(url=""):
    """The secondary way in: a small "Open an address…" disclosure (search is the main one)."""
    return ('<details class="open-address"%s><summary>Open an address…</summary><form method="get" action="/page">'
            '<input type="text" name="url" value="%s" placeholder="gemini://… or gopher://…" aria-label="Address" '
            'autocomplete="off" spellcheck="false" autocapitalize="off"><button>Open</button></form></details>'
            % ("", e(url)))


def search_form(q="", sources=None, big=False):
    """The search field: big and first on the search page, compact in a rendered page's header."""
    sources = sources or [k for k, _ in ENGINES]
    chips = "".join('<label class="engine"><input type="checkbox" name="source" value="%s"%s> %s</label>'
                    % (k, " checked" if k in sources else "", name) for k, name in ENGINES) if big else ""
    return ('<form class="%s" role="search" method="get" action="/"><div class="field">'
            '<input type="search" name="q" value="%s" placeholder="Search the Small Web" aria-label="Search" '
            'autocomplete="off" spellcheck="false" enterkeyhint="search"%s><button>Search</button></div>%s</form>'
            % ("find big" if big else "find", e(q), " autofocus" if big and not q else "",
               ('<div class="engines-pick">%s</div>' % chips) if chips else ""))


def page(title, body, url="", native=True, archive=None, notice="", status="", q="", home=False, sources=None):
    """The whole document. home: the search page (the big field first, results, then "Open an address…").
    Otherwise a rendered page: a compact search in the header, the address as a quiet line, then the page."""
    if home:
        head = '<header class="top"><a class="brand" href="/">smallweb</a></header>'
        top = search_form(q, sources, big=True)
        tail = open_address()
    else:
        head = '<header class="top"><a class="brand" href="/">smallweb</a>%s</header>' % search_form(q)
        bits = []
        if url:
            bits.append('<code class="url">%s</code>' % e(url))
            if native:
                bits.append('<a class="native" href="%s" title="Open in a Gemini or Gopher app">Open natively</a>' % e(url))
            if archive:
                bits.append(link(archive, "Archived"))
        top = ('<p class="addr">%s</p>' % " · ".join(bits)) if bits else ""
        tail = open_address()
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>%s</title>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="dark light">\n'
        '<meta name="referrer" content="no-referrer">\n'
        '<link rel="stylesheet" href="/static/smallweb.css">\n</head>\n<body class="%s">\n%s\n'
        '<main>\n%s%s%s\n%s</main>\n<footer>%s%s</footer>\n</body>\n</html>\n'
    ) % (e(title or url or "smallweb"), "home" if home else "reader", head, top,
         ('<p class="notice">%s</p>' % notice) if notice else "", body, tail,
         ('<span class="status">%s</span> ' % e(status)) if status else "",
         "Gemini and Gopher through smallweb, for you only. Pages you open are saved to Hister.")


def results(q, data, sources=None):
    """The search page's HTML body (the same data as /api/search)."""
    rows = []
    for r in data["results"]:
        snip = r["snippet"]
        marked, i = [], 0
        for a, b in r["marks"]:
            marked.append(e(snip[i:a]) + "<mark>" + e(snip[a:b]) + "</mark>")
            i = b
        marked.append(e(snip[i:]))
        extra = [e(", ".join(r["sources"])), e(r["kind"] or ""), e(r["size"] or "")]
        rows.append('<li><a class="title" href="%s">%s</a><span class="url">%s</span><p>%s</p>'
                    '<span class="meta">%s%s</span></li>'
                    % (e(proxied(r["url"])), e(r["title"]), e(r["url"]), "".join(marked),
                       " · ".join(x for x in extra if x),
                       (" · " + link(r["archive_url"], "archived")) if r["archive_url"] else ""))
    status = " · ".join("%s %s" % (k, ("%d" % v["total"] if v.get("total") is not None else "ok") if v["ok"]
                                   else data["errors"].get(k, "failed")) for k, v in data["sources"].items())
    body = '<p class="engines">%s</p><ol class="results">%s</ol>' % (e(status), "".join(rows))
    if not rows:
        body += '<div class="empty"><h2>No Results</h2><p>Nothing on the small web matched “%s”.</p></div>' % e(q)
    nav, src = [], ("&amp;source=" + ",".join(sources)) if sources and len(sources) < len(ENGINES) else ""
    if data["page"] > 1:
        nav.append('<a href="/?q=%s%s&amp;page=%d">Previous</a>' % (quote(q), src, data["page"] - 1))
    if any(v.get("next") for v in data["sources"].values()):
        nav.append('<a href="/?q=%s%s&amp;page=%d">Next</a>' % (quote(q), src, data["page"] + 1))
    return body + ('<p class="pager">%s</p>' % " · ".join(nav) if nav else "")
