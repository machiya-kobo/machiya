"""A fake NewsBlur for the dev stack: the four GET calls feed-import makes, answered from seed/newsblur.json (stdlib
only, dev only, invented stories). Shaped like NewsBlur's own answers (apps/reader/views.py), including its quirks:
read_stories pages overlap by one (Redis LRANGE is inclusive), and a missing or wrong token is 200
`{"authenticated": false}`, not 401.

    GET /reader/feeds?flat=true                         feeds and flat_folders
    GET /reader/read_stories?page=&limit=&order=newest  stories read one by one, newest read first
    GET /reader/starred_story_hashes?include_timestamps=true
    GET /reader/starred_stories?h=…&h=…                 at most 100
    GET /healthz

    NEWSBLUR_SEED (default seed/newsblur.json)   NEWSBLUR_SITE (seed/site: story_content comes from the page's <body>)
    NEWSBLUR_TOKEN_FILE (required: the token feed-import must send as Authorization: Bearer)
    NEWSBLUR_BIND (0.0.0.0)   NEWSBLUR_PORT (8080)
"""
import calendar
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.environ.get("NEWSBLUR_SEED") or os.path.join(HERE, "seed", "newsblur.json")
SITE = os.environ.get("NEWSBLUR_SITE") or os.path.join(HERE, "seed", "site")
CALLS = {"total": 0}


def ts(iso):
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


def token():
    try:
        with open(os.environ["NEWSBLUR_TOKEN_FILE"]) as f:
            return f.readline().strip()
    except (KeyError, OSError):
        return ""


def load():
    with open(SEED) as f:
        seed = json.load(f)

    def story(s):
        try:
            with open(os.path.join(SITE, s["file"]), encoding="utf-8") as f:
                page = f.read()
            m = re.search(r"<body>(.*)</body>", page, re.S)
            content = re.sub(r"<p><small>.*?</small></p>", "", m.group(1) if m else page, flags=re.S).strip()
        except (KeyError, OSError):
            content = "<p>%s</p>" % s["title"]
        out = {"story_hash": s["story_hash"], "story_feed_id": int(s["feed"]), "story_title": s["title"],
               "story_permalink": s["permalink"], "story_content": content, "story_timestamp": str(ts(s["timestamp"])),
               "story_authors": "Dev Seed", "story_tags": [], "read_status": 1}
        if s.get("starred"):
            out.update(starred=True, starred_timestamp=str(ts(s["starred"])), user_tags=s.get("user_tags") or [])
        return out

    return seed, [story(s) for s in seed["read"]], [story(s) for s in seed["starred"]]


SEEDED, READ, STARRED = load()


class H(BaseHTTPRequestHandler):
    server_version = "machiya-dev-fake-newsblur"

    def log_message(self, fmt, *args):
        print("fake-newsblur %s" % urlsplit(self.path).path, flush=True)

    def send(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/healthz":
            return self.send({"ok": True, "read": len(READ), "starred": len(STARRED), "calls": CALLS["total"]})
        want = token()
        auth = self.headers.get("Authorization") or ""
        if not want or auth != "Bearer " + want:
            return self.send({"authenticated": False, "result": "ok"})
        CALLS["total"] += 1
        feeds = {fid: {"id": int(fid), "feed_title": f["feed_title"]} for fid, f in SEEDED["feeds"].items()}
        if u.path == "/reader/feeds":
            folders = {}
            for fid, f in SEEDED["feeds"].items():
                folders.setdefault(" " + f["folder"], []).append(int(fid))      # NewsBlur's flat folder names
            return self.send({"authenticated": True, "feeds": feeds, "flat_folders": folders})
        if u.path == "/reader/read_stories":
            page = max(1, int((q.get("page") or ["1"])[0]))
            limit = max(1, int((q.get("limit") or ["10"])[0]))
            start = (page - 1) * limit
            return self.send({"authenticated": True, "stories": READ[start:start + limit + 1], "feeds": feeds})
        if u.path == "/reader/starred_story_hashes":
            pairs = sorted(((s["story_hash"], int(s["starred_timestamp"])) for s in STARRED), key=lambda p: -p[1])
            if (q.get("include_timestamps") or [""])[0] == "true":
                return self.send({"authenticated": True, "starred_story_hashes": [list(p) for p in pairs]})
            return self.send({"authenticated": True, "starred_story_hashes": [p[0] for p in pairs]})
        if u.path == "/reader/starred_stories":
            wanted = set((q.get("h") or [])[:100])
            return self.send({"authenticated": True, "stories": [s for s in STARRED if s["story_hash"] in wanted],
                              "feeds": feeds})
        self.send({"authenticated": True, "message": "not in the fake"}, 404)


if __name__ == "__main__":
    bind, port = os.environ.get("NEWSBLUR_BIND", "0.0.0.0"), int(os.environ.get("NEWSBLUR_PORT", "8080"))
    if not token():
        raise SystemExit("fake-newsblur: NEWSBLUR_TOKEN_FILE is unset or empty")
    print("fake-newsblur: %d read, %d starred stories on %s:%d" % (len(READ), len(STARRED), bind, port), flush=True)
    ThreadingHTTPServer((bind, port), H).serve_forever()
