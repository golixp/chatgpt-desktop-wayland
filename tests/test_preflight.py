"""Exercise preflight stage ordering and failure reporting without a download.

The shell, Node discovery and Node test runner are real. Only the signed-package
verifier and archive/patch inputs are tiny local stubs; signature cryptography
itself remains covered by the builder's upstream verifier tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PREFLIGHT = Path(__file__).resolve().parents[1] / "builder-preflight.sh"
NODE = shutil.which("node")


@unittest.skipUnless(NODE and shutil.which("bash"), "preflight requires Node and Bash")
class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="preflight-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.builder = self.root / "builder"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self.reports = self.root / "reports"
        self.deb = self.root / "fixture.deb"
        self.deb.write_text("local test input; no network")
        self.trace = self.root / "trace"
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "CODEX_ASAR_BIN": str(self.bin / "asar"),
            "TEST_PREFLIGHT_TRACE": str(self.trace),
            "TEST_PREFLIGHT_FAIL_PHASE": "",
        })
        self.write_builder("scripts/lib/upstream-linux-package.js", """
const fs = require('node:fs');
fs.appendFileSync(process.env.TEST_PREFLIGHT_TRACE, 'verify\\n');
if (process.env.TEST_PREFLIGHT_FAIL_PHASE === 'verify') process.exit(73);
""")
        self.write_builder("scripts/patches/lib/assets.js", """
const fs = require('node:fs');
exports.findMainProcessChunks = (app) => {
  fs.appendFileSync(process.env.TEST_PREFLIGHT_TRACE, 'discover\\n');
  if (process.env.TEST_PREFLIGHT_FAIL_PHASE === 'discover') return null;
  return { buildDir: app, chunks: ['main.js'] };
};
""")
        self.write_builder("linux-features/computer-use-linux/current-signed.test.js", """
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('pristine contract', () => {
  fs.appendFileSync(process.env.TEST_PREFLIGHT_TRACE, 'pristine_contracts\\n');
  assert(!fs.existsSync(path.join(process.env.CODEX_SIGNED_EXTRACTED_APP, 'patched')));
  assert.notEqual(process.env.TEST_PREFLIGHT_FAIL_PHASE, 'pristine_contracts');
});
""")
        self.write_builder("scripts/patch-linux-window-ui.js", """
const fs = require('node:fs');
const path = require('node:path');
const args = process.argv.slice(2);
if (!args.includes('--enforce-critical')) throw Error('Critical enforcement is required');
fs.appendFileSync(process.env.TEST_PREFLIGHT_TRACE, 'patch\\n');
const failed = process.env.TEST_PREFLIGHT_FAIL_PHASE === 'patch';
fs.writeFileSync(args[args.indexOf('--report-json') + 1], JSON.stringify({ rejected: failed }));
if (failed) process.exit(74);
fs.writeFileSync(path.join(args.at(-1), 'patched'), 'patched fixture');
""")
        self.write_builder("linux-features/computer-use-linux/resume.test.js", """
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('patched contract', () => {
  fs.appendFileSync(process.env.TEST_PREFLIGHT_TRACE, 'patched_contracts\\n');
  assert.equal(process.env.CODEX_TEST_ALREADY_PATCHED, '1');
  assert(fs.existsSync(path.join(process.env.CODEX_SIGNED_EXTRACTED_APP, 'patched')));
  assert.notEqual(process.env.TEST_PREFLIGHT_FAIL_PHASE, 'patched_contracts');
});
""")
        self.write_builder("linux-features/disable-pets/test.js", """
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('pet contract runs before and after patching', () => {
  assert.equal(fs.existsSync(path.join(process.env.CODEX_SIGNED_EXTRACTED_APP, 'patched')),
    process.env.CODEX_TEST_ALREADY_PATCHED === '1');
});
""")
        self.command("dpkg-deb", """
import os
from pathlib import Path
with Path(os.environ['TEST_PREFLIGHT_TRACE']).open('a') as trace:
    trace.write('extract\\n')
""")
        self.command("bsdtar", """
import os, sys
from pathlib import Path
if os.environ['TEST_PREFLIGHT_FAIL_PHASE'] == 'extract': sys.exit(75)
resources = Path(sys.argv[sys.argv.index('-C') + 1]) / 'usr/lib/chatgpt/resources'
resources.mkdir(parents=True)
(resources / 'app.asar').write_bytes(b'fixture')
(resources / 'app.asar.unpacked').mkdir()
(resources / 'icon-chatgpt.png').write_bytes(b'fixture-icon')
""")
        self.command("asar", """
import os, sys
from pathlib import Path
app = Path(sys.argv[-1])
app.mkdir()
(app / 'main.js').write_text('module.exports = {};')
if os.environ['TEST_PREFLIGHT_FAIL_PHASE'] != 'manifest':
    (app / 'package.json').write_text('{"main":"main.js"}')
""")

    def write_builder(self, relative: str, source: str) -> None:
        file = self.builder / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(source)

    def command(self, name: str, source: str) -> None:
        file = self.bin / name
        file.write_text("#!/usr/bin/env python3\n" + source)
        file.chmod(0o755)

    def run_preflight(self, fail_phase: str = "") -> subprocess.CompletedProcess[str]:
        env = {**self.env, "TEST_PREFLIGHT_FAIL_PHASE": fail_phase}
        return subprocess.run(
            ["bash", str(PREFLIGHT), str(self.builder), str(self.deb), str(self.work),
             str(self.reports), str(self.root / "icon.png")],
            env=env, capture_output=True, text=True, timeout=30,
        )

    def status(self) -> dict:
        return json.loads((self.reports / "preflight-status.json").read_text())

    def test_success_requires_all_stages_and_separates_pristine_from_patched(self) -> None:
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.trace.read_text().splitlines(), [
            "verify", "extract", "discover", "pristine_contracts", "patch", "patched_contracts",
        ])
        self.assertEqual(self.status(), {"schema": 1, "phase": "complete", "exit_code": 0, "result": "passed"})
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual((self.root / "icon.png").read_bytes(), b"fixture-icon")

    def test_verification_failure_stops_before_extraction_and_records_original_code(self) -> None:
        result = self.run_preflight("verify")
        self.assertEqual(result.returncode, 73)
        self.assertEqual(self.trace.read_text().splitlines(), ["verify"])
        self.assertEqual(self.status()["phase"], "verify")
        self.assertEqual(self.status()["result"], "failed")
        self.assertEqual(self.status()["exit_code"], 73)
        self.assertFalse((self.reports / "preflight-report.json").exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_production_input_cannot_use_manifestless_fixture_fallback(self) -> None:
        result = self.run_preflight("manifest")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.trace.read_text().splitlines(), ["verify", "extract"])
        self.assertEqual(self.status()["phase"], "discover")
        self.assertEqual(self.status()["result"], "failed")
        self.assertIn("regular package.json", result.stderr)

    def test_patch_rejection_preserves_report_and_never_runs_post_patch_tests(self) -> None:
        result = self.run_preflight("patch")
        self.assertEqual(result.returncode, 74)
        self.assertEqual(self.trace.read_text().splitlines()[-1], "patch")
        self.assertEqual(self.status()["phase"], "patch")
        self.assertEqual(self.status()["result"], "failed")
        self.assertTrue(json.loads((self.reports / "preflight-report.json").read_text())["rejected"])
        self.assertEqual(list(self.work.iterdir()), [])

    def test_other_stage_failures_are_never_reported_as_passed(self) -> None:
        for phase in ("extract", "discover", "pristine_contracts", "patched_contracts"):
            with self.subTest(phase=phase):
                result = self.run_preflight(phase)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.status()["phase"], phase)
                self.assertEqual(self.status()["result"], "failed")
                self.assertEqual(self.status()["exit_code"], result.returncode)
                self.assertEqual(list(self.work.iterdir()), [])

    def test_status_writer_failure_does_not_replace_verification_failure(self) -> None:
        self.command("python", "raise SystemExit(91)\n")
        result = self.run_preflight("verify")
        self.assertEqual(result.returncode, 73)
        self.assertIn("Could not write preflight status", result.stderr)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_cleanup_failure_does_not_replace_patch_failure(self) -> None:
        self.command("rm", "raise SystemExit(92)\n")
        result = self.run_preflight("patch")
        self.assertEqual(result.returncode, 74)
        self.assertEqual(self.status()["phase"], "patch")
        self.assertEqual(self.status()["exit_code"], 74)
        self.assertEqual(self.status()["result"], "failed")
        self.assertIn("Could not clean preflight scratch", result.stderr)

    def test_cleanup_failure_after_success_is_reported_as_failure(self) -> None:
        self.command("rm", "raise SystemExit(92)\n")
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()["phase"], "cleanup")
        self.assertEqual(self.status()["result"], "failed")

    def test_status_writer_failure_after_success_returns_failure(self) -> None:
        self.command("python", "raise SystemExit(91)\n")
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.reports / "preflight-status.json").exists())


if __name__ == "__main__":
    unittest.main()
