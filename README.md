# hanabi ✿

A terminal dashboard for managing Claude Code agent sessions. hanabi lives in the left pane of a tmux split — your agents work in the right pane(s).

---

## Features

 Session management                                                                                     
  - Session explorer — browse tmux sessions grouped by project folder; open, rename, and kill from the sidebar                                                                                             
  - Session search — search across JSONL session history by keyword with ctrl+f; matching sessions surface with a snippet in context                                                               
  - Sessions survive restart — agents keep running in tmux; relaunch hanabi and everything reappears as-is                                                                                             
  - Session auto-close — agents can signal completion by printing `existence is pain`; hanabi kills the session automatically
                                                                                                         
  Agent monitoring
  - Live status indicators — see at a glance which agents are running, waiting for approval, or error-looping                                                                                          
  - Approval queue — pending Claude Code permission requests surface in a chronological queue; approve or deny with y/n without switching terminals                                                             
  - Todo strip — surfaces in-progress tasks parsed from the active session's agent JSONL log             
  - Watch mode — read-only overlay showing todos, subagent workers, or last output for any session
                                                                                                         
  Layout & navigation                                                                                    
  - Split modes — cycle between 1, 2 (vertical or horizontal), and 4-pane layouts with ctrl+\            
  - Pane swap — swap the TUI and terminal sides with S                                                   
  - Drag-to-resize — sidebar divider and widget handles are all draggable
  - Themes — kawaii, tron, automata (dark + light)                                                       
                                                                                                         
  Widgets                                                                                                
  - Widget pane — configurable stack of data panels on the right; each panel runs a script on an interval and renders its Rich markup output; ships with a built-in agents panel                                
                                  
  Helper                                                                                                 
  - Hanabi Helper — opens a dedicated AI session in the hanabi project directory, so any AI CLI (claude,
  etc.) immediately has full project context. Use it as a live interactive manual, build custom widgets, or tweak your dashboard through conversation.

---

## Requirements

- Python 3.10+
- [tmux](https://github.com/tmux/tmux)
- macOS (primary target; Linux should work but is untested)
- [Claude Code](https://claude.ai/code) or any terminal-based AI CLI

---

## Install

```bash
git clone https://github.com/jujubes-taut0/hanabi.git
cd hanabi
pip install -r requirements.txt
```

---

## Run

```bash
python3 main.py
```

On first run, hanabi bootstraps a tmux session (`tui-control`) with the TUI on the left and a terminal on the right. If the session already exists, it re-attaches.

---

## Using with Claude Code

hanabi is designed to work alongside Claude Code:

1. Clone the repo and `cd` into it
2. Run `python3 main.py` — hanabi opens in a tmux split
3. In the right pane, run `claude` to start a Claude Code session
4. Add your project folders to the explorer and manage sessions from the TUI

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

See `USER-MANUAL.md` for the full reference.

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

`height`: integer = fixed lines, `"1fr"` = fills remaining space. One widget should be `"1fr"`.

**Source types:**

| Source | Description |
|--------|-------------|
| `built-in:agents` | All tmux sessions with live status indicators and context % |
| `built-in:claude-limits` | Claude rate limit bars with reset countdowns |
| `command:<cmd>` | Shell command — stdout displayed, refreshed on interval |
| `file:<path>` | File contents displayed in widget |

### Custom widgets

Custom widgets are Python scripts that print [Rich markup](https://rich.readthedocs.io/en/latest/markup.html) to stdout. hanabi runs them as subprocesses and displays the output.

```python
# my_widget.py
print("[bold green]hello[/bold green] from my widget")
```

Add it to `hanabi-layout.json`:

```json
{"id": "my-widget", "title": "✦ my widget", "source": "command:python3 ~/my_widget.py", "height": 6, "refresh_secs": 60}
```

Constraints: 10s hard timeout per run. No ANSI codes — use Rich markup. Single self-contained file.

---

## Sessions Survive Restart

Closing hanabi does **not** kill your agent sessions. They keep running in tmux.

```bash
tmux ls                  # list all sessions
tmux attach -t name      # attach manually if needed
```

Relaunch `python3 main.py` and they reappear in the explorer.

---

## License

MIT © 2026 Sonia Yu
