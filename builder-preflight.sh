#!/usr/bin/env bash
# Verify and exercise the real upstream JavaScript before compiling native code.
set -Eeuo pipefail

if [[ $# != 5 ]]; then
    echo 'Usage: builder-preflight.sh BUILDER DEB WORK_PARENT REPORT_DIR ICON_OUTPUT' >&2
    exit 2
fi
builder_dir="$(realpath "$1")"
upstream_deb="$(realpath "$2")"
work_parent="$(realpath "$3")"
report_dir="$(realpath -m "$4")"
icon_output="$(realpath -m "$5")"
mkdir -p "$report_dir"
# The caller supplies disk-backed scratch; do not expand a multi-GB app in /tmp.
scratch="$(mktemp -d "$work_parent/preflight.XXXXXXXX")"
phase=verify
finish() {
    local result=$?
    trap - EXIT
    # Teardown must not replace the original verification/patch failure. If
    # validation succeeded, a teardown failure still prevents a false success.
    if ! rm -rf -- "$scratch"; then
        printf 'Could not clean preflight scratch: %s\n' "$scratch" >&2
        if (( result == 0 )); then
            result=1
            phase=cleanup
        fi
    fi
    if ! python - "$report_dir/preflight-status.json" "$phase" "$result" <<'PY'
import json
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "schema": 1, "phase": sys.argv[2], "exit_code": int(sys.argv[3]),
    "result": "passed" if sys.argv[3] == "0" else "failed",
}, indent=2) + "\n")
PY
    then
        printf 'Could not write preflight status; original exit code: %s\n' "$result" >&2
        if (( result == 0 )); then
            result=1
        fi
    fi
    exit "$result"
}
trap finish EXIT

# This verifier checks the signed index, payload hash and Debian package fields.
# A pinned payload does not silently bypass verification of the official source.
node "$builder_dir/scripts/lib/upstream-linux-package.js" \
    --output-dir "$scratch/verification" \
    --metadata "$report_dir/upstream-linux-package.json" \
    --key-base64 "$builder_dir/assets/openai-codex-linux-repository-key.gpg.base64" \
    --arch amd64 --local-package "$upstream_deb"

phase=extract
mkdir -p "$scratch/payload"
# Only extract data. In particular, never run the .deb maintainer scripts.
dpkg-deb --fsys-tarfile "$upstream_deb" | bsdtar -xf - -C "$scratch/payload" \
    ./usr/lib/chatgpt/resources/app.asar \
    ./usr/lib/chatgpt/resources/app.asar.unpacked \
    ./usr/lib/chatgpt/resources/icon-chatgpt.png
resources="$scratch/payload/usr/lib/chatgpt/resources"
"${CODEX_ASAR_BIN:-asar}" extract "$resources/app.asar" "$scratch/app"
cp "$resources/icon-chatgpt.png" "$icon_output"

phase=discover
[[ -f "$scratch/app/package.json" && ! -L "$scratch/app/package.json" ]] || {
    echo 'Official ASAR must contain a regular package.json entry manifest' >&2
    exit 1
}
node - "$builder_dir" "$scratch/app" "$report_dir/discovery.json" <<'JS'
const fs = require('node:fs');
const path = require('node:path');
const [builder, app, output] = process.argv.slice(2);
const { findMainProcessChunks } = require(path.join(builder, 'scripts/patches/lib/assets.js'));
const result = findMainProcessChunks(app);
if (!result || result.chunks.length === 0) throw new Error('No main-process JavaScript candidates');
fs.writeFileSync(output, JSON.stringify({
  schema: 1,
  directory: path.relative(app, result.buildDir),
  candidates: result.chunks,
}, null, 2) + '\n');
JS

phase=pristine_contracts
# These tests require pristine official code, not an already patched package.
CODEX_SIGNED_EXTRACTED_APP="$scratch/app" CODEX_TEST_ALREADY_PATCHED=0 node --test \
    "$builder_dir/linux-features/computer-use-linux/current-signed.test.js" \
    "$builder_dir/linux-features/disable-pets/test.js"

phase=patch
node "$builder_dir/scripts/patch-linux-window-ui.js" \
    --report-json "$report_dir/preflight-report.json" --enforce-critical "$scratch/app"

phase=patched_contracts
CODEX_SIGNED_EXTRACTED_APP="$scratch/app" CODEX_TEST_ALREADY_PATCHED=1 node --test \
    "$builder_dir/linux-features/computer-use-linux/resume.test.js" \
    "$builder_dir/linux-features/disable-pets/test.js"
phase=complete
echo 'Compatibility preflight passed; native compilation may proceed.'
