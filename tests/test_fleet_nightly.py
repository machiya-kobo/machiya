"""tools/fleet-nightly: the plan, the lock skip, the budget, the scrubbing of excerpts and the summary files, without
a VM. python3 -m unittest tests.test_fleet_nightly (standard library only)."""
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def load():
    loader = importlib.machinery.SourceFileLoader("fleet_nightly", os.path.join(ROOT, "tools", "fleet-nightly"))
    spec = importlib.util.spec_from_loader("fleet_nightly", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


fn = load()


class Config(unittest.TestCase):
    def test_file_merges_per_os_and_env_wins(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.json")
            with open(p, "w") as f:
                json.dump({"budget_minutes": 30, "vmctl": "from-file", "oses": {"freebsd": {"hister_bin": "/x/hister"}}}, f)
            cfg = fn.load_config(p, env={"FLEET_VMCTL": "from-env"})
        self.assertEqual(cfg["budget_minutes"], 30)
        self.assertEqual(cfg["vmctl"], "from-env")
        self.assertEqual(cfg["oses"]["freebsd"]["hister_bin"], "/x/hister")
        self.assertEqual(cfg["oses"]["freebsd"]["vm"], "tv-freebsd")          # the rest of the entry is kept
        self.assertNotIn("hister_bin", fn.DEFAULTS["oses"]["freebsd"])        # the defaults are not touched

    def test_remote_dir_must_be_plain(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ("/", "/var", "relative/dir", "/var/tmp/x y", "/var/tmp/../etc", "/var/tmp/$(id)"):
                p = os.path.join(d, "c.json")
                with open(p, "w") as f:
                    json.dump({"remote_dir": bad}, f)
                with self.assertRaises(SystemExit):
                    fn.load_config(p, env={})

    def test_plan_filters_and_hister(self):
        cfg = fn.load_config("/nonexistent", env={})
        entries = fn.plan(cfg, ["debian", "haiku"], ["dev-native", "dev-docker"])
        self.assertEqual([e["os"] for e in entries], ["debian", "haiku"])
        self.assertEqual(entries[0]["legs"], ["dev-docker", "dev-native"])
        self.assertEqual(fn.hister_plan(entries[0], cfg)[0], "none")           # the Hister host can't borrow from itself
        self.assertEqual(fn.hister_plan(entries[1], cfg), ("via", "debian"))
        where, argv = fn.leg_spec("dev-native", entries[1], cfg, "/s", "tv-debian")
        self.assertEqual(where, "local")
        self.assertEqual(argv[-3:], ["--native", "--hister-via", "tv-debian"])
        where, script = fn.leg_spec("niwa-native", dict(cfg["oses"]["freebsd"], os="freebsd"), cfg, "/s")
        self.assertEqual((where, script), ("vm", "cd /var/tmp/machiya-nightly/niwa && python3 tools/quickstart-test "
                                                 "--only native --with-packages"))

    def test_every_default_leg_and_bootstrap_exists(self):
        for o, e in fn.DEFAULTS["oses"].items():
            for leg in e["legs"]:
                self.assertIn(leg, fn.LEGS)
                if fn.LEGS[leg]["where"] == "vm":
                    self.assertIn(e["system"], fn.BOOTSTRAP, "%s %s" % (o, leg))


class Output(unittest.TestCase):
    def test_scrub(self):
        line = ("token=abcdef123456 password: hunter22hunter Location https://localhost/cb?code=mhc_DV6HijHYBe "
                "Authorization Bearer 0123456789abcdef0123456789abcdef https://u:pw@host/x")
        s = fn.scrub(line)
        for secret in ("abcdef123456", "hunter22hunter", "DV6HijHYBe", "0123456789abcdef0123456789abcdef", "u:pw@"):
            self.assertNotIn(secret, s)
        self.assertEqual(fn.scrub("/var/tmp/machiya-dev-test/machiya/compose/dev/dev down"),
                         "/var/tmp/machiya-dev-test/machiya/compose/dev/dev down")

    def test_summarize_and_classify(self):
        dev = "PASS  a\nFAIL  landing: x\n\n23 check(s), 1 failed\ndev-test: FAIL (exit 1) on tv-debian\n"
        self.assertEqual(fn.summarize("dev-docker", dev, 1), "23 checks, 1 failed")
        qs = "FAILED:\n  native/check: expected '<title>konbini' in the output\n"
        self.assertEqual(fn.summarize("konbini-native", qs, 1), "native/check: expected '<title>konbini' in the output")
        self.assertEqual(fn.summarize("niwa-native", "== scenario native: PASS\n\nquickstart test: all passed\n", 0), "all passed")
        self.assertEqual(fn.classify("scenario native: SKIPPED (no native blocks for Haiku)\nnothing to run on this machine\n", 0, False), "skipped")
        self.assertEqual(fn.classify("", 0, False), "pass")
        self.assertEqual(fn.classify("", 1, False), "fail")
        self.assertEqual(fn.classify("", -1, True), "timeout")
        self.assertEqual(fn.worst(["pass", "timeout", "error"]), "fail")
        self.assertEqual(fn.worst(["pass", "skipped"]), "pass")
        self.assertEqual(fn.worst(["skipped"]), "skipped")

    def test_excerpt_is_short(self):
        text = "\n".join("line %d" % i for i in range(100)) + "\nTraceback: boom\n" + "x" * 500
        ex = fn.excerpt(text)
        self.assertLessEqual(len(ex), 10)
        self.assertIn("Traceback: boom", ex)
        self.assertTrue(all(len(l) <= 200 for l in ex))


class Run(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="fleet-nightly-test-", dir="/var/tmp" if os.path.isdir("/var/tmp") else None)
        self.cfg = fn.load_config("/nonexistent", env={})
        self.cfg.update(lock_dir=self.d, vmctl="false")
        self.run_dir = os.path.join(self.d, "out", "runs", "20261005T073000Z")
        os.makedirs(self.run_dir)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_held_lock_skips_the_os(self):
        held = open(os.path.join(self.d, "testvm-freebsd.lock"), "a")
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            entries = fn.plan(self.cfg, ["freebsd"])
            r = fn.Runner(self.cfg, entries, self.run_dir, os.path.join(self.run_dir, "src"), 3600, log=lambda *a: None)
            rec = r.one_os(0, entries[0])
        finally:
            held.close()
        self.assertEqual(rec["status"], "skipped")
        self.assertIn("another run holds", rec["reason"])
        self.assertEqual({l["status"] for l in rec["legs"]}, {"skipped"})
        self.assertEqual(r.records, [rec])

    def test_a_spent_budget_skips_and_the_files_are_written(self):
        entries = fn.plan(self.cfg, ["haiku"])
        r = fn.Runner(self.cfg, entries, self.run_dir, os.path.join(self.run_dir, "src"), 0, log=lambda *a: None)
        r.one_os(0, entries[0])
        result = {"schema": 1, "kind": fn.KIND, "started": "2026-10-05T07:30:00Z", "duration_s": 5, "oses": r.records,
                  "sources": {"kura": {"commit": "da57851", "describe": "v0.8.0"}}}
        fn.finish(result, os.path.join(self.d, "out"), self.run_dir, self.cfg, publish=False)
        with open(os.path.join(self.d, "out", "nightly.json")) as f:
            got = json.load(f)
        self.assertEqual(got["status"], "skipped")
        self.assertEqual(got["counts"], {"pass": 0, "fail": 0, "error": 0, "skipped": 1})
        self.assertIn("budget", got["oses"][0]["legs"][0]["summary"])
        with open(os.path.join(self.run_dir, "nightly.txt")) as f:
            text = f.read()
        self.assertIn("kura da57851 (v0.8.0)", text)
        self.assertIn("haiku", text)


if __name__ == "__main__":
    unittest.main()
