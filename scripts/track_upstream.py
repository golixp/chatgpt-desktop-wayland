#!/usr/bin/env python3
"""Validate signed upstream metadata and update the checked-in AUR pins.

The metadata is produced by the pinned upstream verifier. This script remains
deliberately boring: it validates types and paths, never executes downloaded
source, rejects version downgrades, and edits only the expected recipe fields.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+~-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PATH_RE = re.compile(r"^pool/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+\.deb$")
REPOSITORY = "https://persistent.oaistatic.com/codex-app-prod/linux/deb"


def fail(message: str) -> None:
    raise SystemExit(f"track_upstream.py: {message}")


def read_metadata(path: Path) -> dict[str, object]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read metadata: {exc}")
    if not isinstance(metadata, dict):
        fail("metadata must be a JSON object")

    if metadata.get("package") != "chatgpt":
        fail("metadata package must be chatgpt")
    if metadata.get("architecture") != "amd64":
        fail("metadata architecture must be amd64")
    if metadata.get("repository") != REPOSITORY:
        fail("metadata repository does not match the pinned OpenAI repository")

    version = metadata.get("version")
    repository_path = metadata.get("repositoryPath", metadata.get("repository_path"))
    sha256 = metadata.get("sha256")
    size = metadata.get("size")
    builder_commit = metadata.get("builderCommit")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        fail(f"unsafe upstream version: {version!r}")
    if not isinstance(repository_path, str) or not PATH_RE.fullmatch(repository_path):
        fail(f"unsafe upstream repository path: {repository_path!r}")
    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256.lower()):
        fail("upstream SHA256 must contain exactly 64 hexadecimal characters")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        fail("upstream size must be a positive integer")
    if builder_commit is not None and (
        not isinstance(builder_commit, str) or not COMMIT_RE.fullmatch(builder_commit.lower())
    ):
        fail("builderCommit must contain exactly 40 hexadecimal characters")

    return {
        "version": version,
        "repository_path": repository_path,
        "sha256": sha256.lower(),
        "size": size,
        "builder_commit": builder_commit.lower() if isinstance(builder_commit, str) else None,
    }


def current_value(source: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}=(?:'([^']+)'|([^\s#]+))\s*$", source)
    if not match:
        fail(f"PKGBUILD is missing {name}")
    return match.group(1) or match.group(2)


def replace_single(source: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        fail(f"expected exactly one {label} field in PKGBUILD/.SRCINFO")
    return updated


def debian_version_is_newer(candidate: str, installed: str) -> bool:
    dpkg = shutil.which("dpkg")
    if dpkg:
        result = subprocess.run(
            [dpkg, "--compare-versions", candidate, "gt", installed],
            check=False,
        )
        return result.returncode == 0

    # The fallback is only used on developer machines without dpkg. GitHub's
    # Arch build and the official metadata job both have dpkg available.
    def key(value: str) -> tuple[object, ...]:
        return tuple(int(part) if part.isdigit() else part for part in re.split(r"([0-9]+)", value))

    return key(candidate) > key(installed)


def update_pkgbuild(path: Path, metadata: dict[str, object], builder_commit: str) -> tuple[str, bool]:
    source = path.read_text(encoding="utf-8")
    current_version = current_value(source, "pkgver")
    current_builder = current_value(source, "_builder_commit")
    current_rel = current_value(source, "pkgrel")
    if not current_rel.isdigit() or int(current_rel) < 1:
        fail(f"invalid pkgrel: {current_rel!r}")

    version = str(metadata["version"])
    if version != current_version and not debian_version_is_newer(version, current_version):
        fail(f"upstream version {version} is not newer than current {current_version}")
    if builder_commit == current_builder and version == current_version:
        return source, False

    if version != current_version:
        pkgrel = "1"
    else:
        pkgrel = str(int(current_rel) + 1)

    source = replace_single(source, r"^pkgver=.*$", f"pkgver={version}", "pkgver")
    source = replace_single(source, r"^pkgrel=.*$", f"pkgrel={pkgrel}", "pkgrel")
    source = replace_single(source, r"^_builder_commit='[^']+'$", f"_builder_commit='{builder_commit}'", "_builder_commit")
    source = replace_single(source, r"^_upstream_path='[^']+'$", f"_upstream_path='{metadata['repository_path']}'", "_upstream_path")
    source = replace_single(source, r"^_upstream_sha256='[^']+'$", f"_upstream_sha256='{metadata['sha256']}'", "_upstream_sha256")
    source = replace_single(source, r"^_upstream_size='[^']+'$", f"_upstream_size='{metadata['size']}'", "_upstream_size")
    return source, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("pkgbuild", type=Path)
    parser.add_argument("srcinfo", type=Path)
    parser.add_argument("builder_commit")
    args = parser.parse_args()

    metadata = read_metadata(args.metadata)
    builder_commit = args.builder_commit.lower()
    if not COMMIT_RE.fullmatch(builder_commit):
        fail("builder commit must contain exactly 40 hexadecimal characters")

    updated, changed = update_pkgbuild(args.pkgbuild, metadata, builder_commit)
    if not changed:
        print("changed=false")
        return 0

    # Build both complete files in memory first. A malformed or stale .SRCINFO
    # must never leave a half-updated PKGBUILD behind.
    srcinfo = args.srcinfo.read_text(encoding="utf-8")
    pkgrel = current_value(updated, "pkgrel")
    srcinfo = replace_single(srcinfo, r"^\tpkgver = .*$", f"\tpkgver = {metadata['version']}", "pkgver")
    srcinfo = replace_single(srcinfo, r"^\tpkgrel = .*$", f"\tpkgrel = {pkgrel}", "pkgrel")
    srcinfo = replace_single(srcinfo, r"^\tsource = builder::git\+https://github\.com/ilysenko/codex-desktop-linux\.git#commit=[0-9a-f]{40}$", f"\tsource = builder::git+https://github.com/ilysenko/codex-desktop-linux.git#commit={builder_commit}", "builder source")
    srcinfo = replace_single(srcinfo, r"^\tsource = chatgpt_[^ ]+_amd64\.deb::https://persistent\.oaistatic\.com/codex-app-prod/linux/deb/.+$", f"\tsource = chatgpt_{metadata['version']}_amd64.deb::{REPOSITORY}/{metadata['repository_path']}", "upstream source")
    srcinfo = replace_single(srcinfo, r"^\tsha256sums = [0-9a-f]{64}$", f"\tsha256sums = {metadata['sha256']}", "upstream checksum")
    args.pkgbuild.write_text(updated, encoding="utf-8")
    args.srcinfo.write_text(srcinfo, encoding="utf-8")
    print(f"changed=true\nversion={metadata['version']}\npkgrel={pkgrel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
