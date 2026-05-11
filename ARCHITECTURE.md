# Architecture

This document describes how `hanabi-tui` is wired together. It is intended for contributors and curious users — for end-user features and keybindings, see [README.md](README.md) and [USER-MANUAL.md](USER-MANUAL.md).

---

## High-level model

`hanabi-tui` is a [Textual](https://textual.textualize.io/) app that lives in the **left pane** of a managed `tmux` split. Agent terminals run as **real tmux sessions** in the right pane(s) — there is no embedded PTY. The TUI controls the right pane(s) by issuing `tmux switch-client` and `tmux respawn-pane` commands targeted at the right pane's TTY.

```
┌──────────────────────────────────────────────────────────┐
│ tmux session: tui-control                                │
│ ┌──────────────────┬─────────────────────────────────┐   │
│ │                  │                                 │   │
│ │  hanabi TUI      │   agent tmux session            │   │
│ │  (Textual)       │   (claude, shell, etc.)         │   │
│ │  pane 0.0        │   pane 0.1 (or split further)   │   │
│ │                  │                                 │   │
│ └──────────────────┴─────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

This decision (no embedded terminal) eliminates an entire class of bugs: cursor misalignment, garbled output, broken text selection, and PTY size desync. The trade-off is that every right-pane control is a `tmux` command rather than an in-process call.

---

## Module layout

```
hanabi-tui-public/
├── main.py             # App entry, CSS, themes, app keybindings, tmux bootstrap
├── data.py             # Pure data helpers — file I/O, tmux subprocess, formatters
├── explorer_pane.py    # ExplorerPane — session list, splits, approvals, todos, watch mode
├── widget_pane.py      # WidgetPane, _W, _ResizeHandle — widget stack
├── screens.py          # Modal overlays — ContextMenu, Rename, Manual
├── hanabi-layout.json  # Persisted layout (sidebar width + widget stack)
├── requirements.txt    # Python dependencies
├── setup.sh            # First-time setup script
├── README.md           # User-facing docs
├── USER-MANUAL.md      # Full keybinding/feature reference
├── ARCHITECTURE.md     # This file
└── CLAUDE.md           # Agent-facing project brain (loaded by Claude Code)
```

### File ownership rules

Changes belong in **exactly one place**:

| Concern | Owner |
|---|---|
| Data loading, tmux subprocess calls, JSON parsing, formatting | `data.py` only |
| Modal dialogs | `screens.py` only |
| Session list, splits, approvals, todo strip | `explorer_pane.py` only |
| Widget layout, resize, reorder, sources | `widget_pane.py` only |
| CSS, app-level keybindings, themes, app compose | `main.py` only |
| Persisted layout (sidebar width, widget stack) | `hanabi-layout.json` |

`data.py` functions are **pure** — they read files or run subprocesses and return values. No state. All mutable state lives as instance attributes on the widget that owns it; there are no module-level globals.

---

## Module-by-module

### `main.py` — `DashboardApp`

- **`DashboardApp`** (Textual `App`) — root app. Composes `ExplorerPane` + `_PaneDivider` + `WidgetPane`.
- **`_PaneDivider`** — 1-char vertical bar that drag-resizes the sidebar (also bound to `[` / `]`).
- **`HanabiCommands`** — command palette provider for hanabi-specific actions.
- **Tmux bootstrap** (`main.py:263-317`):
  1. If `tui-control` already exists with ≥2 panes, attach and exit.
  2. Else `new-session -d -s tui-control -x 220 -y 50`.
  3. `split-window -h -t tui-control:0.0 -p 60` (right pane = 60%).
  4. Configure: `set -g mouse on`, `set-window-option remain-on-exit on`, bind `MouseDragEnd1Pane` → `pbcopy` (macOS clipboard).
  5. Launch TUI in left pane: `send-keys "TUI_RIGHT_PANE=tui-control:0.1 python3 {script}" Enter`.
  6. `attach-session -t tui-control`.
- **CSS / themes** — kawaii, tron, automata (dark + light) registered on mount.
- **App-level keybindings** — `ctrl+q`, `ctrl+r`, `n`, `u`, `?`, `t`, `[`, `]`, `ctrl+\`, etc.
- **Sidebar resize** — drag (mouse) or `[` / `]` (keyboard); width clamped to `[20, 80]` chars.

### `data.py` — pure data layer

No Textual imports. Functions read files / spawn subprocesses / format strings.

- **Constants** — `BASE`, `AGENTS_FILE`, `FOLDERS_FILE`, `DASHBOARDS_FILE`, `CLAUDE_PROJECTS`, `SCRIPTS_DIR`, `WAIT_KEYWORDS`, `ERROR_KEYWORDS`, `SIGNAL_PHRASE`.
- **Loaders** — `load_agents()`, `load_folders()`.
- **Tmux** — `get_tmux_windows()`, `session_has_claude()`, `capture_pane()`, kill/rename helpers.
- **Status detection** — `pane_is_waiting()`, `pane_is_error_looping()`, `pane_has_signal_phrase()`, `extract_question()`.
- **JSONL** — `load_chat_messages()`, `parse_todo_items()`, `find_jsonl_for_cwd()` using path encoding `Path(cwd).as_posix().lstrip("/").replace("/", "-")`.
- **Formatters** — `fmt_bar()`, `fmt_ago()`.

### `explorer_pane.py` — `ExplorerPane`

The biggest module. Owns the session tree, splits, approvals strip, todo strip, search, and watch mode.

Key state (instance attrs):
- `folders`, `list_items`, `windows` — UI tree data
- `waiting_status`, `live_status`, `error_status`, `pane_text` — derived from `_poll_status()`
- `_pane_slots: list[dict]` — `[{"tty": str, "session": str | None}]`, one entry per right pane (1, 2, or 4)
- `_search_mode`, `_search_query`, `_search_results` — async JSONL search
- `_recently_exited` — short-lived dict of just-killed sessions, expires after one poll

Key methods:
- `_build_list_items()` — rebuilds the explorer tree (folders → sessions → workers)
- `_poll_status()` — runs every **8 seconds**: captures panes, derives waiting/live/error/signal states
- `_mount_session()` → `_switch_right_pane()` — the core "open this session in pane X" path
- `_apply_split_mode()` — implements `1` / `2V` / `2H` / `4` layouts via `tmux split-window`
- `_update_approvals_strip()`, `_update_todo_strip()` — bottom strips, hidden when empty
- Watch mode (`w`) — read-only overlay cycling todos / workers / last output via ↑ / ↓

### `widget_pane.py` — widget stack

- **`_ResizeHandle`** — `· · ·` bar at the bottom of each `_W`; drag resizes the widget.
- **`_W`** — single widget container. Reads `{id, title, source, height, refresh_secs}` from layout JSON.
  - Source dispatch:
    - `built-in:agents` — list of live tmux sessions with status & context %
    - `built-in:claude-limits` — Claude rate-limit bars
    - `command:<shell>` — async subprocess, **10 s hard timeout**, ANSI-stripped, capped at 8192 chars / 40 lines
    - `file:<path>` — file contents (home-dir-only)
  - Height: integer (fixed lines) or `"1fr"` (fills remainder); exactly one widget should be `"1fr"`.
  - Refresh: `set_interval(refresh_secs, self._refresh)`, default 15 s.
- **`WidgetPane`** — container. Handles add/remove/reorder, widget focus, layout persistence.
- **`_persist_layout()`** — writes `hanabi-layout.json` immediately after any drag, reorder, or rename.

### `screens.py` — modal overlays

- **`ContextMenuScreen`** — right-click menu; options vary by row type (session vs folder).
- **`RenameScreen`** — input dialog; reused for sessions, workers, and widgets.
- **`ManualScreen`** — scrollable display of `USER-MANUAL.md`.

---

## Configuration & state

| Path | Purpose | Owner |
|---|---|---|
| `$HANABI_BASE/config/agents-status.json` | Agent metadata (cost, context %, last prompt, timestamp) | Written by agents, read by hanabi |
| `$HANABI_BASE/config/explorer-folders.json` | Persisted folder list | hanabi |
| `$HANABI_BASE/config/scripts/` | Optional user scripts | User |
| `~/.claude/projects/{encoded_cwd}/*.jsonl` | Claude Code session logs | Claude CLI |
| `{repo}/hanabi-layout.json` | Sidebar width + widget stack | hanabi |

`HANABI_BASE` defaults to `~/.config/hanabi` (`data.py:18`).

JSONL path encoding: drop the leading `/`, replace remaining `/` with `-`. Example: `/Users/me/code/foo` → `~/.claude/projects/Users-me-code-foo/`.

---

## Tmux integration

### Bootstrap

See `main.py:263-317`. Key invariants:

- `tui-control` is the wrapper session. It always contains the TUI (pane `0.0`) and at least one right pane.
- `TUI_RIGHT_PANE` is set to the **base** right pane target (e.g., `tui-control:0.1`). When unset, `main.py` performs the bootstrap and re-exec.

### Switching the right pane

`ExplorerPane._switch_right_pane(session, right_pane, cwd, slot)` (`explorer_pane.py:414`):

1. Look up the TTY from `_pane_slots[slot]["tty"]`.
2. `tmux switch-client -c {pane_tty} -t {session_name}` — TTY-targeted so it survives pane swaps and reorders.
3. If no nested client yet, fall back to `respawn-pane -k -t {pane_id} "env -u TMUX tmux attach-session -t {session}"`.
4. If the session doesn't exist, `new-session -d -s {session_name} -c {cwd}` first.

### Split modes

`ctrl+\` cycles `1 → 2V → 2H → 4` (`explorer_pane.py:320-389`). Number keys `1`–`4` select the active slot. `S` swaps the TUI pane with the base right pane.

`_pane_slots` is a list of `{"tty", "session"}` dicts — one entry per right pane. TTY targeting (rather than pane index) survives reorders and swaps.

---

## Status detection

| State | Detection | Indicator |
|---|---|---|
| **Live** | `claude` process or version-pattern `^\d+\.\d+\.\d+` running in the session | `✿` green |
| **Waiting** | Pane output contains any of `WAIT_KEYWORDS` | `！` bold yellow |
| **Error looping** | Pane output matches ≥2 of `ERROR_KEYWORDS` | `✗` bold red |
| **Inactive** | None of the above | `✧` dim |
| **Done (auto-kill)** | Pane output contains `existence is pain` | session killed on next poll |

The poll interval is **8 seconds** (`explorer_pane.py:105`). `data.py` exposes the detection primitives; `ExplorerPane._poll_status()` orchestrates them and updates the visible indicators.

---

## Widget system internals

- Layout JSON shape:
  ```json
  {
    "sidebar_width_chars": 45,
    "widgets": [
      {"id": "agents", "title": "✦ agents", "source": "built-in:agents", "height": "1fr"},
      {"id": "uptime", "title": "✦ uptime", "source": "command:uptime", "height": 4, "refresh_secs": 30}
    ]
  }
  ```
- Widget IDs are deduped automatically (`-2`, `-3`, …) on collision (`widget_pane.py:487`).
- Drag-to-resize: mousedown on the handle snapshots `_drag_start_y` / `_drag_start_h`; mousemove updates `height`; mouseup persists.
- Custom command widgets are sandboxed only loosely — **10 s timeout, ANSI stripped, output capped**. Don't run untrusted scripts.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `HANABI_BASE` | `~/.config/hanabi` | Root for config files |
| `TUI_RIGHT_PANE` | unset | Set by bootstrap to `tui-control:0.1`; presence signals "in tmux, run normally" |

---

## Design decisions

- **Native tmux panes over embedded terminal.** `terminal.py` was deleted. Right panes are real tmux sessions. Eliminates cursor misalignment, garbling, and selection bugs.
- **TTY targeting for `switch-client`.** `switch-client -c {tty}` survives pane swaps; pane-index targeting breaks after any reorder.
- **Splits only work from explorer focus.** Intentional — keeps split control in one place.
- **Widget config in JSON.** `hanabi-layout.json` makes the widget stack user-editable without touching code.
- **`_pane_slots` as a list.** Supports arbitrary split counts (1, 2, 4) without hardcoded pane index attributes.
- **Rename key is `r`** across sessions, workers, and widgets.
- **Widget reorder is `<` / `>`** (not shift+arrow — tmux strips that before Textual sees it).
- **`data.py` is pure.** No state, no Textual imports. Easy to test in isolation.

---

## Known issues

- **Command palette includes Textual built-ins.** Using `COMMANDS = {HanabiCommands}` breaks the palette; we use `App.COMMANDS | {HanabiCommands}` and accept the noise. A custom provider filter is the planned fix.
- **Widget resize/reorder** — implemented but not yet end-to-end verified in the live TUI under all split modes.

---

## Testing

The codebase has no automated test suite yet. `data.py` is the easiest target — pure functions, no Textual dependency. See [README.md → Testing](README.md#testing) for current ad-hoc verification steps.
