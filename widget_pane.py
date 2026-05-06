from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from rich.markup import escape
from textual import events

_DEBUG = os.environ.get("TUI_DEBUG") == "1"
_DEBUG_LOG = os.path.join(tempfile.gettempdir(), f"hanabi-tui-debug-{os.getuid()}.log")


def _dbg(msg: str) -> None:
    if not _DEBUG:
        return
    line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} {msg}\n"
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Static

from screens import RenameScreen

from data import (
    AGENTS_FILE,
    check_port,
    fmt_ago,
    fmt_bar,
    fmt_resets_in,
    get_tmux_windows,
    jsonl_last_mtime,
    load_agents_by_cwd,
    load_dashboards,
    session_has_claude,
)


def _sanitize_widget_output(text: str, max_lines: int = 40) -> str:
    import re
    text = re.sub(r'\x1b\[[0-9;]*[mABCDEFGHJKSTfhilmnsu]', '', text)
    if len(text) > 8192:
        text = text[:8192]
        text += "\n[dim]… output too large, truncated[/dim]"
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append("[dim]… output truncated[/dim]")
    return "\n".join(lines)

LAYOUT_FILE = Path(__file__).parent / "hanabi-layout.json"

_DEFAULT_LAYOUT: dict = {
    "sidebar_width_chars": 45,
    "widgets": [
        {"id": "agents", "source": "built-in:agents", "height": "1fr"},
        {"id": "limits", "source": "built-in:claude-limits", "height": 8},
    ],
}


def load_layout() -> dict:
    try:
        return json.loads(LAYOUT_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return _DEFAULT_LAYOUT


def save_sidebar_width(width: int) -> None:
    layout = load_layout()
    layout["sidebar_width_chars"] = width
    LAYOUT_FILE.write_text(json.dumps(layout, indent=2))


def _save_layout(layout: dict) -> None:
    LAYOUT_FILE.write_text(json.dumps(layout, indent=2))


# --- built-in data sources ---

_AGENTS_SKIP = {"tui-control", "hanabi-ctx"}

def _render_agents() -> str:
    windows = get_tmux_windows()
    agents_by_cwd = load_agents_by_cwd()

    # One entry per session — use first window's cwd as the session's cwd
    sessions: dict[str, str] = {}
    for w in windows:
        s = w["session"]
        if s not in _AGENTS_SKIP and s not in sessions:
            sessions[s] = w["cwd"]

    if not sessions:
        return "[dim]no sessions[/dim]"

    # Sort: live first, then by most recent activity descending
    def _sort_key(item: tuple[str, str]) -> tuple[bool, str]:
        s, cwd = item
        is_live = session_has_claude(s, windows)
        a = agents_by_cwd.get(cwd, {})
        ts = a.get("last_session_ts", "") or jsonl_last_mtime(cwd)
        return (is_live, ts)

    sorted_sessions = sorted(sessions.items(), key=_sort_key, reverse=True)

    lines = []
    for session_name, cwd in sorted_sessions:
        is_live = session_has_claude(session_name, windows)
        dot = "[#86efac]✿[/#86efac]" if is_live else "[dim]✧[/dim]"
        row = f"{dot} [bold]{escape(session_name)}[/bold]"
        a = agents_by_cwd.get(cwd, {})
        ts = a.get("last_session_ts", "") or jsonl_last_mtime(cwd)
        if ts:
            row += f"  [dim]{fmt_ago(ts)}[/dim]"
        lines.append(row)
        if ctx := (a.get("context_pct") or 0):
            lines.append(f"  {fmt_bar(ctx)}")
        lines.append("")
    return "\n".join(lines)


def _render_limits() -> str:
    try:
        by_type = json.loads(AGENTS_FILE.read_text()).get("claude_limits", {}).get("by_type", {})
    except (FileNotFoundError, json.JSONDecodeError):
        by_type = {}
    order = [
        ("five_hour",        "session"),
        ("seven_day",        "weekly · all"),
        ("seven_day_sonnet", "weekly · sonnet"),
        ("seven_day_opus",   "weekly · opus"),
    ]
    lines = []
    shown = False
    for key, label in order:
        e = by_type.get(key)
        if not e or not (pct := e.get("pct_used") or 0):
            continue
        t = fmt_resets_in(e.get("resets_at_iso", ""))
        lines.append(f"[dim]{label:<22}[/dim] {fmt_bar(pct)}" + (f"  [dim]resets in {t}[/dim]" if t else ""))
        shown = True
    if not shown:
        lines.append("[dim]all clear[/dim]")
    return "\n".join(lines)


def _resolve_source(source: str) -> str | None:
    if source == "built-in:agents":
        return _render_agents()
    if source == "built-in:claude-limits":
        return _render_limits()
    if source.startswith("file:"):
        p = Path(source[5:]).expanduser().resolve()
        if not str(p).startswith(str(Path.home())):
            return "[dim red]file: path outside home — blocked[/dim red]"
        return p.read_text()
    return None  # async sources (command:) handled separately


WIDGET_TEMPLATE = '''\
#!/usr/bin/env python3
"""
INSTRUCTIONS FOR THE AI FILLING IN THIS WIDGET
═══════════════════════════════════════════════
You are writing a hanabi TUI widget. hanabi runs this script as a subprocess
on a timer, captures stdout, and displays it in a sidebar panel.

HOW IT WORKS:
  - Script runs every ~15 seconds (or configured refresh_secs)
  - Everything printed to stdout appears in the widget
  - Hard timeout: 10 seconds. If the script exceeds this, it is killed and the
    widget shows "timed out". Cache any slow network/API calls to /tmp/.
  - Output height is fixed to the widget's configured height (default ~6-14
    lines). Keep output concise — the most important line should be first.

OUTPUT FORMAT — Rich markup only, no ANSI codes:
  [bold]text[/bold]      [dim]text[/dim]       [green]text[/green]
  [red]text[/red]        [yellow]text[/yellow]  [cyan]text[/cyan]
  Nest them: [bold][green]live[/green][/bold]

RULES:
  1. Print to stdout only. No input, no prompts, no interactive output.
  2. Self-contained: all imports inside this file, no hanabi modules.
  3. Never loop forever or sleep — script must exit cleanly every run.
  4. Cache slow calls: write results to /tmp/hanabi-{widget-name}-cache.json,
     check age before re-fetching (e.g. skip if <60s old).
  5. On error: print a [red]dim error line[/red] and exit — don\'t crash silently.

GOOD USES: GitHub PRs/issues, Jira tickets, Slack summaries, git status,
  disk/CPU/memory, log tails, API dashboards, countdown timers, weather.

NOT SUITABLE: anything needing >10s, streaming output, user interaction.

When done, tell the user: save the file, then in hanabi use the widget action
bar (↑ import) and point it at this file. It mounts live — no restart needed.
"""

# ── widget logic below — replace everything from here down ───────────────────

import sys

title = "my widget"
rows = [
    ("[green]✿[/green] status", "ok"),
    ("[dim]value[/dim]",        "42"),
    ("[dim]note[/dim]",         "[dim]replace this with real data[/dim]"),
]

print(f"[bold]{title}[/bold]")
print()
for label, value in rows:
    print(f"  {label:<28} {value}")
'''


# --- widget container ---

class _ResizeHandle(Static):
    """Visual-only drag handle — mouse events are handled by parent _W."""
    can_focus = False

    def __init__(self, **kw) -> None:
        super().__init__("[dim]· · · · · · · · · · · · · · ·[/dim]", markup=True, **kw)



class _W(Vertical):
    can_focus = True

    def __init__(self, cfg: dict, **kw) -> None:
        super().__init__(**kw)
        self._cfg = cfg
        self._drag_start_y: int | None = None
        self._drag_start_h: int | None = None
        self._dash_rows: list[dict] = []
        self._dash_sel: int = 0

    def compose(self) -> ComposeResult:
        wid = self._cfg["id"]
        yield Static(f"[dim]✦ {wid}[/dim]", id=f"wh-{wid}", markup=True)
        if self._cfg.get("height") == "1fr":
            with ScrollableContainer(can_focus=False):
                yield Static("", id=f"w-{wid}", markup=True)
        else:
            yield Static("", id=f"w-{wid}", markup=True)
        yield _ResizeHandle()

    def _on_handle_row(self, screen_y: int) -> bool:
        """True if the click landed on the _ResizeHandle row (screen coords)."""
        try:
            handle = self.query_one(_ResizeHandle)
            return screen_y >= handle.region.y
        except Exception:
            return False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        _dbg(f"[drag] mouse_down screen_y={event.screen_y} on_handle={self._on_handle_row(event.screen_y)} wid={self._cfg.get('id')!r}")
        if not self._on_handle_row(event.screen_y):
            return
        self._drag_start_y = event.screen_y
        # for 1fr widgets, snapshot current rendered height as the drag baseline
        h = self._cfg.get("height", 6)
        self._drag_start_h = self.size.height if h == "1fr" else int(h)
        _dbg(f"[drag] drag started screen_y={event.screen_y} start_h={self._drag_start_h}")
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag_start_y is None:
            return
        delta = event.screen_y - self._drag_start_y
        new_h = max(2, self._drag_start_h + delta)
        self._cfg["height"] = new_h
        self.styles.height = new_h
        _dbg(f"[drag] mouse_move delta={delta} new_h={new_h}")
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag_start_y is None:
            return
        _dbg(f"[drag] mouse_up — saving h={self._cfg.get('height')}")
        self.release_mouse()
        self._drag_start_y = None
        self._drag_start_h = None
        self.app.query_one(WidgetPane)._persist_layout()
        event.stop()

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(self._cfg.get("refresh_secs", 15), self._refresh)

    def on_focus(self) -> None:
        wid = self._cfg["id"]
        _dbg(f"[widget] focus wid={wid!r}")
        try:
            hint = f"[bold]✦ {wid}[/bold]  [dim]· drag · < > move · r rename · esc back[/dim]"
            self.query_one(f"#wh-{wid}", Static).update(hint)
        except Exception:
            pass

    def on_blur(self) -> None:
        wid = self._cfg["id"]
        _dbg(f"[widget] blur wid={wid!r}")
        try:
            self.query_one(f"#wh-{wid}", Static).update(f"[dim]✦ {wid}[/dim]")
        except Exception:
            pass

    def on_key(self, event) -> None:
        _dbg(f"[widget] key={event.key!r} wid={self._cfg.get('id')!r}")
        if self._cfg.get("source") == "built-in:dashboards" and self._dash_rows:
            if event.key in ("up", "k"):
                self._dash_sel = max(0, self._dash_sel - 1)
                self._render_dash(); event.stop(); return
            if event.key in ("down", "j"):
                self._dash_sel = min(len(self._dash_rows) - 1, self._dash_sel + 1)
                self._render_dash(); event.stop(); return
            if event.key == "enter":
                url = self._dash_rows[self._dash_sel]["data"].get("url", "")
                if url:
                    subprocess.Popen(["open", url])
                event.stop(); return
            if event.key == "s":
                asyncio.create_task(self._start_dashboard_server())
                event.stop(); return
        if event.key == "less_than_sign":
            self.app.query_one(WidgetPane)._move_widget(self, -1); event.stop()
        elif event.key == "greater_than_sign":
            self.app.query_one(WidgetPane)._move_widget(self, +1); event.stop()
        elif event.key == "r":
            self.app.push_screen(
                RenameScreen(self._cfg["id"]),
                lambda name: self.app.query_one(WidgetPane)._rename_widget(self, name),
            )
            event.stop()
        elif event.key == "escape":
            try:
                self.app.query_one("#explorer-folder-list").focus()
            except Exception:
                pass
            event.stop()

    def _refresh(self) -> None:
        src = self._cfg.get("source", "")
        wid = self._cfg["id"]
        try:
            if src.startswith("command:"):
                asyncio.create_task(self._run_cmd(wid, src[8:]))
            elif src == "built-in:dashboards":
                asyncio.create_task(self._refresh_builtin_dashboards())
            else:
                result = _resolve_source(src)
                self._set(wid, result if result is not None else f"[dim]unknown source: {src}[/dim]")
        except Exception as e:
            self._set(wid, f"[dim red]{e}[/dim red]")

    async def _refresh_builtin_dashboards(self) -> None:
        wid = self._cfg["id"]
        try:
            def fetch():
                rows, err = load_dashboards()
                if err:
                    return None, err
                result = []
                for d in rows:
                    if d.get("type") == "component":
                        continue
                    result.append({"data": d, "live": check_port(d.get("port", ""))})
                return result, None
            checked, err = await asyncio.to_thread(fetch)
            if err:
                self._set(wid, f"[dim red]{err}[/dim red]")
                return
            self._dash_rows = checked
            self._dash_sel = min(self._dash_sel, max(0, len(self._dash_rows) - 1))
            self._render_dash()
        except Exception as e:
            self._set(wid, f"[dim red]{e}[/dim red]")

    def _render_dash(self) -> None:
        from collections import defaultdict as _dd
        by_project: dict = _dd(list)
        for row in self._dash_rows:
            by_project[row["data"].get("project", "?")].append(row)
        lines = []
        idx = 0
        for project, entries in by_project.items():
            lines.append(f"[bold]{escape(project)}[/bold]")
            for row in entries:
                d, live = row["data"], row["live"]
                cursor = "▸" if idx == self._dash_sel else " "
                title = escape(d.get("title") or d.get("project", "?"))
                port = d.get("port", "")
                if port:
                    dot = "[green]✿[/green]" if live else "[dim]✧[/dim]"
                    status = "[green]live[/green]" if live else "[dim]stopped[/dim]"
                    lines.append(f"  {cursor}{dot} {title}  [dim]:{port}[/dim]  {status}")
                else:
                    lines.append(f"  {cursor}[dim]○[/dim] {title}  [dim](file)[/dim]")
                idx += 1
            lines.append("")
        self._set(self._cfg["id"], "\n".join(lines))

    async def _start_dashboard_server(self) -> None:
        if not self._dash_rows:
            return
        row = self._dash_rows[self._dash_sel]
        d = row["data"]
        launch = d.get("launch", "")
        if not launch:
            self.app.notify("no launch command configured", severity="warning")
            return
        project = d.get("project", "unknown")
        title = d.get("title") or project
        cwd = d.get("cwd") or str(Path.home())
        session_name = f"dash-{project}"
        tmux = shutil.which("tmux") or "tmux"
        r = await asyncio.to_thread(subprocess.run, [tmux, "has-session", "-t", session_name], capture_output=True)
        if r.returncode == 0:
            self.app.notify(f"already running → {session_name}")
            return
        await asyncio.to_thread(subprocess.run, [tmux, "new-session", "-d", "-s", session_name, "-c", cwd], capture_output=True)
        await asyncio.to_thread(subprocess.run, [tmux, "send-keys", "-t", session_name, launch, "Enter"], capture_output=True)
        self.app.notify(f"starting {escape(title)} → {session_name}")
        await asyncio.sleep(1.0)
        asyncio.create_task(self._refresh_builtin_dashboards())

    def _set(self, wid: str, text: str) -> None:
        try:
            self.query_one(f"#w-{wid}", Static).update(text)
        except Exception:
            pass

    async def _run_cmd(self, wid: str, cmd: str) -> None:
        try:
            r = await asyncio.wait_for(
                asyncio.to_thread(subprocess.run, cmd, shell=True, capture_output=True, text=True),
                timeout=10,
            )
            if r.stdout:
                out = _sanitize_widget_output(r.stdout)
            else:
                out = "[dim](no output)[/dim]"
            self._set(wid, out)
        except asyncio.TimeoutError:
            self._set(wid, "[dim red]timed out[/dim red]")
        except Exception as e:
            self._set(wid, f"[dim red]{e}[/dim red]")


# --- pane ---

class WidgetPane(Vertical):
    can_focus = False

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._layout = load_layout()

    def compose(self) -> ComposeResult:
        for cfg in self._layout.get("widgets", []):
            yield _W(cfg, id=f"widget-{cfg['id']}")

    def on_mount(self) -> None:
        for cfg in self._layout.get("widgets", []):
            h = cfg.get("height", "1fr")
            try:
                w = self.query_one(f"#widget-{cfg['id']}")
                w.styles.height = "1fr" if h == "1fr" else int(h)
            except Exception:
                pass

    def _add_widget_after(self, source: str, widget_id: str, after: "_W | None") -> None:
        layout = load_layout()
        existing_ids = {w["id"] for w in layout.get("widgets", [])}
        if widget_id in existing_ids:
            widget_id = f"{widget_id}-{len(existing_ids)}"
        cfg = {"id": widget_id, "source": source, "height": 6, "refresh_secs": 30}
        widgets_list = layout.setdefault("widgets", [])
        if after is not None:
            idx = next((i for i, w in enumerate(widgets_list) if w["id"] == after._cfg["id"]), len(widgets_list) - 1)
            widgets_list.insert(idx + 1, cfg)
        else:
            widgets_list.append(cfg)
        LAYOUT_FILE.write_text(json.dumps(layout, indent=2))
        new_w = _W(cfg, id=f"widget-{widget_id}")
        if after is not None:
            self.mount(new_w, after=after)
        else:
            self.mount(new_w)
        new_w.styles.height = 6
        self.app.notify(f"widget '{widget_id}' added", timeout=5)

    def _rename_widget(self, target: "_W", new_name: str | None) -> None:
        if not new_name or new_name == target._cfg["id"]:
            return
        old_id = target._cfg["id"]
        target._cfg["id"] = new_name
        try:
            target.query_one(f"#wh-{old_id}", Static).id = f"wh-{new_name}"
            target.query_one(f"#w-{old_id}", Static).id = f"w-{new_name}"
            target.id = f"widget-{new_name}"
            target.query_one(f"#wh-{new_name}", Static).update(f"[dim]✦ {new_name}[/dim]")
        except Exception:
            pass
        self._persist_layout()

    def _persist_layout(self) -> None:
        widgets = list(self.query(_W))
        self._layout["widgets"] = [w._cfg for w in widgets]
        _save_layout(self._layout)

    async def _remount_widgets(self, focus_idx: int | None = None) -> None:
        for w in list(self.query(_W)):
            await w.remove()
        for cfg in self._layout.get("widgets", []):
            w = _W(cfg, id=f"widget-{cfg['id']}")
            await self.mount(w)
            h = cfg.get("height", "1fr")
            w.styles.height = "1fr" if h == "1fr" else int(h)
        if focus_idx is not None:
            widgets = list(self.query(_W))
            if focus_idx < len(widgets):
                widgets[focus_idx].focus()

    def _move_widget(self, target: "_W", direction: int) -> None:
        widgets = list(self.query(_W))
        idx = widgets.index(target)
        new_idx = max(0, min(len(widgets) - 1, idx + direction))
        if new_idx == idx:
            return
        cfgs = self._layout["widgets"]
        cfgs[idx], cfgs[new_idx] = cfgs[new_idx], cfgs[idx]
        _save_layout(self._layout)
        asyncio.create_task(self._remount_widgets(focus_idx=new_idx))

    def refresh_data(self) -> None:
        for w in self.query(_W):
            w._refresh()
