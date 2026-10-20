#!/usr/bin/env bash
# jlc-pcb-layout installer for macOS / Linux
# Usage: bash ./install.sh
set -euo pipefail

NAME="jlc-pcb-layout"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/$NAME"

CODEX_DIR="${CODEX_SKILLS_DIR:-$HOME/.codex/skills}"
WORKBUDDY_DIR="${WORKBUDDY_SKILLS_DIR:-$HOME/.workbuddy/skills}"

echo ""
echo "=== jlc-pcb-layout installer ==="
echo "source: $SRC"

if [ ! -f "$SRC/SKILL.md" ]; then
  echo "ERROR: SKILL.md not found under $SRC" >&2
  echo "Run this script from the directory that contains the $NAME folder." >&2
  exit 1
fi

if [ "$(head -n 1 "$SRC/SKILL.md" | tr -d '[:space:]')" != "---" ]; then
  echo "ERROR: SKILL.md does not start with YAML frontmatter delimiter '---'" >&2
  exit 1
fi

install_to() {
  local base="$1"
  local dest="$base/$NAME"
  mkdir -p "$base"
  if [ -e "$dest" ]; then
    rm -rf "$dest"
    echo "removed old: $dest"
  fi
  cp -R "$SRC" "$dest"
  find "$dest" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
  if [ -f "$dest/SKILL.md" ]; then
    echo "installed: $dest  ($(find "$dest" -type f | wc -l | tr -d ' ') files)"
  else
    echo "ERROR: copy failed, SKILL.md missing at $dest" >&2
    exit 1
  fi
}

install_to "$CODEX_DIR"
install_to "$WORKBUDDY_DIR"

echo ""
echo "=== verify ==="
for d in "$CODEX_DIR/$NAME" "$WORKBUDDY_DIR/$NAME"; do
  echo "  $d"
  if grep -qE '^name:[[:space:]]*jlc-pcb-layout[[:space:]]*$' "$d/SKILL.md"; then
    echo "    frontmatter name : ok"
  else
    echo "    frontmatter name : MISSING"
  fi
  if grep -qE '^description:[[:space:]]*[^[:space:]]' "$d/SKILL.md"; then
    echo "    frontmatter desc : ok"
  else
    echo "    frontmatter desc : MISSING"
  fi
done

echo ""
echo "Done."
echo "NEXT STEP: fully quit Codex (exit the process, not just close the window) and reopen it."
echo "Then in any chat type:  \$jlc-pcb-layout   or ask to layout a PCB in EasyEDA Pro."
echo ""
echo "Self-test:"
echo "  cd \"$CODEX_DIR/$NAME\""
echo "  python3 scripts/rules.py width --current 3 --copper 1oz"
echo "  python3 scripts/check.py examples/snapshot.demo.json --min-severity WARN"
echo ""
