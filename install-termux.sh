#!/data/data/com.termux/files/usr/bin/bash
# ytgrab installer for Termux (Android). Run from the extracted project directory.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
if [ ! -f "$DIR/pyproject.toml" ]; then
    echo "Run this script from the extracted ytgrab project directory." >&2
    exit 2
fi
if [ -z "${TERMUX_VERSION:-}" ] && [[ "$PREFIX" != *com.termux* ]]; then
    echo "This installer is intended for Termux on Android." >&2
    exit 2
fi
command -v pkg >/dev/null || { echo "Termux pkg is required." >&2; exit 2; }

pkg update -y
pkg install -y python ffmpeg nodejs-lts

ROOT="$PREFIX/opt/ytgrab"
VENV="$ROOT/venv"
mkdir -p "$ROOT" "$PREFIX/bin"
if [ ! -x "$VENV/bin/python" ]; then
    python -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install "$DIR[tui]"

cat > "$PREFIX/bin/ytgrab" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
exec "$VENV/bin/ytgrab" "\$@"
EOF
cat > "$PREFIX/bin/ytstream" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
exec "$VENV/bin/ytstream" "\$@"
EOF
chmod +x "$PREFIX/bin/ytgrab" "$PREFIX/bin/ytstream"
echo
echo "Installed ytgrab. Try: ytgrab --version"
echo "Optional Android integration: pkg install termux-api, and install the matching Termux:API Android app."
echo "To save into Android shared Downloads: run termux-setup-storage, grant access, then ytgrab set-dir ~/storage/downloads"
echo "Stock Termux does not include desktop VLC's remote-control interface; ytstream may not be available."
