#!/usr/bin/env bash
# ytgrab installer. Safe to re-run any time: it replaces an old install and keeps your data.
#
#   ./install.sh            install (replaces any previous install)
#   ./install.sh --dev      editable install: edits to this folder take effect immediately
#   ./install.sh --no-tui   skip the TUI dependency (Textual)
#   ./install.sh --yes      never ask questions
#
# Your settings, history, archive and subscriptions (~/.config/ytgrab) are backed up
# before anything is changed and are never deleted.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$HOME/.config/ytgrab"
DEV=0; TUI=1; YES=0
for a in "$@"; do
    case "$a" in
        --dev) DEV=1 ;;
        --no-tui) TUI=0 ;;
        -y|--yes) YES=1 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a (try --help)"; exit 2 ;;
    esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
note() { printf '  \033[2m·\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1; }
ask()  { [ "$YES" = 1 ] && return 0; read -r -p "    $1 [y/N] " r; [ "$r" = y ] || [ "$r" = Y ]; }

echo; echo "  ytgrab installer"; echo "  ────────────────"; echo

[ -f "$DIR/pyproject.toml" ] || die "run this from inside the ytgrab folder (pyproject.toml not found)"

# ---- requirements -------------------------------------------------------
need python3 || die "python3 is required:  sudo apt install -y python3"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "Python 3.9 or newer is required"
ok "python3 $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

if ! need ffmpeg; then
    warn "ffmpeg not found, installing…"
    need apt || die "install ffmpeg manually, then re-run"
    sudo apt update -qq && sudo apt install -y ffmpeg
fi
ok "ffmpeg"

if ! need deno && ! need node && ! need bun; then
    warn "no JavaScript runtime (deno / node / bun) found: recent yt-dlp needs one for YouTube."
    warn "install one, e.g.:  sudo apt install -y nodejs     (or see https://deno.com)"
else
    ok "JavaScript runtime"
fi

if ! need pipx; then
    warn "pipx not found, installing…"
    need apt || die "install pipx manually (https://pipx.pypa.io), then re-run"
    sudo apt install -y pipx
fi
pipx ensurepath >/dev/null 2>&1 || true
ok "pipx"

# ---- back up your data (private: it may hold your cookies) ---------------
BIN="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"; BIN="${BIN:-$HOME/.local/bin}"
if [ -d "$CONF" ]; then
    chmod 700 "$CONF" 2>/dev/null || true
    chmod 600 "$CONF/cookies.txt" 2>/dev/null || true
    BK="$CONF.bak-$(date +%Y%m%d-%H%M%S)"
    cp -a "$CONF" "$BK"
    chmod 700 "$BK"; chmod 600 "$BK/cookies.txt" 2>/dev/null || true
    # keep the three newest backups
    ls -dt "$CONF".bak-* 2>/dev/null | tail -n +4 | while read -r old; do rm -rf "$old"; done
    ok "settings & history backed up → ${BK/#$HOME/~}"
fi

# ---- install ------------------------------------------------------------
# `pipx install --force` swaps an existing pipx install in one step, so the old
# version is only touched at the moment the new one goes in.
SPEC="$DIR"; [ "$TUI" = 1 ] && SPEC="$DIR[tui]"
echo
if [ "$DEV" = 1 ]; then
    note "installing in editable (dev) mode…"
    pipx install --force -e "$SPEC" || die "install failed. Your settings are safe (backup above). Re-run to see the error."
else
    note "installing…"
    pipx install --force "$SPEC" || die "install failed. Your settings are safe (backup above). Re-run to see the error."
    rm -rf "$DIR/build"
fi

# ---- clean up older installs that could shadow the new one (only after success) ----
if python3 -m pip show ytgrab >/dev/null 2>&1; then
    note "removing previous pip install"
    python3 -m pip uninstall -y ytgrab >/dev/null 2>&1 \
        || python3 -m pip uninstall -y --break-system-packages ytgrab >/dev/null 2>&1 || true
fi
hash -r 2>/dev/null || true
STRAY="$(command -v ytgrab 2>/dev/null || true)"
if [ -n "$STRAY" ] && [ "$STRAY" != "$BIN/ytgrab" ]; then
    warn "an old 'ytgrab' command is still on your PATH: $STRAY"
    if [ -w "$(dirname "$STRAY")" ] && ask "delete it?"; then
        rm -f "$STRAY"; ok "removed $STRAY"
    else
        warn "leaving it; the new one may be shadowed. Remove it yourself if 'ytgrab --version' is wrong."
    fi
fi

# ---- verify -------------------------------------------------------------
hash -r 2>/dev/null || true
if need ytgrab; then EXE="$(command -v ytgrab)"; else EXE="$BIN/ytgrab"; fi
[ -x "$EXE" ] || die "install finished but I can't find the ytgrab command ($EXE)"
VER="$("$EXE" --version 2>&1)" || die "ytgrab was installed but failed to start: $VER"
echo; ok "installed: $VER"
need ytgrab || warn "$BIN is not on PATH yet: open a new terminal (or run: source ~/.bashrc)"

cat <<MSG

    run         ytgrab            ytgrab tui            ytgrab --help
    stream      ytstream          (watch in VLC without downloading; needs VLC installed)
    check       ytgrab doctor     ytgrab about
    update      ytgrab self-update                 (re-install from this folder, git pull first)
                ytgrab self-update new-version.zip (install from a zip)
                ytgrab update                      (just upgrade yt-dlp)
    uninstall   ${DIR/#$HOME/~}/uninstall.sh

MSG
[ "$DEV" = 1 ] && note "dev mode: edit files in ${DIR/#$HOME/~} and they run immediately, no update needed."
[ "$DEV" = 0 ] && note "after editing files here, run: ytgrab self-update   (or re-run ./install.sh)"
echo
