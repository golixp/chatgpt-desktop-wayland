#!/usr/bin/env python3
"""Fetch and verify the official OpenAI Linux APT metadata.

Only the signed InRelease file is trusted. It authenticates the Packages index,
which in turn authenticates the application payload used by PKGBUILD. No code
from the upstream builder is executed by this tracker.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

DEFAULT_REPOSITORY = "https://persistent.oaistatic.com/codex-app-prod/linux/deb"
EXPECTED_FINGERPRINT = "3BFA0E4AE8B8CC16A2D9BA684A3B4A566C4660E4"
EXPECTED_KEY_SHA256 = "23e2cfbdef6afe95505f9e95a2cb63585da7ffe9b06a51ec08a32407c847d596"
FILENAME_RE = re.compile(r"^pool/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+\.deb$")
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+~-]*$")


def fail(message: str) -> None:
    raise SystemExit(f"fetch_upstream_metadata.py: {message}")


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "chatgpt-desktop-wayland-tracker/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except Exception as exc:  # pragma: no cover - network error wording varies
        fail(f"download failed for {url}: {exc}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_key(key_path: Path) -> None:
    result = subprocess.run(
        ["gpg", "--batch", "--show-keys", "--with-colons", str(key_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        fail(f"cannot inspect repository key: {result.stderr.strip()}")
    fingerprints = [line.split(":")[9] for line in result.stdout.splitlines() if line.startswith("fpr:")]
    if EXPECTED_FINGERPRINT not in fingerprints:
        fail(f"repository key does not contain fingerprint {EXPECTED_FINGERPRINT}")


def clear_signed_payload(in_release: bytes) -> str:
    lines = in_release.decode("utf-8").replace("\r\n", "\n").split("\n")
    if not lines or lines[0] != "-----BEGIN PGP SIGNED MESSAGE-----":
        fail("InRelease is not an OpenPGP clear-signed message")
    try:
        blank = lines.index("", 1)
    except ValueError:
        fail("InRelease has no signed-message header separator")
    payload: list[str] = []
    for line in lines[blank + 1 :]:
        if line == "-----BEGIN PGP SIGNATURE-----":
            break
        payload.append(line[2:] if line.startswith("- ") else line)
    if not payload:
        fail("InRelease signed payload is empty")
    return "\n".join(payload)


def verify_in_release(in_release: bytes, key_path: Path) -> str:
    verify_key(key_path)
    with tempfile.NamedTemporaryFile(prefix="chatgpt-inrelease-", delete=False) as handle:
        path = Path(handle.name)
        handle.write(in_release)
    try:
        result = subprocess.run(
            ["gpgv", "--keyring", str(key_path), str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        path.unlink(missing_ok=True)
    if result.returncode:
        fail(f"InRelease signature verification failed: {result.stderr.strip()}")
    return clear_signed_payload(in_release)


def release_sha256(payload: str, relative_path: str) -> tuple[str, int]:
    in_sha256 = False
    for line in payload.splitlines():
        if re.fullmatch(r"[A-Za-z0-9]+:", line):
            in_sha256 = line == "SHA256:"
            continue
        if not in_sha256:
            continue
        match = re.fullmatch(r"\s*([0-9a-fA-F]{64})\s+(\d+)\s+(\S+)\s*", line)
        if match and match.group(3) == relative_path:
            return match.group(1).lower(), int(match.group(2))
    fail(f"signed InRelease does not index {relative_path}")


def parse_deb822(payload: bytes) -> list[dict[str, str]]:
    paragraphs: list[dict[str, str]] = []
    current: dict[str, str] = {}
    field: str | None = None
    for raw_line in payload.decode("utf-8").splitlines():
        if not raw_line.strip():
            if current:
                paragraphs.append(current)
                current = {}
            field = None
            continue
        if raw_line[0].isspace() and field:
            current[field] += "\n" + raw_line[1:]
            continue
        if ":" not in raw_line:
            fail(f"malformed Packages line: {raw_line!r}")
        field, value = raw_line.split(":", 1)
        if not field or field in current:
            fail(f"malformed Packages field: {raw_line!r}")
        current[field] = value.strip()
    if current:
        paragraphs.append(current)
    return paragraphs


def select_package(packages: bytes) -> dict[str, object]:
    matches = [
        item for item in parse_deb822(packages)
        if item.get("Package") == "chatgpt" and item.get("Architecture") == "amd64"
    ]
    if len(matches) != 1:
        fail(f"expected exactly one chatgpt/amd64 package entry, found {len(matches)}")
    item = matches[0]
    version = item.get("Version", "")
    filename = item.get("Filename", "")
    package_sha256 = item.get("SHA256", "").lower()
    size = item.get("Size", "")
    if not VERSION_RE.fullmatch(version):
        fail(f"unsafe upstream version: {version!r}")
    if not FILENAME_RE.fullmatch(filename):
        fail(f"unsafe upstream filename: {filename!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", package_sha256):
        fail("Packages entry has an invalid SHA256")
    if not size.isdigit() or int(size) <= 0:
        fail("Packages entry has an invalid size")
    return {
        "package": "chatgpt",
        "version": version,
        "architecture": "amd64",
        "repository_path": filename,
        "sha256": package_sha256,
        "size": int(size),
        "depends": item.get("Depends", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    args = parser.parse_args()
    repository = args.repository.rstrip("/")
    if repository != DEFAULT_REPOSITORY:
        fail("repository override is disabled for the production tracker")

    try:
        key_bytes = base64.b64decode("".join(args.key.read_text(encoding="utf-8").split()), validate=True)
    except Exception as exc:
        fail(f"invalid base64 repository key: {exc}")
    if sha256(key_bytes) != EXPECTED_KEY_SHA256:
        fail("repository key bytes do not match the pinned public key")
    with tempfile.NamedTemporaryFile(prefix="chatgpt-key-", delete=False) as handle:
        key_path = Path(handle.name)
        handle.write(key_bytes)
    try:
        in_release = download(f"{repository}/dists/stable/InRelease")
        release = verify_in_release(in_release, key_path)
        relative = "main/binary-amd64/Packages"
        expected_sha, expected_size = release_sha256(release, relative)
        packages = download(f"{repository}/dists/stable/{relative}")
        if len(packages) != expected_size or sha256(packages) != expected_sha:
            fail("Packages index does not match the signed InRelease hash or size")
        metadata = select_package(packages)
        metadata["repository"] = repository
        args.metadata.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        key_path.unlink(missing_ok=True)
    print(args.metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
