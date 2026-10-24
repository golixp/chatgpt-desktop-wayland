# Maintainer: golixp
pkgname=chatgpt-desktop-wayland
pkgver=26.1021.22027
pkgrel=2
pkgdesc="Unofficial ChatGPT desktop with Linux Computer Use on native Wayland"
arch=('x86_64')
url='https://github.com/ilysenko/codex-desktop-linux'
license=('MIT' 'LicenseRef-OpenAI-Terms')

# The application is rebuilt locally from OpenAI's signed Linux payload. The
# native feature is enabled in the staged app, while the launcher below pins
# Electron to Wayland for both desktop-file and terminal launches.
depends=(
    'bash'
    'alsa-lib'
    'at-spi2-core'
    'cairo'
    'dbus'
    'expat'
    'gcc-libs'
    'gdk-pixbuf2'
    'glib2'
    'glibc'
    'gtk3'
    'grim'
    'hicolor-icon-theme'
    'libcups'
    'libdrm'
    'libglvnd'
    'libnotify'
    'libusb'
    'libx11'
    'libxcb'
    'libxcomposite'
    'libxdamage'
    'libxext'
    'libxfixes'
    'libxkbcommon'
    'libxrandr'
    'mesa'
    'nspr'
    'nss'
    'openssl'
    'pango'
    'polkit'
    'systemd-libs'
    'tpm2-tss'
    'xdg-utils'
    'xz'
    'ydotool'
)
makedepends=(
    'asar'
    'curl'
    'dpkg'
    'git'
    'gnupg'
    'nodejs'
    'python'
    'rust'
)
optdepends=(
    'git: project source control integration'
    'niri: native window targeting on Niri'
    'polkit-gnome: graphical authentication agent for privileged updater actions'
    'pulseaudio: audio integration'
    'xdg-desktop-portal: Wayland screenshot portal fallback'
    'xdg-desktop-portal-gnome: GNOME screenshot portal backend'
    'xdg-desktop-portal-kde: KDE screenshot portal backend'
    'xdg-desktop-portal-wlr: wlroots screenshot portal backend'
)
provides=('chatgpt-desktop')
conflicts=('chatgpt-desktop' 'codex-desktop' 'codex-desktop-git' 'codex-desktop-linux' 'openai-codex-desktop')
replaces=('chatgpt-desktop')
options=('!debug' '!strip')
install="$pkgname.install"

# These values are changed by scripts/track_upstream.py after signed metadata
# and the builder's exact commit have passed validation.
_builder_commit='56fb909e80c22f29f5a310f6243428ec9b9e8347'
_upstream_repo='https://persistent.oaistatic.com/codex-app-prod/linux/deb'
_upstream_path='pool/main/c/chatgpt/chatgpt_26.1021.22027_amd64.deb'
_upstream_sha256='eb0831c734f154f93f8ebcf0e17d118da0dea7dc1737dc61a1e71444375bf190'
_upstream_size='526398010'

source=(
    "builder::git+https://github.com/ilysenko/codex-desktop-linux.git#commit=${_builder_commit}"
    "chatgpt_${pkgver}_amd64.deb::${_upstream_repo}/${_upstream_path}"
    'patches-grim-niri.patch'
    'patches-computer-use-compat.patch'
    'LicenseRef-OpenAI-Terms'
)
sha256sums=(
    'SKIP'
    "${_upstream_sha256}"
    '678ccaaa0935b9873d939f7b5057efbc312ec0de7c41c56422b0e5872cfcb960'
    '1c1063ab2605845d32ffc9e0a3b9388e7bd9d52e626ebdd470c212417ae120c1'
    '7abd10bce8b40b17ff0902c0fa0750fcea831fd5afe67f8b4c02e4aeb8505ed3'
)

prepare() {
    cd "$srcdir/builder"

    # Niri exposes a GNOME-compatible screenshot D-Bus name that fails on
    # multi-output sessions. Use the compositor-native grim route instead.
    patch -Np1 -i "$srcdir/patches-grim-niri.patch"
    # Backport the current official bundle contracts without changing the
    # reviewed builder pin or disabling critical patch enforcement.
    patch -Np1 -i "$srcdir/patches-computer-use-compat.patch"

    local deb="$srcdir/chatgpt_${pkgver}_amd64.deb"
    [[ "$(stat -L -c '%s' "$deb")" == "$_upstream_size" ]] || {
        printf 'unexpected upstream payload size\n' >&2
        return 1
    }
    [[ "$(sha256sum "$deb" | awk '{print $1}')" == "$_upstream_sha256" ]] || {
        printf 'unexpected upstream payload SHA256\n' >&2
        return 1
    }

    # Preserve the official desktop artwork before install.sh replaces it
    # with the builder's community icon. Extract data only, never deb scripts.
    (
        set -o pipefail
        dpkg-deb --fsys-tarfile "$deb" | bsdtar -xOf - \
            ./usr/lib/chatgpt/resources/icon-chatgpt.png > "$srcdir/chatgpt-upstream.png"
    )

    printf '%s\n' '{"enabled":["computer-use-linux"]}' > linux-features/features.json
    export CODEX_LINUX_FEATURES_CONFIG="$PWD/linux-features/features.json"
    export CODEX_APP_ID="$pkgname"
    export CODEX_APP_DISPLAY_NAME='ChatGPT Wayland'
    export CODEX_INSTALL_DIR="$srcdir/packaged-app"
    export CODEX_ASAR_BIN="$(command -v asar)"

    # Build from the pinned builder and locked Cargo graph before install.sh
    # stages the feature into the official application payload.
    cargo build --locked --release -p codex-computer-use-linux \
        --bin codex-computer-use-linux --bin codex-computer-use-cosmic
    ./install.sh "$deb"
}

check() {
    cd "$srcdir/builder"
    # Exercise chat resume against the actual patched app, in addition to the
    # small upstream fixtures, so bundle changes cannot silently skip coverage.
    local check_app="$srcdir/check-app"
    asar extract "$srcdir/packaged-app/resources/app.asar" "$check_app"
    # The desktop exports these identifiers to child processes. Launcher tests
    # use their own app fixtures and must not inherit the running app identity.
    env -u CODEX_LINUX_APP_ID -u CODEX_LINUX_APP_DISPLAY_NAME \
        CODEX_SIGNED_EXTRACTED_APP="$check_app" node --test \
        linux-features/computer-use-linux/test.js \
        linux-features/computer-use-linux/*.test.js \
        launcher/start.test.js
    # The upstream binary uses a static mimalloc allocator.  Arch's current
    # lld orders that archive before the test harness and rejects the test
    # link, while the release binary built above links correctly.  Check all
    # Rust targets here and keep the release link as the executable check.
    cargo check --locked --all-targets -p codex-computer-use-linux

    python - "$srcdir/packaged-app/.codex-linux/build-info.json" "$pkgver" \
        "$srcdir/chatgpt-upstream.png" <<'PY'
import json
import struct
import sys

info = json.load(open(sys.argv[1], encoding="utf-8"))
assert info["upstreamLinuxPackage"]["version"] == sys.argv[2]
assert info["linuxFeatures"]["enabled"] == ["computer-use-linux"]

with open(sys.argv[3], "rb") as icon:
    header = icon.read(26)
assert header[:8] == b"\x89PNG\r\n\x1a\n"
assert header[12:16] == b"IHDR"
width, height = struct.unpack(">II", header[16:24])
assert width == height and width > 0
assert header[25] == 6, "the official icon must preserve its RGBA transparency"
PY
}

package() {
    (
        cd "$srcdir/builder"
        export REPO_DIR="$PWD"
        export APP_DIR="$srcdir/packaged-app"
        export PACKAGE_NAME="$pkgname"
        export PACKAGE_DISPLAY_NAME='ChatGPT Wayland'
        export PACKAGE_COMMENT='ChatGPT with Linux Computer Use on native Wayland'
        export PACKAGE_WITH_UPDATER=0
        export DESKTOP_TEMPLATE="$REPO_DIR/packaging/linux/codex-desktop.desktop"

        . scripts/lib/package-common.sh
        # Use the untouched icon from the verified official package for both
        # desktop and runtime/tray resources, including its transparent corners.
        ICON_SOURCE="$srcdir/chatgpt-upstream.png"
        stage_common_package_files "$pkgdir"
        write_launcher_stub "$pkgdir"
        stage_linux_feature_package_resources "$pkgdir" pacman
        run_linux_feature_package_hooks "$pkgdir" pacman
        normalize_package_payload_permissions "$pkgdir"
        restore_linux_feature_payload_permissions "$pkgdir"
        restore_linux_feature_package_resource_permissions "$pkgdir" pacman
    )

    grep -q '/usr/bin/chatgpt-desktop-wayland %u' \
        "$pkgdir/usr/share/applications/chatgpt-desktop-wayland.desktop"
    # The builder assumes a 256px icon. Register the original PNG under its
    # actual resolution without resampling or flattening the alpha channel.
    local icon icon_size icon_dir
    icon_size="$(python -c 'import struct,sys; print(struct.unpack(">I", open(sys.argv[1], "rb").read(20)[16:20])[0])' \
        "$srcdir/chatgpt-upstream.png")"
    icon_dir="usr/share/icons/hicolor/${icon_size}x${icon_size}/apps"
    if [[ "$icon_size" != 256 ]]; then
        install -d "$pkgdir/$icon_dir"
        mv "$pkgdir/usr/share/icons/hicolor/256x256/apps/$pkgname.png" "$pkgdir/$icon_dir/"
        rmdir "$pkgdir/usr/share/icons/hicolor/256x256/apps" "$pkgdir/usr/share/icons/hicolor/256x256"
    fi
    for icon in \
        "opt/$pkgname/.codex-linux/$pkgname.png" \
        "opt/$pkgname/resources/icon-chatgpt.png" \
        "$icon_dir/$pkgname.png"; do
        cmp "$srcdir/chatgpt-upstream.png" "$pkgdir/$icon" || return 1
    done

    # start.sh honours user flag files for ordinary community builds. This
    # package is deliberately a Wayland variant, so the wrapper supplies a
    # final command-line selection after those files and after user arguments.
    # Keep accessibility enabled for Computer Use's AT-SPI integration.
    cat > "$pkgdir/usr/bin/$pkgname" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
# Codex's own host may set this to keep its process out of AT-SPI. The
# ChatGPT target must clear it so Electron can publish its accessibility tree.
unset NO_AT_BRIDGE
# Electron's Chromium accessibility bridge is loaded through GTK_MODULES on
# Linux. Preserve any user modules while making the AT-SPI bridge explicit.
case ":${GTK_MODULES:-}:" in
    *:atk-bridge:*) ;;
    *) export GTK_MODULES="${GTK_MODULES:+$GTK_MODULES:}atk-bridge" ;;
esac
# Some Niri sessions leave the AT-SPI bus runtime disabled even when the
# toolkit setting is enabled. Enable it for this target before Electron
# starts; failures are harmless on sessions without the AT-SPI bus.
if command -v busctl >/dev/null 2>&1; then
    busctl --user set-property \
        org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled b true \
        >/dev/null 2>&1 || true
elif command -v dbus-send >/dev/null 2>&1; then
    dbus-send --session --dest=org.a11y.Bus --type=method_call \
        /org/a11y/bus org.freedesktop.DBus.Properties.Set \
        string:org.a11y.Status string:IsEnabled variant:boolean:true \
        >/dev/null 2>&1 || true
fi
export CODEX_OZONE_PLATFORM=wayland
exec /opt/chatgpt-desktop-wayland/start.sh "$@" \
    --ozone-platform=wayland \
    --force-renderer-accessibility
SCRIPT
    chmod 0755 "$pkgdir/usr/bin/$pkgname"

    install -Dm644 "$srcdir/builder/LICENSE" \
        "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    install -Dm644 "$srcdir/LicenseRef-OpenAI-Terms" \
        "$pkgdir/usr/share/licenses/$pkgname/LicenseRef-OpenAI-Terms"
}
