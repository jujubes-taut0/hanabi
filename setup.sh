#!/usr/bin/env bash
# hanabi-tui first-time setup
#
# Verifies prerequisites, installs Python dependencies, and creates the
# config directory layout. Safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HANABI_BASE="${HANABI_BASE:-$HOME/.config/hanabi}"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
err()   { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

fail() {
  err "$1"
  exit 1
}

# ---- 1. Prerequisites ----
bold "Checking prerequisites..."

# Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 not found. Install Python 3.10 or newer."
fi

PY_VERSION="$(python3 -c 'import sys; print("{}.{}".format(sys.version_info[0], sys.version_info[1]))')"
PY_MAJOR="${PY_VERSION%.*}"
PY_MINOR="${PY_VERSION#*.}"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  fail "Python 3.10+ required (found $PY_VERSION)."
fi
ok "Python $PY_VERSION"

# tmux
if ! command -v tmux >/dev/null 2>&1; then
  fail "tmux not found. Install with 'brew install tmux' (macOS) or your package manager."
fi
TMUX_VERSION="$(tmux -V | awk '{print $2}')"
ok "tmux $TMUX_VERSION"

# pip
if ! python3 -m pip --version >/dev/null 2>&1; then
  fail "python3 -m pip not available. Install pip for your Python distribution."
fi
ok "pip available"

# Optional: claude CLI
if command -v claude >/dev/null 2>&1; then
  ok "claude CLI detected ($(claude --version 2>/dev/null | head -n 1 || echo 'version unknown'))"
else
  warn "claude CLI not found — install Claude Code if you want managed agent sessions"
fi

echo

# ---- 2. Python dependencies ----
bold "Installing Python dependencies..."

REQS="$SCRIPT_DIR/requirements.txt"
if [ ! -f "$REQS" ]; then
  fail "requirements.txt not found at $REQS"
fi

if python3 -m pip install -r "$REQS" --quiet --disable-pip-version-check; then
  ok "Installed: $(tr '\n' ' ' < "$REQS")"
else
  fail "pip install failed. Try 'pip3 install -r requirements.txt' manually."
fi

echo

# ---- 3. Config directory ----
bold "Creating config directory..."

mkdir -p "$HANABI_BASE/config"
mkdir -p "$HANABI_BASE/config/scripts"
ok "$HANABI_BASE/config/"

# Seed empty config files so first launch doesn't have to create them
for f in agents-status.json explorer-folders.json dashboards.json; do
  target="$HANABI_BASE/config/$f"
  if [ ! -f "$target" ]; then
    case "$f" in
      agents-status.json)     echo '{"agents": []}'        > "$target" ;;
      explorer-folders.json)  echo '{"folders": []}'       > "$target" ;;
      dashboards.json)        echo '{"dashboards": []}'    > "$target" ;;
    esac
    ok "Seeded $f"
  else
    ok "$f exists, leaving alone"
  fi
done

echo

# ---- 4. Layout file ----
bold "Checking layout config..."

LAYOUT="$SCRIPT_DIR/hanabi-layout.json"
if [ -f "$LAYOUT" ]; then
  ok "hanabi-layout.json exists"
else
  cat > "$LAYOUT" <<'JSON'
{
  "sidebar_width_chars": 45,
  "widgets": [
    {"id": "agents", "title": "✦ agents", "source": "built-in:agents",        "height": "1fr"},
    {"id": "limits", "title": "✦ limits", "source": "built-in:claude-limits", "height": 8}
  ]
}
JSON
  ok "Created default hanabi-layout.json"
fi

echo

# ---- 5. Smoke test ----
bold "Running smoke test..."

if python3 -m py_compile \
  "$SCRIPT_DIR/main.py" \
  "$SCRIPT_DIR/data.py" \
  "$SCRIPT_DIR/explorer_pane.py" \
  "$SCRIPT_DIR/widget_pane.py" \
  "$SCRIPT_DIR/screens.py"; then
  ok "All modules compile cleanly"
else
  fail "Syntax errors detected. See output above."
fi

if python3 -c "import data" 2>/dev/null; then
  ok "data.py imports cleanly"
else
  warn "data.py import failed — check for missing dependencies"
fi

echo
bold "Setup complete!"
echo
echo "Next steps:"
echo "  1. Run hanabi:        python3 main.py"
echo "  2. Open the manual:   press '?' inside the TUI"
echo "  3. Read the docs:     README.md, ARCHITECTURE.md, USER-MANUAL.md"
echo
echo "If the TUI gets wedged, recover with:"
echo "  tmux kill-session -t tui-control"
echo
