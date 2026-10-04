"""A code document as HTML (stdlib only). Hister extracts the text from the HTML, as it does for a visited page, and
its sensitive-content check reads only the HTML, so every document is sent with it.

The markdown is rendered just enough to read well in Hister's preview: headings, fenced code, paragraphs, lists as
lines. Everything is escaped; no link, image or raw HTML from a repo is ever passed through.
"""
import html
import re

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def markdown(text):
    out, para, code, fence = [], [], None, None

    def flush():
        if para:
            out.append("<p>%s</p>" % "<br>".join(html.escape(x) for x in para))
            para.clear()

    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if code is not None:
            if line.strip().startswith(fence):
                out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code)))
                code, fence = None, None
            else:
                code.append(line)
            continue
        m = FENCE.match(line)
        if m:
            flush()
            code, fence = [], m.group(1)
            continue
        m = HEADING.match(line)
        if m:
            flush()
            level = min(6, len(m.group(1)) + 1)          # the page's own <h1> is the title
            out.append("<h%d>%s</h%d>" % (level, html.escape(m.group(2)), level))
            continue
        if not line.strip():
            flush()
            continue
        para.append(line.strip())
    if code is not None:
        out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code)))
    flush()
    return "\n".join(out)


def page(title, url, meta_line, body_md, extra=""):
    """The whole document: title, a line of facts (repo, kind, state), then the body."""
    t = html.escape(title)
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>%s</title>"
            "<link rel=\"canonical\" href=\"%s\"></head><body><article><h1>%s</h1><p>%s</p>%s\n%s</article></body></html>"
            % (t, html.escape(url, quote=True), t, html.escape(meta_line), extra, markdown(body_md)))
