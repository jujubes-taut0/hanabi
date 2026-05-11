from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import Footer, Header, ListView, Static
from textual.widget import Widget

from pathlib import Path

from textual.command import Hit, Hits, Provider

from explorer_pane import ExplorerPane
from screens import ManualScreen
from widget_pane import WidgetPane, _W, load_layout, save_sidebar_width

KAWAII_THEME = Theme(
    name="kawaii", primary="#b094c4", secondary="#8b6fa8", accent="#c9b3d9",
    warning="#f9a8d4", error="#fca5a5", success="#86efac",
    surface="#1e1b24", panel="#2b2638", dark=True,
)

TRON_THEME = Theme(
    name="tron", primary="#388bfd", secondary="#30363d", accent="#58a6ff",
    warning="#d29922", error="#f85149", success="#3fb950",
    surface="#0d1116", panel="#161b22", dark=True,
    variables={"text": "#c9d1d9", "text-muted": "#8b949e"},
)

AUTOMATA_DARK_THEME = Theme(
    name="automata", primary="#8c857b", secondary="#5a5e54", accent="#a6937c",
    warning="#9c6d53", error="#9e4e44", success="#88917d",
    surface="#161514", panel="#22201e", dark=True,
    variables={"text": "#e0deda", "text-muted": "#75706b"},
)

AUTOMATA_LIGHT_THEME = Theme(
    name="automata-lt", primary="#302e2a", secondary="#5a7848", accent="#8b1a1a",
    warning="#7a5030", error="#a84830", success="#4a6838",
    surface="#e8e8e6", panel="#d8d6d2", dark=False,
)

THEMES = [
    ("kawaii ✿", "kawaii"),
    ("tron ▸", "tron"),
    ("automata ◆", "automata"),
    ("automata ◇", "automata-lt"),
]


class HanabiCommands(Provider):
    async def search(self, query: str) -> Hits:
        app = self.app
        options = [
            ("User Manual", "view the hanabi user manual", app.action_open_manual),
        ]
        matcher = self.matcher(query)
        for name, help_text, callback in options:
            score = matcher.match(name)
            if score > 0 or not query:
                yield Hit(score or 1.0, matcher.highlight(name), callback, help_text)


class _PaneDivider(Widget):
    """Draggable vertical divider between sidebar and widget pane."""
    can_focus = False

    def render(self) -> str:
        return ""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._drag_start_x: int | None = None
        self._drag_start_w: int | None = None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._drag_start_x = event.screen_x
        self._drag_start_w = self.app.query_one("#explorer-pane").size.width
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag_start_x is None:
            return
        delta = event.screen_x - self._drag_start_x
        new_w = max(20, min(80, self._drag_start_w + delta))
        self.app.query_one("#explorer-pane").styles.width = new_w
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag_start_x is None:
            return
        self.release_mouse()
        save_sidebar_width(self.app, self.app.query_one("#explorer-pane").size.width)
        self._drag_start_x = None
        self._drag_start_w = None
        event.stop()


class DashboardApp(App):
    CSS = """
    Screen { layout: vertical; }

    #app-body { height: 1fr; }
    #explorer-pane { width: 45; }
    #pane-divider { width: 1; height: 1fr; background: $panel; }
    #pane-divider:hover { background: $accent 40%; }
    #content-area { width: 1fr; height: 1fr; }

    ExplorerPane, WidgetPane { height: 1fr; }

_W { padding: 0 1; border-top: solid $panel; }
    _W:focus { border: solid $accent; }
    _W > Static:first-child { color: $accent; }
_ResizeHandle { height: 1; padding: 0 1; }
    _ResizeHandle:hover { background: $accent 20%; }

    #explorer-folder-list { height: 1fr; }
    #explorer-add-btn { width: 100%; height: auto; }
    #hanabi-ctx-btn { width: 100%; height: auto; text-align: center; background: $accent 12%; color: $accent; border: none; }
    #hanabi-ctx-btn:hover { background: $accent 25%; }

    #slot-indicator { display: none; height: 1; padding: 0 1; background: $panel; }
    #search-input { display: none; height: 3; }
    #search-input.visible { display: block; }
    #todo-strip { display: none; border-top: solid $primary; background: $surface-darken-1; }
    #todo-strip.visible { display: block; height: auto; max-height: 8; }
    #todo-log { height: 1fr; padding: 0 1; }

    #watch-overlay { display: none; height: auto; max-height: 12; border-top: solid cyan; background: $surface-darken-1; padding: 0 1; }
    #watch-overlay.visible { display: block; }

    #approvals-strip { height: 0; border-top: solid $warning; background: $surface-darken-1; color: $warning; }
    #approvals-strip.visible { height: 12; }
    #approvals-header { height: 1; padding: 0 1; }
    #approvals-list { height: 1fr; }

    ListView { height: 1fr; }
    ListView > ListItem.--highlight { background: $primary 15%; }
    Toast { background: #252525; color: #e0e0e0; }
    Toast.-information { border-left: thick #707070; }
    Toast.-warning { border-left: thick #e8a020; }
    Toast.-error { border-left: thick #d06050; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("n", "new_session", "New Session", show=True),
        Binding("ctrl+f", "search_panes", "Search", show=True),
        Binding("r", "rename_session", "Rename", show=True),
        Binding("ctrl+d", "kill_session", "Kill", show=True),
        Binding("ctrl+backslash", "split_terminal", "Split", show=True),
        Binding("u", "focus_widgets", "Widgets", show=True),
        Binding("t", "cycle_theme", "Theme", show=True),
        Binding("bracketleft", "sidebar_shrink", "◀ sidebar", show=True),
        Binding("bracketright", "sidebar_grow", "▶ sidebar", show=True),
        Binding("escape", "focus_list", "List", show=False),
        Binding("question_mark", "open_manual", "Manual", show=True),
    ]

    COMMANDS = App.COMMANDS | {HanabiCommands}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="app-body"):
            yield ExplorerPane(id="explorer-pane")
            yield _PaneDivider(id="pane-divider")
            with Vertical(id="content-area"):
                yield WidgetPane(id="pane-widgets")
        yield Footer()

    def on_mount(self) -> None:
        self._theme_idx = 0
        self._sidebar_width = load_layout().get("sidebar_width_chars", 45)
        self.register_theme(KAWAII_THEME)
        self.register_theme(TRON_THEME)
        self.register_theme(AUTOMATA_DARK_THEME)
        self.register_theme(AUTOMATA_LIGHT_THEME)
        self.theme = THEMES[0][1]
        self.title = "✿ hanabi (◡ ‿ ◡ ✿)"
        self.call_after_refresh(self._apply_sidebar_width)

    def _apply_sidebar_width(self) -> None:
        self.query_one("#explorer-pane").styles.width = self._sidebar_width

    def _resize_sidebar(self, delta: int) -> None:
        self._sidebar_width = max(20, min(80, self._sidebar_width + delta))
        self.query_one("#explorer-pane").styles.width = self._sidebar_width
        save_sidebar_width(self, self._sidebar_width)

    def action_cycle_theme(self) -> None:
        self._theme_idx = (self._theme_idx + 1) % len(THEMES)
        label, name = THEMES[self._theme_idx]
        self.theme = name
        self.notify(f"theme: {label}")

    def action_sidebar_grow(self) -> None:
        self._resize_sidebar(2)

    def action_sidebar_shrink(self) -> None:
        self._resize_sidebar(-2)

    def action_focus_widgets(self) -> None:
        widgets = list(self.query_one(WidgetPane).query(_W))
        if widgets:
            self.call_after_refresh(widgets[0].focus)

    def action_refresh(self) -> None:
        self.query_one(ExplorerPane).refresh_data()
        self.query_one(WidgetPane).refresh_data()

    def action_new_session(self) -> None:
        self.query_one(ExplorerPane).new_session()

    def action_split_terminal(self) -> None:
        self.query_one(ExplorerPane).post_message(events.Key("ctrl+backslash", "ctrl+backslash"))

    def action_rename_session(self) -> None:
        self.query_one(ExplorerPane).post_message(events.Key("r", "r"))

    def action_kill_session(self) -> None:
        self.query_one(ExplorerPane).post_message(events.Key("ctrl+d", "ctrl+d"))

    def action_search_panes(self) -> None:
        self.query_one(ExplorerPane).post_message(events.Key("ctrl+f", "ctrl+f"))

    def action_focus_list(self) -> None:
        try:
            self.query_one("#explorer-folder-list", ListView).focus()
        except Exception:
            pass

    def action_open_manual(self) -> None:
        self.push_screen(ManualScreen())



if __name__ == "__main__":
    tmux = shutil.which("tmux") or "tmux"
    right_pane = os.environ.get("TUI_RIGHT_PANE", "")

    if right_pane:
        DashboardApp().run()
    else:
        session = "tui-control"
        script = os.path.abspath(__file__)

        # Re-attach if the session already exists with a valid two-pane layout.
        existing = subprocess.run([tmux, "has-session", "-t", session], capture_output=True)
        if existing.returncode == 0:
            panes = subprocess.run(
                [tmux, "list-panes", "-t", f"{session}:0", "-F", "#{pane_index}"],
                capture_output=True, text=True,
            )
            if len(panes.stdout.strip().splitlines()) >= 2:
                os.execvp(tmux, [tmux, "attach-session", "-t", session])
            else:
                subprocess.run([tmux, "kill-session", "-t", session], capture_output=True)

        # Create session with a single window; left pane will run the TUI.
        subprocess.run(
            [tmux, "new-session", "-d", "-s", session, "-x", "220", "-y", "50"],
            check=True,
        )

        # Split horizontally: left 40% (TUI) | right 60% (agent terminal).
        subprocess.run(
            [tmux, "split-window", "-h", "-t", f"{session}:0.0", "-p", "60"],
            check=True,
        )

        right_pane_id = f"{session}:0.1"

        # Enable mouse mode and pane borders.
        subprocess.run([tmux, "set-option", "-t", session, "mouse", "on"], check=False, capture_output=True)
        subprocess.run([tmux, "set-option", "-t", session, "pane-border-style", "fg=colour240"], check=False, capture_output=True)
        subprocess.run([tmux, "set-option", "-t", session, "pane-active-border-style", "fg=colour141"], check=False, capture_output=True)
        # Keep right panes alive after their attached session dies so respawn-pane can reuse them.
        subprocess.run([tmux, "set-window-option", "-t", f"{session}:0", "remain-on-exit", "on"], check=False, capture_output=True)
        # Wire mouse drag-select to macOS clipboard so cmd+c works after selecting text.
        subprocess.run([tmux, "bind-key", "-T", "copy-mode", "MouseDragEnd1Pane", "send-keys", "-X", "copy-pipe-and-cancel", "pbcopy"], check=False, capture_output=True)
        subprocess.run([tmux, "bind-key", "-T", "copy-mode-vi", "MouseDragEnd1Pane", "send-keys", "-X", "copy-pipe-and-cancel", "pbcopy"], check=False, capture_output=True)

        # Launch TUI in left pane with env vars injected.
        subprocess.run(
            [tmux, "send-keys", "-t", f"{session}:0.0",
             f"TUI_RIGHT_PANE={shlex.quote(right_pane_id)} {shlex.quote(sys.executable)} {shlex.quote(script)}",
             "Enter"],
            check=True,
        )

        # Ensure left pane (TUI) starts focused.
        subprocess.run(
            [tmux, "select-pane", "-t", f"{session}:0.0"],
            check=False, capture_output=True,
        )

        os.execvp(tmux, [tmux, "attach-session", "-t", session])
