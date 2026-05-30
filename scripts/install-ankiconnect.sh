#!/usr/bin/env bash
#
# Install AnkiConnect addon into the local Anki Desktop install.
#
# AnkiConnect (addon id 2055492159) is a small HTTP server inside Anki Desktop
# that lets tools/md_to_anki.py push cards directly into Anki.
#
# This script downloads the addon source from GitHub (FooSoft/anki-connect)
# and places it at the OS-specific addons directory. Anki has to be CLOSED
# during install; restart it afterwards to activate the addon.
#
# Usage:  bash scripts/install-ankiconnect.sh
#
set -euo pipefail

ADDON_ID=2055492159
TARBALL_URL="https://github.com/FooSoft/anki-connect/archive/refs/heads/master.tar.gz"

case "$(uname -s)" in
    Linux*)   ADDON_BASE="$HOME/.local/share/Anki2/addons21" ;;
    Darwin*)  ADDON_BASE="$HOME/Library/Application Support/Anki2/addons21" ;;
    MINGW*|MSYS*|CYGWIN*)
              ADDON_BASE="${APPDATA:-$HOME/AppData/Roaming}/Anki2/addons21" ;;
    *)        echo "✘ Unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac

ADDON_DIR="$ADDON_BASE/$ADDON_ID"

if pgrep -fi 'anki(\.exe)?$' >/dev/null 2>&1; then
    echo "✘ Anki Desktop is running. Quit Anki first, then re-run this script." >&2
    exit 1
fi

if [[ ! -d "$ADDON_BASE" ]]; then
    echo "✘ Anki addons directory not found: $ADDON_BASE" >&2
    echo "  Install Anki Desktop first and launch it once so it creates the profile." >&2
    exit 1
fi

echo "→ Anki addons base: $ADDON_BASE"
echo "→ Target           : $ADDON_DIR"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "→ Downloading AnkiConnect source from GitHub..."
curl -fL --retry 3 --max-time 60 -o "$TMP/src.tar.gz" "$TARBALL_URL"

echo "→ Extracting..."
tar -xzf "$TMP/src.tar.gz" -C "$TMP"
SRC=$(find "$TMP" -maxdepth 2 -type d -name plugin | head -n1)
if [[ -z "$SRC" ]]; then
    echo "✘ Could not locate plugin/ inside the downloaded tarball." >&2
    exit 1
fi

rm -rf "$ADDON_DIR"
mkdir -p "$ADDON_DIR"
cp -R "$SRC"/* "$ADDON_DIR/"

echo ""
echo "✓ AnkiConnect installed at: $ADDON_DIR"
echo ""
echo "Next steps:"
echo "  1. Start Anki Desktop."
echo "  2. Verify the HTTP API is up:"
echo "     curl -s localhost:8765 -d '{\"action\":\"version\",\"version\":6}'"
echo "     (should return {\"result\":6,\"error\":null})"
echo "  3. Run: python3 tools/md_to_anki.py"
