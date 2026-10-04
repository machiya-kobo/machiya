"""machiya-mcp with Machiya's identity file (MACHIYA_IDENTITY_FILE, vaultkit.identity): who may call it (the `mcp`
`use` grant; 401 for no proof or a bad one, never a fall-through to a login; 403 without the grant), buckets and apply
tokens per principal with the principal's own limits, the audit log's principal and `via`, and the startup checks.
Run: python3 -m unittest discover -s stack/mcp/tests"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp                                  # noqa: E402  (puts the vendored vaultkit on the path)
from backend import HISTER_ORIGIN, Backend, BackendError   # noqa: E402
from test_mcp import Fake, call, make_server, rooms        # noqa: E402
from vaultkit import identity               # noqa: E402

PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def tool(name, args=None, mid=1):
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}


def write_identity(folder):
    """The owner (a Tailscale login), an agent allowed the MCP with its own limits, an agent without the `mcp` grant,
    a person behind a proxy allowed the MCP, and the `mcp` principal the rooms see. -> {name: token}."""
    with open(os.path.join(folder, "session.key"), "w") as f:
        f.write("k" * 43)
    data = {"version": 1, "session_key_file": "session.key", "principals": {
        "owner": {"id": "ownerid000000001", "kind": "person", "owner": True, "tailscale": ["owner@test"]},
        "claude-vm": {"kind": "agent", "grants": {"mcp": ["use"]},
                      "limits": {"reads_per_min": 2, "writes_per_day": 1, "not_a_bucket": 0}},
        "reader": {"kind": "agent", "grants": {"kura": ["read"], "konbini": ["read"]}},
        "partner": {"id": "partnerid0000001", "kind": "person", "proxy": ["partner"], "tailscale": ["partner@test"],
                    "grants": {"mcp": ["use"]}},
        "mcp": {"kind": "agent", "grants": {"kura": ["read"], "konbini": ["read", "write"], "niwa": ["read", "suggest"]}}}}
    tokens = {n: identity.new_token(data, n, "test") for n in ("claude-vm", "reader", "mcp")}
    identity.write_file(os.path.join(folder, "identity.toml"), data)
    return tokens


class WithFile(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.tokens = write_identity(self.folder)
        self.file = os.path.join(self.folder, "identity.toml")
        self.fakes = rooms()
        self.addCleanup(lambda: [f.close() for f in self.fakes.values()])

    def serve(self, **env):
        env.setdefault("MCP_AUTH", "tailscale")
        env.setdefault("MCP_BIND", "127.0.0.1")
        env.setdefault("MACHIYA_IDENTITY_FILE", self.file)
        server = make_server(self.fakes, **env)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), mcp.make_handler(server))
        threading.Thread(target=httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), os.unlink(server.log_path)))
        return server, "http://127.0.0.1:%d" % httpd.server_address[1]

    def post(self, base, body, headers=None):
        req = urllib.request.Request(base + "/mcp", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def bearer(self, name):
        return {"Authorization": "Bearer " + self.tokens[name]}

    @staticmethod
    def audit(server):
        with open(server.log_path) as f:
            return [json.loads(line) for line in f.read().splitlines()]


class Inbound(WithFile):
    def test_an_agent_with_the_mcp_grant_is_served_and_logged_by_name_and_proof(self):
        server, base = self.serve()
        self.assertEqual(self.post(base, PING, self.bearer("claude-vm"))[0], 200)
        code, reply = self.post(base, tool("board_review"), self.bearer("claude-vm"))
        self.assertEqual(code, 200)
        self.assertFalse(reply["result"].get("isError"))
        row = self.audit(server)[-1]
        tid = self.tokens["claude-vm"].split("_")[1]
        self.assertEqual((row["principal"], row["via"]), ("claude-vm", "token:" + tid))
        self.assertNotIn("login", row)
        with open(server.log_path) as f:
            self.assertNotIn(self.tokens["claude-vm"].split("_")[2], f.read())     # the id, never the secret

    def test_a_principal_without_the_grant_is_refused(self):
        _, base = self.serve()
        code, body = self.post(base, PING, self.bearer("reader"))
        self.assertEqual(code, 403)
        self.assertIn("error", body)
        self.assertEqual(self.fakes["konbini"].seen, [])

    def test_no_proof_or_a_bad_one_is_401_and_never_falls_through_to_a_login(self):
        _, base = self.serve()
        self.assertEqual(self.post(base, PING)[0], 401)
        bad = self.tokens["claude-vm"][:-4] + "AAAA"
        for auth in ("Bearer " + bad, "Bearer mch_nope_secret", "Bearer ", "Basic b3duZXI6eA=="):
            code, _ = self.post(base, PING, {"Authorization": auth, "Tailscale-User-Login": "owner@test"})
            self.assertEqual(code, 401, auth)

    def test_tailscale_logins_come_from_the_file_not_mcp_users(self):
        _, base = self.serve(MCP_USERS="stranger@test")
        self.assertEqual(self.post(base, PING, {"Tailscale-User-Login": "owner@test"})[0], 200)
        self.assertEqual(self.post(base, PING, {"Tailscale-User-Login": "partner@test"})[0], 200)
        self.assertEqual(self.post(base, PING, {"Tailscale-User-Login": "stranger@test"})[0], 403)

    def test_browser_origins_are_refused_in_every_mode(self):
        for env in ({}, {"MCP_AUTH": "open"}, {"MCP_AUTH": "header", "MCP_AUTH_HEADER": "Remote-User"}):
            _, base = self.serve(**env)
            code, _ = self.post(base, PING, dict(self.bearer("claude-vm"), Origin="https://evil.example"))
            self.assertEqual(code, 403, env)

    def test_the_principals_limits_replace_the_defaults(self):
        server, base = self.serve(MCP_READS_PER_MIN="50")
        replies = [self.post(base, tool("board_review", mid=i), self.bearer("claude-vm"))[1] for i in range(3)]
        self.assertEqual([bool(r["result"].get("isError")) for r in replies], [False, False, True])
        self.assertEqual(replies[2]["result"]["structuredContent"]["code"], "rate_limited")
        # the owner has no limits in the file: the server's defaults, in a bucket of its own
        replies = [self.post(base, tool("board_review", mid=i), {"Tailscale-User-Login": "owner@test"})[1] for i in range(3)]
        self.assertFalse(any(r["result"].get("isError") for r in replies))
        self.assertEqual(set(server.limits["read"].buckets), {"claude-vm", "owner"})
        # writes_per_day = 1: the second claim of the day is refused
        claims = [self.post(base, tool("board_claim", {"slug": "alpha"}), self.bearer("claude-vm"))[1] for _ in range(2)]
        self.assertEqual(claims[1]["result"]["structuredContent"]["code"], "rate_limited")

    def test_rates_come_from_the_named_limits_only(self):
        caller = mcp.Caller("claude-vm", "token:x", {"reads_per_min": 3, "bulk_per_hour": 0, "not_a_bucket": 9})
        self.assertEqual(mcp.Server.rate("read", caller), 3)
        self.assertEqual(mcp.Server.rate("bulk_hour", caller), 0)
        self.assertIsNone(mcp.Server.rate("board_write", caller))
        self.assertIsNone(mcp.Server.rate("read", "owner"))           # a login without the file: the defaults
        self.assertEqual(set(mcp.LIMIT_KEYS.values()), {"read", "board_write", "board_write_day", "garden_day",
                                                         "notes_write", "notes_write_day", "hister_write", "bulk_hour"})

    def test_apply_tokens_belong_to_the_principal(self):
        server, _ = self.serve()
        token = server.mint("relabel", mcp.Caller("claude-vm", "token:a"), {"x": 1})
        self.assertIsNone(server.redeem("relabel", mcp.Caller("partner", "proxy"), token))
        token = server.mint("relabel", mcp.Caller("claude-vm", "token:a"), {"x": 1})
        self.assertEqual(server.redeem("relabel", mcp.Caller("claude-vm", "tailscale"), token), {"x": 1})

    def test_the_rooms_get_the_principals_name_with_the_clients_label(self):
        _, base = self.serve()
        self.post(base, tool("board_review"), dict(self.bearer("claude-vm"), **{"X-Agent": "session-1"}))
        self.post(base, tool("board_review", mid=2), dict(self.bearer("claude-vm"), **{"X-Agent": "owner"}))
        self.post(base, tool("board_review", mid=3), {"Tailscale-User-Login": "partner@test"})   # (claude-vm: 2 a minute)
        agents = [r["headers"]["x-agent"] for r in self.fakes["konbini"].seen]
        self.assertEqual(agents, ["mcp:claude-vm/session-1", "mcp:claude-vm/owner", "mcp:partner"])

    def test_the_rooms_get_the_mcp_token_never_the_callers(self):
        token_file = os.path.join(self.folder, "mcp.token")
        with open(token_file, "w") as f:
            f.write(self.tokens["mcp"] + "\n")
        _, base = self.serve(MCP_TOKEN_FILE=token_file)
        self.post(base, tool("board_review"), dict(self.bearer("claude-vm"), **{"X-Agent": "session-1"}))
        req = self.fakes["konbini"].seen[-1]
        self.assertEqual(req["headers"]["authorization"], "Bearer " + self.tokens["mcp"])
        self.assertEqual(req["headers"]["x-agent"], "mcp:claude-vm/session-1")

    def test_header_mode_with_a_proxy(self):
        _, base = self.serve(MCP_AUTH="header", MCP_AUTH_HEADER="Remote-User")
        self.assertEqual(self.post(base, PING, {"Remote-User": "partner"})[0], 200)
        self.assertEqual(self.post(base, PING, {"Remote-User": "someone"})[0], 403)
        self.assertEqual(self.post(base, PING, {"Tailscale-User-Login": "owner@test"})[0], 401)

    def test_open_mode_is_the_owner_unless_a_token_says_otherwise(self):
        server, base = self.serve(MCP_AUTH="open", MCP_BIND="0.0.0.0")
        self.assertEqual(self.post(base, tool("board_review"))[0], 200)
        self.assertEqual(self.audit(server)[-1]["via"], "open")
        self.assertEqual(self.post(base, PING, self.bearer("reader"))[0], 403)


class KeepAlive(WithFile):
    """0.7.3: Tailscale Serve sends different people's requests down one kept-alive connection; each is answered
    alone, and a body the server didn't read never becomes the next request."""

    def one_connection(self, base, requests):
        import http.client
        conn = http.client.HTTPConnection(base.split("//")[1], timeout=10)
        out = []
        try:
            for headers in requests:
                conn.request("POST", "/mcp", body=json.dumps(tool("board_review")).encode(),
                             headers={"Content-Type": "application/json", **headers})
                r = conn.getresponse()
                out.append((r.status, json.loads(r.read() or b"null")))
        finally:
            conn.close()
        return out

    @staticmethod
    def raw(base, data):
        import socket
        host, port = base.split("//")[1].split(":")
        s = socket.create_connection((host, int(port)), timeout=2)
        s.sendall(data)
        out, closed = b"", False
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    closed = True
                    break
                out += chunk
        except socket.timeout:
            pass
        s.close()
        return out, closed

    def test_each_request_is_answered_alone(self):
        server, base = self.serve()
        got = self.one_connection(base, [
            {},                                                     # nobody: 401
            {"Tailscale-User-Login": "owner@test"},                 # the owner
            {},                                                     # nobody again
            {"Authorization": "Bearer mch_nope_secret"},            # a bad token
            self.bearer("claude-vm"),                               # an agent with the grant
            self.bearer("reader"),                                  # an agent without it: 403
            {"Tailscale-User-Login": "stranger@test"},              # a login with no access: 403
            {"Tailscale-User-Login": "partner@test"},               # another person
            {},
        ])
        self.assertEqual([g[0] for g in got], [401, 200, 401, 401, 200, 403, 403, 200, 401])
        self.assertEqual([r["principal"] for r in self.audit(server)], ["owner", "claude-vm", "partner"])

    def test_an_unread_body_never_becomes_a_request(self):
        server, base = self.serve()
        inner = json.dumps(tool("board_review")).encode()
        smuggled = (b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                    b"Tailscale-User-Login: owner@test\r\nContent-Length: %d\r\n\r\n" % len(inner)) + inner
        for head in (b"POST /mcp HTTP/1.1\r\nHost: x\r\n",                                   # nobody: 401
                     b"POST /mcp HTTP/1.1\r\nHost: x\r\nTailscale-User-Login: stranger@test\r\n",   # 403
                     b"POST /mcp HTTP/1.1\r\nHost: x\r\nOrigin: https://evil.example\r\n",   # a browser: 403
                     b"POST /nope HTTP/1.1\r\nHost: x\r\n",                                  # 404
                     b"GET /api/status HTTP/1.1\r\nHost: x\r\n"):                            # open, body unread
            out, closed = self.raw(base, head + b"Content-Length: %d\r\n\r\n" % len(smuggled) + smuggled)
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, head)
            self.assertTrue(closed, head)
            self.assertIn(b"\r\nConnection: close\r\n", out)
        self.assertEqual(self.fakes["konbini"].seen, [])
        self.assertFalse(os.path.exists(server.log_path) and self.audit(server))


class Startup(WithFile):
    def config(self, **env):
        return mcp.Config(dict({"MACHIYA_IDENTITY_FILE": self.file, "MCP_LOG": os.devnull}, **env))

    def test_header_mode_needs_the_identity_file(self):
        with self.assertRaises(SystemExit):
            mcp.Config({"MCP_AUTH": "header", "MCP_AUTH_HEADER": "Remote-User", "MCP_BIND": "127.0.0.1"})
        c = self.config(MCP_AUTH="header", MCP_AUTH_HEADER="Remote-User", MCP_BIND="127.0.0.1")
        self.assertEqual((c.auth, c.identity.auth, c.identity.header), ("header", "header", "Remote-User"))

    def test_a_header_mode_refuses_a_public_bind_unless_behind_the_proxy(self):
        for env in ({}, {"MCP_BIND": "0.0.0.0"}, {"MCP_AUTH": "header", "MCP_AUTH_HEADER": "Remote-User"}):
            with self.assertRaises(SystemExit, msg=env):
                self.config(**env)                      # MCP_BIND defaults to 0.0.0.0
        self.assertEqual(self.config(MCP_BIND="127.0.0.1").identity.room, "mcp")
        self.assertIsNotNone(self.config(MCP_BIND_BEHIND_PROXY="1").identity)
        self.assertIsNotNone(self.config(MCP_AUTH="open").identity)

    def test_a_bad_identity_file_refuses_to_start(self):
        with open(self.file, "a") as f:
            f.write("\n[principals.x]\nkind = \"robot\"\n")
        with self.assertRaises(SystemExit):
            self.config(MCP_BIND="127.0.0.1")
        with self.assertRaises(SystemExit):
            self.config(MCP_BIND="127.0.0.1", MACHIYA_IDENTITY_FILE=os.path.join(self.folder, "missing.toml"))

    def test_no_sign_in_here(self):
        c = self.config(MCP_BIND="127.0.0.1", MCP_SIGNIN="1")
        self.assertFalse(c.identity.signin)


class Outbound(unittest.TestCase):
    """MCP_TOKEN_FILE: the `mcp` principal's token goes to Kura, Konbini and Niwa as Authorization, never to Hister,
    never through a redirect, never into a log; X-Agent stays the label and no Origin or Referer reaches the rooms."""

    TOKEN = "mch_abcd_" + "s" * 43

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.file = os.path.join(self.folder, "mcp.token")
        with open(self.file, "w") as f:
            f.write(self.TOKEN + "\n")
        self.fakes = rooms()
        self.addCleanup(lambda: [f.close() for f in self.fakes.values()])

    def test_the_token_reaches_the_rooms_and_never_hister(self):
        server = make_server(self.fakes, MCP_TOKEN_FILE=self.file)
        self.addCleanup(os.unlink, server.log_path)
        for name, args in (("machiya_status", {}), ("machiya_search", {"q": "x"}), ("notes_read", {"path": "Projects/Alpha.md"}),
                           ("board_set_next", {"slug": "alpha", "next": "n"}), ("garden_suggest", {"note": "Projects/Alpha.md", "reason": "A finished write-up."}),
                           ("pages_labels", {}), ("pages_set_label", {"url": "https://example.com/a", "label": "python"})):
            self.assertFalse(call(server, name, args).get("isError"), name)
        for room in ("kura", "konbini", "niwa"):
            seen = self.fakes[room].seen
            self.assertTrue(seen, room)
            for req in seen:
                self.assertEqual(req["headers"].get("authorization"), "Bearer " + self.TOKEN, room)
                self.assertEqual(req["headers"]["x-agent"], "mcp:t")
                self.assertFalse({"origin", "referer"} & set(req["headers"]), room)
        self.assertTrue(self.fakes["niwa"].writes() and self.fakes["konbini"].writes())
        self.assertTrue(self.fakes["hister"].seen)
        for req in self.fakes["hister"].seen:
            self.assertNotIn("authorization", req["headers"])
            self.assertEqual(req["headers"]["origin"], HISTER_ORIGIN)
        with open(server.log_path) as f:
            self.assertNotIn(self.TOKEN[9:], f.read())
        self.assertNotIn(self.TOKEN[9:], repr(server.backends) + repr(vars(server.backends["kura"]).get("base")))

    def test_without_the_file_no_token_is_sent(self):
        server = make_server(self.fakes)
        self.addCleanup(os.unlink, server.log_path)
        call(server, "machiya_status")
        for f in self.fakes.values():
            self.assertTrue(all("authorization" not in r["headers"] for r in f.seen))

    def test_a_set_file_that_is_empty_or_missing_refuses_to_start(self):
        empty = os.path.join(self.folder, "empty")
        open(empty, "w").close()
        for path in (empty, os.path.join(self.folder, "missing"), self.folder):
            with self.assertRaises(SystemExit, msg=path) as cm:
                mcp.Config({"MCP_AUTH": "open", "MCP_TOKEN_FILE": path})
            self.assertNotIn(self.TOKEN, str(cm.exception))
        with open(empty, "w") as f:
            f.write("mch_abcd_with a space\n")
        with self.assertRaises(SystemExit):
            mcp.Config({"MCP_AUTH": "open", "MCP_TOKEN_FILE": empty})
        self.assertEqual(mcp.Config({"MCP_AUTH": "open", "MCP_TOKEN_FILE": ""}).token, "")

    def test_a_redirect_is_not_followed_with_the_token(self):
        elsewhere = Fake({"/api/status": {"ok": True}})
        self.addCleanup(elsewhere.close)

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", elsewhere.url + "/api/status")
                self.send_header("Content-Length", "0")
                self.end_headers()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close()))
        url = "http://127.0.0.1:%d" % httpd.server_address[1]
        with self.assertRaises(BackendError) as cm:
            Backend("kura", url, token=self.TOKEN).get("/api/status")
        self.assertEqual(cm.exception.status, 302)
        self.assertNotIn(self.TOKEN, cm.exception.message)
        self.assertEqual(elsewhere.seen, [])
        self.assertEqual(Backend("kura", url).get("/api/status"), {"ok": True})    # without a token, as before
        self.assertEqual(len(elsewhere.seen), 1)

    def test_hister_can_never_carry_the_token(self):
        with self.assertRaises(ValueError):
            Backend("hister", "http://127.0.0.1:1", origin=HISTER_ORIGIN, token=self.TOKEN)


class NoFile(unittest.TestCase):
    """Without MACHIYA_IDENTITY_FILE nothing changes: MCP_USERS logins, `login` in the audit log, no header mode."""

    def test_the_old_gate_and_log(self):
        c = mcp.Config({"MCP_AUTH": "tailscale", "MCP_USERS": "me@x"})
        self.assertIsNone(c.identity)
        fakes = rooms()
        self.addCleanup(lambda: [f.close() for f in fakes.values()])
        server = make_server(fakes)
        self.addCleanup(os.unlink, server.log_path)
        server.handle(tool("board_review"), "local", "t")
        with open(server.log_path) as f:
            row = json.loads(f.read().splitlines()[-1])
        self.assertEqual(row["login"], "local")
        self.assertNotIn("principal", row)
        self.assertEqual(set(server.limits["read"].buckets), {"local"})


if __name__ == "__main__":
    unittest.main()
