"""The machiya plugin's scripts (plugins/machiya): bin/hister-headers never hands a production token to anything but a
production address (sweep MACH-M-1). Run as a real process with a fake `hpass` on PATH and a throwaway HOME."""
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

    def test_pass_only_for_a_production_https_address(self):
        self.assertEqual(self.headers(HISTER_MCP_URL="https://hister.example.ts.net/mcp"), {"X-Access-Token": PROD})
        for url in ("http://127.0.0.1:19224/mcp", "https://127.0.0.1/mcp", "https://localhost/mcp",
                    "https://claude.example.ts.net:19204/mcp", "https://claude.example.ts.net:19224/mcp",
                    "http://hister.example.ts.net/mcp", "https://user:pw@hister.example.ts.net/mcp", None):
            self.assertEqual(self.headers(HISTER_MCP_URL=url), {}, url)

    def test_the_address_claude_code_connects_to_wins(self):
        self.assertEqual(self.headers(HISTER_MCP_URL="https://hister.example.ts.net/mcp",
                                      CLAUDE_CODE_MCP_SERVER_URL="http://127.0.0.1:19224/mcp"), {})
        self.assertEqual(self.headers(CLAUDE_CODE_MCP_SERVER_URL="https://hister.example.ts.net/mcp"), {"X-Access-Token": PROD})

    def test_not_a_token_sends_nothing(self):
        with open(self.token_file, "w") as f:
            f.write('bad"token\n')
        self.assertEqual(self.headers(HISTER_TOKEN_FILE=self.token_file, HISTER_MCP_URL="https://h.example/mcp"), {})


if __name__ == "__main__":
    unittest.main()
