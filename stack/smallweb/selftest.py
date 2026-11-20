"""smallweb's live self-test: what the unit tests can't check (the real engines, the proxy, Nogon). Run it in a throwaway
container on the proxy's network (+ Nogon's for --nogon):

    docker run --rm --network proxy-net -e SMALLWEB_SOCKS=socks5h://proxy:1080 machiya-smallweb:local \
        python3 /app/selftest.py [--nogon]

By default it is a dry run: it never contacts Nogon. With --nogon (and SMALLWEB_NOGON_URL, on Nogon's network) it adds
ONE document under gemini://smallweb-selftest.invalid/…, checks Nogon indexed it, and deletes it again (also on
failure); it never touches any other document. State goes to a throwaway dir, never the real /data.
Output: one line per check ("PASS|FAIL|SKIP name: detail"), then "selftest: OK|FAILED". Exit 0 only when nothing
FAILed. Takes 10 to 40 s; give it 90. Engines being down is a SKIP for that engine, not a FAIL, unless none answers.
0.6.0: --hister and SMALLWEB_HISTER_URL are old names (Hister is now Nogon): on their own they stop the test, naming
the new one.
"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

if "--hister" in sys.argv:
    raise SystemExit("selftest: --hister is an old name: use --nogon (Hister is now Nogon)")
if "SMALLWEB_HISTER_URL" in os.environ and "SMALLWEB_NOGON_URL" not in os.environ:
    raise SystemExit("selftest: SMALLWEB_HISTER_URL is an old name and is no longer read: rename it SMALLWEB_NOGON_URL "
                     "(Hister is now Nogon)")
DATA = tempfile.mkdtemp(prefix="smallweb-selftest-")
os.environ["SMALLWEB_DATA"] = DATA
NOGON = os.environ.get("SMALLWEB_NOGON_URL", "").rstrip("/")
USE_NOGON = "--nogon" in sys.argv
for _name in ("SMALLWEB_NOGON_URL", "SMALLWEB_HISTER_URL"):
    os.environ.pop(_name, None)                         # smallweb itself never saves during the test
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smallweb   # noqa: E402
import smolnet    # noqa: E402

fails = 0


def report(ok, name, detail=""):
    global fails
    word = "PASS" if ok is True else "SKIP" if ok is None else "FAIL"
    fails += ok is False
    print("%s %s%s" % (word, name, (": " + detail) if detail else ""), flush=True)


def check_egress():
    """Optional: with SMALLWEB_SOCKS set, an HTTPS request must make it through the proxy."""
    if not smallweb.SOCKS:
        return report(None, "egress", "SMALLWEB_SOCKS unset: connecting directly")
    import ssl
    try:
        raw = smolnet.connect("www.cloudflare.com", 443, smallweb.SOCKS, 10)
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2    # as web.py and smolnet.py
        with ctx.wrap_socket(raw, server_hostname="www.cloudflare.com") as t:
            t.sendall(b"GET /cdn-cgi/trace HTTP/1.0\r\nHost: www.cloudflare.com\r\n\r\n")
            body = smolnet.read_all(t, 1 << 16).decode("utf-8", "replace")
        report(bool(re.search(r"^ip=\S+", body, re.M)), "egress", "answered through the proxy")
    except (smolnet.FetchError, OSError) as ex:
        report(False, "egress", str(ex))


def check_search():
    t0 = time.time()
    d = smallweb.search("gemini", list(smallweb.engines.ORDER), 1, "https://smallweb.test")
    ok = [k for k, v in d["sources"].items() if v["ok"]]
    for k in smallweb.engines.ORDER:
        if k in ok:
            report(True, "search:" + k, "%d ms, total %s" % (d["sources"][k]["ms"], d["sources"][k]["total"]))
        else:
            report(None, "search:" + k, d["errors"].get(k, "no answer"))
    report(bool(ok) and len(d["results"]) > 0, "search:merged",
           "%d results from %s in %.1f s" % (len(d["results"]), "+".join(ok) or "no engine", time.time() - t0))
    bad = [r["url"] for r in d["results"] if not r["proxy_url"].startswith("https://smallweb.test/page?url=")
           or r["scheme"] not in ("gemini", "gopher") or any(not 0 <= a < b <= len(r["snippet"]) for a, b in r["marks"])]
    report(not bad, "search:contract", "%d results, %d malformed" % (len(d["results"]), len(bad)))


def check_pages():
    for url, want in (("gemini://geminiprotocol.net/", "<h1>"), ("gopher://gopher.floodgap.com/1/", '<pre class="menu">')):
        p = smallweb.open_page(url)
        ok = p.status == 200 and want in p.html and "<script" not in p.html
        report(ok if p.status != 502 else None, "page:" + url.split(":", 1)[0],
               "%d %s%s" % (p.status, "rendered" if ok else "not rendered",
                            " (the host refused the proxy/VPN)" if p.status == 502 else ""))
    p = smallweb.open_page("gemini://geminiprotocol.net/")
    report(p.save is not None and p.save["url"] == "gemini://geminiprotocol.net/", "page:save-payload",
           "canonical URL, %d chars of text" % len(p.save["text"]) if p.save else "nothing would be saved")


def nogon(path, obj=None, method="POST"):
    req = urllib.request.Request(NOGON + path, data=json.dumps(obj).encode() if obj is not None else None, method=method,
                                 headers=dict(smallweb.nogon_headers(), Accept="application/json"))
    try:
        with smallweb.NOGON_OPENER.open(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read()


def check_nogon():
    if not USE_NOGON:
        return report(None, "nogon", "dry run: pass --nogon to save and delete one test document")
    if not NOGON:
        return report(False, "nogon", "--nogon needs SMALLWEB_NOGON_URL")
    url = "gemini://smallweb-selftest.invalid/%d" % int(time.time())
    q = 'url:"%s"' % url
    try:
        status, _ = nogon("/api/add", {"url": url, "title": "smallweb self-test", "text": "smallweb self-test document",
                                        "html": "<html><body><article><p>smallweb self-test document</p></article></body></html>",
                                        "metadata": {"source": "smallweb", "smallweb_scheme": "gemini", "smallweb_selftest": True}})
        report(status == 201, "nogon:add", "HTTP %d" % status)
        status, body = nogon("/search?q=" + urllib.parse.quote(q) + "&format=json", method="GET")
        docs = (json.loads(body).get("documents") or []) if status == 200 else []
        report(len(docs) == 1 and not docs[0].get("label"), "nogon:found", "%d document(s), no label" % len(docs))
    finally:
        status, body = nogon("/api/delete", {"query": q})
        status2, body2 = nogon("/search?q=" + urllib.parse.quote(q) + "&format=json", method="GET")
        left = len(json.loads(body2).get("documents") or []) if status2 == 200 else -1
        report(status == 200 and left == 0, "nogon:cleanup", "deleted; %d left" % left)


def main():
    print("smallweb selftest: egress %s, Nogon %s" % (smallweb.SOCKS or "DIRECT", ("real, one throwaway document" if USE_NOGON else "not contacted")), flush=True)
    for step in (check_egress, check_search, check_pages, check_nogon):
        try:
            step()
        except Exception as ex:                         # a crash is a failure, with its name
            report(False, step.__name__, "%s: %s" % (type(ex).__name__, ex))
    print("selftest: " + ("OK" if not fails else "FAILED (%d)" % fails), flush=True)
    sys.exit(1 if fails else 0)


main()
