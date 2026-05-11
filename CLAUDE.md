# hanabi-tui — Project Brain

A Python `textual` TUI that serves as a command center for Claude Code agent sessions. The TUI lives in the left pane of a managed tmux split; agent terminals live in the right pane(s) as native tmux sessions.

---

## Agent Rules — Read Before Touching Anything

These are mandatory, not suggestions.

**Before making any change:**
1. Read the full file you're about to edit. Do not make changes from memory or context alone.
2. For any bug, grep the entire `hanabi-tui/` directory for the same pattern before fixing. Fix the pattern everywhere, not just the instance you found.

**File ownership — changes belong in exactly one place:**
- Data loading, tmux calls, JSON parsing, formatting → `data.py` only
- Modal dialogs → `screens.py` only
- Explorer pane (session list, splits, approvals, todo strip) → `explorer_pane.py`
- Widget system (widget layout, resize, reorder, sources) → `widget_pane.py`
- CSS, app-level keybindings, themes, app compose → `main.py` only
- Layout persistence (sidebar width, widget stack) → `hanabi-layout.json`

**State rules:**
- All mutable state lives as instance attributes on the widget that owns it. Never use module-level globals.
- `data.py` functions are pure — they read files or run subprocesses and return values. No state.

**Before calling a fix done:**
- Confirm the fix applies to all callers, not just the one that surfaced the symptom.
- If the fix changes a `data.py` function signature or return value, check all import sites.

---

## Files

```
hanabi-tui/
├── main.py             # App entry point, CSS, themes, keybindings, _PaneDivider, tmux bootstrap
├── data.py             # All data helpers and formatters (no textual deps)
├── explorer_pane.py    # ExplorerPane — session list, splits, approvals, todo strip
├── widget_pane.py      # WidgetPane, _W, _ResizeHandle — widget system
├── screens.py          # Modal screens — ContextMenuScreen, RenameScreen, ManualScreen
├── hanabi-layout.json  # Layout config — sidebar_width_chars + widgets[]
└── USER-MANUAL.md      # End-user keybinding and feature reference
```

---

## Run

```bash
python3 /path/to/hanabi-tui/main.py
```

On first run (no `TUI_RIGHT_PANE` set), `main.py` bootstraps the environment:
1. Creates a `tui-control` tmux session (220×50)
2. Splits it: left 40% (TUI) | right 60% (agent terminal)
3. Sets `TUI_RIGHT_PANE=tui-control:0.1`
4. Re-launches itself in the left pane with that env var set
5. Attaches to the session

When `TUI_RIGHT_PANE` is set, the Textual app runs normally and controls the right pane via `tmux switch-client`.

---

## Architecture

```
main.py (DashboardApp)
├── ExplorerPane (explorer_pane.py)     — left sidebar (width: 45 chars, draggable)
│   ├── Input                           — search/filter sessions
│   ├── Button                          — add folder
│   ├── Static#slot-indicator           — shows active slot in multi-pane mode
│   ├── ListView#explorer-folder-list   — folders + sessions + workers
│   ├── Vertical#todo-strip             — task progress (hidden unless tasks exist)
│   └── Vertical#approvals-strip        — pending approvals (hidden unless waiting)
├── _PaneDivider (main.py)              — draggable 1-char divider
└── Vertical#content-area
    └── WidgetPane (widget_pane.py)     — configurable widget stack (1fr)
        └── _W × N                     — individual widgets with resize handles

data.py  — single source of truth for all data loading and formatting
screens.py — ContextMenuScreen, RenameScreen, ManualScreen (modal overlays)
```

### How Session Switching Works

Selecting a session calls `ExplorerPane._switch_right_pane(session, right_pane, cwd, slot)`:
1. Looks up TTY for the target slot from `_pane_slots[slot]["tty"]`
2. Calls `tmux switch-client -c {pane_tty} -t {session_name}`
3. Falls back to `send-keys "unset TMUX; exec tmux attach-session -t {session}"` if no nested client yet
4. Creates the tmux session with `new-session -d` first if it doesn't exist

`_pane_slots` is a `list[dict]` — each entry is `{"tty": str, "session": str | None}`. TTY targeting survives pane swaps and reorders.

### Split Modes

`ctrl+\` cycles: `1` → `2V` → `2H` → `4`

- `1` — single right pane
- `2V` — two right panes side by side
- `2H` — two right panes stacked
- `4` — four-quadrant grid

Number keys `1`–`4` select the target slot. `S` swaps the TUI pane with the base right pane.

---

## Widget System

Widgets are defined in `hanabi-layout.json`:

```json
{
  "sidebar_width_chars": 45,
  "widgets": [
    {"id": "agents",  "title": "✦ agents",  "source": "built-in:agents",        "height": 12},
    {"id": "limits",  "title": "✦ limits",  "source": "built-in:claude-limits", "height": 13}
  ]
}
```

**Source types:**
- `built-in:agents` — agent list from `agents-status.json`
- `built-in:claude-limits` — Claude usage/cost from usage data
- `command:some shell command` — runs in a subprocess, output displayed
- `file:/path/to/file` — reads and displays a file

**Widget controls (when `_W` is focused):**
- `<` / `>` — reorder (move up/down in stack)
- `r` — rename widget
- `escape` — return focus to session list

**Drag to resize:** drag the `· · ·` handle at the bottom of each widget.

---

## Data Sources

| Source | Path | Purpose |
|--------|------|---------|
| Agent metadata | `$HANABI_BASE/config/agents-status.json` | Name, cwd, cost, context%, last prompt |
| JSONL sessions | `~/.claude/projects/{encoded_path}/*.jsonl` | Live session detection, todo parsing, search |
| tmux windows | `tmux list-windows -a` | Live/dead session detection |
| Folder list | `$HANABI_BASE/config/explorer-folders.json` | Persisted project folders |
| Layout config | `{repo}/hanabi-layout.json` | Sidebar width + widget stack |

JSONL path encoding: `~/.claude/projects/-{posix_path_with_slashes_as_hyphens}/`

---

## Status Indicators

| Indicator | Meaning |
|-----------|---------|
| `！` bold yellow | Waiting for approval — pane shows Claude Code prompt |
| `✗` bold red | Error looping — repeated error keywords in pane output |
| `✿` green | Live — claude process running in this session |
| `✧` dim | Inactive — no live session |

Folders show the aggregate status of their sessions. Workers show with `↳` indent under their orchestrator parent.

---

## Keybindings

### App-level

| Key | Action |
|-----|--------|
| `ctrl+q` | Quit |
| `ctrl+r` | Refresh all data |
| `n` | New session (in selected folder) |
| `ctrl+f` | Search session history |
| `r` | Rename selected session |
| `ctrl+d` | Kill selected session / remove folder |
| `ctrl+\` | Cycle split mode (1→2V→2H→4) |
| `u` | Focus first widget |
| `t` | Cycle theme (kawaii→tron→automata→automata-lt) |
| `[` / `]` | Shrink / grow sidebar |
| `escape` | Focus session list |
| `?` | Open user manual |
| `ctrl+p` | Command palette |

### Explorer-level

| Key | Action |
|-----|--------|
| `ctrl+f` | Toggle search input |
| `a` | Focus approvals strip (when visible) |
| `y` / `n` | Approve / deny (when approvals focused) |
| `enter` | Open session (when approvals focused) |
| `S` | Swap TUI pane with right pane |
| `1`–`4` | Select target slot (multi-pane mode) |
| `+` | Add folder |
| `ctrl+d` | Kill session or remove folder |
| `r` | Rename session or worker |

### Widget-level (when a widget is focused)

| Key | Action |
|-----|--------|
| `<` | Move widget up |
| `>` | Move widget down |
| `r` | Rename widget |
| `escape` | Return focus to session list |

---

## Known Issues

- **Palette has Textual built-ins** — can't use `COMMANDS = {HanabiCommands}` (breaks palette entirely). Currently `App.COMMANDS | {HanabiCommands}`. Need a custom provider filter to remove unwanted Textual entries.
- **Widget resize/reorder** — implemented but not fully verified in live TUI end-to-end.

---

## Design Decisions

- **Native tmux panes over embedded terminal** — `terminal.py` was deleted. Right panes are real tmux sessions. No PTY-inside-Textual. Eliminates cursor misalignment, garbling, and selection bugs.
- **TTY targeting for switch-client** — `switch-client -c {tty}` survives pane swaps; pane index targeting breaks after any reorder.
- **Splits only work from explorer focus** — intentional; keeps split control in one place.
- **Widget config in JSON** — `hanabi-layout.json` makes the widget stack user-editable without touching code.
- **`_pane_slots` list** — supports arbitrary split counts (1, 2, 4) without hardcoded pane index attributes.
- **Rename key is `r`** — consistent across sessions, workers, and widgets.
- **Widget reorder is `<`/`>` not shift+arrow** — shift+arrow is stripped by tmux before Textual sees it.

---

