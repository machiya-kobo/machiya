"""The machiya plugin's scripts (plugins/machiya): bin/hister-headers never hands a production token to anything but a
production address (sweep MACH-M-1), and install.sh denies Hister's own MCP to AI clients (plugin 0.3.0). Both run as
real processes with a fake `hpass` and a fake `claude` on PATH and a throwaway HOME."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "..", "..", "plugins", "machiya")
HEADERS = os.path.join(PLUGIN, "bin", "hister-headers")
MACHIYA_HEADERS = os.path.join(PLUGIN, "bin", "machiya-headers")
ROOM_TOKEN = "mht_" + "R" * 43
INSTALL = os.path.join(PLUGIN, "install.sh")
PROD, DEV = "ProductionOwnerToken123", "DevStackToken456"


class Scratch(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="machiya-plugin-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = os.path.join(self.dir, "bin")
        os.mkdir(self.bin)
        self.fake("hpass", "#!/bin/sh\necho %s\n" % PROD)
        self.fake("claude", "#!/bin/sh\nexit 1\n")         # nothing installed, no older server to remove
        self.token_file = os.path.join(self.dir, "owner-token")
        with open(self.token_file, "w") as f:
            f.write(DEV + "\n")

    def fake(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)

    def env(self, **kw):
        env = {"PATH": self.bin + os.pathsep + os.path.dirname(sys.executable) + os.pathsep + "/usr/bin:/bin",
               "HOME": self.dir}
        env.update({k: v for k, v in kw.items() if v is not None})
        return env


class HisterHeaders(Scratch):
    def headers(self, **kw):
        r = subprocess.run([sys.executable, HEADERS], capture_output=True, text=True, env=self.env(**kw), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return json.loads(r.stdout)

    def test_a_missing_token_file_never_falls_back_to_pass(self):
        # the sweep's scenario: a dev reset removes the token file, and the owner's production token went to the dev Hister
        gone = os.path.join(self.dir, "no-such-file")
        for url in ("http://127.0.0.1:19224/mcp", "https://hister.example.ts.net/mcp"):
            self.assertEqual(self.headers(HISTER_TOKEN_FILE=gone, HISTER_MCP_URL=url), {}, url)
        os.chmod(self.token_file, 0)
        if os.geteuid() != 0:
            self.assertEqual(self.headers(HISTER_TOKEN_FILE=self.token_file, HISTER_MCP_URL="https://h.example/mcp"), {})

    def test_the_token_file_goes_to_https_or_loopback_http_only(self):
        for url in ("http://127.0.0.1:19224/mcp", "http://localhost:19224/mcp", "http://[::1]:19224/mcp",
                    "https://hister.example.ts.net/mcp"):
            self.assertEqual(self.headers(HISTER_TOKEN_FILE=self.token_file, HISTER_MCP_URL=url), {"X-Access-Token": DEV}, url)
        for url in ("http://hister.example.ts.net/mcp", "http://10.0.0.5:4433/mcp", "ftp://127.0.0.1/", "", "not a url"):
            self.assertEqual(self.headers(HISTER_TOKEN_FILE=self.token_file, HISTER_MCP_URL=url), {}, url)

    # plugin 0.4.1: pass is read only for the entry HISTER_TOKEN_PASS names (the per-host copies are retired)
    ENTRY = "hosts/example/hister-token"

    def test_pass_only_for_a_production_https_address(self):
        self.assertEqual(self.headers(HISTER_TOKEN_PASS=self.ENTRY, HISTER_MCP_URL="https://hister.example.ts.net/mcp"),
                         {"X-Access-Token": PROD})
        for url in ("http://127.0.0.1:19224/mcp", "https://127.0.0.1/mcp", "https://localhost/mcp",
                    "https://claude.example.ts.net:19204/mcp", "https://claude.example.ts.net:19224/mcp",
                    "http://hister.example.ts.net/mcp", "https://user:pw@hister.example.ts.net/mcp", None):
            self.assertEqual(self.headers(HISTER_TOKEN_PASS=self.ENTRY, HISTER_MCP_URL=url), {}, url)

    def test_no_named_entry_no_pass(self):
        # plugin 0.4.1: without HISTER_TOKEN_PASS nothing is read from pass, even for a production address
        self.assertEqual(self.headers(HISTER_MCP_URL="https://hister.example.ts.net/mcp"), {})

    def test_the_address_claude_code_connects_to_wins(self):
        self.assertEqual(self.headers(HISTER_TOKEN_PASS=self.ENTRY, HISTER_MCP_URL="https://hister.example.ts.net/mcp",
                                      CLAUDE_CODE_MCP_SERVER_URL="http://127.0.0.1:19224/mcp"), {})
        self.assertEqual(self.headers(HISTER_TOKEN_PASS=self.ENTRY, CLAUDE_CODE_MCP_SERVER_URL="https://hister.example.ts.net/mcp"),
                         {"X-Access-Token": PROD})

    def test_not_a_token_sends_nothing(self):
        with open(self.token_file, "w") as f:
            f.write('bad"token\n')
        self.assertEqual(self.headers(HISTER_TOKEN_FILE=self.token_file, HISTER_MCP_URL="https://h.example/mcp"), {})


class MachiyaHeaders(Scratch):
    """plugin 0.4.0: machiya-mcp gets a room token from MACHIYA_TOKEN_FILE only, never Hister's token, never pass
    (bin/machiya-headers for a server added by hand; the plugin's own server through its settings)."""

    def headers(self, **kw):
        r = subprocess.run([sys.executable, MACHIYA_HEADERS], capture_output=True, text=True, env=self.env(**kw),
                           timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)

    def room_file(self, value=ROOM_TOKEN):
        path = os.path.join(self.dir, "room-token")
        with open(path, "w") as f:
            f.write(value + "\n")
        return path

    def test_a_room_token_to_https_or_loopback(self):
        f = self.room_file()
        want = {"Authorization": "Bearer " + ROOM_TOKEN}
        self.assertEqual(self.headers(MACHIYA_TOKEN_FILE=f, MACHIYA_MCP_URL="https://mcp.example.ts.net/mcp"), want)
        self.assertEqual(self.headers(MACHIYA_TOKEN_FILE=f, MACHIYA_MCP_URL="http://127.0.0.1:19226/mcp"), want)
        self.assertEqual(self.headers(MACHIYA_TOKEN_FILE=f, MACHIYA_MCP_URL="http://mcp.example.ts.net/mcp"), {})
        self.assertEqual(self.headers(MACHIYA_TOKEN_FILE=f, MACHIYA_MCP_URL="https://x/mcp",
                                      CLAUDE_CODE_MCP_SERVER_URL="http://evil.example/mcp"), {})

    def test_never_hister_never_pass(self):
        self.assertEqual(self.headers(MACHIYA_MCP_URL="https://mcp.example.ts.net/mcp"), {})          # no file: none
        self.assertEqual(self.headers(MACHIYA_TOKEN_FILE=self.token_file,                           # a Hister token
                                      MACHIYA_MCP_URL="https://mcp.example.ts.net/mcp"), {})
        self.assertEqual(self.headers(MACHIYA_TOKEN_FILE=os.path.join(self.dir, "gone"),
                                      MACHIYA_MCP_URL="https://mcp.example.ts.net/mcp"), {})

    def test_the_plugin_sends_it_as_a_fixed_header_from_the_settings(self):
        """Claude Code 2.1 runs no headersHelper for a plugin's own server: the plugin sends X-Machiya-Token from its
        settings env, which install.sh fills from MACHIYA_TOKEN_FILE (a room token only, https or loopback only)."""
        with open(os.path.join(PLUGIN, ".mcp.json")) as f:
            server = json.load(f)["mcpServers"]["machiya"]
        self.assertEqual(server["headers"]["X-Machiya-Token"], "${MACHIYA_MCP_TOKEN:-}")
        self.assertNotIn("headersHelper", server)
        os.makedirs(os.path.join(self.dir, ".claude"))
        settings = os.path.join(self.dir, ".claude", "settings.json")
        with open(settings, "w") as fh:
            fh.write("{}")
        run = lambda **kw: subprocess.run(["bash", INSTALL, "hister-remove"], capture_output=True, text=True,  # noqa
                                          timeout=60, env=self.env(**kw))
        r = run(MACHIYA_TOKEN_FILE=self.room_file(), MACHIYA_MCP_URL="https://mcp.example.ts.net/mcp")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(settings) as fh:
            env = json.load(fh)["env"]
        self.assertEqual(env["MACHIYA_MCP_TOKEN"], ROOM_TOKEN)
        self.assertNotIn("MACHIYA_TOKEN_FILE", env)
        # Hister's token, or a plain-http address that isn't loopback: refused, nothing saved
        for kw in ({"MACHIYA_TOKEN_FILE": self.token_file, "MACHIYA_MCP_URL": "https://mcp.example.ts.net/mcp"},
                   {"MACHIYA_TOKEN_FILE": self.room_file(), "MACHIYA_MCP_URL": "http://mcp.example.ts.net/mcp"}):
            with open(settings, "w") as fh:
                fh.write("{}")
            self.assertNotEqual(run(**kw).returncode, 0, kw)
            with open(settings) as fh:
                self.assertNotIn("MACHIYA_MCP_TOKEN", json.load(fh).get("env", {}))
        r = run(MACHIYA_TOKEN_FILE=self.room_file(), MACHIYA_MCP_URL="http://127.0.0.1:19226/mcp")
        self.assertEqual(r.returncode, 0, r.stderr)
        subprocess.run(["bash", INSTALL, "uninstall"], capture_output=True, text=True, timeout=60, env=self.env())
        with open(settings) as fh:
            self.assertNotIn("MACHIYA_MCP_TOKEN", json.load(fh)["env"])


class Install(Scratch):
    def settings(self):
        with open(os.path.join(self.dir, ".claude", "settings.json")) as f:
            return json.load(f)

    def test_hister_mcp_is_denied_and_pages_come_from_machiya_mcp(self):
        os.makedirs(os.path.join(self.dir, ".claude"))
        H = "mcp__plugin_machiya_hister__"
        old = {"permissions": {"allow": [H + "search", H + "get_preview", "Bash(ls)"], "deny": [H + "get_history"]},
               "env": {"HISTER_MCP_URL": "http://127.0.0.1:19224/mcp"}}
        with open(os.path.join(self.dir, ".claude", "settings.json"), "w") as f:
            json.dump(old, f)
        r = subprocess.run(["bash", INSTALL, "hister-remove"], capture_output=True, text=True, timeout=60,
                           env=self.env(MACHIYA_AGENT_NAME="t@test"))
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.settings()["permissions"]
        self.assertNotIn(H + "search", p["allow"])
        self.assertNotIn(H + "get_preview", p["allow"])
        self.assertIn("Bash(ls)", p["allow"])
        for name in ("mcp__plugin_machiya_machiya__pages_search", "mcp__plugin_machiya_machiya__pages_read"):
            self.assertIn(name, p["allow"])
        for name in (H + "*", "mcp__hister__*", H + "search", H + "get_preview", H + "get_history", "mcp__hister__get_history"):
            self.assertIn(name, p["deny"])
        self.assertEqual(len(p["deny"]), len(set(p["deny"])))
        r = subprocess.run(["bash", INSTALL, "uninstall"], capture_output=True, text=True, timeout=60, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.settings()
        self.assertIn(H + "*", d["permissions"]["deny"])           # the denies stay
        self.assertNotIn("HISTER_MCP_URL", d["env"])

    def test_the_plugin_connects_no_hister_mcp(self):
        with open(os.path.join(PLUGIN, ".mcp.json")) as f:
            servers = json.load(f)["mcpServers"]
        self.assertEqual(list(servers), ["machiya"])


if __name__ == "__main__":
    unittest.main()
