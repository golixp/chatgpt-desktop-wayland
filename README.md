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
`xdg-desktop-portal`/accessibility setup. The `grim` dependency is used by
wlroots compositors such as Niri; other desktops use their portal or native
screenshot route.

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

The local workflow in
`.github/workflows/track-upstream.yml` checks these pins hourly, creates a
candidate recipe, builds it in a disposable Arch container, runs the native
Computer Use and launcher tests, and publishes only the validated recipe. AUR
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
workflow therefore also supports `workflow_dispatch`, and every package build
still verifies the signed metadata and payload hash locally.
