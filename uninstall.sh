#!/usr/bin/env bash
# Remove ytgrab. Your data in ~/.config/ytgrab is KEPT unless you pass --purge.
#   ./uninstall.sh            remove the program
#   ./uninstall.sh --purge    also delete settings, history, archive, subscriptions and backups
#   ./uninstall.sh --yes      don't ask
set -euo pipefail
PURGE=0; YES=0
for a in "$@"; do
    case "$a" in
        --purge) PURGE=1 ;;
        -y|--yes) YES=1 ;;
        -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a"; exit 2 ;;
    esac
done
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
note() { printf '  \033[2m·\033[0m %s\n' "$*"; }
echo

if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -qiE '^ytgrab( |$)'; then
    pipx uninstall ytgrab >/dev/null 2>&1 && ok "removed the pipx install"
fi
if python3 -m pip show ytgrab >/dev/null 2>&1; then
    { python3 -m pip uninstall -y ytgrab || python3 -m pip uninstall -y --break-system-packages ytgrab; } >/dev/null 2>&1 \
        && ok "removed the pip install"
fi
hash -r 2>/dev/null || true
if command -v ytgrab >/dev/null 2>&1; then
    note "a 'ytgrab' command is still on your PATH: $(command -v ytgrab)  (not installed by this tool; remove it by hand)"
fi

if [ "$PURGE" = 1 ]; then
    if [ "$YES" != 1 ]; then
        read -r -p "    Delete ALL ytgrab data (settings, history, archive, subscriptions, backups)? type 'yes': " r
        [ "$r" = yes ] || { echo "  Kept your data."; exit 0; }
    fi
    rm -rf "$HOME/.config/ytgrab" "$HOME/.config"/ytgrab.bak-* "${XDG_DATA_HOME:-$HOME/.local/share}/ytgrab"
    ok "deleted settings, history, archive, subscriptions and unpacked updates"
else
    note "kept your data in ~/.config/ytgrab  (delete with: ./uninstall.sh --purge)"
fi
echo
