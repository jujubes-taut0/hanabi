# hanabi — User Manual

Your command center for Claude Code agents and sessions.

---

## Launch

```bash
python3 /path/to/hanabi-tui/main.py
```

Does **not** need to run inside tmux. hanabi creates its own `tui-control` tmux session with a split layout and attaches you automatically.

**Layout:** left pane = hanabi TUI · right pane = your agent terminal. Selecting a session switches the right pane to show it.

---

## Overview

| Area | What it's for |
|------|--------------|
| **Explorer (left)** | Folder tree, sessions, approvals, search |
| **Widget pane (right)** | Data panels — agents, custom widgets |
| **Right terminal** | Live agent terminal, switches with your selection |

---

## Explorer

### Session list

Folders and their sessions appear in the left list.

| Symbol | Meaning |
|--------|---------|
| `✿` green | Session is live (claude running) |
| `✧` dim | Session exists, no active claude process |
| `！` yellow | Agent waiting for your input |
| `✗` red | Session in error loop |
| `↳` | Worker session (spawned by a parent session) |

Folder rows show aggregate status. Selecting any session switches the right terminal to it.

### Managing sessions

| Key | Action |
|-----|--------|
| `n` | New session in selected folder |
| `+` | Add folder |
| `r` | Rename selected session |
| `ctrl+d` | Kill session or remove folder |
| `escape` | Return focus to session list |
| right-click | Context menu (open, new, kill, remove) |

**Adding a folder:** press `+` or click `add folder` and enter a path.

**Right-click context menu:** right-click any session or folder row for a quick-action menu (open, new session, kill, remove folder).

### Search

`ctrl+f` shows the search bar. Searches JSONL session history. Matching sessions show a snippet. Empty search restores the full list.

### Split terminal

`ctrl+\` cycles right-pane layouts: **1** → **2V** → **2H** → **4** → back to 1.

When multiple panes are open, press `1`–`4` to pick which slot a session loads into. A slot indicator shows the active slot.

`S` swaps the TUI and terminal pane sides. Press `S` again to swap back.

### Approvals strip

When agents need input, a strip appears at the bottom of the explorer.

| Key | Action |
|-----|--------|
| `a` | Jump to approvals strip |
| `y` | Send `y` + Enter to focused session |
| `n` (in strip) | Send `n` + Enter to focused session |
| `enter` | Switch right pane to that session |

### Todo strip

When the selected session has an active todo list, a progress strip shows completed vs pending items above the approvals strip.

### Watch mode

Press `w` on any session to open a read-only overlay without switching the right pane.

| Key | Action |
|-----|--------|
| `w` | Enter watch mode for highlighted session |
| `↓` / `↑` | Cycle views: todos → workers → last output |
| any other key | Exit watch mode |

The overlay cycles through three views:
- **todos** — active todo list for the session
- **workers** — subagent worker sessions and their status
- **output** — last ~8 lines of terminal output

### Session auto-close

If a session outputs the signal phrase `existence is pain`, hanabi detects it on the next status poll and automatically kills that session. Useful for unattended agent runs that need to signal completion back to the TUI.

---

## Widget Pane

Press `u` to enter widget mode (focuses the first widget). `tab` cycles between widgets.

When a widget is focused (accent border visible):

| Key | Action |
|-----|--------|
| `<` | Move widget up in the stack |
| `>` | Move widget down in the stack |
| `r` | Rename widget |
| `esc` | Exit widget mode → back to explorer |
| drag `· · ·` | Resize widget height (drag the dotted bottom edge) |

Changes save to `hanabi-layout.json` immediately.

### Built-in widgets

| Widget | Source | Shows |
|--------|--------|-------|
| agents | `built-in:agents` | All tmux sessions with live indicators and context % |

### Hanabi helper

The `≽^- ˕ -^≼ hanabi helper` / `(=^･ω･^=) hanabi helper` button at the bottom of the explorer opens a dedicated `hanabi-ctx` tmux session. Launch any AI CLI there (`claude`, `gemini`, etc.) — the session starts in the hanabi-tui directory, so any AI that reads local context picks up `CLAUDE.md` and `USER-MANUAL.md` automatically.

Press the button again to toggle back to your previous session. `ctrl+d` while `hanabi-ctx` is active kills it (allows a fresh restart).

Use it as a live interactive manual — start with `claude`, then ask anything about hanabi. The session directory contains `USER-MANUAL.md` and `CLAUDE.md` so the AI has full context.

### Custom widgets

Custom widgets are Python scripts that print Rich-markup text to stdout. hanabi runs them as subprocesses on a configurable interval and displays the output.

Add a `command:` source to `hanabi-layout.json` pointing to your script.

**Output format:** Rich markup — `[bold]`, `[dim]`, `[green]`, `[red]`, etc. No ANSI codes. No interactive output.

**Constraints:** 10s hard timeout per run. Cache slow API calls to `/tmp/`. Single self-contained file.

---

## Sidebar resize

Drag the vertical divider between the explorer and widget pane. Or use `[` / `]` to shrink / grow the sidebar in steps. Width saves automatically.

---

## Keybindings — full reference

| Key | Context | Action |
|-----|---------|--------|
| `ctrl+q` | Anywhere | Quit |
| `ctrl+r` | Anywhere | Refresh all data |
| `?` | Anywhere | Open this manual |
| `u` | Anywhere | Enter widget mode |
| `t` | Anywhere | Cycle theme |
| `[` / `]` | Anywhere | Shrink / grow sidebar |
| `n` | Explorer | New session in selected folder |
| `+` | Explorer | Add folder |
| `r` | Explorer | Rename selected session |
| `ctrl+d` | Explorer | Kill session or remove folder |
| `ctrl+f` | Explorer | Toggle search |
| `ctrl+\` | Explorer | Cycle split layout (1 → 2V → 2H → 4) |
| `S` | Explorer | Swap TUI and terminal pane sides |
| `1`–`4` | Multi-pane | Select target slot |
| `w` | Explorer | Enter watch mode for highlighted session |
| `↓` / `↑` | Watch mode | Cycle views (todos / workers / output) |
| `a` | Explorer | Jump to approvals strip |
| `y` / `n` | Approvals | Approve / deny waiting agent |
| `escape` | Anywhere | Return focus to session list |
| `tab` | Widget mode | Cycle between widgets |
| `<` / `>` | Focused widget | Move widget up / down in stack |
| `r` | Focused widget | Rename widget |
| drag `· · ·` | Focused widget | Resize widget height |

---

## Sessions survive restart

Closing hanabi does **not** kill agent sessions. They keep running in tmux. Relaunch and they reappear.

```bash
tmux ls                  # list all sessions
tmux attach -t name      # attach manually
tmux kill-session -t name        # force-kill a session if hanabi is unresponsive
tmux kill-session -t tui-control # kill the entire hanabi app if it freezes

```

---

## What hanabi can't do

- VS Code terminal sessions — cannot attach to existing ones
- Non-tmux processes — only sessions hanabi created or knows about
- Multiple users — single-user, local only

---

## Layout config — hanabi-layout.json

Located at `hanabi-tui/hanabi-layout.json`. Controls sidebar width and widget stack:

```json
{
  "sidebar_width_chars": 45,
  "widgets": [
    { "id": "agents", "source": "built-in:agents",        "height": "1fr" },
    { "id": "my-widget", "source": "command:python3 ~/my_widget.py", "height": 6, "refresh_secs": 30 }
  ]
}
```

`height`: integer = fixed lines, `"1fr"` = fills remaining space. One widget should be `1fr`.

`source` types:

| Type | Example |
|------|---------|
| `built-in:agents` | All live tmux sessions with status |
| `command:<cmd>` | Run a shell command, display stdout |
| `file:<path>` | Display a file's contents |
