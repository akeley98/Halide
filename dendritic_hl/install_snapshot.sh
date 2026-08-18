#!/usr/bin/env bash
# Freeze the live dendritic_hl harness into an immutable, versioned snapshot AND
# install a stable launcher symlink, so another agent can run a fixed version of
# `dh_hl` while you keep editing the dev tree.  The tool is pure-stdlib Python
# and picks its catalog per-invocation with -C, so a snapshot needs no venv and
# shares no data with the dev tree.
#
# Usage:
#   ./install_snapshot.sh ALLOW_HARNESS GUIDE_ENABLED [LABEL]
#
# ALLOW_HARNESS and GUIDE_ENABLED are booleans (1/0, true/false, yes/no, on/off)
# that get baked into the snapshot's dendritic_hl_lib/harness_config.json, which
# is what dendritic_hl_lib.allow_harness_flag.enabled and
# dendritic_hl_lib.guide_flag.enabled read their default from at import time.  No
# code editing, no grep-and-replace: the snapshot is just a frozen tree plus a
# rewritten JSON.
# LABEL (optional) defaults to a UTC timestamp.
#
# Freezes the tree to ~/.dh_hl/snapshots/<LABEL>/ and (re)points the symlink
#   ~/.local/bin/dh_hl -> <that snapshot>/dh_hl
# overwriting any previous one.  ~/.local/bin is on PATH inside Claude Code
# (Claude appends its own install dir there), so the other agent can just call
# `dh_hl` -- no root, no /usr/local/bin, no PATH edits.
set -euo pipefail

# --- parse the two boolean flag args -------------------------------------
# Accept the common spellings and normalise to JSON literals true/false.
parse_bool() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|y|t)   echo true ;;
    0|false|no|off|n|f)  echo false ;;
    *) echo "install_snapshot: invalid boolean '$1' (use 1/0, true/false)" >&2
       exit 1 ;;
  esac
}

if [[ $# -lt 2 ]]; then
  echo "usage: $0 ALLOW_HARNESS GUIDE_ENABLED [LABEL]" >&2
  echo "  ALLOW_HARNESS / GUIDE_ENABLED: 1/0, true/false, yes/no, on/off" >&2
  exit 1
fi
ALLOW_HARNESS="$(parse_bool "$1")"
GUIDE_ENABLED="$(parse_bool "$2")"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="${3:-$(date -u +%Y%m%d-%H%M%SZ)}"
DEST_ROOT="${DH_HL_SNAPSHOT_ROOT:-$HOME/.dh_hl/snapshots}"
DEST="$DEST_ROOT/$LABEL"
LINK_DIR="$HOME/.local/bin"
LINK="$LINK_DIR/dh_hl"

# --- safety rails ---------------------------------------------------------
# set -u already turns any undefined/mistyped variable into a hard error.  The
# checks below add belt-and-suspenders around the only recursive operation
# (`chmod -R a-w "$DEST"`), so no typo or hostile LABEL can ever point it at
# $HOME, /, or anything outside the snapshots root.
[[ -n "$SRC" && -d "$SRC" ]] || { echo "install_snapshot: bad SRC '$SRC'" >&2; exit 1; }
[[ -n "$DEST_ROOT" ]]        || { echo "install_snapshot: empty DEST_ROOT" >&2; exit 1; }
# LABEL must be a single bare path component -- no separators, no '.'/'..'.
case "$LABEL" in
  ""|.|..|*/*|*$'\n'*)
    echo "install_snapshot: invalid LABEL '$LABEL' (must be a bare name, no '/')" >&2; exit 1;;
esac
# DEST must be exactly a direct child of DEST_ROOT (never DEST_ROOT itself).
[[ "$DEST" == "$DEST_ROOT/$LABEL" && "$DEST" != "$DEST_ROOT" ]] || {
  echo "install_snapshot: refusing suspicious DEST '$DEST'" >&2; exit 1; }

if [[ -e "$DEST" ]]; then
  echo "install_snapshot: '$DEST' already exists (snapshots are immutable); pick another LABEL" >&2
  exit 1
fi

mkdir -p "$DEST_ROOT" "$LINK_DIR"

# Copy the whole tree but drop dev-only / regenerable / heavy dirs.  Everything
# the tool reads at runtime (code + docs + detail/ + examples/) is kept.
rsync -a \
  --exclude='.*/' \
  --exclude='__pycache__/' \
  --exclude='sandbox/' \
  --exclude='tmp_bench_tools/' \
  --exclude='human_stuff/' \
  --exclude='*~' \
  --exclude='#*#' \
  "$SRC/" "$DEST/"

# Bake the requested flag defaults into the snapshot's config.  This is the ONLY
# knob the two flag modules read at import time, so rewriting this one file is
# what freezes allow_harness_flag.enabled / guide_flag.enabled for the snapshot.
CONFIG="$DEST/dendritic_hl_lib/harness_config.json"
[[ -f "$CONFIG" ]] || { echo "install_snapshot: missing '$CONFIG' in snapshot" >&2; exit 1; }
cat > "$CONFIG" <<EOF
{
  "allow_harness": $ALLOW_HARNESS,
  "guide_enabled": $GUIDE_ENABLED
}
EOF

echo "Snapshot flags:    allow_harness=$ALLOW_HARNESS  guide_enabled=$GUIDE_ENABLED"

# Freeze it: no writes to the snapshotted harness.
chmod -R a-w "$DEST"

# (Re)install the stable launcher, overwriting any previous frozen version.
ln -sfn "$DEST/dh_hl" "$LINK"

echo "Snapshot created:  $DEST"
echo "Launcher symlink:  $LINK -> $DEST/dh_hl"
echo
echo "The other agent can now run:  dh_hl <args>"
