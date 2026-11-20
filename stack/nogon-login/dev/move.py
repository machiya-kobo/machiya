"""The design's §8 data move, on dummy data, for the dev stack (gate 0). Run in a python container on the stack's network
(seed, numbers, verify: Hister's API at http://hister:4433) or with Hister's data volume at /hister/data and Hister
stopped (sql). Tokens come from files, never argv.

  seed            with user_handling off: pool documents (some labelled vault), global rules and an alias, a history
                  entry and a pin
  numbers OUT     /api/stats and the totals the design writes down first, as JSON
  sql OWNER       Hister stopped: the owner's rules_json from rules.json; histories user_id 0 -> the owner
  bind OWNER OAUTH_ID   Hister stopped: users.o_auth_id = OAUTH_ID (binds the OIDC login to the owner)
  verify BEFORE   with the owner token (/run/dev-secrets/owner-token): the owner's totals match BEFORE, the pool is empty,
                  the rules and alias and the history entry are the owner's
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

HISTER = "http://hister:4433"
DOCS = [("https://vault.example/n/One", "Vault note one", "vault"), ("https://vault.example/n/Two", "Vault note two",
                                                                     "vault"),
        ("https://pages.example/a", "A saved page", ""), ("https://pages.example/b", "Another page", "reading"),
        ("https://pages.example/c", "Third page", "")]
QUERIES = ["*", "label:vault", "* -label:vault", "@pages"]


def call(method, path, data=None, form=False, token=None):
    headers = {"Origin": "hister://", "Accept": "application/json"}
    if token:
        headers["X-Access-Token"] = token
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(HISTER + path, body, headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        return e.code, None


def total(q, token=None):
    status, data = call("GET", "/search?" + urllib.parse.urlencode({"q": q, "limit": 100}), token=token)
    if status != 200 or not isinstance(data, dict):
        return "HTTP %d" % status
    docs = data.get("documents") or []
    return data.get("total", len(docs))


def numbers(token=None):
    out = {q: total(q, token) for q in QUERIES}
    status, stats = call("GET", "/api/stats", token=token)
    out["stats"] = stats if status == 200 else "HTTP %d" % status
    status, rules = call("GET", "/api/rules", token=token)
    out["rules"] = rules if status == 200 else "HTTP %d" % status
    return out


def main(argv):
    cmd = argv[0]
    if cmd == "seed":
        for url, title, label in DOCS:
            st, _ = call("POST", "/api/add", {"url": url, "title": title, "text": "dummy text of " + title,
                                              "label": label})
            assert st in (200, 201), (url, st)
        assert call("POST", "/api/rules", {"skip": "^https://skip\\.example/", "priority": "^https://vault\\.example/"},
                    form=True)[0] == 200
        assert call("POST", "/api/add_alias", {"alias-keyword": "pages", "alias-value": "* -label:vault"},
                    form=True)[0] == 200
        assert call("POST", "/api/history", {"url": "https://pages.example/a", "title": "A saved page",
                                             "query": "saved page"})[0] == 200
        assert call("POST", "/api/history", {"url": "https://vault.example/n/One", "title": "Vault note one",
                                             "query": "vault one", "pin": True})[0] == 200
        print("seeded %d documents, rules, an alias, a history entry and a pin" % len(DOCS))
    elif cmd == "numbers":
        out = numbers()
        with open(argv[1], "w") as f:
            json.dump(out, f, indent=1, sort_keys=True)
        print(json.dumps({k: v for k, v in out.items() if k in QUERIES}))
    elif cmd == "sql":
        owner = argv[1]
        db = sqlite3.connect("/hister/data/db.sqlite3")
        try:
            with open("/hister/data/rules.json") as f:
                rules = f.read()
        except FileNotFoundError:
            rules = None
        uid = db.execute("SELECT id FROM users WHERE username = ?", (owner,)).fetchone()[0]
        if rules is not None:
            db.execute("UPDATE users SET rules_json = ? WHERE username = ?", (rules, owner))
        n = db.execute("UPDATE histories SET user_id = ? WHERE user_id = 0", (uid,)).rowcount
        db.commit()
        print("owner id %d; rules_json %s; %d history row(s) moved" % (uid, "set" if rules else "absent", n))
    elif cmd == "bind":
        db = sqlite3.connect("/hister/data/db.sqlite3")
        other =db.execute("SELECT username FROM users WHERE o_auth_id = ? AND username != ?",
                           (argv[2], argv[1])).fetchall()
        for (name,) in other:            # the account a first, unbound OIDC sign-in created: its id moves to the owner
            db.execute("UPDATE users SET o_auth_id = NULL WHERE username = ?", (name,))
        n = db.execute("UPDATE users SET o_auth_id = ? WHERE username = ?", (argv[2], argv[1])).rowcount
        db.commit()
        print("bound %d user(s); unbound %s" % (n, [o[0] for o in other]))
    elif cmd == "verify":
        with open("/run/dev-secrets/owner-token") as f:
            token = f.read().strip()
        with open(argv[1]) as f:
            before = json.load(f)
        after = numbers(token)
        ok = True
        for q in QUERIES:
            same = after[q] == before[q]
            ok &= same
            print("%-16s before %-4s after %-4s %s" % (q, before[q], after[q], "ok" if same else "DIFFERENT"))
        pool = total("user_id:0", token)
        print("user_id:0        %s" % pool)
        ok &= pool == 0
        rules = after["rules"] if isinstance(after["rules"], dict) else {}
        alias = (rules.get("aliases") or {}).get("pages")
        print("alias @pages     %r" % alias)
        print("skip rules       %r" % rules.get("skip"))
        ok &= alias == "* -label:vault" and bool(rules.get("skip"))
        st, hist = call("GET", "/api/history?opened=true", token=token)
        hist_items = (hist or {}).get("documents") or [] if isinstance(hist, dict) else []
        print("history          HTTP %d, %d item(s)" % (st, len(hist_items) if isinstance(hist_items, list) else -1))
        ok &= st == 200 and len(hist_items) == 2           # the opened link and the pin, now the owner's
        print("RESULT", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
