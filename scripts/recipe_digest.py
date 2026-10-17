#!/usr/bin/env python3
"""Create or verify a digest for the files allowed into an AUR publish."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

FILES = (
    "PKGBUILD",
    ".SRCINFO",
    "chatgpt-desktop-wayland.install",
    "LicenseRef-OpenAI-Terms",
    "patches-grim-niri.patch",
    "upstream.json",
)


def fail(message: str) -> None:
    raise SystemExit(f"recipe_digest.py: {message}")


def read_pkgbuild_value(source: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}=(?:'([^']+)'|([^\s#]+))\s*$", source)
    if not match:
        fail(f"PKGBUILD is missing {name}")
    return match.group(1) or match.group(2)


def calculate(root: Path) -> dict[str, object]:
    file_hashes: dict[str, str] = {}
    digest = hashlib.sha256()
    for relative in FILES:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            fail(f"recipe file is missing or is a symlink: {relative}")
        data = path.read_bytes()
        file_hash = hashlib.sha256(data).hexdigest()
        file_hashes[relative] = file_hash
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")

    pkgbuild = (root / "PKGBUILD").read_text(encoding="utf-8")
    return {
        "schema": 1,
        "pkgname": read_pkgbuild_value(pkgbuild, "pkgname"),
        "pkgver": read_pkgbuild_value(pkgbuild, "pkgver"),
        "pkgrel": read_pkgbuild_value(pkgbuild, "pkgrel"),
        "builder_commit": read_pkgbuild_value(pkgbuild, "_builder_commit"),
        "files": file_hashes,
        "recipe_sha256": digest.hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
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
