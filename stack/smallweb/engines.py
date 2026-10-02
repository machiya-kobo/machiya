"""The three search engines (formats checked against the live engines) and the merge.
None has an API: each answers a search as a gemtext page or a gophermap, parsed here.

- TLGS      gemini://tlgs.one/search?<q>, page N /search/N?<q>          10 per page, full text, TARDIS archive links
- Kennedy   gemini://kennedy.gemi.dev/search?<q>, page N /search/p:N/?<q>  15 per page, full text
- Veronica  gopher.floodgap.com:70 "/v2/vs<TAB>q", page N "/v2/vs?q forward=30(N-1)"  30 per page, titles/selectors only
"""
import re
from urllib.parse import quote

import smolnet

ENGINES = {    # host, port: tests point these at local fakes
    "tlgs": {"scheme": "gemini", "host": "tlgs.one", "port": 1965},
    "kennedy": {"scheme": "gemini", "host": "kennedy.gemi.dev", "port": 1965},
    "veronica": {"scheme": "gopher", "host": "gopher.floodgap.com", "port": 70},
}
ORDER = ["tlgs", "kennedy", "veronica"]
GOPHER_KIND = {"0": "text", "1": "menu", "7": "search", "h": "html", "i": "info", "3": "error"}


def netloc(e):
    return e["host"] if e["port"] == smolnet.DEFAULT_PORT[e["scheme"]] else "%s:%d" % (e["host"], e["port"])


def request(engine, q, page):
    """What to ask: ("gemini", url) or ("gopher", host, port, selector, query)."""
    e = ENGINES[engine]
    if engine == "tlgs":
        path = "/search" if page == 1 else "/search/%d" % page
        return ("gemini", "gemini://%s%s?%s" % (netloc(e), path, quote(q)))
    if engine == "kennedy":
        path = "/search" if page == 1 else "/search/p:%d/" % page
        return ("gemini", "gemini://%s%s?%s" % (netloc(e), path, quote(q)))
    if page == 1:
        return ("gopher", e["host"], e["port"], "/v2/vs", q)
    return ("gopher", e["host"], e["port"], "/v2/vs?%s forward=%d" % (q, 30 * (page - 1)), "")


# -- snippets --------------------------------------------------------------------------------------------------------

def highlight(word, terms):
    """Is '[word]' an engine's highlight? The engines bracket single matched words (stemmed: [examples] for example);
    anything else in brackets is the page's own text, e.g. a Markdown link '[Some Link](…)'."""
    w = word.lower()
    if not re.fullmatch(r"[\w'’.-]+", w):
        return False
    if not terms:
        return True
    for t in terms:
        stem = min(len(t), len(w), 5)
        if t in w or w in t or (stem >= 3 and w[:stem] == t[:stem]):
            return True
    return False


def unbracket(text, terms=()):
    """'my [example] of' -> ('my example of', [[3, 10]]): the engines mark matched words with brackets. Offsets are code
    points into the returned text. Brackets that aren't highlights stay as they are."""
    out, marks, i = [], [], 0
    for m in re.finditer(r"\[([^\[\]]{1,80})\]", text):
        if not highlight(m.group(1), terms):
            continue
        out.append(text[i:m.start()])
        start = sum(len(p) for p in out)
        out.append(m.group(1))
        marks.append([start, start + len(m.group(1))])
        i = m.end()
    out.append(text[i:])
    return "".join(out), marks


def terms_of(q):
    return [t for t in re.findall(r"[\w'’-]+", (q or "").lower()) if t not in ("and", "or", "not") and ":" not in t]


def hit(engine, url, title, snippet="", kind="", size=None, archive=None, terms=()):
    snip, marks = unbracket(" ".join(snippet.split()), terms)
    title = " ".join(title.split())
    try:
        url = smolnet.canonical(url)
    except ValueError:
        return None
    return {"title": title.strip() or url, "url": url, "snippet": snip, "marks": marks, "source": engine,
            "sources": [engine], "scheme": url.split(":", 1)[0], "kind": kind or None, "size": size,
            "archive_url": archive}


# -- parsers ---------------------------------------------------------------------------------------------------------

LINK_RE = re.compile(r"^=>\s*(\S+)(?:\s+(.*))?$")


def parse_tlgs(text, q=""):
    """-> (hits, total, next). A hit: '=> gemini://… Title (mime, size)', '* url', one snippet line, a TARDIS link."""
    lines = text.splitlines()
    hits, total, nxt, i = [], None, False, 0
    while i < len(lines):
        line = lines[i]
        m = LINK_RE.match(line)
        if m and m.group(1).startswith("gemini://") and i + 1 < len(lines) and lines[i + 1].startswith("* "):
            url, label = m.group(1), (m.group(2) or "")
            t = re.match(r"^(.*?)\s*\(([^,()]+),\s*([^()]+)\)\s*$", label)
            title, kind, size = (t.group(1), t.group(2).strip(), t.group(3).strip()) if t else (label, "", None)
            i += 2
            snippet, archive = "", None
            if i < len(lines) and not lines[i].startswith("=>") and lines[i].strip():
                snippet = lines[i]
                i += 1
            a = LINK_RE.match(lines[i]) if i < len(lines) else None
            if a and "tardis" in a.group(1) and a.group(1).startswith("gemini://"):
                archive = a.group(1)
                i += 1
            h = hit("tlgs", url, title, snippet, kind, size, archive, terms_of(q))
            if h:
                hits.append(h)
            continue
        f = re.search(r"Page (\d+) of (\d+) \((\d+) results?\)", line)
        if f:
            total = int(f.group(3))
            nxt = int(f.group(1)) < int(f.group(2))
        if m and "Next Page" in (m.group(2) or ""):
            nxt = True
        i += 1
    return hits, total, nxt


def parse_kennedy(text, q=""):
    """-> (hits, total, next). A hit: '=> gemini://… N. Title (mime • lines • size)', '* breadcrumb',
    'Language: …' (optional), '>snippet'. A Gemipedia box (no number) comes first sometimes and is skipped."""
    lines = text.splitlines()
    hits, total, nxt, i = [], None, False, 0
    while i < len(lines):
        line = lines[i]
        m = LINK_RE.match(line)
        n = re.match(r"^(\d+)\.\s+(.*)$", (m.group(2) or "")) if m else None
        if m and n and m.group(1).startswith("gemini://"):
            url, label = m.group(1), n.group(2)
            t = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", label)
            title, meta = (t.group(1), t.group(2)) if t else (label, "")
            parts = [p.strip() for p in meta.split("•")] if meta else []
            kind = parts[0] if parts else ""
            size = parts[-1] if len(parts) > 1 else None
            i += 1
            snippet = ""
            while i < len(lines) and not lines[i].startswith("=>"):
                if lines[i].startswith(">"):
                    snippet = lines[i][1:].lstrip("…").strip()
                    i += 1
                    break
                i += 1
            h = hit("kennedy", url, title, snippet, kind, size, terms=terms_of(q))
            if h:
                hits.append(h)
            continue
        f = re.search(r"Showing \d+ - \d+ of (\d+) results?", line)
        if f:
            total = int(f.group(1))
        if m and "Next Page" in (m.group(2) or ""):
            nxt = True
        i += 1
    return hits, total, nxt


def menu_items(text):
    """A gophermap -> [(type, display, selector, host, port)]."""
    out = []
    for line in text.splitlines():
        if line == ".":
            break
        if not line:
            continue
        f = line[1:].split("\t")
        out.append((line[0], f[0], f[1] if len(f) > 1 else "", f[2] if len(f) > 2 else "",
                    f[3].strip() if len(f) > 3 else "70"))
    return out


def parse_veronica(text, q=""):
    """-> (hits, total, next). Each hit is a menu item followed by an 'i' line holding its gopher:// URL; Floodgap's
    own navigation and notices have no such line. No snippets: the item's kind, host and selector stand in."""
    items = menu_items(text)
    hits, total, nxt = [], None, False
    for k, (t, disp, sel, host, port) in enumerate(items):
        if t == "i":
            m = re.search(r"Approximately ([\d,]+) total matches", disp)
            if m:
                total = int(m.group(1).replace(",", ""))
            continue
        if t == "1" and disp.strip().lower().startswith("next ") and "forward=" in sel:
            nxt = True
            continue
        after = items[k + 1] if k + 1 < len(items) else None
        if not (after and after[0] == "i" and after[1].startswith("gopher://")):
            continue
        kind = GOPHER_KIND.get(t, "binary")
        disp = re.sub(r"\s{2,}\d{4}-\w{3}-\d{2}\s+\d{2}:\d{2}\s+\S+\s*$", "", disp)   # a listing's date/size columns
        where = "%s%s" % (host, (" › " + sel) if sel and sel != "/" else "")
        h = hit("veronica", after[1].strip(), disp, "%s · %s" % (kind, where), kind)
        if h:
            hits.append(h)
    return hits, total, nxt


PARSERS = {"tlgs": parse_tlgs, "kennedy": parse_kennedy, "veronica": parse_veronica}


# -- merge -----------------------------------------------------------------------------------------------------------

def dedupe_key(url):
    return url.rstrip("/").lower()


def merge(lists):
    """[(engine, hits)] in the requested order -> one list, interleaved by rank, deduped on the normalised URL (a
    duplicate adds its engine to `sources` and fills in a missing snippet or archive link)."""
    out, seen = [], {}
    depth = max((len(h) for _, h in lists), default=0)
    for r in range(depth):
        for engine, hits in lists:
            if r >= len(hits):
                continue
            h = hits[r]
            key = dedupe_key(h["url"])
            if key in seen:
                first = seen[key]
                if engine not in first["sources"]:
                    first["sources"].append(engine)
                if not first["snippet"] and h["snippet"]:
                    first["snippet"], first["marks"] = h["snippet"], h["marks"]
                first["archive_url"] = first["archive_url"] or h["archive_url"]
                continue
            seen[key] = h
            out.append(h)
    return out
