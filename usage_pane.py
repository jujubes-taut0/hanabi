from __future__ import annotations

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import Static

from data import (
    AGENTS_FILE,
    agent_status,
    fmt_ago,
    fmt_bar,
    fmt_resets_in,
    get_tmux_sessions,
    load_agents_by_cwd,
    load_folders,
)


class UsagePane(Vertical):
    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            yield Static("", id="usage-agents")
            yield Static("", id="usage-limits")

    def on_mount(self) -> None:
        self.refresh_data()
        self.set_interval(15, self.refresh_data)

    def refresh_data(self) -> None:
        folders = load_folders()
        agents_by_cwd = load_agents_by_cwd()
        live_sessions = set(get_tmux_sessions())

        try:
            raw = json.loads(AGENTS_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            raw = {}

        self._render_agents(folders, agents_by_cwd, live_sessions)
        self._render_limits(raw.get("claude_limits", {}))

    def _render_agents(
        self,
        folders: list[str],
        agents_by_cwd: dict[str, dict],
        live_sessions: set[str],
    ) -> None:
        from data import assign_sessions_to_folders, get_tmux_windows
        from rich.markup import escape

        windows = get_tmux_windows()
        sessions_by_folder = assign_sessions_to_folders(folders, windows)

        lines = ["[dim]◈ agents[/dim]\n"]
        for folder_path in folders:
            path = Path(folder_path)
            agent = agents_by_cwd.get(folder_path, {})
            sessions = sessions_by_folder.get(folder_path, [])
            is_live = any(s in live_sessions for s in sessions)

            dot = "[#86efac]✿[/#86efac]" if is_live else "[dim]✧[/dim]"
            name = escape(path.name)
            ts = agent.get("last_session_ts", "")
            ctx_pct = agent.get("context_pct") or 0

            row = f"{dot} [bold]{name}[/bold]"
            if ts:
                row += f"  [dim]{fmt_ago(ts)}[/dim]"
            lines.append(row)

            if ctx_pct > 0:
                lines.append(f"  {fmt_bar(ctx_pct)}")
            lines.append("")

        self.query_one("#usage-agents", Static).update("\n".join(lines))

    def _render_limits(self, limits: dict) -> None:
        by_type = limits.get("by_type", {})
        order = [
            ("five_hour",        "session"),
            ("seven_day",        "weekly · all"),
            ("seven_day_sonnet", "weekly · sonnet"),
            ("seven_day_opus",   "weekly · opus"),
        ]
        lines = ["[bold]✦ Claude Limits[/bold]\n"]
        any_shown = False
        for key, label in order:
            entry = by_type.get(key)
            if not entry:
                continue
            pct = entry.get("pct_used", 0) or 0
            if pct == 0:
                continue
            t = fmt_resets_in(entry.get("resets_at_iso", ""))
            reset_str = f"  [dim]resets in {t}[/dim]" if t else ""
            lines.append(f"[dim]{label:<22}[/dim] {fmt_bar(pct)}{reset_str}")
            any_shown = True
        if not any_shown:
            lines.append("[dim]all clear[/dim]")

        self.query_one("#usage-limits", Static).update("\n".join(lines))
