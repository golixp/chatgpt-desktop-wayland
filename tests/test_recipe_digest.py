"""Validation markers bind the published recipe to the checks that approved it."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "recipe_digest.py"
sys.path.insert(0, str(SCRIPT.parent))
import recipe_digest as recipe


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


if __name__ == "__main__":
    unittest.main()
