"""Allow-list HTML for a note's rendered body (v0.13): what Vault.render returns, and what a room puts in its pages.

A note is data, not code. Its Markdown may carry raw HTML (python-markdown passes it through), and a vault can be
written by someone other than the person reading it (a shared vault, a clipped web page), so every rendered body goes
through clean() before it reaches a page: known tags and attributes only; no script, style, iframe, form or event
handler; links and images only with a safe scheme (never javascript:, data:, vbscript:).

    clean(markup)                               pages: URLs kept as written; http(s), mailto, obsidian, gemini, gopher
    clean(markup, base=URL, schemes=API_SCHEMES)  an API answer read outside the room: relative URLs made absolute

autolink=True also turns bare http(s)/gemini/gopher URLs in text into links (never inside <a>, <code> or <pre>), with
trailing punctuation and an unbalanced closing bracket left out of the link.
"""
import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

ALLOWED = {"a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "details", "div", "dl", "dt", "em",
           "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "input", "ins", "kbd", "li",
           "mark", "ol", "p", "pre", "s", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td",
           "tfoot", "th", "thead", "tr", "u", "ul"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math",
                     "form", "textarea", "select", "button", "head", "title"}
ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title", "width", "height"},
         "td": {"colspan", "rowspan", "align"}, "th": {"colspan", "rowspan", "align"}, "ol": {"start"},
         "table": {"border", "cellpadding", "cellspacing"},      # Niwa's retro pages (Netscape-era tables)
         "input": {"type", "checked", "disabled"}, "span": {"title"}, "abbr": {"title"}}
COMMON = {"class"}
VOID = {"br", "hr", "img", "input"}
PAGE_SCHEMES = ("http", "https", "mailto", "obsidian", "gemini", "gopher")
API_SCHEMES = ("http", "https", "mailto", "obsidian")
SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*):", re.I)
URL_RE = re.compile(r"\b(?:https?|gemini|gopher)://[^\s<>\"']+", re.I)
NO_LINK_IN = {"a", "code", "pre"}


def _trim(url):
    """A bare URL without the sentence around it: trailing .,;:!?'" and a closing bracket nobody opened."""
    while url:
        last = url[-1]
        if last in ".,;:!?'\"":
            url = url[:-1]
        elif last in ")]}" and url.count(last) > url.count({")": "(", "]": "[", "}": "{"}[last]):
            url = url[:-1]
        else:
            break
    return url


def safe_url(value, schemes=PAGE_SCHEMES, base=None):
    """The URL to keep, or None. Characters a browser ignores (controls, whitespace) are removed before the scheme is
    read, so "java\\tscript:" is seen for what it is. With base, a relative URL is made absolute on it."""
    v = "".join(c for c in (value or "") if ord(c) > 32 and ord(c) != 127)
    m = SCHEME_RE.match(v)
    if m:
        return v if m.group(1).lower() in schemes else None
    if v.startswith("//"):                       # another host, scheme-relative: only via a known scheme
        return None
    return urljoin(base, v) if base else v


class Sanitizer(HTMLParser):
    def __init__(self, base=None, schemes=PAGE_SCHEMES, autolink=False):
        super().__init__(convert_charrefs=True)
        self.base = base.rstrip("/") + "/" if base else None
        self.schemes, self.autolink = schemes, autolink
        self.out, self.skip, self.open = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        if tag == "input" and dict(attrs).get("type") != "checkbox":
            return                                # a task list's box, nothing else
        kept = []
        for k, v in attrs:
            if k not in ATTRS.get(tag, set()) | COMMON:
                continue
            if k == "checked":
                kept.append(" checked")
                continue
            if k == "disabled":
                continue                          # every box is disabled (below)
            if v is None:
                continue
            if k in ("href", "src"):
                v = safe_url(v, self.schemes, self.base)
                if v is None:
                    continue
            kept.append(' %s="%s"' % (k, html.escape(v, quote=True)))
        if tag == "input":
            kept.append(" disabled")              # a page shows the note; it can't change it
        self.out.append("<%s%s>" % (tag, "".join(kept)))
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            return                                # <script/>: nothing inside to skip
        self.handle_starttag(tag, attrs)
        if tag in self.open[-1:] and tag not in VOID:
            self.out.append("</%s>" % tag)
            self.open.pop()

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED or tag in VOID or tag not in self.open:
            return
        while self.open:                          # close what was left open inside it, so the output stays balanced
            t = self.open.pop()
            self.out.append("</%s>" % t)
            if t == tag:
                break

    def handle_data(self, data):
        if self.skip:
            return
        if not self.autolink or any(t in NO_LINK_IN for t in self.open):
            self.out.append(html.escape(data, quote=False))
            return
        at = 0
        for m in URL_RE.finditer(data):
            url = _trim(m.group(0))
            if not url or safe_url(url, self.schemes) is None:
                continue
            self.out.append(html.escape(data[at:m.start()], quote=False))
            self.out.append('<a href="%s">%s</a>' % (html.escape(url, quote=True), html.escape(url, quote=False)))
            at = m.start() + len(url)
        self.out.append(html.escape(data[at:], quote=False))

    def close(self):
        super().close()
        while self.open:
            self.out.append("</%s>" % self.open.pop())


def clean(markup, base=None, schemes=PAGE_SCHEMES, autolink=False):
    """markup with everything not on the allow-list removed (see the module docstring)."""
    s = Sanitizer(base, schemes, autolink)
    s.feed(markup or "")
    s.close()
    return "".join(s.out)
