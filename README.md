# hanabi ✿

A terminal dashboard for managing [Claude Code](https://claude.ai/code) agent sessions. `hanabi` lives in the left pane of a `tmux` split — your agents work in the right pane(s).

```
┌──────────────────┬─────────────────────────────────┐
│  hanabi TUI      │   agent terminal                │
│  ────────────    │   ────────────                  │
│  ✦ agents        │   $ claude                      │
│  ✿ alpha         │   > help me refactor this       │
│  ！ beta         │                                 │
│  ✗ gamma         │                                 │
│  ✦ limits  ▓▓▓░  │                                 │
└──────────────────┴─────────────────────────────────┘
```

---

## Features

- **Session explorer** — browse, open, rename, and kill `tmux` sessions grouped by project folder
- **Live status indicators** — see at a glance which agents are running, waiting for approval, or error-looping
- **Split modes** — cycle between 1, 2 (vertical or horizontal), and 4-pane layouts with `ctrl+\`
- **Approval strip** — approve or deny Claude Code permission requests with `y` / `n` without leaving the TUI
- **Todo strip** — surfaces in-progress tasks parsed from agent JSONL logs
- **Watch mode** — read-only overlay showing todos, subagent workers, or last output for any session
- **Session auto-close** — agents can signal completion by printing `existence is pain`; hanabi kills the session automatically
- **Right-click context menus** — quick actions on any session or folder row
- **Hanabi helper** — one-click button that opens a dedicated `tmux` session in the hanabi directory, so any AI CLI auto-loads project context
- **Widget pane** — configurable stack of data panels (agent list, Claude usage/limits, shell commands, file watchers)
- **Custom Python widgets** — write a script that prints Rich markup, hanabi runs it on an interval and displays the output
- **Pane swap** — swap the TUI and terminal pane sides with `S`
- **Session search** — fuzzy search across JSONL session history with `ctrl+f`
- **Drag-to-resize** — sidebar divider and widget handles are all draggable
- **Sessions survive restart** — agents keep running in `tmux`; relaunch hanabi and they reappear
- **Themes** — kawaii, tron, automata (dark + light)

---

## Requirements

- **Python 3.10+**
- **[tmux](https://github.com/tmux/tmux) 3.0+** (modern `list-windows -a -F` support assumed)
- **macOS** is the primary target (uses `pbcopy` and `osascript`); Linux mostly works but isn't part of CI
- **[Claude Code](https://claude.ai/code)** or any terminal AI CLI you want to manage

---

## Install

```bash
git clone https://github.com/your-username/hanabi-tui.git
cd hanabi-tui
./setup.sh
```

`setup.sh` checks prerequisites, installs the Python dependency (`textual`), and creates the config directory at `~/.config/hanabi/config/`. If you'd rather do it manually:

```bash
pip3 install -r requirements.txt
mkdir -p ~/.config/hanabi/config
```

---

## Run

```bash
python3 main.py
```

On first run, hanabi bootstraps a `tmux` session called `tui-control` with the TUI on the left (40%) and an agent terminal on the right (60%). If the session already exists, it re-attaches.

---

## Using with Claude Code

`hanabi` is designed to work alongside Claude Code:

1. Clone the repo and `cd` into it.
2. Run `python3 main.py` — hanabi opens in a `tmux` split.
3. In the right pane, run `claude` to start a Claude Code session.
4. Add your project folders to the explorer with `+` and manage sessions from the TUI.

`CLAUDE.md` in this repo auto-loads when you run `claude` inside the directory, giving the agent full context about the codebase.

The **hanabi helper** button at the bottom of the explorer opens a dedicated `hanabi-ctx` session pre-loaded in the hanabi directory. Launch `claude` there to get AI help configuring widgets, understanding keybindings, or modifying the app.

---

## Key Bindings

| Key | Context | Action |
|-----|---------|--------|
| `ctrl+q` | Anywhere | Quit |
| `ctrl+r` | Anywhere | Refresh all data |
| `?` | Anywhere | Open user manual |
| `u` | Anywhere | Enter widget mode |
| `t` | Anywhere | Cycle theme |
| `[` / `]` | Anywhere | Shrink / grow sidebar |
| `escape` | Anywhere | Return focus to session list |
| `n` | Explorer | New session in selected folder |
| `+` | Explorer | Add folder |
| `r` | Explorer | Rename selected session |
| `ctrl+d` | Explorer | Kill session or remove folder |
| `ctrl+f` | Explorer | Toggle search |
| `ctrl+\` | Explorer | Cycle split layout (1 → 2V → 2H → 4) |
| `S` | Explorer | Swap TUI and terminal pane sides |
| `1`–`4` | Multi-pane | Select target slot |
| `w` | Explorer | Enter watch mode for highlighted session |
| `↑` / `↓` | Watch mode | Cycle views (todos / workers / output) |
| `a` | Explorer | Jump to approvals strip |
| `y` / `n` | Approvals | Approve / deny waiting agent |
| `tab` | Widget mode | Cycle between widgets |
| `<` / `>` | Focused widget | Move widget up / down in stack |
| `r` | Focused widget | Rename widget |
| drag `· · ·` | Focused widget | Resize widget height |

See [`USER-MANUAL.md`](USER-MANUAL.md) for the full reference.

---

## Status Indicators

| Indicator | Meaning |
|-----------|---------|
| `！` bold yellow | Waiting for approval — pane shows a Claude Code prompt |
| `✗` bold red | Error looping — repeated error keywords in pane output |
| `✿` green | Live — `claude` process running in this session |
| `✧` dim | Inactive — no live session |

Folders show the aggregate status of their sessions. Workers show with a `↳` indent under their orchestrator parent.

---

## Widget Configuration

Widgets are defined in `hanabi-layout.json`:

```json
{
  "sidebar_width_chars": 45,
  "widgets": [
    {"id": "agents",  "title": "✦ agents",  "source": "built-in:agents",        "height": "1fr"},
    {"id": "limits",  "title": "✦ limits",  "source": "built-in:claude-limits", "height": 8},
    {"id": "uptime",  "title": "✦ uptime",  "source": "command:uptime",         "height": 4, "refresh_secs": 30}
  ]
}
```

`height`: integer = fixed lines, `"1fr"` = fills remaining space. **Exactly one** widget should be `"1fr"`.

**Source types:**

| Source | Description |
|--------|-------------|
| `built-in:agents` | All `tmux` sessions with live status indicators and context % |
| `built-in:claude-limits` | Claude rate-limit bars with reset countdowns |
| `command:<cmd>` | Shell command — stdout displayed, refreshed on interval |
| `file:<path>` | File contents displayed in widget |

### Custom widgets

Custom widgets are scripts that print [Rich markup](https://rich.readthedocs.io/en/latest/markup.html) to stdout. hanabi runs them as subprocesses and displays the output.

```python
# my_widget.py
print("[bold green]hello[/bold green] from my widget")
```

Add it to `hanabi-layout.json`:

```json
{"id": "my-widget", "title": "✦ my widget", "source": "command:python3 ~/my_widget.py", "height": 6, "refresh_secs": 60}
```

**Constraints:** 10 s hard timeout per run. No raw ANSI codes (stripped) — use Rich markup. Output capped at ~8 KB / 40 lines.

---

## Sessions Survive Restart

Closing hanabi does **not** kill your agent sessions. They keep running in `tmux`.

```bash
tmux ls                  # list all sessions
tmux attach -t name      # attach manually if needed
```

Relaunch `python3 main.py` and they reappear in the explorer.

---

## Configuration Files

`hanabi` reads and writes a few files. All paths use `$HANABI_BASE` (default: `~/.config/hanabi`).

| Path | Purpose |
|------|---------|
| `$HANABI_BASE/config/agents-status.json` | Agent metadata (cost, context %, last prompt) — written by your agents |
| `$HANABI_BASE/config/explorer-folders.json` | Persisted folder list |
| `$HANABI_BASE/config/scripts/` | Optional user scripts |
| `~/.claude/projects/{encoded_cwd}/*.jsonl` | Claude Code session logs (created by Claude CLI) |
| `./hanabi-layout.json` | Sidebar width + widget stack (in repo root) |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HANABI_BASE` | `~/.config/hanabi` | Root for config files |
| `TUI_RIGHT_PANE` | unset | Set automatically by the bootstrap (`tui-control:0.1`) — don't set manually |

---

## Troubleshooting

**The TUI didn't bootstrap and I'm stuck in tmux.** Kill the wrapper session and try again:
```bash
tmux kill-session -t tui-control
python3 main.py
```

**Sessions show as `✧ inactive` but I know `claude` is running.** Status detection looks for the `claude` binary or version-pattern (`x.y.z`) in the session output. If you've aliased it or are running a custom wrapper, it may not register. Capture the pane to confirm:
```bash
tmux capture-pane -p -t <session-name>
```

**Widget stuck on `(timed out)`.** Custom command widgets have a 10 s hard timeout. Make the script faster, or precompute and use `file:<path>` instead.

**The right pane won't switch when I open a session.** This usually means the right pane lost its TTY. Try `ctrl+\` to cycle split mode (recreates panes), or quit and relaunch.

**`hanabi-layout.json` got corrupted.** Delete it — hanabi will recreate it with defaults on next launch.

**I want to reset everything.** Remove the config dir and the wrapper session:
```bash
tmux kill-session -t tui-control 2>/dev/null
rm -rf ~/.config/hanabi
rm hanabi-layout.json
```

---

## Testing

There is no automated test suite yet. To smoke-test changes manually:

```bash
# 1. Syntax check all modules
python3 -m py_compile main.py data.py explorer_pane.py widget_pane.py screens.py

# 2. Sanity check pure data functions (no Textual import path)
python3 -c "import data; print(data.fmt_ago('2026-05-07T00:00:00Z'))"
python3 -c "import data; print(data.SIGNAL_PHRASE, data.WAIT_KEYWORDS)"

# 3. Launch
python3 main.py
```

`data.py` is the easiest target for unit tests — pure functions, no Textual dependency. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the module map.

---

## Contributing

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the module map and design decisions, and [`CLAUDE.md`](CLAUDE.md) for the file-ownership rules every change must follow:

- Data loading / `tmux` / formatting → `data.py`
- Modal dialogs → `screens.py`
- Session list, splits, approvals → `explorer_pane.py`
- Widget system → `widget_pane.py`
- CSS, themes, app keybindings, bootstrap → `main.py`

Before calling a fix done: grep the whole repo for the same pattern, fix every instance, and confirm callers still work.

---

## License

MIT © 2026 Sonia Yu
