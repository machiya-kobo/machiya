"""MCP_AUTH=oauth (0.10.0, docs/services/mcp.md, The public connector): the read-only connector for a cloud agent on a
Funnel host. Only a Bearer mho_… access token that hister-login's /v1/check vouches for (this server's resource, an
admitted user) gets in; only the allowed read tools exist; nothing that writes can be configured; the server says
nothing about itself. Run: python3 -m unittest discover -s stack/mcp/tests"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp                                  # noqa: E402
from test_mcp import make_server, rooms     # noqa: E402

PUBLIC = "https://mcp-public.example.ts.net"
RESOURCE = PUBLIC + "/mcp"
GOOD = "mho_" + "G" * 43                    # both scopes
PAGES = "mho_" + "P" * 43                   # pages:read only
OTHER_RES = "mho_" + "R" * 43               # another server's resource
OTHER_USER = "mho_" + "U" * 43              # a Hister user this server doesn't admit
ROOMISH = "mho_" + "K" * 43                 # the helper calls it a room token, not an OAuth one
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def rpc(method, params=None, mid=1):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}


def tool(name, args=None):
    return rpc("tools/call", {"name": name, "arguments": args or {}})


class Helper:
    """hister-login's internal /v1/check, as the OAuth server answers it for the room mcp-public."""
    ANSWERS = {GOOD: {"username": "owner", "kind": "oauth", "scopes": "pages:read notes:read", "resource": RESOURCE,
                      "client_id": "mhc_duet1"},
               PAGES: {"username": "owner", "kind": "oauth", "scopes": "pages:read", "resource": RESOURCE},
               OTHER_RES: {"username": "owner", "kind": "oauth", "scopes": "pages:read notes:read",
                           "resource": "https://elsewhere.example/mcp"},
               OTHER_USER: {"username": "guest", "kind": "oauth", "scopes": "pages:read notes:read", "resource": RESOURCE},
               ROOMISH: {"username": "owner", "kind": "token"}}

    def __init__(self):
        self.seen, self.down = [], False
        helper = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                cred, room = self.headers.get("X-Machiya-Session"), self.headers.get("X-Machiya-Room")
                helper.seen.append((cred, room))
                answer = helper.ANSWERS.get(cred) if room == mcp.OAUTH_ROOM and self.path == "/v1/check" else None
                status = 502 if helper.down else 200 if answer else 401
                body = json.dumps(answer or {"reason": "revoked"}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class OAuth(unittest.TestCase):
    def setUp(self):
        self.fakes = rooms()
        self.helper = Helper()
        self.addCleanup(lambda: ([f.close() for f in self.fakes.values()], self.helper.close()))

    def env(self, **extra):
        e = {"MCP_AUTH": "oauth", "MCP_TOOLS": ",".join(mcp.OAUTH_TOOLS), "MCP_PUBLIC_URL": PUBLIC,
             "MCP_OAUTH_ISSUER": PUBLIC, "MCP_AUTH_URL": self.helper.url, "MCP_HISTER_USERS": "owner",
             "MCP_LOG": os.devnull, "KURA_URL": self.fakes["kura"].url, "HISTER_URL": self.fakes["hister"].url}
        e.update(extra)
        return {k: v for k, v in e.items() if v is not None}

    def serve(self, **extra):
        env = self.env(**extra)
        env.pop("MCP_LOG")
        server = make_server({k: self.fakes[k] for k in ("kura", "hister")}, **env)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), mcp.make_handler(server))
        threading.Thread(target=httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), os.unlink(server.log_path)))
        return server, "http://127.0.0.1:%d" % httpd.server_address[1]

    @staticmethod
    def request(url, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), e.headers

    def post(self, base, body, token=GOOD, **headers):
        if token:
            headers["Authorization"] = "Bearer " + token
        return self.request(base + "/mcp", body, headers)

    # -- startup -------------------------------------------------------------------------------------------------
    def test_it_refuses_to_start_with_anything_that_writes_or_without_its_settings(self):
        mcp.Config(self.env())                                          # the good setup starts
        for bad in ({"MCP_TOOLS": None}, {"MCP_TOOLS": ""}, {"MCP_TOOLS": "pages_search,board_move"},
                    {"MCP_TOOLS": "pages_search,notes_create"}, {"MCP_TOOLS": "machiya_search"},
                    {"MCP_NOTES_DIR": "/data/notes"}, {"KONBINI_URL": "http://konbini:8080"},
                    {"NIWA_URL": "http://niwa:8080"}, {"MACHIYA_IDENTITY_FILE": "/etc/identity.toml"},
                    {"MCP_PUBLIC_URL": None}, {"MCP_PUBLIC_URL": RESOURCE}, {"MCP_PUBLIC_URL": "http://mcp.example"},
                    {"MCP_OAUTH_ISSUER": None}, {"MCP_AUTH_URL": None}, {"MCP_AUTH_URL": "ftp://h"},
                    {"MCP_HISTER_USERS": None}, {"MCP_HISTER_USERS": "*"},
                    {"MCP_ALLOW_CIDRS": "160.79.104.0/21"},
                    {"MCP_ALLOW_CIDRS": "160.79.104.0/21", "MCP_CLIENT_ADDR_HEADER": "X-Forwarded-For"},
                    {"MCP_ALLOW_CIDRS": "nonsense", "MCP_CLIENT_ADDR_HEADER": "X-Forwarded-For",
                     "MCP_TRUSTED_PROXIES": "127.0.0.1"}):
            with self.assertRaises(SystemExit, msg=bad):
                mcp.Config(self.env(**bad))
        mcp.Config(self.env(MCP_PUBLIC_URL="http://127.0.0.1:8099", MCP_OAUTH_ISSUER="http://localhost:8082"))   # dev stack
        with self.assertRaises(SystemExit):                             # a misspelt tool refuses in any mode
            make_server(self.fakes, MCP_TOOLS="pages_serch")

    def test_mcp_tools_filters_in_any_mode(self):
        server = make_server(self.fakes, MCP_TOOLS="board_list_cards,notes_read")
        self.addCleanup(os.unlink, server.log_path)
        self.assertEqual(set(server.tools), {"board_list_cards", "notes_read"})
        self.assertTrue(server.prompts)                                  # only oauth mode drops them

    # -- who gets in -----------------------------------------------------------------------------------------------
    def test_no_token_is_401_with_where_to_sign_in(self):
        _, base = self.serve()
        status, body, headers = self.post(base, PING, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(headers["WWW-Authenticate"],
                         'Bearer resource_metadata="%s/.well-known/oauth-protected-resource"' % PUBLIC)
        self.assertEqual(self.helper.seen, [])
        status, _, headers = self.post(base, PING, token="mho_" + "Z" * 43)     # revoked or unknown
        self.assertEqual(status, 401)
        self.assertTrue(headers["WWW-Authenticate"].startswith('Bearer error="invalid_token", resource_metadata="'))

    def test_the_protected_resource_metadata(self):
        _, base = self.serve()
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            status, doc, _ = self.request(base + path)
            self.assertEqual(status, 200, path)
            self.assertEqual(doc, {"resource": RESOURCE, "authorization_servers": [PUBLIC],
                                   "scopes_supported": ["notes:read", "pages:read"],
                                   "bearer_methods_supported": ["header"], "resource_name": "Machiya"})

    def test_only_an_oauth_token_counts_never_a_login_header_or_another_credential(self):
        _, base = self.serve(MCP_BIND="127.0.0.1", MCP_USERS="me@x", MCP_TRUSTED_PROXIES="127.0.0.1")
        ts = {"Tailscale-User-Login": "me@x"}
        self.assertEqual(self.post(base, PING, token=None, **ts)[0], 401)
        for cred in ("mht_" + "R" * 43, "mch_abc.def", "tok-owner-hister", ROOMISH):
            self.assertEqual(self.post(base, PING, token=cred, **ts)[0], 401, cred)
        self.assertEqual(self.post(base, PING, token=None, **ts, **{"X-Machiya-Token": "mht_" + "R" * 43})[0], 401)
        self.assertEqual(self.post(base, PING, token=None, **{"X-Access-Token": "tok-owner-hister"})[0], 401)
        self.assertEqual(self.request(base + "/mcp", PING, {"Authorization": "Basic b3duZXI6eA=="})[0], 401)
        self.assertEqual(self.post(base, PING)[0], 200)
        self.assertNotIn(("mht_" + "R" * 43, mcp.OAUTH_ROOM), self.helper.seen)   # never even asked about
        self.assertEqual(self.post(base, PING, Origin="https://evil.example")[0], 403)   # still no browser pages

    def test_another_resource_or_user_is_refused(self):
        _, base = self.serve()
        self.assertEqual(self.post(base, PING, token=OTHER_RES)[0], 401)
        self.assertEqual(self.post(base, PING, token=OTHER_USER)[0], 403)

    def test_the_helper_down_is_503_not_a_way_in(self):
        _, base = self.serve()
        self.helper.down = True
        self.assertEqual(self.post(base, PING)[0], 503)

    def test_answers_are_cached_by_the_token(self):
        clock = [1000.0]
        server, base = self.serve()
        server.config.oauth.clock = lambda: clock[0]
        for _ in range(3):
            self.assertEqual(self.post(base, PING)[0], 200)
        self.assertEqual(len(self.helper.seen), 1)
        clock[0] += 31
        self.assertEqual(self.post(base, PING)[0], 200)
        self.assertEqual(len(self.helper.seen), 2)
        self.assertTrue(all(room == mcp.OAUTH_ROOM for _, room in self.helper.seen))

    # -- what it serves --------------------------------------------------------------------------------------------
    def test_tools_list_is_the_allowlist_and_nothing_else_exists(self):
        _, base = self.serve()
        _, body, _ = self.post(base, rpc("tools/list"))
        self.assertEqual([t["name"] for t in body["result"]["tools"]], ["notes_search", "notes_read", "pages_search", "pages_read"])
        self.assertTrue(all(t["annotations"].get("readOnlyHint") for t in body["result"]["tools"]))
        _, body, _ = self.post(base, rpc("initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual(set(body["result"]["capabilities"]), {"tools"})
        self.assertIn("read-only", body["result"]["instructions"])
        for method, key in (("resources/list", "resources"), ("resources/templates/list", "resourceTemplates"),
                            ("prompts/list", "prompts")):
            _, body, _ = self.post(base, rpc(method))
            self.assertEqual(body["result"][key], [], method)
        _, body, _ = self.post(base, rpc("resources/read", {"uri": "machiya://note/Projects/Alpha.md"}))
        self.assertIn("error", body)

    def test_a_write_or_board_tool_is_refused(self):
        _, base = self.serve()
        for name, args in (("pages_set_label", {"url": "https://example.com/a", "label": "python"}),
                           ("notes_create", {"title": "x"}), ("board_move", {"slug": "alpha", "board": "done"}),
                           ("machiya_status", {})):
            _, body, _ = self.post(base, tool(name, args))
            self.assertIn("Unknown tool", body["error"]["message"], name)
        self.assertEqual(self.fakes["hister"].writes(), [])

    def test_the_reads_work_with_their_guards(self):
        server, base = self.serve()
        _, body, _ = self.post(base, tool("pages_search", {"q": "hello"}))
        result = body["result"]
        self.assertFalse(result.get("isError"))
        self.assertEqual(result["structuredContent"]["trust"], "untrusted")
        self.assertTrue(self.fakes["hister"].seen[-1]["query"]["q"].endswith("-label:vault -metadata.source:vault "
                                                                             "-metadata.source:code -label:konbini"))
        _, body, _ = self.post(base, tool("notes_search", {"q": "alpha"}))
        notes = body["result"]["structuredContent"]["untrusted_content"]
        self.assertNotIn("Secret.md", json.dumps(notes))                 # the work vault never passes
        _, body, _ = self.post(base, tool("notes_read", {"path": "Secret.md"}))
        self.assertTrue(body["result"].get("isError"))
        kura = [r for r in self.fakes["kura"].seen if r["path"] != "/api/vaults"]
        self.assertTrue(kura and all(r["headers"]["x-agent"] == "mcp:oauth:owner/mhc_duet1" for r in kura))
        with open(server.log_path) as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual({(r["principal"], r["via"]) for r in rows}, {("oauth:owner/mhc_duet1", "oauth")})
        self.assertNotIn("hello", json.dumps(rows))                      # argument keys only, never the query
        self.assertNotIn(GOOD, json.dumps(rows))

    def test_a_missing_scope_is_403_insufficient_scope(self):
        _, base = self.serve()
        self.assertFalse(self.post(base, tool("pages_search", {"q": "x"}), token=PAGES)[1]["result"].get("isError"))
        status, _, headers = self.post(base, tool("notes_search", {"q": "x"}), token=PAGES)
        self.assertEqual(status, 403)
        self.assertTrue(headers["WWW-Authenticate"].startswith('Bearer error="insufficient_scope", scope="notes:read", '
                                                               'resource_metadata="'))
        status, _, _ = self.post(base, [tool("pages_read", {"url": "https://example.com/a"}),
                                        tool("notes_read", {"path": "Projects/Alpha.md"})], token=PAGES)
        self.assertEqual(status, 403)                                    # a batch is refused as a whole

    def test_it_says_nothing_about_itself(self):
        _, base = self.serve()
        self.assertEqual(self.request(base + "/healthz")[:2], (200, {"ok": True}))
        for path in ("/api/status", "/api/changelog", "/"):
            self.assertEqual(self.request(base + path)[0], 404, path)

    def test_the_read_limit_is_lower_here(self):
        self.assertEqual(mcp.Config(self.env()).reads_per_min, 60)
        self.assertEqual(mcp.Config(self.env(MCP_READS_PER_MIN="20")).reads_per_min, 20)

    # -- where calls may come from ---------------------------------------------------------------------------------
    def test_allow_cidrs_reads_the_proxys_header_only_from_the_proxy(self):
        anthropic = {"X-Forwarded-For": "160.79.105.7"}
        _, base = self.serve(MCP_ALLOW_CIDRS="160.79.104.0/21", MCP_CLIENT_ADDR_HEADER="X-Forwarded-For",
                             MCP_TRUSTED_PROXIES="127.0.0.1")
        self.assertEqual(self.post(base, PING, **anthropic)[0], 200)
        self.assertEqual(self.post(base, PING, **{"X-Forwarded-For": "203.0.113.9"})[0], 403)
        self.assertEqual(self.post(base, PING, **{"X-Forwarded-For": "160.79.105.7, 203.0.113.9"})[0], 403)  # the proxy's is last
        self.assertEqual(self.post(base, PING)[0], 403)                   # no header, no way in
        self.assertEqual(self.request(base + "/.well-known/oauth-protected-resource")[0], 200)   # /mcp only
        _, base = self.serve(MCP_ALLOW_CIDRS="160.79.104.0/21", MCP_CLIENT_ADDR_HEADER="X-Forwarded-For",
                             MCP_TRUSTED_PROXIES="10.9.9.9")
        self.assertEqual(self.post(base, PING, **anthropic)[0], 403)      # the header from anyone else counts for nothing


if __name__ == "__main__":
    unittest.main()
