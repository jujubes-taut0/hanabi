from __future__ import annotations

from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Markdown


class ContextMenuScreen(ModalScreen):
    CSS = """
    ContextMenuScreen { align: center middle; background: transparent; }
    #ctx-popup {
        width: 28; height: auto;
        background: $panel; border: solid $accent; padding: 0;
    }
    #ctx-popup Button {
        width: 100%; height: 1; border: none; padding: 0 1;
        background: transparent; content-align: left middle;
    }
    #ctx-popup Button:focus { background: $accent; }
    #ctx-popup Button.danger { color: $error; }
    """

    def __init__(self, item: dict) -> None:
        super().__init__()
        self._item = item

    def compose(self) -> ComposeResult:
        with Vertical(id="ctx-popup"):
            if self._item["type"] == "session":
                yield Button("Open terminal", id="ctx-open")
                yield Button("New session here", id="ctx-new")
                yield Button("Kill session", id="ctx-kill", classes="danger")
            else:
                yield Button("New session", id="ctx-new")
                yield Button("Remove from list", id="ctx-remove", classes="danger")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(None)

    def on_click(self, event: events.Click) -> None:
        if not self.query_one("#ctx-popup").region.contains(event.screen_x, event.screen_y):
            self.dismiss(None)


class RenameScreen(ModalScreen):
    CSS = """
    RenameScreen { align: center middle; background: transparent; }
    #rename-popup {
        width: 44; height: auto;
        background: $panel; border: solid $accent; padding: 1 2;
    }
    #rename-popup Label { margin-bottom: 1; }
    """

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self._current = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-popup"):
            yield Label(f"rename: [bold]{self._current}[/bold]")
            yield Input(value=self._current, id="rename-input")

    def on_mount(self) -> None:
        inp = self.query_one("#rename-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(None)


class ManualScreen(ModalScreen):
    CSS = """
    ManualScreen { align: center middle; background: $background 70%; }
    #manual-popup { width: 80%; height: 85%; background: $panel; border: solid $accent; }
    #manual-scroll { height: 1fr; padding: 0 1; }
    #manual-close { width: 100%; height: 1; border: none; background: $panel; }
    """
    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        manual = Path(__file__).parent / "USER-MANUAL.md"
        content = manual.read_text() if manual.exists() else "# User Manual\n\nFile not found."
        with Vertical(id="manual-popup"):
            with ScrollableContainer(id="manual-scroll"):
                yield Markdown(content)
            yield Button("close  [dim]esc[/dim]", id="manual-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()




