"""Page labels and collections (rooms/hister_write.py) against a stateful fake Hister."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp                      # noqa: E402
import test_mcp as T            # noqa: E402
from fake_hister import FakeHister   # noqa: E402

TECH = "https://tech.example/p/000"


ZONE = "Europe/Berlin"
# The settings of a deployment with a weekly backup, import labels and a hand-kept collection (none of them is a default).
SETTINGS = {"MCP_TZ": ZONE, "MCP_HISTER_BACKUP_WINDOW": "Sat 03:10-03:40", "MCP_RESERVED_LABELS": "feedreader,importer",
            "MCP_RESERVED_COLLECTIONS": "everything"}


def at(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(ZONE)).timestamp()


def in_window():
    return at(2026, 10, 3, 3, 20)          # a Saturday


class Base(unittest.TestCase):
    SETTINGS = SETTINGS

    def setUp(self):
        self.h = FakeHister()
        self.addCleanup(self.h.close)
        self.tmp = tempfile.mkdtemp(prefix="pages-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.server = self.make()
        self.now = [1_800_000_000.0]      # a Friday
        self.server.clock = lambda: self.now[0]

    def make(self, **env):
        server = T.make_server({"hister": self.h}, MCP_ROLLBACK_DIR=os.path.join(self.tmp, "rb"), **dict(self.SETTINGS, **env))
        self.addCleanup(os.unlink, server.log_path)
        return server

    def call(self, _tool, **args):
        return T.call(self.server, _tool, args)

    def ok(self, _tool, **args):
        r = self.call(_tool, **args)
        self.assertFalse(r.get("isError"), T.body(r))
        return T.data(r)

    def err(self, _tool, *needles, owner=None, **args):
        r = self.call(_tool, **args)
        self.assertTrue(r.get("isError"), "expected a refusal: %s" % json.dumps(T.body(r))[:200])
        b = T.body(r)
        for n in needles:
            self.assertIn(n.lower(), b["error"].lower())
        if owner is not None:
            self.assertEqual(bool(b.get("needs_owner")), owner)
        return b

    def label_of(self, url):
        return self.h.docs[url]["label"]

    def posts(self):
        return self.h.writes


class Reads(Base):
    def test_labels_census_pages_through_every_page_and_separates_notes_and_imports(self):
        d = self.ok("pages_labels")
        by = {x["label"]: x["count"] for x in d["labels"]}
        self.assertEqual(by, {"tech": 120, "hardware": 60, "music": 30})
        self.assertEqual(d["unlabelled"], 20)
        self.assertEqual(d["other"], {"feedreader": 10, "importer": 4})
        self.assertEqual(d["pages"], 244)                          # 120+60+30+20+10+4: no note, no card
        self.assertNotIn("vault", json.dumps(d)); self.assertNotIn("konbini", json.dumps(d))
        searches = [r for r in self.h.requests if r[1] == "/search"]
        self.assertGreaterEqual(len(searches), 3)                   # 244 pages are more than one 100-page
        for _, _, q, headers in searches:
            self.assertTrue(q["q"].endswith(" -label:vault -metadata.source:vault -label:konbini"), q["q"])
            self.assertEqual(headers.get("Origin"), "hister://")

    def test_census_is_cached_and_dropped_after_a_write(self):
        self.ok("pages_labels"); n1 = len(self.h.requests)
        self.ok("pages_labels"); self.assertEqual(len(self.h.requests), n1)
        self.ok("pages_set_label", url=TECH, label="music")
        self.ok("pages_labels"); self.assertGreater(len(self.h.requests), n1 + 1)
        self.now[0] += 400
        n2 = len(self.h.requests)
        self.ok("pages_labels"); self.assertGreater(len(self.h.requests), n2)

    def test_audit_collections(self):
        d = self.ok("collections_audit")
        self.assertEqual(d["collections"]["@tech"]["labels"], ["hardware", "tech"])
        self.assertEqual(d["collections"]["@tech"]["pages"], 180)

    def test_audit_details(self):
        d = self.ok("collections_audit")
        self.assertIn("music", d["labels_in_no_collection"])
        self.assertEqual(d["dead_labels_in_collections"], {"@legacy": ["dead"]})
        self.assertEqual(d["owners_own"], ["@mix", "everything"])       # not editable: a non-label value, and a plain keyword
        self.assertEqual(d["everything_drift"]["labels_missing_from_everything"], [])
        self.h.aliases["@mixed"] = "label:(tech|konbini)"
        self.h.aliases["@cards"] = "label:konbini"
        d = self.ok("collections_audit")
        self.assertEqual(d["aliases_naming_reserved_labels"], {"@cards": ["konbini"], "@mixed": ["konbini"]})
        self.assertIn("@mixed", d["owners_own"])
        self.assertIn("feedreader", json.dumps(d["everything_drift"]) + "feedreader")
        self.assertNotIn("@notes", json.dumps(d)); self.assertNotIn("@pages", json.dumps(d))


class SetLabel(Base):
    def test_sets_a_label_with_origin_and_the_agent(self):
        d = self.ok("pages_set_label", url=TECH, label="music")
        self.assertEqual((d["old_label"], d["label"], d["changed"]), ("tech", "music", True))
        self.assertEqual(self.label_of(TECH), "music")
        w = self.posts()[-1]
        self.assertEqual((w["path"], w["body"]), ("/api/label", {"url": TECH, "label": "music"}))
        self.assertEqual(w["headers"]["Origin"], "hister://")
        self.assertEqual(w["headers"]["X-Agent"], "mcp:t")

    def test_clearing_and_no_change(self):
        self.assertTrue(self.ok("pages_set_label", url=TECH, label="")["changed"])
        self.assertEqual(self.label_of(TECH), "")
        n = len(self.posts())
        self.assertFalse(self.ok("pages_set_label", url=TECH, label="")["changed"])
        self.assertEqual(len(self.posts()), n)

    def test_new_reserved_and_malformed_labels(self):
        self.err("pages_set_label", "not an existing label", owner=True, url=TECH, label="brand-new")
        for bad in ("vault", "konbini", "feedreader", "FeedReader", "importer"):
            self.err("pages_set_label", "not a topic", url=TECH, label=bad)
        for bad in ("a b", "x)|(label:vault", "-x", "a" * 60):
            self.err("pages_set_label", url=TECH, label=bad)
        self.assertFalse(self.posts())

    def test_notes_cards_imports_and_room_hosts_are_refused(self):
        self.err("pages_set_label", "vault note", url="https://kura.test/n/Notes/Note0", label="tech")
        self.err("pages_set_label", url="https://konbini.test/p/card0", label="tech")
        self.err("pages_set_label", "import", url="https://news.example/p/000", label="tech")
        self.err("pages_set_label", url="file:///etc/passwd", label="tech")
        self.assertFalse(self.posts())

    def test_expected_label(self):
        b = self.err("pages_set_label", "read it again", url=TECH, label="music", expected_label="hardware")
        self.assertEqual(b["current"], "tech")
        self.ok("pages_set_label", url=TECH, label="music", expected_label="tech")

    def test_paused_during_the_backup_window(self):
        self.now[0] = in_window()
        b = self.err("pages_set_label", "backup", url=TECH, label="music")
        self.assertEqual(b["code"], "backup_window")
        self.assertIn("Sat 03:10-03:40", b["error"])
        self.assertFalse(self.posts())
        self.now[0] = in_window() + 20 * 60           # 03:40, the end
        self.ok("pages_set_label", url=TECH, label="music")


class Relabel(Base):
    Q = "label:hardware"

    def dry(self, query=None, label="tech"):
        return self.ok("pages_relabel", query=query or self.Q, label=label)

    def test_dry_run_shows_the_plan_and_writes_nothing(self):
        d = self.dry()
        self.assertEqual((d["matched"], d["will_change"], d["already_labelled"]), (60, 60, 0))
        self.assertEqual(d["from"], {"hardware": 60})
        self.assertEqual(len(d["sample"]), 10)
        self.assertTrue(d["apply_token"])
        self.assertFalse(self.posts())

    def test_apply_changes_each_page_one_update_and_saves_a_rollback_file(self):
        tok = self.dry()["apply_token"]
        r = self.ok("pages_relabel", apply_token=tok)
        self.assertEqual((r["applied"], r["problems"]), (60, []))
        self.assertEqual(sum(1 for d in self.h.docs.values() if d["label"] == "tech"), 180)
        self.assertEqual(sum(1 for d in self.h.docs.values() if d["label"] == "hardware"), 0)
        ups = [w for w in self.posts() if w["path"] == "/api/update"]
        self.assertEqual(len(ups), 60)
        for w in ups:
            self.assertEqual(set(w["body"]), {"query", "changes"})
            self.assertEqual(w["body"]["changes"], {"label": "tech"})
            self.assertTrue(w["body"]["query"].startswith('url:"'))
        with open(r["rollback_file"]) as f:
            old = json.load(f)
        self.assertEqual(len(old), 60)
        self.assertEqual(set(old.values()), {"hardware"})        # a JSON map of url -> old label

    def test_a_token_is_single_use_and_only_the_same_login_and_unexpired(self):
        tok = self.dry()["apply_token"]
        r = T.call(self.server, "pages_relabel", {"apply_token": tok})        # the caller in tests is "owner"
        self.assertFalse(r.get("isError"))
        self.assertEqual(self.err("pages_relabel", apply_token=tok)["code"], "bad_token")
        tok = self.dry(query="domain:music.example", label="tech")["apply_token"]
        self.now[0] += 601
        self.assertEqual(self.err("pages_relabel", apply_token=tok)["code"], "bad_token")
        tok = self.dry(query="domain:music.example", label="tech")["apply_token"]
        other = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "pages_relabel", "arguments": {"apply_token": tok}}}, "someone-else", "t")["result"]
        self.assertEqual(T.body(other)["code"], "bad_token")

    def test_a_changed_result_set_invalidates_the_plan(self):
        tok = self.dry()["apply_token"]
        self.h.docs["https://hardware.example/p/000"]["label"] = "music"       # the owner labelled one meanwhile
        self.assertEqual(self.err("pages_relabel", apply_token=tok)["code"], "stale_plan")
        self.assertFalse([w for w in self.posts() if w["path"] == "/api/update"])

    def test_more_than_the_cap_is_refused(self):
        self.h.aliases["@all"] = "*"
        self.err("pages_relabel", "more than 200", query="@all", label="music")
        self.assertFalse(self.posts())

    def test_notes_and_cards_can_never_match_whatever_the_query(self):
        d = self.dry(query="label:vault", label="tech")
        self.assertEqual(d.get("matched"), 0)
        d = self.dry(query="label:konbini", label="tech")
        self.assertEqual(d.get("matched"), 0)
        d = self.dry(query="Vault note", label="tech")
        self.assertEqual(d.get("matched"), 0)
        self.assertFalse(self.posts())

    def test_pages_with_an_import_label_are_never_changed(self):
        d = self.dry(query="domain:news.example", label="tech")
        self.assertEqual((d["matched"], d.get("will_change", 0)), (10, 0))
        d = self.dry(query="label:(feedreader|importer|music)", label="tech")
        self.assertEqual((d["matched"], d["will_change"]), (44, 30))      # only the 30 music pages

    def test_label_rules_apply_to_a_relabel_too(self):
        self.err("pages_relabel", "not an existing label", owner=True, query=self.Q, label="brand-new")
        self.err("pages_relabel", "not a topic", query=self.Q, label="vault")
        self.err("pages_relabel", query="", label="tech")
        d = self.dry(query="domain:visited.example", label="")          # clearing is allowed, but nothing has a label to clear there
        self.assertEqual(d["will_change"], 0)
        d = self.dry(query="domain:music.example", label="")
        self.assertEqual(d["will_change"], 30)

    def test_bulk_applies_are_limited_per_hour_and_a_refused_one_keeps_the_token_cost_out(self):
        self.server = self.make(MCP_BULK_PER_HOUR="1")
        self.server.clock = lambda: self.now[0]
        t1 = self.dry(query="domain:music.example", label="tech")["apply_token"]
        self.ok("pages_relabel", apply_token=t1)
        t2 = self.dry(query="domain:visited.example", label="tech")["apply_token"]
        self.assertEqual(self.err("pages_relabel", apply_token=t2)["code"], "rate_limited")

    def test_the_backup_window_pauses_the_apply_but_not_the_dry_run(self):
        self.now[0] = in_window()
        tok = self.dry()["apply_token"]
        self.assertEqual(self.err("pages_relabel", apply_token=tok)["code"], "backup_window")

    def test_a_route_can_only_change_a_label(self):
        for room, path, body in (("hister", "/api/update", {"query": "x", "changes": {"title": "t"}}),
                                 ("hister", "/api/update", {"query": "x", "changes": {"label": "a", "metadata": {}}}),
                                 ("hister", "/api/update", {"query": "x"}), ("hister", "/api/delete", {"query": "x"}),
                                 ("hister", "/api/add", {"url": "x"}), ("hister", "/api/label", {"url": "u", "label": "l", "extra": 1}),
                                 ("hister", "/api/add_alias", {"alias-keyword": "a", "alias-value": "b", "x": 1})):
            with self.assertRaises(RuntimeError):
                mcp.check_route(room, "POST", path, body)
        mcp.check_route("hister", "POST", "/api/update", {"query": "x", "changes": {"label": "a"}})


class Collections(Base):
    def test_set_creates_and_changes_a_label_collection(self):
        d = self.ok("collections_set", name="@Music-Stuff", labels=["music", "tech"])
        self.assertEqual((d["name"], d["before"], d["after"]), ("@music-stuff", None, "label:(music|tech)"))
        self.assertEqual(self.h.aliases["@music-stuff"], "label:(music|tech)")
        w = self.posts()[-1]
        self.assertEqual((w["path"], w["body"], w["ctype"]), ("/api/add_alias", {"alias-keyword": "@music-stuff", "alias-value": "label:(music|tech)"},
                                                              "application/x-www-form-urlencoded"))
        self.assertEqual(w["headers"]["Origin"], "hister://")
        d = self.ok("collections_set", name="music-stuff", labels=["music"])
        self.assertEqual((d["before"], d["after"]), ("label:(music|tech)", "label:music"))
        self.assertFalse(self.ok("collections_set", name="music-stuff", labels=["music"])["changed"])

    def test_the_owners_own_aliases_reserved_names_and_bad_labels_are_refused(self):
        for name in ("@mix", "everything", "@notes", "@pages", "notes", "pages"):
            self.err("collections_set", name=name, labels=["tech"])
        self.err("collections_set", "not an existing label", owner=True, name="new", labels=["brand-new"])
        for bad in (["vault"], ["tech", "konbini"], ["feedreader"], []):
            self.err("collections_set", name="new", labels=bad)
        self.err("collections_set", name="a b", labels=["tech"])
        self.assertEqual(self.h.aliases["@mix"], "label:tech -domain:x.example")
        self.assertFalse(self.posts())

    def test_remove_is_two_steps_and_saves_the_old_definition(self):
        d = self.ok("collections_remove", name="@legacy")
        self.assertEqual((d["dry_run"], d["value"]), (True, "label:(hardware|dead)"))
        self.assertIn("@legacy", self.h.aliases)
        r = self.ok("collections_remove", name="legacy", apply_token=d["apply_token"])
        self.assertEqual(r["removed"], "@legacy")
        self.assertNotIn("@legacy", self.h.aliases)
        with open(r["rollback_file"]) as f:
            self.assertEqual(json.load(f), {"@legacy": "label:(hardware|dead)"})
        w = self.posts()[-1]
        self.assertEqual((w["path"], w["body"], w["ctype"]), ("/api/delete_alias", {"alias": "@legacy"}, "application/x-www-form-urlencoded"))
        self.assertEqual(self.label_of(TECH), "tech")                      # no page changed

    def test_remove_refuses_the_owners_aliases_and_bad_tokens(self):
        for name in ("@mix", "everything", "@notes", "@pages"):
            self.err("collections_remove", name=name)
        self.err("collections_remove", "no collection", name="@ghost")
        tok = self.ok("collections_remove", name="@legacy")["apply_token"]
        self.assertEqual(self.err("collections_remove", name="@tech", apply_token=tok)["code"], "bad_token")
        tok = self.ok("collections_remove", name="@legacy")["apply_token"]
        self.h.aliases["@legacy"] = "label:music"                          # changed meanwhile
        self.assertEqual(self.err("collections_remove", name="@legacy", apply_token=tok)["code"], "stale_plan")
        self.assertIn("@legacy", self.h.aliases)

    def test_a_failed_delete_is_a_clean_error(self):
        tok = self.ok("collections_remove", name="@legacy")["apply_token"]
        del self.h.aliases["@legacy"]
        r = self.call("collections_remove", name="@legacy", apply_token=tok)
        self.assertTrue(r["isError"])


class Defaults(Base):
    """No settings at all: no pause window, UTC, and only `vault` and `konbini` reserved."""
    SETTINGS = {}

    def test_no_backup_window_and_utc(self):
        c = mcp.Config({})
        self.assertEqual((c.backup_window, c.tz, c.notes_tz, c.reserved_collections), (None, "UTC", "UTC", set()))
        self.now[0] = in_window()
        self.assertFalse(self.server.in_backup_window())
        self.ok("pages_set_label", url=TECH, label="music")

    def test_only_vault_and_konbini_are_reserved_labels(self):
        self.assertEqual(mcp.Config({}).reserved_labels, {"vault", "konbini"})
        for bad in ("vault", "konbini", "Vault"):
            self.err("pages_set_label", "not a topic", url=TECH, label=bad)
        d = self.ok("pages_labels")
        self.assertEqual(d["other"], {})
        self.assertIn("feedreader", {x["label"] for x in d["labels"]})
        self.ok("pages_set_label", url=TECH, label="feedreader")

    def test_no_collection_name_is_reserved_beyond_notes_and_pages(self):
        self.ok("collections_set", name="everything", labels=["tech"])
        self.err("collections_set", "notes and pages are reserved", name="notes", labels=["tech"])
        d = self.ok("collections_audit")
        self.assertFalse([k for k in d if k.endswith("_drift")])
        self.assertIn("@everything", d["collections"])


class Settings(Base):
    def test_timezone_settings(self):
        self.assertEqual((mcp.Config({"MCP_TZ": ZONE}).tz, mcp.Config({"MCP_TZ": ZONE}).notes_tz), (ZONE, ZONE))
        c = mcp.Config({"MCP_TZ": ZONE, "MCP_NOTES_TZ": "Asia/Tokyo"})
        self.assertEqual((c.tz, c.notes_tz), (ZONE, "Asia/Tokyo"))
        c = mcp.Config({"MCP_NOTES_TZ": "Asia/Tokyo"})
        self.assertEqual((c.tz, c.notes_tz), ("Asia/Tokyo", "Asia/Tokyo"))

    def test_the_window_edges_in_the_configured_zone(self):
        for when, paused in ((at(2026, 10, 3, 3, 9), False), (at(2026, 10, 3, 3, 10), True), (at(2026, 10, 3, 3, 39), True),
                             (at(2026, 10, 3, 3, 40), False), (at(2026, 10, 2, 3, 20), False), (at(2026, 10, 10, 3, 20), True)):
            self.now[0] = when
            self.assertEqual(self.server.in_backup_window(), paused, when)
        self.now[0] = datetime(2026, 10, 3, 3, 20, tzinfo=ZoneInfo("UTC")).timestamp()       # the same wall clock in another zone is not this window
        self.assertFalse(self.server.in_backup_window())

    def test_another_window_replaces_the_first(self):
        server = self.make(MCP_HISTER_BACKUP_WINDOW="Wed 23:00-23:30")
        server.clock = lambda: self.now[0]
        for when, paused in ((at(2026, 10, 7, 23, 10), True), (at(2026, 10, 7, 22, 59), False), (at(2026, 10, 7, 23, 30), False),
                             (at(2026, 10, 3, 3, 20), False)):
            self.now[0] = when
            self.assertEqual(server.in_backup_window(), paused, when)
        self.now[0] = at(2026, 10, 7, 23, 10)
        r = T.call(server, "pages_set_label", {"url": TECH, "label": "music"})
        self.assertEqual(T.body(r)["code"], "backup_window")
        self.assertIn("Wed 23:00-23:30", T.body(r)["error"])

    def test_the_window_format(self):
        for text, want in (("Sat 03:10-03:40", (5, (3, 10), (3, 40))), ("saturday 3:10 - 3:40", (5, (3, 10), (3, 40))),
                           ("Mon 00:00-23:59", (0, (0, 0), (23, 59))), ("", None), ("  ", None)):
            self.assertEqual(mcp.backup_window(text), want, text)
        self.assertIsNone(mcp.Config({"MCP_HISTER_BACKUP_WINDOW": ""}).backup_window)
        for bad in ("03:10-03:40", "Funday 03:10-03:40", "Sat 03:40-03:10", "Sat 03:10-03:10", "Sat 22:00-02:00", "Sat 25:00-26:00", "Sat 03:10"):
            with self.assertRaises(SystemExit, msg=bad):
                mcp.Config({"MCP_HISTER_BACKUP_WINDOW": bad})

    def test_reserved_labels_add_to_vault_and_konbini(self):
        self.assertEqual(self.server.config.reserved_labels, {"vault", "konbini", "feedreader", "importer"})
        self.assertEqual(mcp.Config({"MCP_RESERVED_LABELS": " A, b ,"}).reserved_labels, {"vault", "konbini", "a", "b"})

    def test_reserved_collections_are_refused_and_audited_for_drift(self):
        self.assertEqual(self.server.config.reserved_collections, {"everything"})
        self.assertEqual(mcp.Config({"MCP_RESERVED_COLLECTIONS": "@Mine, other"}).reserved_collections, {"mine", "other"})
        for name in ("everything", "@Everything"):
            self.err("collections_set", "notes, pages and everything are reserved", name=name, labels=["tech"])
        self.err("collections_remove", "reserved", name="everything")
        d = self.ok("collections_audit")
        self.assertEqual(d["everything_drift"]["note"], "everything is the owner's own keyword: reported, never edited here")
        self.assertEqual(d["everything_drift"]["labels_in_everything_with_no_pages"], [])
        self.h.aliases["drafts"] = "label:(music|hardware)"
        server = self.make(MCP_RESERVED_COLLECTIONS="everything,drafts")
        d = T.data(T.call(server, "collections_audit", {}))
        self.assertEqual(d["drafts_drift"]["labels_missing_from_drafts"], ["tech"])
        self.assertIn("drafts", d["owners_own"])


class Tools(Base):
    def test_annotations_limits_and_prompt(self):
        t = self.server.tools
        self.assertTrue(t["pages_labels"]["annotations"]["readOnlyHint"] and t["collections_audit"]["annotations"]["readOnlyHint"])
        for name in ("pages_relabel", "collections_remove"):
            self.assertTrue(t[name]["annotations"]["destructiveHint"], name)
        for name in ("pages_set_label", "pages_relabel", "collections_set", "collections_remove"):
            self.assertFalse(t[name]["annotations"]["readOnlyHint"], name)
            self.assertEqual(t[name]["limits"], ("hister_write",))
        self.assertIn("tidy_collections", [p["name"] for p in self.server.prompts])

    def test_hister_write_rate_limit_and_audit_has_no_label_text(self):
        server = self.make(MCP_HISTER_WRITES_PER_MIN="2")
        server.clock = lambda: self.now[0]
        rs = [T.call(server, "pages_set_label", {"url": "https://tech.example/p/00%d" % i, "label": "music"}) for i in range(3)]
        self.assertEqual([bool(r.get("isError")) for r in rs], [False, False, True])
        self.assertEqual(T.body(rs[2])["code"], "rate_limited")


if __name__ == "__main__":
    unittest.main()
