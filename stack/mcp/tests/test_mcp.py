"""machiya-mcp tests: the server logic against fake rooms (a local HTTP server that records what it receives), plus
the HTTP gate. Run: python3 -m unittest discover -s stack/mcp/tests"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import envelope   # noqa: E402
import mcp        # noqa: E402
from backend import Backend, BackendError   # noqa: E402


class Fake:
    """A room on a local port. `routes` maps "/path" (GET) or "METHOD /path" (writes) to a JSON body, an (status, body)
    pair or a callable(query or body) returning either. Every request is recorded in `seen`."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def handle_any(self):
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.headers.get("Content-Length") else b""
                body = json.loads(raw) if raw else None
                fake.seen.append({"method": self.command, "path": u.path, "query": q, "body": body,
                                  "headers": {k.lower(): v for k, v in self.headers.items()}})
                key = u.path if self.command == "GET" else "%s %s" % (self.command, u.path)
                r = fake.routes.get(key)
                if r is None:
                    status, out = 404, {"error": "no such route"}
                else:
                    if callable(r):
                        r = r(q if self.command == "GET" else (body or {}))
                    status, out = r if isinstance(r, tuple) else (200, r)
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_DELETE = handle_any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def writes(self):
        return [r for r in self.seen if r["method"] != "GET"]


NOTE = {"path": "Projects/Alpha.md", "vault": "personal", "title": "Alpha", "url": "https://kura.test/n/Projects/Alpha",
        "summary": "s", "tags": ["type/project"], "created": 1, "changed": 2, "published": False, "card_url": None}
WORK = {"path": "Secret.md", "vault": "work", "title": "Work note", "url": "https://kura.test/v/work/n/Secret",
        "summary": "work", "tags": [], "created": 1, "changed": 2, "published": False, "card_url": None}
CARD = {"slug": "alpha", "path": "Projects/Alpha.md", "areas": ["tools"], "topics": ["hister"], "title": "Alpha", "board": "wip", "priority": 1, "area": "tools", "next": "n", "waiting": "",
        "stream": "Platform", "dependsOn": [], "due": "", "updated": "2026-01-15", "summary": "x"}
DOC = {"url": "https://example.com/a", "title": "A page", "domain": "example.com", "label": "python", "added": 5,
       "score": 0.5, "text": "hello world " * 10, "metadata": {"client": "alpha"}}
VAULT_DOC = {"url": "https://kura.test/n/X", "title": "X", "domain": "kura.test", "label": "vault", "metadata": {"source": "vault"}}


def rooms():
    kura = Fake({
        "/api/status": {"head": "abc", "version": "0.3.0"},
        "/api/vaults": {"vaults": [{"name": "personal", "default": True}, {"name": "work", "default": False, "private": True}]},
        "/api/search": {"total": 2, "results": [NOTE, WORK]},
        "/api/recent": {"total": 2, "results": [WORK, NOTE]},
        "/api/note": lambda q: dict(WORK if q["path"].startswith("Secret") else NOTE, markdown="---\n---\n" + "abcdefghij" * 10,
                                    backlinks=[{"path": "a.md", "title": "a", "url": "https://kura.test/n/a"},
                                               {"path": "w.md", "title": "w", "url": "https://kura.test/v/work/n/w"}], outlinks=[]),
        "/api/notes": {"notes": [NOTE, WORK], "missing": ["gone.md"]},
        "/api/tags": {"tags": [{"tag": "topic/x", "count": 1}]},
        "/api/folders": {"folders": []},
    })
    konbini = Fake({
        "/api/status": {"ok": True, "head": "abc"},
        "/api/cards": {"cards": [CARD, dict(CARD, slug="kura", title="Kura", stream="", board="ready", path="Projects/Kura.md", areas=["ops"], topics=[])]},
        "/api/cards/alpha": dict(CARD),
        "/api/cards/alpha/events": {"events": [{"ts": "t", "type": "edit"}] * 30},
        "/api/review": {"counts": {"wip": 1}, "sections": []},
        "/api/roundup": {"period": "week", "markdown": "## Roundup"},
        "/api/cards/locked": (403, {"error": "only the owner"}),
        "POST /api/cards": lambda b: dict(CARD, slug="new-idea", title=b.get("title"), board=b.get("board"), summary=b.get("summary"), area=b.get("area")),
        "PATCH /api/cards/alpha": lambda b: dict(CARD, **{k: v for k, v in b.items() if k in ("next", "priority", "stream", "dependsOn", "waiting")},
                                                  board=b.get("board", CARD["board"])),
        "PATCH /api/cards/new-idea": lambda b: dict(CARD, slug="new-idea", title="New idea"),
        "PATCH /api/cards/locked": (403, {"error": "only the owner publishes"}),
        "PATCH /api/cards/topicless": (409, {"error": "unknown_tag: topic/zzz"}),
        "PATCH /api/cards/selfdep": (422, {"error": "a card can't depend on itself"}),
        "POST /api/cards/alpha/events": (201, {"ok": True}),
        "POST /api/cards/alpha/claim": {"claimed": True},
        "DELETE /api/cards/alpha/claim": {"released": True},
        "PATCH /api/cards/readonly": (405, {"error": "read-only"}),
    })
    niwa = Fake({"/api/status": {"version": "0.1.0", "head": "abc"},
                 "/api/suggestions": {"suggestions": [{"path": "Projects/Open.md", "reason": "r", "agent": "x", "date": "2026-01-15"}], "days": 60},
                 "POST /api/suggest": lambda b: (409, {"error": "already in the garden"}) if "Garden" in b["path"] else (201, {"id": 7, "path": b["path"]})})
    hister = Fake({
        "/search": {"total": 3, "documents": [DOC, VAULT_DOC, dict(DOC, url="https://konbini.test/p/x", domain="konbini.test")]},
        "/api/document": lambda q: VAULT_DOC if "kura" in q["url"] else DOC,
        "/api/stats": {"doc_count": 9},
        "/api/rules": {"aliases": {"@travel": "label:(travel)", "@notes": "label:vault", "@pages": "* -label:vault",
                                   "@sneaky": "x metadata.source:vault", "@tools": "label:(tech)",
                                   "@code": "metadata.source:code", "@repos": "metadata.source:code metadata.code_kind:repo"}},
    })
    return {"kura": kura, "konbini": konbini, "niwa": niwa, "hister": hister}


def make_server(fakes, **env):
    log = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    log.close()
    e = {"MCP_AUTH": "open", "MCP_LOG": log.name}
    e.update({k.upper() + "_URL": f.url for k, f in fakes.items()})
    e.update(env)
    server = mcp.Server(mcp.Config(e))
    server.log_path = log.name
    return server


def call(server, name, args=None, agent="t"):
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}},
                      "owner", agent)
    return r["result"]


def data(result):
    return result["structuredContent"]["untrusted_content"]


class Base(unittest.TestCase):
    def setUp(self):
        self.fakes = rooms()
        self.server = make_server(self.fakes)

    def tearDown(self):
        for f in self.fakes.values():
            f.close()
        os.unlink(self.server.log_path)


class Protocol(Base):
    def rpc(self, method, params=None, mid=1):
        return self.server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}, "owner", "t")

    def test_initialize_negotiates(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertEqual(r["serverInfo"]["name"], "machiya-mcp")
        self.assertEqual(self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], "2025-11-25")

    def test_notification_gets_no_reply(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, "owner", "t"))

    def test_unknown_method_and_bad_request(self):
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.server.handle({"id": 1}, "owner", "t")["error"]["code"], -32600)
        self.assertEqual(self.rpc("tools/call", {"name": "nope"})["error"]["code"], -32602)

    def test_tools_listed_in_fixed_order_with_annotations(self):
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, [t["name"] for t in self.rpc("tools/list", mid=2)["result"]["tools"]])
        self.assertEqual(names[0], "board_list_cards")
        for t in self.rpc("tools/list")["result"]["tools"]:
            self.assertEqual(t["inputSchema"]["type"], "object")

    READS = {"board_list_cards", "board_get_card", "board_review", "board_roundup", "notes_search", "notes_read", "notes_recent",
             "notes_lookup", "notes_tags", "notes_folders", "collections_list", "machiya_search",
             "machiya_status", "garden_candidates", "pages_labels", "collections_audit"}
    WRITES = {"board_add_backlog", "board_move", "board_set_next", "board_block", "board_set_priority", "board_set_stream",
              "board_set_dependencies", "board_tag", "board_log", "board_claim", "board_release", "garden_suggest",
              "board_set_goal", "board_set_due", "pages_set_label", "pages_relabel", "collections_set", "collections_remove"}

    def test_exactly_the_planned_tools_and_no_publish_or_label_writes(self):
        self.assertEqual(set(self.server.tools), self.READS | self.WRITES)
        self.assertFalse([n for n in self.server.tools if "publish" in n or "delete" in n or "history" in n])
        for name, t in self.server.tools.items():
            self.assertEqual(t["annotations"]["readOnlyHint"], name in self.READS, name)
            self.assertFalse(t["annotations"].get("openWorldHint"), name)
            if name not in ("notes_read", "pages_set_label"):      # these take a URL only as a lookup key, never fetched
                self.assertFalse({"url", "uri", "host"} & set(t["inputSchema"]["properties"]), name)

    def test_absent_room_hides_its_tools(self):
        server = make_server({"kura": self.fakes["kura"]})
        self.assertFalse([n for n in server.tools if n.startswith(("board_", "pages_", "collections_", "garden_"))])
        self.assertIn("notes_search", server.tools)
        self.assertIn("machiya_status", server.tools)

    def test_resources(self):
        self.assertEqual(len(self.rpc("resources/templates/list")["result"]["resourceTemplates"]), 2)
        r = self.rpc("resources/read", {"uri": "machiya://card/alpha"})["result"]["contents"][0]
        self.assertIn("untrusted_content", r["text"])
        self.assertEqual(self.rpc("resources/read", {"uri": "http://evil/"})["error"]["code"], -32602)


class Headers(Base):
    def test_no_origin_or_referer_to_rooms_and_agent_always_sent(self):
        for name, args in (("board_list_cards", {}), ("board_get_card", {"slug": "alpha"}), ("board_review", {}),
                           ("board_roundup", {}), ("notes_search", {"q": "x"}), ("notes_read", {"path": "Projects/Alpha.md"}),
                           ("notes_recent", {}), ("notes_lookup", {"paths": ["a.md"]}), ("notes_tags", {}),
                           ("machiya_status", {}), ("machiya_search", {"q": "x"})):
            self.assertFalse(call(self.server, name, args).get("isError"), name)
        for room in ("kura", "konbini", "niwa"):
            self.assertTrue(self.fakes[room].seen, room)
            for req in self.fakes[room].seen:
                self.assertNotIn("origin", req["headers"], room)
                self.assertNotIn("referer", req["headers"], room)
                self.assertEqual(req["headers"]["x-agent"], "mcp:t")

    def test_hister_always_has_origin_and_pages_exclude_vault(self):
        call(self.server, "pages_labels")
        self.server.census = (0.0, None)
        call(self.server, "pages_relabel", {"query": "domain:example.com", "label": "python"})
        call(self.server, "pages_set_label", {"url": "https://example.com/a", "label": "python"})
        call(self.server, "collections_list")
        call(self.server, "machiya_status")
        self.assertEqual({r["path"] for r in self.fakes["hister"].seen}, {"/search", "/api/document", "/api/rules", "/api/stats"})
        for req in self.fakes["hister"].seen:
            self.assertEqual(req["headers"]["origin"], "hister://")
            self.assertEqual(req["headers"]["x-agent"], "mcp:t")
        searches = [r for r in self.fakes["hister"].seen if r["path"] == "/search"]
        self.assertTrue(len(searches) >= 3)
        for r in searches:
            self.assertTrue(r["query"]["q"].endswith(" -label:vault -metadata.source:vault -metadata.source:code -label:konbini"), r["query"]["q"])
            self.assertEqual(r["query"]["format"], "json")
        self.assertIn("domain:example.com", searches[-1]["query"]["q"])

    def test_backend_refuses_urls_and_caller_origin(self):
        b = Backend("kura", self.fakes["kura"].url)
        for bad in ("http://evil/x", "//evil/x", "relative"):
            with self.assertRaises(ValueError):
                b.get(bad)
        self.assertNotIn("Origin", b.headers("a"))
        self.assertEqual(Backend("hister", "http://h", origin="hister://").headers("a")["Origin"], "hister://")

    def test_agent_from_header_is_sanitised_by_context(self):
        call(self.server, "board_review", agent="x" * 200)
        self.assertLessEqual(len(self.fakes["konbini"].seen[-1]["headers"]["x-agent"]), 80)


class WorkVaults(Base):
    def test_no_request_to_kura_carries_vault(self):
        call(self.server, "notes_search", {"q": "vault:work"})
        call(self.server, "notes_recent")
        call(self.server, "notes_read", {"path": "Projects/Alpha.md"})
        call(self.server, "notes_lookup", {"paths": ["Projects/Alpha.md"]})
        call(self.server, "notes_tags")
        call(self.server, "machiya_search", {"q": "x"})
        self.assertTrue(self.fakes["kura"].seen)
        for req in self.fakes["kura"].seen:
            self.assertNotIn("vault", req["query"], req["path"])

    def test_work_notes_are_dropped_from_every_list(self):
        for name, args, key in (("notes_search", {"q": "x"}, "results"), ("notes_recent", {}, "results"),
                                ("notes_lookup", {"paths": ["a.md", "Secret.md"]}, "notes")):
            urls = [n["url"] for n in data(call(self.server, name, args))[key]]
            self.assertEqual(urls, [NOTE["url"]], name)
        self.assertNotIn("Work note", json.dumps(data(call(self.server, "machiya_search", {"q": "x"}))))

    def test_work_note_url_shape_wins_over_vault_field(self):
        lying = dict(WORK, vault="personal")
        self.fakes["kura"].routes["/api/search"] = {"total": 1, "results": [lying]}
        self.assertEqual(data(call(self.server, "notes_search", {"q": "x"}))["results"], [])

    def test_reading_a_work_note_is_refused(self):
        r = call(self.server, "notes_read", {"path": "Secret.md"})
        self.assertTrue(r["isError"])
        for url in ("https://kura.test/v/work/n/Secret", "https://kura.test/a/file.png", "https://kura.test/"):
            self.assertTrue(call(self.server, "notes_read", {"url": url})["isError"], url)

    def test_equivalent_spellings_of_the_work_prefix_are_dropped(self):
        for url in ("https://kura.test//v/work/n/Secret", "https://kura.test/%76/work/n/Secret", "https://kura.test/v//work/n/S"):
            self.fakes["kura"].routes["/api/search"] = {"total": 1, "results": [dict(WORK, vault="personal", url=url)]}
            self.assertEqual(data(call(self.server, "notes_search", {"q": "x"}))["results"], [], url)
            self.assertTrue(call(self.server, "notes_read", {"url": url})["isError"], url)

    def test_links_drop_work_notes(self):
        links = data(call(self.server, "notes_read", {"path": "Projects/Alpha.md"}))["backlinks"]
        self.assertEqual([x["path"] for x in links], ["a.md"])

    def test_path_escapes_refused(self):
        for p in ("../etc/passwd", "/etc/passwd", "a/../../b"):
            self.assertTrue(call(self.server, "notes_read", {"path": p})["isError"], p)


class FailClosed(Base):
    def test_unknown_default_vault_refuses_every_note_tool(self):
        for bad in ((500, {"error": "boom"}), {"vaults": []}, ["not", "a", "dict"], {"vaults": ["x", None]}):
            self.fakes["kura"].routes["/api/vaults"] = bad
            self.server._vault = (0, None)
            for name, args in (("notes_search", {"q": "x"}), ("notes_recent", {}), ("notes_read", {"path": "Projects/Alpha.md"}),
                               ("notes_lookup", {"paths": ["a.md"]})):
                r = call(self.server, name, args)
                self.assertTrue(r["isError"], (bad, name))
                self.assertIn("default vault is unknown", r["structuredContent"]["error"])

    def test_failure_is_cached_briefly_then_recovers(self):
        self.fakes["kura"].routes["/api/vaults"] = (503, {"error": "starting"})
        self.server._vault = (0, None)
        self.assertTrue(call(self.server, "notes_recent")["isError"])
        self.fakes["kura"].routes["/api/vaults"] = {"vaults": [{"name": "personal", "default": True}]}
        self.assertTrue(call(self.server, "notes_recent")["isError"])             # still inside the 15 s window
        self.server._vault = (self.server._vault[0] - 16, None)
        self.assertFalse(call(self.server, "notes_recent").get("isError"))


class MountPath(unittest.TestCase):
    def test_kura_behind_a_path_prefix(self):
        fakes = rooms()
        self.addCleanup(lambda: [f.close() for f in fakes.values()])
        work = dict(WORK, url="https://kura.test/kura/v/work/n/Secret")
        ok = dict(NOTE, url="https://kura.test/kura/n/Projects/Alpha")
        fakes["kura"].routes["/api/search"] = {"total": 2, "results": [work, ok]}
        server = make_server(fakes, KURA_PUBLIC_URL="https://kura.test/kura")
        self.addCleanup(os.unlink, server.log_path)
        self.assertEqual([n["url"] for n in data(call(server, "notes_search", {"q": "x"}))["results"]], [ok["url"]])
        self.assertTrue(call(server, "notes_read", {"url": "https://kura.test/kura/v/work/n/Secret"})["isError"])
        call(server, "notes_read", {"url": "https://kura.test/kura/n/Projects/Some%20Note"})
        self.assertIn("Projects/Some Note.md", [r["query"].get("path") for r in fakes["kura"].seen])


class Notes(Base):
    def test_read_pages_the_markdown(self):
        d = data(call(self.server, "notes_read", {"path": "Projects/Alpha", "max_chars": 20, "offset": 4}))
        self.assertEqual(d["content"]["chars"], 20)
        self.assertEqual(d["content"]["offset"], 4)
        self.assertEqual(d["content"]["next_offset"], 24)

    def test_read_by_url(self):
        call(self.server, "notes_read", {"url": "https://kura.test/n/Projects/Some%20Note"})
        self.assertIn("Projects/Some Note.md", [r["query"].get("path") for r in self.fakes["kura"].seen])

    def test_search_requires_q_and_validates_sort(self):
        self.assertTrue(call(self.server, "notes_search", {})["isError"])
        self.assertTrue(call(self.server, "notes_search", {"q": "x", "sort": "random"})["isError"])
        self.assertTrue(call(self.server, "notes_search", {"q": "x", "limit": "many"})["isError"])

    def test_lookup_fetches_comma_paths_one_by_one(self):
        seen = self.fakes["kura"].seen
        d = data(call(self.server, "notes_lookup", {"paths": ["a.md", "Comma, Note.md"]}))
        batch = [r for r in seen if r["path"] == "/api/notes"]
        self.assertEqual(batch[-1]["query"]["paths"], "a.md")
        self.assertIn("Comma, Note.md", [r["query"].get("path") for r in seen if r["path"] == "/api/note"])
        self.assertEqual(d["missing"], ["gone.md"])
        self.assertTrue(any(n["path"] == "Projects/Alpha.md" for n in d["notes"]))
        self.fakes["kura"].routes["/api/note"] = (404, {"error": "no such note"})
        d = data(call(self.server, "notes_lookup", {"paths": ["Only, Comma.md"]}))
        self.assertEqual((d["notes"], d["missing"]), ([], ["Only, Comma.md"]))

    def test_lookup_bounds(self):
        self.assertTrue(call(self.server, "notes_lookup", {"paths": []})["isError"])
        self.assertTrue(call(self.server, "notes_lookup", {"paths": ["a.md"] * 101})["isError"])
        self.assertEqual(data(call(self.server, "notes_lookup", {"paths": ["a"]}))["missing"], ["gone.md"])


class Board(Base):
    def test_filters(self):
        d = data(call(self.server, "board_list_cards", {"stream": "platform"}))
        self.assertEqual([c["slug"] for c in d["cards"]], ["alpha"])
        d = data(call(self.server, "board_list_cards", {"text": "kura"}))
        self.assertEqual([c["slug"] for c in d["cards"]], ["kura"])
        self.assertEqual(self.fakes["konbini"].seen[-1]["query"], {})
        call(self.server, "board_list_cards", {"board": "wip", "area": "tools"})
        self.assertEqual(self.fakes["konbini"].seen[-1]["query"], {"board": "wip", "area": "tools"})
        self.assertTrue(call(self.server, "board_list_cards", {"board": "nonsense"})["isError"])

    def test_card_url_and_events(self):
        d = data(call(self.server, "board_get_card", {"slug": "alpha", "events": True, "event_limit": 3}))
        self.assertEqual(len(d["events"]), 3)
        self.assertTrue(d["url"].endswith("/p/alpha"))

    def test_slug_is_validated(self):
        for bad in ("../x", "a/b", "", ".hidden"):
            self.assertTrue(call(self.server, "board_get_card", {"slug": bad})["isError"], bad)

    def test_backend_refusal_becomes_a_question_for_the_owner(self):
        r = call(self.server, "board_get_card", {"slug": "locked"})
        self.assertTrue(r["isError"])
        self.assertTrue(r["structuredContent"]["needs_owner"])
        self.assertEqual(r["structuredContent"]["status"], 403)

    def test_unknown_card_is_an_error_not_a_crash(self):
        r = call(self.server, "board_get_card", {"slug": "missing"})
        self.assertTrue(r["isError"])
        self.assertNotIn("needs_owner", r["structuredContent"])

    def test_period_validated(self):
        self.assertTrue(call(self.server, "board_roundup", {"period": "decade"})["isError"])
        self.assertEqual(data(call(self.server, "board_roundup", {"period": "day"}))["period"], "week")   # fake answers fixed


class Pages(Base):
    def test_page_search_and_text_are_histers_own_mcp(self):
        # since 0.7.0: Hister's MCP (search with @pages, get_preview) reads pages; this server keeps labels and collections
        for name in ("pages_search", "pages_read"):
            self.assertNotIn(name, self.server.tools)
            r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}},
                                   "owner", "t")
            self.assertEqual(r["error"]["code"], -32602, name)
        self.assertEqual(set(data(call(self.server, "machiya_search", {"q": "x"}))), {"notes", "cards"})
        self.assertFalse([r for r in self.fakes["hister"].seen if r["path"] == "/search"])

    def test_query_injection_cannot_drop_the_exclusion(self):
        call(self.server, "pages_relabel", {"query": "label:vault", "label": "python"})
        searches = [r for r in self.fakes["hister"].seen if r["path"] == "/search"]
        self.assertTrue(searches)
        self.assertTrue(searches[-1]["query"]["q"].endswith(" -label:vault -metadata.source:vault -metadata.source:code -label:konbini"))

    def test_collections_hide_vault_aliases(self):
        d = data(call(self.server, "collections_list"))["collections"]
        self.assertEqual(sorted(d), ["@tools", "@travel"])


class Envelope(unittest.TestCase):
    def test_wrap_marks_untrusted_and_strips_controls(self):
        r = envelope.wrap({"t": "a‮b\x00c​d\ne", "l": ["\x07x"]}, "notes")
        c = r["structuredContent"]
        self.assertEqual(c["trust"], "untrusted")
        self.assertEqual(c["untrusted_content"], {"t": "abcd\ne", "l": ["x"]})
        self.assertEqual(json.loads(r["content"][0]["text"]), c)

    def test_clip(self):
        self.assertEqual(envelope.clip("abcdef", 4)["next_offset"], 4)
        self.assertIsNone(envelope.clip("abcdef", 4, 4)["next_offset"])
        self.assertEqual(envelope.clip("", 10)["total_chars"], 0)


def body(result):
    return result["structuredContent"]


class BoardWrites(Base):
    def konbini_writes(self):
        return self.fakes["konbini"].writes()

    def test_simple_writes_send_exactly_their_fields(self):
        for name, args, method, path, sent in (
                ("board_set_next", {"slug": "alpha", "next": "Ship it"}, "PATCH", "/api/cards/alpha", {"next": "Ship it"}),
                ("board_set_priority", {"slug": "alpha", "priority": "high"}, "PATCH", "/api/cards/alpha", {"priority": "high"}),
                ("board_set_stream", {"slug": "alpha", "stream": "Platform"}, "PATCH", "/api/cards/alpha", {"stream": "Platform"}),
                ("board_set_goal", {"slug": "alpha", "goal": "Release 1.0"}, "PATCH", "/api/cards/alpha", {"goal": "Release 1.0"}),
                ("board_set_due", {"slug": "alpha", "due": "2026-11-01"}, "PATCH", "/api/cards/alpha", {"due": "2026-11-01"}),
                ("board_set_due", {"slug": "alpha", "due": ""}, "PATCH", "/api/cards/alpha", {"due": ""}),
                ("board_block", {"slug": "alpha", "reason": "waiting on owner"}, "PATCH", "/api/cards/alpha", {"status": "blocked", "waiting": "waiting on owner"}),
                ("board_move", {"slug": "alpha", "column": "done"}, "PATCH", "/api/cards/alpha", {"board": "done"}),
                ("board_log", {"slug": "alpha", "message": "shipped v1"}, "POST", "/api/cards/alpha/events", {"type": "comment", "body": "shipped v1"}),
                ("board_claim", {"slug": "alpha"}, "POST", "/api/cards/alpha/claim", {}),
                ("board_release", {"slug": "alpha"}, "DELETE", "/api/cards/alpha/claim", {})):
            r = call(self.server, name, args)
            self.assertFalse(r.get("isError"), (name, r))
            last = self.konbini_writes()[-1]
            self.assertEqual((last["method"], last["path"], last["body"]), (method, path, sent), name)

    def test_writes_never_carry_origin_or_referer_and_always_name_the_agent(self):
        call(self.server, "board_set_next", {"slug": "alpha", "next": "x"}, agent="tester@box")
        call(self.server, "garden_suggest", {"note": "Projects/Alpha.md", "reason": "good"}, agent="tester@box")
        for room in ("konbini", "niwa"):
            for req in self.fakes[room].writes():
                self.assertNotIn("origin", req["headers"])
                self.assertNotIn("referer", req["headers"])
                self.assertEqual(req["headers"]["x-agent"], "mcp:tester@box")
                self.assertEqual(req["headers"]["content-type"], "application/json")
        self.assertTrue(self.fakes["niwa"].writes())

    def test_due_must_be_a_date_and_goal_one_line(self):
        for name, args in (("board_set_due", {"slug": "alpha", "due": "next week"}), ("board_set_due", {"slug": "alpha", "due": "2026-13-01x"}),
                           ("board_set_due", {"slug": "alpha", "due": "2026-13-01"}), ("board_set_due", {"slug": "alpha", "due": "2026-02-30"}),
                           ("board_set_goal", {"slug": "alpha", "goal": "a\nb"})):
            self.assertTrue(call(self.server, name, args)["isError"], args)
        self.assertFalse(self.konbini_writes())

    def test_move_validation_and_archive_needs_the_owner(self):
        self.assertTrue(call(self.server, "board_move", {"slug": "alpha", "column": "nonsense"})["isError"])
        r = call(self.server, "board_move", {"slug": "alpha", "column": "archived"})
        self.assertTrue(r["isError"])
        self.assertTrue(body(r)["needs_owner"])
        self.assertFalse(self.konbini_writes())
        self.assertFalse(call(self.server, "board_move", {"slug": "alpha", "column": "archived", "confirm": True}).get("isError"))

    def test_move_with_a_log_line_posts_the_event(self):
        r = call(self.server, "board_move", {"slug": "alpha", "column": "done", "log": "shipped"})
        self.assertTrue(data(r)["logged"])
        self.assertEqual([w["path"] for w in self.konbini_writes()], ["/api/cards/alpha", "/api/cards/alpha/events"])

    def test_one_line_fields(self):
        for name, args in (("board_set_next", {"slug": "alpha", "next": "a\nb"}), ("board_block", {"slug": "alpha", "reason": ""}),
                           ("board_log", {"slug": "alpha", "message": ""}), ("board_set_priority", {"slug": "alpha", "priority": "urgent"}),
                           ("board_set_dependencies", {"slug": "alpha"}), ("board_tag", {"slug": "alpha"})):
            self.assertTrue(call(self.server, name, args)["isError"], name)
        self.assertFalse(self.konbini_writes())

    def test_tag_only_existing_topic_and_machine_tags_and_never_confirm_new_tags(self):
        for bad in ("area/tools", "status/active", "type/project", "topic/", "TOPIC/X", "topic/a b"):
            r = call(self.server, "board_tag", {"slug": "alpha", "add": [bad]})
            self.assertTrue(r["isError"], bad)
        self.assertFalse(self.konbini_writes())
        r = call(self.server, "board_tag", {"slug": "topicless", "add": ["topic/zzz"]})        # Konbini: 409 unknown_tag
        self.assertTrue(r["isError"])
        self.assertTrue(body(r)["needs_owner"])
        self.assertEqual(self.konbini_writes()[-1]["body"], {"tags_add": ["topic/zzz"], "tags_remove": []})
        self.assertNotIn("confirm_new_tags", json.dumps(self.konbini_writes()))

    def test_refusals_from_the_board_are_owner_questions_but_validation_errors_are_not(self):
        r = call(self.server, "board_set_next", {"slug": "locked", "next": "x"})
        self.assertTrue(body(r)["needs_owner"])
        self.assertIn("question for the owner", body(r)["error"])
        r = call(self.server, "board_set_dependencies", {"slug": "selfdep", "add": ["selfdep"]})
        self.assertEqual(body(r)["status"], 404)      # the fake has no GET for it: a plain error, not an owner question
        self.assertNotIn("needs_owner", body(r))
        self.fakes["konbini"].routes["/api/cards/selfdep"] = dict(CARD, slug="selfdep", dependsOn=[])
        r = call(self.server, "board_set_dependencies", {"slug": "selfdep", "add": ["selfdep"]})
        self.assertEqual(body(r)["status"], 422)
        self.assertNotIn("needs_owner", body(r))

    def test_read_only_board_says_so(self):
        r = call(self.server, "board_set_next", {"slug": "readonly", "next": "x"})
        self.assertIn("read-only", body(r)["error"])

    def test_dependencies_add_and_remove_like_pm(self):
        self.fakes["konbini"].routes["/api/cards/alpha"] = dict(CARD, dependsOn=["Kura", "Niwa"])
        call(self.server, "board_set_dependencies", {"slug": "alpha", "add": ["Konbini", "kura"], "remove": ["niwa"]})
        self.assertEqual(self.konbini_writes()[-1]["body"], {"dependsOn": ["Kura", "Konbini"]})
        call(self.server, "board_set_dependencies", {"slug": "alpha", "remove": ["kura"]})     # a slug matches the stored link "Kura"
        self.assertEqual(self.konbini_writes()[-1]["body"], {"dependsOn": ["Niwa"]})

    def test_log_is_one_per_card_per_ten_minutes(self):
        now = [1000.0]
        self.server.clock = lambda: now[0]
        self.assertFalse(call(self.server, "board_log", {"slug": "alpha", "message": "one"}).get("isError"))
        r = call(self.server, "board_log", {"slug": "alpha", "message": "two"})
        self.assertEqual(body(r)["code"], "rate_limited")
        self.assertEqual(len([w for w in self.konbini_writes() if w["path"].endswith("/events")]), 1)
        now[0] += 601
        self.assertFalse(call(self.server, "board_log", {"slug": "alpha", "message": "three"}).get("isError"))


class AddBacklog(Base):
    ARGS = {"title": "Brand new thing", "area": "tools", "summary": "One line."}

    def setUp(self):
        super().setUp()
        self.fakes["kura"].routes["/api/search"] = {"total": 0, "results": []}     # nothing similar in the vault unless a test says so

    def posts(self):
        return [w for w in self.fakes["konbini"].writes() if w["method"] == "POST" and w["path"] == "/api/cards"]

    def test_creates_a_backlog_stub(self):
        r = call(self.server, "board_add_backlog", self.ARGS)
        self.assertTrue(data(r)["created"])
        self.assertEqual(self.posts()[0]["body"], {"title": "Brand new thing", "area": "tools", "board": "backlog", "summary": "One line."})
        self.assertEqual(len(self.fakes["konbini"].writes()), 1)          # no topics: no tag PATCH

    def test_topics_are_added_after_creation(self):
        call(self.server, "board_add_backlog", dict(self.ARGS, topics=["hister", "topic/hister"]))
        last = self.fakes["konbini"].writes()[-1]
        self.assertEqual((last["method"], last["path"], last["body"]), ("PATCH", "/api/cards/new-idea", {"tags_add": ["topic/hister", "topic/hister"]}))

    def test_unknown_area_or_topic_is_the_owners_and_creates_nothing(self):
        for extra in ({"area": "brandnew"}, {"topics": ["nonexistent"]}):
            r = call(self.server, "board_add_backlog", dict(self.ARGS, **extra))
            self.assertTrue(r["isError"], extra)
            self.assertTrue(body(r)["needs_owner"])
        self.assertFalse(self.fakes["konbini"].writes())

    def test_similar_card_or_note_stops_the_create(self):
        r = data(call(self.server, "board_add_backlog", dict(self.ARGS, title="Alpha app")))
        self.assertFalse(r["created"])
        self.assertIn("card", [x["kind"] for x in r["similar"]])
        self.assertFalse(self.fakes["konbini"].writes())
        r = data(call(self.server, "board_add_backlog", dict(self.ARGS, title="Alpha app", even_if_similar=True)))
        self.assertTrue(r["created"])

    def test_a_shared_significant_word_counts_as_similar_but_filler_does_not(self):
        r = data(call(self.server, "board_add_backlog", dict(self.ARGS, title="Kura reader")))
        self.assertEqual([x["slug"] for x in r["similar"]], ["kura"])
        r = data(call(self.server, "board_add_backlog", dict(self.ARGS, title="A new project idea for things")))
        self.assertTrue(r["created"])

    def test_similar_note_from_kura_stops_the_create_and_work_notes_never_count(self):
        self.fakes["kura"].routes["/api/search"] = {"total": 2, "results": [dict(NOTE, title="Brand thing note"), WORK]}
        r = data(call(self.server, "board_add_backlog", self.ARGS))
        self.assertEqual([x["kind"] for x in r["similar"]], ["note"])
        self.assertNotIn("Work note", json.dumps(r))

    def test_kura_down_still_creates_and_says_it_didnt_check(self):
        self.fakes["kura"].close()
        r = data(call(self.server, "board_add_backlog", self.ARGS))
        self.assertTrue(r["created"])
        self.assertFalse(r["kura_checked"])

    def test_inputs_validated(self):
        for args in (dict(self.ARGS, title=""), dict(self.ARGS, summary="two\nlines"), dict(self.ARGS, topics="x"), {"title": "t"}):
            self.assertTrue(call(self.server, "board_add_backlog", args)["isError"], args)


class RouteTable(unittest.TestCase):
    def test_forbidden_fields_and_unknown_routes_are_refused_before_any_request(self):
        for field in ("publish", "growth", "confidence", "garden_pin", "confirm_new_tags", "new_label"):
            with self.assertRaises(RuntimeError):
                mcp.check_route("konbini", "PATCH", "/api/cards/alpha", {field: True})
        for room, method, path, b in (("konbini", "PATCH", "/api/cards/alpha", {"rank": 1}),
                                      ("konbini", "POST", "/api/order", {}), ("konbini", "DELETE", "/api/cards/alpha", {}),
                                      ("konbini", "PATCH", "/api/cards/a/b", {}), ("niwa", "POST", "/api/publish", {}),
                                      ("niwa", "POST", "/api/suggest", {"path": "a", "reason": "b", "publish": True}),
                                      ("kura", "POST", "/api/anything", {}), ("hister", "POST", "/api/delete", {})):
            with self.assertRaises(RuntimeError):
                mcp.check_route(room, method, path, b)
        mcp.check_route("konbini", "PATCH", "/api/cards/alpha", {"next": "x", "tags_add": []})
        mcp.check_route("niwa", "POST", "/api/suggest", {"path": "a", "reason": "b"})

    def test_a_bad_write_reaches_no_room(self):
        fakes = rooms()
        self.addCleanup(lambda: [f.close() for f in fakes.values()])
        server = make_server(fakes)
        self.addCleanup(os.unlink, server.log_path)
        with self.assertRaises(RuntimeError):
            server.context("t").write("konbini", "PATCH", "/api/cards/alpha", {"publish": True})
        self.assertFalse(fakes["konbini"].writes())


class Garden(Base):
    def test_suggest_posts_path_and_reason_only(self):
        r = data(call(self.server, "garden_suggest", {"note": "Projects/Alpha.md", "reason": "A finished write-up."}))
        self.assertTrue(r["suggested"])
        w = self.fakes["niwa"].writes()[0]
        self.assertEqual((w["method"], w["path"], w["body"]), ("POST", "/api/suggest", {"path": "Projects/Alpha.md", "reason": "A finished write-up."}))

    def test_a_note_with_an_open_suggestion_is_not_suggested_again(self):
        r = data(call(self.server, "garden_suggest", {"note": "Projects/Open.md", "reason": "Again."}))
        self.assertFalse(r["suggested"])
        self.assertEqual(r["already_open"]["agent"], "x")
        self.assertFalse(self.fakes["niwa"].writes())

    def test_an_older_niwa_without_the_endpoint_still_works(self):
        del self.fakes["niwa"].routes["/api/suggestions"]
        self.assertTrue(data(call(self.server, "garden_suggest", {"note": "Projects/Alpha.md", "reason": "Fine."}))["suggested"])

    def test_a_card_slug_resolves_to_its_note(self):
        call(self.server, "garden_suggest", {"note": "alpha", "reason": "Because."})
        self.assertEqual(self.fakes["niwa"].writes()[0]["body"]["path"], "Projects/Alpha.md")

    def test_work_notes_and_unknown_notes_are_never_suggested(self):
        self.assertTrue(call(self.server, "garden_suggest", {"note": "Secret.md", "reason": "x"})["isError"])
        self.assertFalse(self.fakes["niwa"].writes())
        for bad in ("../x.md", "/etc/passwd"):
            self.assertTrue(call(self.server, "garden_suggest", {"note": bad, "reason": "x"})["isError"], bad)

    def test_reason_required_and_short(self):
        self.assertTrue(call(self.server, "garden_suggest", {"note": "Projects/Alpha.md", "reason": ""})["isError"])
        self.assertTrue(call(self.server, "garden_suggest", {"note": "Projects/Alpha.md", "reason": "x" * 301})["isError"])
        self.assertFalse(self.fakes["niwa"].writes())

    def test_already_in_the_garden_is_information_not_an_owner_question(self):
        r = call(self.server, "garden_suggest", {"note": "Garden/Thing.md", "reason": "x"})
        self.assertTrue(r["isError"])
        self.assertIn("already in the garden", body(r)["error"])
        self.assertNotIn("needs_owner", body(r))

    def test_daily_cap_and_a_refused_call_costs_nothing(self):
        server = make_server(self.fakes, MCP_SUGGESTS_PER_DAY="2")
        self.addCleanup(os.unlink, server.log_path)
        for _ in range(5):                   # refused by validation: costs none of the day's allowance
            self.assertTrue(call(server, "garden_suggest", {"note": "Projects/Alpha.md", "reason": ""})["isError"])
        results = [call(server, "garden_suggest", {"note": "Projects/Alpha.md", "reason": "r%d" % i}) for i in range(3)]
        self.assertEqual([bool(r.get("isError")) for r in results], [False, False, True])
        self.assertEqual(body(results[2])["code"], "rate_limited")

    RECENT = {"total": 6, "results": [
        dict(NOTE, path="Projects/A.md"), dict(NOTE, path="Templates/T.md"), dict(NOTE, path="Generated/s.md"), dict(NOTE, path="Archive/o.md"),
        dict(NOTE, path="Projects/B.md", published=True), WORK]}

    def test_candidates_skip_published_skipped_folders_and_work(self):
        self.fakes["kura"].routes["/api/recent"] = self.RECENT
        server = make_server(self.fakes, MCP_GARDEN_SKIP="Templates/,Archive/,Generated/")
        self.addCleanup(os.unlink, server.log_path)
        d = data(call(server, "garden_candidates"))
        self.assertEqual([c["path"] for c in d["candidates"]], ["Projects/A.md"])
        self.assertEqual(d["skipped_folders"], ["Templates/", "Archive/", "Generated/"])
        d = data(call(server, "garden_candidates", {"folder": "Reading"}))
        self.assertEqual(d["candidates"], [])

    def test_candidates_skip_only_templates_and_archives_by_default(self):
        self.fakes["kura"].routes["/api/recent"] = self.RECENT
        self.assertEqual(self.server.config.garden_skip, ("Templates/", "Archive/"))
        d = data(call(self.server, "garden_candidates"))
        self.assertEqual([c["path"] for c in d["candidates"]], ["Projects/A.md", "Generated/s.md"])
        self.assertEqual(d["skipped_folders"], ["Templates/", "Archive/"])

    def test_the_garden_skip_setting_can_be_emptied(self):
        self.fakes["kura"].routes["/api/recent"] = self.RECENT
        server = make_server(self.fakes, MCP_GARDEN_SKIP="")
        self.addCleanup(os.unlink, server.log_path)
        d = data(call(server, "garden_candidates"))
        self.assertEqual([c["path"] for c in d["candidates"]], ["Projects/A.md", "Templates/T.md", "Generated/s.md", "Archive/o.md"])
        self.assertEqual(d["skipped_folders"], [])

    def test_candidates_need_kura_and_niwa(self):
        server = make_server({k: v for k, v in self.fakes.items() if k != "niwa"})
        self.addCleanup(os.unlink, server.log_path)
        self.assertNotIn("garden_candidates", server.tools)
        self.assertNotIn("garden_suggest", server.tools)


class WriteLimits(Base):
    def test_board_writes_have_their_own_bucket_and_reads_are_unaffected(self):
        server = make_server(self.fakes, MCP_WRITES_PER_MIN="2")
        self.addCleanup(os.unlink, server.log_path)
        rs = [call(server, "board_set_next", {"slug": "alpha", "next": str(i)}) for i in range(3)]
        self.assertEqual([bool(r.get("isError")) for r in rs], [False, False, True])
        self.assertEqual(body(rs[2])["code"], "rate_limited")
        self.assertEqual(len(self.fakes["konbini"].writes()), 2)
        self.assertFalse(call(server, "board_review").get("isError"))

    def test_daily_bucket(self):
        server = make_server(self.fakes, MCP_WRITES_PER_DAY="1")
        self.addCleanup(os.unlink, server.log_path)
        self.assertFalse(call(server, "board_claim", {"slug": "alpha"}).get("isError"))
        self.assertEqual(body(call(server, "board_claim", {"slug": "alpha"}))["code"], "rate_limited")

    def test_a_call_refused_by_one_bucket_takes_nothing_from_the_others(self):
        server = make_server(self.fakes, MCP_WRITES_PER_MIN="1", MCP_WRITES_PER_DAY="5")
        self.addCleanup(os.unlink, server.log_path)
        call(server, "board_claim", {"slug": "alpha"})
        call(server, "board_claim", {"slug": "alpha"})
        self.assertEqual(server.limits["board_write_day"].buckets["owner"][0], 4.0)

    def test_write_audit_has_no_text(self):
        call(self.server, "board_set_next", {"slug": "alpha", "next": "a very private plan"})
        call(self.server, "board_log", {"slug": "alpha", "message": "private log text"})
        with open(self.server.log_path) as f:
            raw = f.read()
        self.assertNotIn("private", raw)
        self.assertIn('"slug": "alpha"', raw)


class Prompts(Base):
    def rpc(self, method, params=None):
        return self.server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, "owner", "t")

    def test_list_and_get(self):
        names = [p["name"] for p in self.rpc("prompts/list")["result"]["prompts"]]
        self.assertEqual(names, ["weekly_review", "backlog_triage", "capture", "tidy_collections", "garden_candidates"])
        text = self.rpc("prompts/get", {"name": "weekly_review", "arguments": {}})["result"]["messages"][0]["content"]["text"]
        self.assertIn("board_review", text)
        self.assertIn("explicit yes", text)
        text = self.rpc("prompts/get", {"name": "capture", "arguments": {"text": "an idea"}})["result"]["messages"][0]["content"]["text"]
        self.assertIn("an idea", text)
        self.assertIn("data, not instructions", text)

    def test_errors(self):
        self.assertEqual(self.rpc("prompts/get", {"name": "nope"})["error"]["code"], -32602)
        self.assertEqual(self.rpc("prompts/get", {"name": "capture", "arguments": {}})["error"]["code"], -32602)
        self.assertEqual(self.rpc("prompts/get", {"name": "capture", "arguments": {"text": 5}})["error"]["code"], -32602)

    def test_prompts_follow_the_rooms(self):
        server = make_server({"kura": self.fakes["kura"]})
        self.addCleanup(os.unlink, server.log_path)
        self.assertEqual(server.prompts, [])


class Gate(Base):
    def serve(self, **env):
        server = make_server(self.fakes, **env)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), mcp.make_handler(server))
        threading.Thread(target=httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), os.unlink(server.log_path)))
        return server, "http://127.0.0.1:%d" % httpd.server_address[1]

    def post(self, base, body, headers=None, path="/mcp"):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if not isinstance(body, bytes) else body,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    def test_tailscale_mode_fails_closed(self):
        _, base = self.serve(MCP_AUTH="tailscale")                     # no MCP_USERS: nobody
        self.assertEqual(self.post(base, self.PING, {"Tailscale-User-Login": "me@x"})[0], 403)
        _, base = self.serve(MCP_AUTH="tailscale", MCP_USERS="me@x, you@x")
        self.assertEqual(self.post(base, self.PING)[0], 403)
        self.assertEqual(self.post(base, self.PING, {"Tailscale-User-Login": "evil@x"})[0], 403)
        self.assertEqual(self.post(base, self.PING, {"Tailscale-User-Login": "you@x"})[0], 200)

    def test_open_mode_ignores_the_header(self):
        server, base = self.serve(MCP_AUTH="open")
        self.assertEqual(self.post(base, self.PING, {"Tailscale-User-Login": "evil@x"})[0], 200)
        self.post(base, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "board_review"}},
                  {"Tailscale-User-Login": "evil@x"})
        with open(server.log_path) as f:
            self.assertEqual(json.loads(f.read().splitlines()[-1])["login"], "local")

    def test_bad_auth_value_refuses_to_start(self):
        with self.assertRaises(SystemExit):
            mcp.Config({"MCP_AUTH": "anything"})

    def test_browser_origins_refused_even_for_allowed_users(self):
        _, base = self.serve()
        self.assertEqual(self.post(base, self.PING, {"Origin": "https://evil.example"})[0], 403)

    def test_transport_details(self):
        _, base = self.serve()
        self.assertEqual(self.post(base, {"jsonrpc": "2.0", "method": "notifications/initialized"})[0], 202)
        self.assertEqual(self.post(base, b"{not json")[0], 400)
        self.assertEqual(self.post(base, b"")[0], 400)
        self.assertEqual(self.post(base, self.PING, path="/elsewhere")[0], 404)
        code, body = self.post(base, [self.PING, dict(self.PING, id=2)])
        self.assertEqual((code, len(json.loads(body))), (200, 2))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(base + "/mcp", timeout=5)
        self.assertEqual(cm.exception.code, 405)
        self.assertTrue(json.loads(urllib.request.urlopen(base + "/healthz", timeout=5).read())["ok"])

    def get(self, base, path, headers=None, method="GET"):
        req = urllib.request.Request(base + path, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_status_names_the_vendored_vaultkit(self):
        _, base = self.serve(MCP_AUTH="tailscale", MCP_USERS="me@x")      # open: no login needed
        for path in ("/api/status", "/healthz"):
            code, _, body = self.get(base, path)
            self.assertEqual(code, 200)
            d = json.loads(body)
            self.assertEqual(d["version"], mcp.VERSION)
            self.assertRegex(d["vaultkit"], r"^v\d+\.\d+\.\d+")
        import vaultkit
        self.assertEqual(mcp.VAULTKIT, "v" + vaultkit.__version__)       # the manifest and the package agree
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mcp.vaultkit_version(d), "")                 # no manifest: no claim

    def test_changelog_is_open_markdown_with_an_etag(self):
        server, base = self.serve(MCP_AUTH="tailscale", MCP_USERS="me@x")
        self.assertEqual(server.changelog, os.path.join(os.path.dirname(os.path.abspath(mcp.__file__)), "CHANGELOG.md"))
        code, headers, body = self.get(base, "/api/changelog")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "text/markdown; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertTrue(body.startswith(b"# Changelog: machiya-mcp\n"))
        self.assertIn(b"## " + mcp.VERSION.encode() + b"\n", body)         # the running version has its section
        tag = headers["ETag"]
        code, headers, body = self.get(base, "/api/changelog", {"If-None-Match": tag})
        self.assertEqual((code, body, headers["ETag"]), (304, b"", tag))
        code, headers, body = self.get(base, "/api/changelog", {"If-None-Match": '"stale"'})
        self.assertEqual(code, 200)
        code, headers, body = self.get(base, "/api/changelog", method="HEAD")
        self.assertEqual((code, body, headers["ETag"]), (200, b"", tag))
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_changelog_missing_is_404(self):
        server, base = self.serve()
        with tempfile.TemporaryDirectory() as d:
            server.changelog = os.path.join(d, "CHANGELOG.md")
            code, headers, _ = self.get(base, "/api/changelog")
        self.assertEqual(code, 404)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))

    def test_oversized_body(self):
        _, base = self.serve()
        try:
            self.assertEqual(self.post(base, b"x" * (mcp.MAX_BODY + 1))[0], 413)
        except (ConnectionError, urllib.error.URLError):
            pass            # the server answered 413 and closed while the client was still sending: also a refusal


class Limits(Base):
    def test_bucket_empties_then_refills(self):
        now = [0.0]
        lim = mcp.Limiter(3, clock=lambda: now[0])
        self.assertEqual([lim.take("a") for _ in range(4)], [True, True, True, False])
        self.assertTrue(lim.take("b"))
        now[0] = 20.0          # 3/min = one token per 20 s
        self.assertTrue(lim.take("a"))
        self.assertFalse(lim.take("a"))

    def test_tool_call_reports_rate_limit(self):
        server = make_server(self.fakes, MCP_READS_PER_MIN="2")
        results = [call(server, "board_review") for _ in range(3)]
        self.assertEqual([bool(r.get("isError")) for r in results], [False, False, True])
        self.assertEqual(results[2]["structuredContent"]["code"], "rate_limited")
        os.unlink(server.log_path)


class Audit(Base):
    def test_log_has_keys_not_text(self):
        call(self.server, "notes_search", {"q": "my private thought"})
        call(self.server, "board_get_card", {"slug": "alpha"})
        call(self.server, "pages_set_label", {"url": "https://kura.test/n/X", "label": "python"})       # refused
        with open(self.server.log_path) as f:
            raw = f.read()
        rows = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual([r["tool"] for r in rows], ["notes_search", "board_get_card", "pages_set_label"])
        self.assertEqual([r["status"] for r in rows], ["ok", "ok", "refused"])
        self.assertEqual(rows[1]["args"], {"slug": "alpha"})
        self.assertNotIn("private thought", raw)
        self.assertEqual(rows[0]["agent"], "t")


class Failures(Base):
    def test_unreachable_room_is_a_clean_error(self):
        self.fakes["kura"].close()
        r = call(self.server, "notes_search", {"q": "x"})
        self.assertTrue(r["isError"])
        self.assertIn("unreachable", r["structuredContent"]["error"])

    def test_one_room_down_does_not_hide_the_others(self):
        self.fakes["kura"].close()
        d = data(call(self.server, "machiya_search", {"q": "x"}))
        self.assertIn("error", d["notes"])
        self.assertNotIn("pages", d)
        self.assertTrue(d["cards"]["cards"] or d["cards"]["total"] == 0)
        st = call(self.server, "machiya_status")["structuredContent"]["rooms"]
        self.assertFalse(st["kura"]["ok"])
        self.assertTrue(st["konbini"]["ok"])

    def test_handler_bug_does_not_leak(self):
        self.server.tools["board_review"] = dict(self.server.tools["board_review"], handler=lambda c, a: 1 / 0)
        r = call(self.server, "board_review")
        self.assertTrue(r["isError"])
        self.assertNotIn("division", json.dumps(r))


if __name__ == "__main__":
    unittest.main()
