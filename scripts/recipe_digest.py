#!/usr/bin/env python3
"""Define recipe inputs, copy candidates, and verify their validation policy.

Recipe files are the only files published to AUR. Validation files travel with
the CI candidate and are hashed too, so changing a check cannot reuse a marker
created by an older validation policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

RECIPE_FILES = (
    "PKGBUILD",
    ".SRCINFO",
    "chatgpt-desktop-wayland.install",
    "LicenseRef-OpenAI-Terms",
    "patches-grim-niri.patch",
    "patches-builder-portability.patch",
    "patches-disable-pets.patch",
    "builder-main-process.cjs",
    "builder-main-process.test.cjs",
    "builder-acorn.cjs",
    "builder-acorn.LICENSE",
    "builder-preflight.sh",
    "upstream.json",
)
VALIDATION_FILES = (
    ".github/workflows/track-upstream.yml",
    "scripts/recipe_digest.py",
    "scripts/fetch_upstream_metadata.py",
    "scripts/track_upstream.py",
    "tests/test_recipe_digest.py",
    "tests/test_preflight.py",
    "openai-codex-linux-repository-key.gpg.base64",
)
RETIRED_RECIPE_FILES = ("patches-computer-use-compat.patch",)
FILES = RECIPE_FILES + VALIDATION_FILES


def files_for_scope(scope: str) -> tuple[str, ...]:
    return {
        "recipe": RECIPE_FILES,
        "validation": VALIDATION_FILES,
        "candidate": FILES,
        "retired": RETIRED_RECIPE_FILES,
    }[scope]


def fail(message: str) -> None:
    raise SystemExit(f"recipe_digest.py: {message}")


def read_pkgbuild_value(source: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}=(?:'([^']+)'|([^\s#]+))\s*$", source)
    if not match:
        fail(f"PKGBUILD is missing {name}")
    return match.group(1) or match.group(2)


def safe_file(root: Path, relative: str) -> Path:
    """Reject symlinks at the leaf and every directory inside the input root."""
    path = root
    if any(parent.is_symlink() for parent in (root.absolute(), *root.absolute().parents)):
        fail(f"input root is a symlink: {root}")
    for component in Path(relative).parts:
        path = path / component
        if path.is_symlink():
            fail(f"input file has a symlink component: {relative}")
    if not path.is_file():
        fail(f"input file is missing or is not a regular file: {relative}")
    return path


def read_inputs(root: Path, scope: str = "candidate") -> dict[str, bytes]:
    return {
        relative: safe_file(root, relative).read_bytes()
        for relative in files_for_scope(scope)
    }


def copy_inputs(root: Path, destination: Path, scope: str) -> None:
    # Validate and read all inputs before creating a partial candidate.
    inputs = read_inputs(root, scope)
    if any(parent.is_symlink() for parent in (destination.absolute(), *destination.absolute().parents)):
        fail(f"copy destination is a symlink: {destination}")
    destinations = {}
    for relative in inputs:
        path = destination
        components = Path(relative).parts
        for index, component in enumerate(components):
            path = path / component
            if path.is_symlink():
                fail(f"copy destination has a symlink component: {relative}")
            if index < len(components) - 1 and path.exists() and not path.is_dir():
                fail(f"copy destination directory is not a directory: {relative}")
        if path.exists() and not path.is_file():
            fail(f"copy destination is not a regular file: {relative}")
        destinations[relative] = path
    for relative, data in inputs.items():
        path = destinations[relative]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def calculate(root: Path) -> dict[str, object]:
    inputs = read_inputs(root)
    file_hashes: dict[str, str] = {}
    digest = hashlib.sha256()
    for relative, data in inputs.items():
        file_hash = hashlib.sha256(data).hexdigest()
        file_hashes[relative] = file_hash
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")

    pkgbuild = inputs["PKGBUILD"].decode("utf-8")
    return {
        "schema": 2,
        "pkgname": read_pkgbuild_value(pkgbuild, "pkgname"),
        "pkgver": read_pkgbuild_value(pkgbuild, "pkgver"),
        "pkgrel": read_pkgbuild_value(pkgbuild, "pkgrel"),
        "builder_commit": read_pkgbuild_value(pkgbuild, "_builder_commit"),
        "files": file_hashes,
        "recipe_files": list(RECIPE_FILES),
        "recipe_sha256": digest.hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--list-files", choices=("recipe", "validation", "candidate", "retired"))
    parser.add_argument("--copy-to", type=Path)
    parser.add_argument("--scope", choices=("recipe", "candidate"), default="candidate")
    args = parser.parse_args()
    if args.list_files:
        if args.output or args.verify or args.copy_to:
            parser.error("--list-files cannot be combined with copying or marker operations")
        print("\n".join(files_for_scope(args.list_files)))
        return 0
    if args.copy_to:
        if args.output or args.verify:
            parser.error("--copy-to cannot be combined with marker operations")
        copy_inputs(args.root, args.copy_to, args.scope)
        return 0
    marker = calculate(args.root)
    if args.verify:
        try:
            expected = json.loads(args.verify.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"cannot read validation marker: {exc}")
        if expected != marker:
            fail("validation marker does not match the current recipe")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        print(json.dumps(marker, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
