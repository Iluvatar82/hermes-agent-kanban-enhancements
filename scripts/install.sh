#!/usr/bin/env bash
# Install this plugin from a local clone into one or more Hermes profiles.
# Online equivalent:
#   hermes plugins install spitefr/hermes-agent-kanban-enhancements/kanban-enhancements --enable
set -euo pipefail

HERMES_ROOT="${HERMES_ROOT:-$HOME/.hermes}"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/kanban-enhancements"
DEFAULT_ONLY="${DEFAULT_ONLY:-0}"
NO_ENABLE="${NO_ENABLE:-0}"

[ -f "$SOURCE/plugin.yaml" ] || { echo "plugin.yaml not found in $SOURCE" >&2; exit 1; }
[ -d "$HERMES_ROOT" ] || { echo "Hermes root not found: $HERMES_ROOT (set HERMES_ROOT)" >&2; exit 1; }

install_into() {  # <profile name> <root>
    local name="$1" root="$2" dest="$2/plugins/kanban-enhancements"
    mkdir -p "$root/plugins"
    rm -rf "$dest"
    cp -R "$SOURCE" "$dest"
    find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} +
    echo "installed -> $dest"
    if [ "$NO_ENABLE" != "1" ]; then
        if [ "$name" = "default" ]; then
            hermes plugins enable kanban-enhancements >/dev/null 2>&1 \
                && echo "enabled for profile default" \
                || echo "could not enable — run: hermes plugins enable kanban-enhancements"
        else
            hermes -p "$name" plugins enable kanban-enhancements >/dev/null 2>&1 \
                && echo "enabled for profile $name" \
                || echo "could not enable — run: hermes -p $name plugins enable kanban-enhancements"
        fi
    fi
}

install_into default "$HERMES_ROOT"
if [ "$DEFAULT_ONLY" != "1" ] && [ -d "$HERMES_ROOT/profiles" ]; then
    for dir in "$HERMES_ROOT"/profiles/*/; do
        [ -f "$dir/config.yaml" ] || continue
        install_into "$(basename "$dir")" "${dir%/}"
    done
fi

echo
echo "Next: hermes gateway restart   (the desktop half loads on its own)"
