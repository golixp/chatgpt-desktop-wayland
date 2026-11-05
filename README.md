# chatgpt-desktop-wayland

This is an unofficial Arch package for the official OpenAI ChatGPT Linux
`.deb`. It is built locally from the signed upstream APT metadata and the
reproducible repackager at
[`ilysenko/codex-desktop-linux`](https://github.com/ilysenko/codex-desktop-linux).

This package targets `x86_64` Arch-family systems running an active Wayland
session. It is not a general X11 package: the launcher deliberately selects
Electron's Wayland backend, so an X11-only session is outside the supported
runtime.

The package has the requested two runtime properties:

* `computer-use-linux` is enabled and its Linux native helper is compiled from
  the pinned Rust source commit.
* `/usr/bin/chatgpt-desktop-wayland` and the desktop entry append
  `--ozone-platform=wayland`, so an X11 flag left in a user flag file cannot
  make this variant silently start through XWayland.

The official `chatgpt-desktop` package cannot be installed beside this variant;
pacman replaces it so `yay -Syu` continues to own upgrades. Runtime state and
cache files use the package-specific directories selected by the launcher.

## Computer Use prerequisites

The package includes the integration and `ydotool` dependency. The package
manager installs the daemon, its uinput rule and the Computer Use helper, but
it cannot enable a per-user service or change the current login's device
permissions for you. Start the per-user daemon once (the Arch package ships
this unit):

```bash
systemctl --user enable --now ydotool.service
```

If the daemon still cannot open `/dev/uinput`, log out and back in after the
udev rule has been installed. AT-SPI is provided by the desktop session; the
launcher enables the application-side bridge when the session bus is
available. GNOME, KDE and other desktops still need their own normal
`xdg-desktop-portal`/accessibility setup. The `grim` dependency provides a
screencopy route on supported Wayland compositors, including Niri. On Niri it takes priority over the GNOME-compatible
screenshot interface, which cannot capture multiple outputs. Other desktops
retain their GNOME routes before trying grim, portals and native fallbacks.
The screenshot command and doctor share this backend order; detecting an
installed route does not claim that screenshot capture has succeeded.

The backend still follows the desktop's permission model. In ChatGPT Settings,
enable the **Any App** Computer Use row on the first run. Niri, GNOME, KDE and
other supported desktops may need their normal accessibility or RemoteDesktop
permission. A diagnostic command is available inside the package:

```bash
/opt/chatgpt-desktop-wayland/resources/plugins/openai-bundled/plugins/unified-computer-use/bin/codex-computer-use-linux doctor
```

## Reproducible update model

`upstream.json` records the signed official version, payload path, SHA256 and
builder commit. `scripts/track_upstream.py` only accepts a newer safe version,
an exact 64-character payload hash and a 40-character builder commit. It resets
`pkgrel` for a new app version and increments it for a builder-only change.
The scheduled updater keeps the builder at the reviewed commit in `PKGBUILD`;
it does not follow the third-party builder's moving `main` branch. Builder
upgrades require reviewing the local patches and validating a full build first.

The reviewed builder commit
[`75c00cbaaaf6736c75c70362ab58b79b17488c2c`](https://github.com/ilysenko/codex-desktop-linux/commit/75c00cbaaaf6736c75c70362ab58b79b17488c2c)
includes the Computer Use audio-environment changes and Rspack main-process and
webview support for official ChatGPT 26.1029.61003.
`patches-grim-niri.patch` retains the Niri screenshot adaptation and its
regression tests against this builder.

The desktop and runtime/tray icons come directly from
`usr/lib/chatgpt/resources/icon-chatgpt.png` in the verified official `.deb`.
The package preserves the original pixels, resolution and transparent rounded
corners, and registers the desktop icon at its actual size. Each new upstream
release supplies its own icon, covered by the signed package checksum.

## Compatibility-aware build

`builder-main-process.cjs` reads the application entry from `package.json`
and parses its reachable JavaScript dependencies using Acorn. It follows literal
CommonJS/ES module imports and resolves the reviewed Rspack chunk-loader shape
from a finite, data-only table. It never executes application code or evaluates
a downloaded chunk-name function. Unreferenced workers and preload files cannot
be selected just because they contain similar code.

Discovery does not depend on `main-<hash>.js`, `early-bootstrap-<hash>.js` or
specific chunk IDs. File-count, dependency-depth and byte limits bound the scan;
escaping paths, symbolic links, ambiguous dependencies and unsupported computed
local loaders are errors. Production preflight requires a regular entry manifest.
A manifest-less fallback exists only for the builder's small unit fixtures.

`patches-builder-portability.patch` connects that discovery to the builder's
patch engine. Feature descriptors identify their owning code by content, with
strict ownership and idempotence checks. A changed filename or chunk location
therefore does not alone require a new feature patch. Real changes to APIs,
control flow or feature semantics still require a reviewed adapter and tests;
this framework does not infer new application behavior or disable critical
checks to make an incompatible build pass.

Webview descriptors locate code by content in both entry and asynchronous
chunks, using a shared index refreshed after each patch. Duplicated renderer
modules are patched in every matching chunk and checked for syntax and
idempotence. Real-package preflight covers both main-process and webview
contracts; failures retain the owning chunk and underlying error in the log.

The parser is vendored unchanged as `builder-acorn.cjs` from the official
[Acorn 8.15.0 tarball](https://registry.npmjs.org/acorn/-/acorn-8.15.0.tgz),
with its MIT license in `builder-acorn.LICENSE`. The tarball's npm SHA512 integrity
was verified; its SHA256 is
`e46eae89bd9761f16d1d25563b837b55b4b78f3ece06ee0b9789cb2644f523b3`.
The parser file SHA256 is
`fdb08546776ec6228b03e8d02b40d4ab3255bae5f401adba7ff5dad927ac5c9c`.
There is no npm installation or floating parser dependency during a build.

The package separates the work into four stages:

1. `prepare()` applies reviewed builder patches, checks the payload hash and
   runs `builder-preflight.sh`. Preflight verifies the signed official metadata
   and payload, extracts a temporary ASAR copy, checks the pristine Computer Use
   contracts, applies the enabled patches, and tests patched chat resume. A
   compatibility failure stops here, before native compilation.
2. `build()` compiles the native package's declared binary targets from the
   locked Cargo graph, including the Linux, COSMIC and activity-indicator helpers,
   then stages the official payload with the enabled feature. Newly declared
   helpers do not require a second target list in this recipe. The recipe disables
   makepkg's C/C++ LTO flags: GCC LTO archives from mimalloc cannot be consumed by
   Rust's LLVM linker. This keeps linking independent of host makepkg defaults.
3. `check()` runs the feature, patch-engine and launcher regression tests, checks
   native Rust targets and Niri routing tests, and validates resume against the
   packaged application. Small socket-test fixtures use a short temporary path
   to respect Unix socket limits; application extraction remains on disk.
4. `package()` stages the Arch package and checks its launcher and official icons.

Preflight uses scratch space on the build filesystem and removes the temporary
extracted application. Reports under `build-diagnostics/` record the failed
phase, verified upstream metadata, discovered candidates and patch outcomes.
GitHub Actions also retains preparation/build logs and the installed dependency
versions, even when validation fails, and summarizes the failed stage.

The explicit manifests in `scripts/recipe_digest.py` define the recipe files
and validation-policy files. Candidate assembly and publication use those
manifests instead of separate filename lists. The validation marker hashes both
groups, including the workflow and validation scripts, so changing a check
invalidates an older success marker. Only recipe files are eligible for AUR;
CI files and credentials do not become package sources.

## Local validation

Install the dependencies declared in `PKGBUILD`, then run compatibility checks
before spending time on native compilation:

```bash
makepkg --cleanbuild --nobuild
makepkg --noextract
```

The second command reuses the exact sources prepared by the first. A normal
`makepkg --cleanbuild` also runs all four stages in order. Neither command
installs the resulting package. Do not reuse `--noextract` after changing the
recipe, patches or inputs; rerun the first command instead.

The local orchestration tests do not require an application download:

```bash
node --test builder-main-process.test.cjs
python -m unittest discover -s tests -v
bash -n PKGBUILD builder-preflight.sh
```

A successful unit test run is not a substitute for a complete package build.
Only the full CI build may record a fresh publishable validation marker.

## CI publishing

The GitHub workflow in
`.github/workflows/track-upstream.yml` checks official releases hourly, creates a
candidate recipe, runs compatibility preflight and the subsequent full build in
a disposable Arch container, and publishes only the validated recipe. AUR
publishing is disabled until the repository owner explicitly supplies the
required secret and variable; the GitHub updater is active, but no AUR push has
been enabled.

For a future deployment, create the AUR package manually, add an SSH key as the
`AUR_SSH_PRIVATE_KEY` repository secret, add its exact `known_hosts` line as
`AUR_KNOWN_HOSTS`, and set the repository variable
`AUR_PUBLISH_ENABLED=true`. The workflow never places these credentials in the
untrusted build job. It retries AUR synchronization on later runs even when a
previous publish attempt failed.

GitHub scheduled workflows use UTC and can be delayed during load; public
repositories can also have schedules disabled after long inactivity. The
workflow therefore also supports `workflow_dispatch` with an optional
`force_build` input to revalidate an unchanged recipe. Every package build
still verifies the signed metadata and payload hash locally.

For local installation from the same tested CI output, manually dispatch with
`force_build=true` and `upload_package=true`. The installable `.pkg.tar.zst`
is retained as `chatgpt-desktop-wayland-package` for three days after the build
and checks succeed. Scheduled runs do not retain this large artifact.
