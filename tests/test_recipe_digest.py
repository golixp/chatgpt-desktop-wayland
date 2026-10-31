"""Validation markers bind the published recipe to the checks that approved it."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "recipe_digest.py"
sys.path.insert(0, str(SCRIPT.parent))
import recipe_digest as recipe
import track_upstream

ROOT = SCRIPT.parents[1]
TRACK_SCRIPT = SCRIPT.with_name("track_upstream.py")


class RecipeDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="recipe-digest-test-")
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name)
        self.source = self.scratch / "source"
        for relative in recipe.FILES:
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture for {relative}\n")
        (self.source / "PKGBUILD").write_text(
            "pkgname=chatgpt-desktop-wayland\npkgver=26.1027.30824\npkgrel=1\n"
            "_builder_commit='0123456789012345678901234567890123456789'\n"
        )

    def test_candidate_roundtrip_retains_policy_and_recipe(self) -> None:
        candidate = self.scratch / "candidate"
        recipe.copy_inputs(self.source, candidate, "candidate")
        self.assertEqual(recipe.calculate(self.source), recipe.calculate(candidate))
        self.assertTrue((candidate / ".github/workflows/track-upstream.yml").is_file())

    def test_aur_copy_omits_ci_files_markers_and_unlisted_inputs(self) -> None:
        (self.source / "unlisted-secret").write_text("not a recipe input")
        (self.source / ".ci").mkdir()
        (self.source / ".ci/validated.json").write_text("{}")
        aur = self.scratch / "aur"
        recipe.copy_inputs(self.source, aur, "recipe")
        actual = {str(file.relative_to(aur)) for file in aur.rglob("*") if file.is_file()}
        self.assertEqual(actual, set(recipe.RECIPE_FILES))
        self.assertFalse((aur / ".github").exists())
        self.assertFalse((aur / ".ci").exists())

    def test_workflow_verifier_and_recipe_changes_invalidate_marker(self) -> None:
        original = recipe.calculate(self.source)
        for relative in (
            "PKGBUILD", "builder-main-process.cjs",
            ".github/workflows/track-upstream.yml", "scripts/recipe_digest.py",
            "scripts/fetch_upstream_metadata.py",
        ):
            with self.subTest(relative=relative):
                file = self.source / relative
                data = file.read_bytes()
                file.write_bytes(data + b"\n# changed\n")
                self.assertNotEqual(original, recipe.calculate(self.source))
                file.write_bytes(data)

    def test_cli_verifies_explicit_candidate_not_working_directory(self) -> None:
        candidate = self.scratch / "candidate"
        recipe.copy_inputs(self.source, candidate, "candidate")
        marker = self.scratch / "validated.json"
        marker.write_text(json.dumps(recipe.calculate(candidate)))
        (self.source / "builder-main-process.cjs").write_text("different checkout")
        passed = subprocess.run(
            [sys.executable, str(SCRIPT), str(candidate), "--verify", str(marker)],
            cwd=self.source, capture_output=True, text=True,
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        (candidate / "builder-main-process.cjs").write_text("tampered candidate")
        failed = subprocess.run(
            [sys.executable, str(SCRIPT), str(candidate), "--verify", str(marker)],
            cwd=self.source, capture_output=True, text=True,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("does not match", failed.stderr)

    def test_missing_input_rejects_copy_before_writing_any_files(self) -> None:
        (self.source / recipe.FILES[-1]).unlink()
        destination = self.scratch / "destination"
        with self.assertRaisesRegex(SystemExit, "missing"):
            recipe.copy_inputs(self.source, destination, "candidate")
        self.assertFalse(destination.exists())

    def test_source_leaf_symlink_is_rejected(self) -> None:
        file = self.source / "builder-main-process.cjs"
        outside = self.scratch / "outside"
        outside.write_text("outside")
        file.unlink()
        file.symlink_to(outside)
        with self.assertRaisesRegex(SystemExit, "symlink"):
            recipe.calculate(self.source)

    def test_source_directory_symlink_is_rejected(self) -> None:
        scripts = self.source / "scripts"
        outside = self.scratch / "scripts"
        scripts.rename(outside)
        scripts.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(SystemExit, "symlink"):
            recipe.calculate(self.source)

    def test_destination_symlink_does_not_overwrite_target(self) -> None:
        destination = self.scratch / "destination"
        destination.mkdir()
        outside = self.scratch / "outside"
        outside.write_text("leave intact")
        (destination / "PKGBUILD").symlink_to(outside)
        with self.assertRaisesRegex(SystemExit, "symlink"):
            recipe.copy_inputs(self.source, destination, "recipe")
        self.assertEqual(outside.read_text(), "leave intact")

    def test_destination_parent_symlink_is_rejected(self) -> None:
        outside = self.scratch / "outside"
        outside.mkdir()
        link = self.scratch / "link"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(SystemExit, "symlink"):
            recipe.copy_inputs(self.source, link / "candidate", "candidate")
        self.assertFalse((outside / "candidate").exists())

    def test_retired_files_are_not_candidate_inputs(self) -> None:
        self.assertTrue(set(recipe.RETIRED_RECIPE_FILES).isdisjoint(recipe.FILES))
        self.assertEqual(len(recipe.FILES), len(set(recipe.FILES)))


class TrackUpstreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="track-upstream-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Use the actual current recipe, adapted back to the previously
        # published release, then run the real tracker CLI forward again.
        for relative in recipe.RECIPE_FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.metadata = json.loads((ROOT / "upstream.json").read_text())
        self.version = self.metadata["version"]
        self.assertNotEqual(self.version, "26.1021.22027")
        pkgbuild = (self.root / "PKGBUILD").read_text()
        self.builder = track_upstream.current_value(pkgbuild, "_builder_commit")
        self.original_rel = track_upstream.current_value(pkgbuild, "pkgrel")
        # The versioned download and noextract fields are materialized in
        # .SRCINFO, while PKGBUILD's noextract remains a pkgver expansion.
        for name in ("PKGBUILD", ".SRCINFO"):
            file = self.root / name
            source = file.read_text().replace(self.version, "26.1021.22027")
            if name == "PKGBUILD":
                source = source.replace(f"pkgrel={self.original_rel}\n", "pkgrel=2\n")
            else:
                source = source.replace(f"\tpkgrel = {self.original_rel}\n", "\tpkgrel = 2\n")
            file.write_text(source)
        (self.root / "metadata.json").write_text(json.dumps(self.metadata))

    def track(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TRACK_SCRIPT), str(self.root / "metadata.json"),
             str(self.root / "PKGBUILD"), str(self.root / ".SRCINFO"), self.builder],
            cwd=self.root, capture_output=True, text=True,
        )

    def test_release_upgrade_updates_source_noextract_and_resets_pkgrel(self) -> None:
        result = self.track()
        self.assertEqual(result.returncode, 0, result.stderr)
        pkgbuild = (self.root / "PKGBUILD").read_text()
        srcinfo = (self.root / ".SRCINFO").read_text()
        self.assertEqual(track_upstream.current_value(pkgbuild, "pkgver"), self.version)
        self.assertEqual(track_upstream.current_value(pkgbuild, "pkgrel"), "1")
        self.assertIn(f"\tnoextract = chatgpt_{self.version}_amd64.deb\n", srcinfo)
        self.assertIn(f"\tsource = chatgpt_{self.version}_amd64.deb::", srcinfo)
        self.assertNotIn("26.1021.22027", srcinfo)
        expected = (ROOT / ".SRCINFO").read_text().replace(
            f"\tpkgrel = {self.original_rel}\n", "\tpkgrel = 1\n",
        )
        self.assertEqual(srcinfo, expected)
        before = (pkgbuild, srcinfo)
        repeated = self.track()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("changed=false", repeated.stdout)
        self.assertEqual(before, ((self.root / "PKGBUILD").read_text(), (self.root / ".SRCINFO").read_text()))

    @unittest.skipUnless(shutil.which("makepkg"), "makepkg is available on the Arch build host")
    def test_upgraded_srcinfo_matches_actual_makepkg_output(self) -> None:
        result = self.track()
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = subprocess.run(
            ["makepkg", "--printsrcinfo"], cwd=self.root,
            capture_output=True, text=True,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertEqual(generated.stdout, (self.root / ".SRCINFO").read_text())

    def test_missing_noextract_rejects_upgrade_without_partial_writes(self) -> None:
        file = self.root / ".SRCINFO"
        file.write_text("".join(line for line in file.read_text().splitlines(keepends=True)
                                if not line.startswith("\tnoextract = ")))
        before = ((self.root / "PKGBUILD").read_bytes(), file.read_bytes())
        result = self.track()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("upstream noextract", result.stderr)
        self.assertEqual(before, ((self.root / "PKGBUILD").read_bytes(), file.read_bytes()))


if __name__ == "__main__":
    unittest.main()
