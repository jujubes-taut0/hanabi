from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.markup import escape

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, ListItem, ListView, RichLog, Static

from data import (
    HANABI_CTX_SESSION,
    assign_sessions_to_folders,
    capture_pane,
    ensure_hanabi_ctx,
    extract_approval_question,
    extract_last_assistant_message,
    fmt_folder_row,
    fmt_search_result_row,
    fmt_session_row,
    fmt_todo_lines,
    fmt_worker_row,
    get_tmux_sessions,
    get_tmux_windows,
    group_worker_sessions,
    init_folders,
    load_agents_by_cwd,
    load_orch_workers,
    next_session_name,
    pane_has_signal_phrase,
    pane_is_error_looping,
    pane_is_waiting,
    parse_todo_items,
    pick_folder_dialog,
    sanitize_session_name,
    save_folders,
    search_session_logs,
    session_has_claude,
)

_HANABI_DIR = str(Path(__file__).parent)
_INTERNAL_SESSIONS = {HANABI_CTX_SESSION}


from screens import ContextMenuScreen, RenameScreen


class ExplorerPane(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.folders: list[str] = []
        self.list_items: list[dict] = []
        self._current_session: str | None = None
        self._mounting: bool = False
        self.windows: list[dict] = []
        self.waiting_status: dict[str, bool] = {}
        self.waiting_since: dict[str, float] = {}
        self.live_status: dict[str, bool] = {}
        self.error_status: dict[str, bool] = {}
        self.pane_text: dict[str, str] = {}
        self.agents_by_cwd: dict[str, dict] = {}
        self._approvals: list[dict] = []
        self._sessions_by_folder: dict[str, list[str]] = {}
        self._recently_exited: dict[str, tuple[float, str]] = {}
        self._search_mode: bool = False
        self._search_query: str = ""
        self._search_results: dict[str, str] = {}
        self._search_task: asyncio.Task | None = None
        self._todo_items: list[dict] = []
        self._split_mode: str = "1"
        self._target_slot: int = 0
        self._pane_slots: list[dict] = []  # [{tty, session}] for each right pane
        self._watch_mode: bool = False
        self._watch_session: str | None = None
        self._watch_view: int = 0
        self._rebuilding: bool = False

    def compose(self) -> ComposeResult:
        yield Input(placeholder="⌕ search panes...", id="search-input")
        yield Button("add folder", id="explorer-add-btn", variant="default")
        yield Button("≽^- ˕ -^≼ hanabi helper", id="hanabi-ctx-btn", variant="default")
        yield Static("", id="slot-indicator")
        yield ListView(id="explorer-folder-list")
        with Vertical(id="todo-strip"):
            yield RichLog(id="todo-log", markup=True, highlight=False, wrap=False)
        yield Static("", id="watch-overlay", markup=True)
        with Vertical(id="approvals-strip"):
            yield Static(
                "[bold]！✦ approvals ✦！[/bold]  [dim]y ✿ yes   n ✧ no   enter ↝ open[/dim]",
                id="approvals-header",
            )
            yield ListView(id="approvals-list")

    def on_mount(self) -> None:
        self.agents_by_cwd = load_agents_by_cwd()
        self.folders = init_folders()
        self.set_interval(8, self._poll_status)
        self.call_after_refresh(self._initial_load)

    async def _initial_load(self) -> None:
        self.windows = await asyncio.to_thread(get_tmux_windows)
        self.list_items = self._build_list_items()
        self._rebuild_list()
        right_pane = os.environ.get("TUI_RIGHT_PANE", "")
        if right_pane:
            outer_session = right_pane.split(":")[0]
            all_right = await self._get_right_panes(outer_session)
            if all_right:
                base_idx = min(all_right.keys())
                self._pane_slots = [{"tty": all_right[base_idx], "session": None}]

    def _build_list_items(self) -> list[dict]:
        sessions_by_folder = assign_sessions_to_folders(self.folders, self.windows)
        self._sessions_by_folder = sessions_by_folder
        active_sessions = {w["session"] for w in self.windows}
        now = time.time()
        items: list[dict] = []
        for folder_path in self.folders:
            path = Path(folder_path)
            agent = self.agents_by_cwd.get(folder_path)
            sessions = [s for s in sessions_by_folder.get(folder_path, []) if s not in _INTERNAL_SESSIONS]
            any_waiting = any(self.waiting_status.get(s, False) for s in sessions)
            any_live = any(self.live_status.get(s, False) for s in sessions)
            ctx_pct = agent.get("context_pct") if agent else None
            orch_workers = load_orch_workers(folder_path)

            if self._search_mode and self._search_query:
                if not any(s in self._search_results for s in sessions):
                    continue
                items.append({
                    "type": "folder", "path": folder_path, "name": path.name,
                    "agent": agent, "sessions": sessions,
                    "waiting": any_waiting, "live": any_live,
                })
                for parent, workers in group_worker_sessions(sessions, orch_workers):
                    session_item: dict = {
                        "type": "session", "session": parent, "path": folder_path,
                        "waiting": self.waiting_status.get(parent, False),
                        "live": self.live_status.get(parent, False),
                        "erroring": self.error_status.get(parent, False),
                        "ctx_pct": ctx_pct,
                    }
                    if parent in self._search_results:
                        session_item["snippet"] = self._search_results[parent]
                        items.append(session_item)
                        for w_session, w_item in workers:
                            w_dict: dict = {
                                "type": "worker", "session": w_session, "item": w_item, "path": folder_path,
                                "waiting": self.waiting_status.get(w_session, False),
                                "live": self.live_status.get(w_session, False),
                                "erroring": self.error_status.get(w_session, False),
                            }
                            if w_session in self._search_results:
                                w_dict["snippet"] = self._search_results[w_session]
                            items.append(w_dict)
                    else:
                        parent_added = False
                        for w_session, w_item in workers:
                            if w_session in self._search_results:
                                if not parent_added:
                                    items.append(session_item)
                                    parent_added = True
                                items.append({
                                    "type": "worker", "session": w_session, "item": w_item, "path": folder_path,
                                    "waiting": self.waiting_status.get(w_session, False),
                                    "live": self.live_status.get(w_session, False),
                                    "erroring": self.error_status.get(w_session, False),
                                    "snippet": self._search_results[w_session],
                                })
                continue

            items.append({
                "type": "folder", "path": folder_path, "name": path.name,
                "agent": agent, "sessions": sessions,
                "waiting": any_waiting, "live": any_live,
            })
            for parent, workers in group_worker_sessions(sessions, orch_workers):
                items.append({
                    "type": "session", "session": parent, "path": folder_path,
                    "waiting": self.waiting_status.get(parent, False),
                    "live": self.live_status.get(parent, False),
                    "erroring": self.error_status.get(parent, False),
                    "ctx_pct": ctx_pct,
                })
                for w_session, w_item in workers:
                    items.append({
                        "type": "worker", "session": w_session, "item": w_item, "path": folder_path,
                        "waiting": self.waiting_status.get(w_session, False),
                        "live": self.live_status.get(w_session, False),
                        "erroring": self.error_status.get(w_session, False),
                    })
            # Recently-exited sessions for this folder — show briefly with kaomoji
            for s, (ts, exited_path) in list(self._recently_exited.items()):
                if exited_path == folder_path and s not in active_sessions:
                    if now - ts < 3.0:
                        items.append({
                            "type": "session", "session": s, "path": folder_path,
                            "exited": True, "waiting": False, "live": False, "erroring": False,
                        })
                    else:
                        self._recently_exited.pop(s, None)
        return items

    def _rebuild_list(self) -> None:
        self._rebuilding = True
        lv = self.query_one("#explorer-folder-list", ListView)
        lv.clear()
        for item in self.list_items:
            if item["type"] == "folder":
                label = fmt_folder_row(item["name"], item.get("agent"), item["live"], item["waiting"])
            elif item["type"] == "worker":
                if item.get("snippet"):
                    label = fmt_search_result_row(item["session"], item["snippet"])
                else:
                    label = fmt_worker_row(item["session"], item.get("item", ""), item["live"], item["waiting"], item.get("erroring", False), item.get("exited", False))
            else:
                if item.get("snippet"):
                    label = fmt_search_result_row(item["session"], item["snippet"])
                else:
                    label = fmt_session_row(item["session"], item["live"], item["waiting"], item.get("ctx_pct"), item.get("erroring", False), item.get("exited", False))
            lv.append(ListItem(Label(label)))
        self.call_after_refresh(lambda: setattr(self, "_rebuilding", False))

    def _list_structure_changed(self, new_items: list[dict]) -> bool:
        if len(new_items) != len(self.list_items):
            return True
        for new, old in zip(new_items, self.list_items):
            if new["type"] != old["type"]:
                return True
            if new["type"] == "folder" and new["path"] != old["path"]:
                return True
            if new["type"] in ("session", "worker") and new["session"] != old["session"]:
                return True
        return False

    def _update_list_labels(self) -> None:
        lv = self.query_one("#explorer-folder-list", ListView)
        lv_items = list(lv.query(ListItem))
        sessions_by_folder = self._sessions_by_folder
        active_sessions = {w["session"] for w in self.windows}
        for i, item in enumerate(self.list_items):
            if i >= len(lv_items):
                break
            if item["type"] == "folder":
                sessions = sessions_by_folder.get(item["path"], [])
                item["sessions"] = sessions
                item["waiting"] = any(self.waiting_status.get(s, False) for s in sessions)
                item["live"] = any(self.live_status.get(s, False) for s in sessions)
                label = fmt_folder_row(item["name"], item.get("agent"), item["live"], item["waiting"])
            elif item["type"] == "worker":
                s = item["session"]
                item["waiting"] = self.waiting_status.get(s, False)
                item["live"] = self.live_status.get(s, False)
                item["erroring"] = self.error_status.get(s, False)
                if item.get("snippet"):
                    label = fmt_search_result_row(s, item["snippet"])
                else:
                    label = fmt_worker_row(s, item.get("item", ""), item["live"], item["waiting"], item.get("erroring", False), item.get("exited", False))
            else:
                s = item["session"]
                item["waiting"] = self.waiting_status.get(s, False)
                item["live"] = self.live_status.get(s, False)
                item["erroring"] = self.error_status.get(s, False)
                if item.get("snippet"):
                    label = fmt_search_result_row(s, item["snippet"])
                else:
                    label = fmt_session_row(s, item["live"], item["waiting"], item.get("ctx_pct"), item.get("erroring", False), item.get("exited", False))
            lv_items[i].query_one(Label).update(label)

    async def _mount_session(self, session_name: str, cwd: str) -> None:
        self._current_session = session_name
        asyncio.create_task(self._update_todo_strip(cwd))
        right_pane = os.environ.get("TUI_RIGHT_PANE", "")
        if not right_pane:
            return
        tui_session = right_pane.split(":")[0]
        if session_name == tui_session:
            return
        slot = self._target_slot
        if slot < len(self._pane_slots) and self._pane_slots[slot].get("session") == session_name:
            return
        asyncio.create_task(self._switch_right_pane(session_name, right_pane, cwd, slot))

    async def _get_right_panes(self, outer_session: str) -> dict[int, str]:
        """Returns {pane_index: tty} for all panes except pane 0 (TUI)."""
        tmux = shutil.which("tmux") or "tmux"
        r = await asyncio.to_thread(subprocess.run,
            [tmux, "list-panes", "-t", f"{outer_session}:0", "-F", "#{pane_index}:#{pane_tty}"],
            capture_output=True, text=True)
        result = {}
        for line in r.stdout.strip().splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2:
                idx = int(parts[0])
                if idx > 0:
                    result[idx] = parts[1]
        return result

    async def _split_and_get_tty(self, outer_session: str, target: str, flag: str, cwd: str = "") -> str | None:
        """Splits target pane and returns the new pane's TTY."""
        tmux = shutil.which("tmux") or "tmux"
        before = set((await self._get_right_panes(outer_session)).values())
        cmd = [tmux, "split-window", flag, "-t", target]
        if cwd:
            cmd += ["-c", cwd]
        await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
        await asyncio.sleep(0.12)
        after = await self._get_right_panes(outer_session)
        new_ttys = [tty for tty in after.values() if tty not in before]
        return new_ttys[0] if new_ttys else None

    async def _apply_split_mode(self, mode: str) -> None:
        right_pane = os.environ.get("TUI_RIGHT_PANE", "")
        if not right_pane:
            return
        outer_session = right_pane.split(":")[0]
        tmux = shutil.which("tmux") or "tmux"

        # Resolve cwd from currently mounted session; fall back to highlighted item
        cwd = next((w["cwd"] for w in self.windows if w["session"] == self._current_session), "")
        if not cwd:
            lv = self.query_one("#explorer-folder-list", ListView)
            if lv.index is not None and lv.index < len(self.list_items):
                cwd = self.list_items[lv.index].get("path", "")

        all_right = await self._get_right_panes(outer_session)
        if not all_right:
            # No right pane — create base
            base_cmd = [tmux, "split-window", "-h", "-t", f"{outer_session}:0.0", "-p", "60"]
            if cwd:
                base_cmd += ["-c", cwd]
            await asyncio.to_thread(subprocess.run, base_cmd, capture_output=True)
            await asyncio.sleep(0.15)
            all_right = await self._get_right_panes(outer_session)

        if not all_right:
            self.app.notify("Could not create split pane", severity="error")
            return

        base_idx = min(all_right.keys())
        base_tty = all_right[base_idx]

        # Kill all extra right panes, keep only base
        for idx in sorted(all_right.keys()):
            if idx != base_idx:
                await asyncio.to_thread(subprocess.run,
                    [tmux, "kill-pane", "-t", f"{outer_session}:0.{idx}"],
                    capture_output=True)
        await asyncio.sleep(0.12)

        self._pane_slots = [{"tty": base_tty, "session": None}]
        target_base = f"{outer_session}:0.{base_idx}"

        if mode == "2V":
            tty = await self._split_and_get_tty(outer_session, target_base, "-h", cwd)
            if tty:
                self._pane_slots.append({"tty": tty, "session": None})

        elif mode == "2H":
            tty = await self._split_and_get_tty(outer_session, target_base, "-v", cwd)
            if tty:
                self._pane_slots.append({"tty": tty, "session": None})

        elif mode == "4":
            # top-right (slot 1)
            tty1 = await self._split_and_get_tty(outer_session, target_base, "-h", cwd)
            if tty1:
                self._pane_slots.append({"tty": tty1, "session": None})
            # Find top-right pane index for further splits
            all_right2 = await self._get_right_panes(outer_session)
            tr_idx = next((i for i, t in all_right2.items() if t == tty1), None)
            # bottom-left (slot 2)
            tty2 = await self._split_and_get_tty(outer_session, target_base, "-v", cwd)
            if tty2:
                self._pane_slots.append({"tty": tty2, "session": None})
            # bottom-right (slot 3)
            if tr_idx is not None:
                tty3 = await self._split_and_get_tty(outer_session, f"{outer_session}:0.{tr_idx}", "-v", cwd)
                if tty3:
                    self._pane_slots.append({"tty": tty3, "session": None})

        self._split_mode = mode
        if self._target_slot >= len(self._pane_slots):
            self._target_slot = 0
        self._update_slot_indicator()
        n = len(self._pane_slots)
        self.app.notify(f"✿ {mode} split — {n} pane{'s' if n > 1 else ''}")
        self.query_one("#explorer-folder-list", ListView).focus()

    def _update_slot_indicator(self) -> None:
        try:
            w = self.query_one("#slot-indicator", Static)
        except Exception:
            return
        if self._split_mode == "1" or len(self._pane_slots) <= 1:
            w.display = False
            return
        w.display = True
        slots = " ".join(
            f"[bold][[{i + 1}]][/bold]" if i == self._target_slot
            else f"[dim]{i + 1}[/dim]"
            for i in range(len(self._pane_slots))
        )
        w.update(f"[dim]slot →[/dim] {slots}")

    async def _switch_right_pane(self, session_name: str, right_pane: str, cwd: str = "", slot: int = 0) -> None:
        tmux = shutil.which("tmux") or "tmux"
        outer_session = right_pane.split(":")[0]

        # Get TTY for the target slot; fall back to rebuilding from live panes if stale
        pane_tty: str | None = None
        if slot < len(self._pane_slots):
            pane_tty = self._pane_slots[slot]["tty"]

        if pane_tty is None:
            # No right panes at all — recreate base pane
            await asyncio.to_thread(subprocess.run,
                [tmux, "split-window", "-h", "-t", f"{outer_session}:0.0", "-p", "60"],
                capture_output=True)
            await asyncio.sleep(0.15)
            all_right = await self._get_right_panes(outer_session)
            if all_right:
                base_idx = min(all_right.keys())
                pane_tty = all_right[base_idx]
                self._pane_slots = [{"tty": pane_tty, "session": None}]
                slot = 0

        # Ensure target session exists
        has_r = await asyncio.to_thread(subprocess.run,
            [tmux, "has-session", "-t", session_name], capture_output=True)
        if has_r.returncode != 0:
            if not cwd:
                return
            await asyncio.to_thread(subprocess.run,
                [tmux, "new-session", "-d", "-s", session_name, "-c", cwd],
                capture_output=True)

        # Disable alt-screen on the nested session's window. Otherwise the inner tmux's alt-screen
        # compounds with the outer tui-control's pane and Claude's output never reaches scrollback.
        await asyncio.to_thread(subprocess.run,
            [tmux, "set-window-option", "-t", f"{session_name}:0", "alternate-screen", "off"],
            capture_output=True)

        # Try switch-client using the slot's TTY
        if pane_tty:
            sc_r = await asyncio.to_thread(subprocess.run,
                [tmux, "switch-client", "-c", pane_tty, "-t", session_name],
                capture_output=True, text=True)
            if sc_r.returncode == 0:
                if slot < len(self._pane_slots):
                    self._pane_slots[slot]["session"] = session_name
                return

        # Fallback: silently replace the pane's process with attach-session via respawn-pane.
        # respawn-pane -k avoids visible command injection (unlike send-keys).
        # remain-on-exit keeps the pane alive when the attached session dies.
        all_right = await self._get_right_panes(outer_session)
        slot_pane_idx = next((i for i, t in all_right.items() if t == pane_tty), None)
        target_pane = f"{outer_session}:0.{slot_pane_idx}" if slot_pane_idx else right_pane
        attach_cmd = f"env -u TMUX tmux attach-session -t {shlex.quote(session_name)}"
        rp_r = await asyncio.to_thread(subprocess.run,
            [tmux, "respawn-pane", "-k", "-t", target_pane, attach_cmd],
            capture_output=True, text=True)
        if rp_r.returncode == 0 and slot < len(self._pane_slots):
            self._pane_slots[slot]["session"] = session_name

    async def _poll_status(self) -> None:
        self.windows = await asyncio.to_thread(get_tmux_windows)
        active = {w["session"] for w in self.windows}
        for slot in self._pane_slots:
            if slot.get("session") and slot["session"] not in active:
                slot["session"] = None
        all_sessions = list(active)

        if all_sessions:
            async def check(s: str) -> tuple[str, str, bool, bool, bool]:
                live = session_has_claude(s, self.windows)
                pane = await asyncio.to_thread(capture_pane, s)
                waiting = pane_is_waiting(pane) if pane else False
                erroring = pane_is_error_looping(pane) if (pane and not waiting) else False
                return s, pane, waiting, live, erroring

            results = await asyncio.gather(*[check(s) for s in all_sessions])
            for s, pane, waiting, live, erroring in results:
                if waiting and not self.waiting_status.get(s):
                    self.waiting_since[s] = datetime.now(timezone.utc).timestamp()
                elif not waiting:
                    self.waiting_since.pop(s, None)
                self.waiting_status[s] = waiting
                self.live_status[s] = live
                self.error_status[s] = erroring
                self.pane_text[s] = pane
                if pane_has_signal_phrase(pane) and s not in self._recently_exited:
                    cwd_path = next((w["cwd"] for w in self.windows if w["session"] == s), "")
                    folder = next((f for f in self.folders if cwd_path == f or cwd_path.startswith(f + "/")), cwd_path)
                    self._recently_exited[s] = (time.time(), folder)
                    asyncio.create_task(self._kill_session(s))

        new_items = self._build_list_items()
        if self._search_mode or self._list_structure_changed(new_items):
            self.list_items = new_items
            self._rebuild_list()
        else:
            self.list_items = new_items
            self._update_list_labels()

        self._update_approvals_strip()
        self._update_hanabi_btn()

        if self._current_session:
            cwd = next((w["cwd"] for w in self.windows if w["session"] == self._current_session), "")
            if cwd:
                asyncio.create_task(self._update_todo_strip(cwd))

        if self._watch_mode:
            asyncio.create_task(self._refresh_watch_overlay())

    def _update_approvals_strip(self) -> None:
        strip = self.query_one("#approvals-strip")
        lv = self.query_one("#approvals-list", ListView)
        was_visible = "visible" in strip.classes

        known_sessions = {s for sessions in self._sessions_by_folder.values() for s in sessions}

        self._approvals = []
        for s, waiting in self.waiting_status.items():
            if not waiting:
                continue
            if s not in known_sessions:
                continue
            cwd = next((w["cwd"] for w in self.windows if w["session"] == s), "")
            dir_name = Path(cwd).name if cwd else s
            pane = self.pane_text.get(s, "")
            question = extract_approval_question(pane)
            self._approvals.append({"session": s, "cwd": cwd, "dir": dir_name, "question": question, "kind": "approval"})

        for s, erroring in self.error_status.items():
            if not erroring:
                continue
            if self.waiting_status.get(s):
                continue
            if s not in known_sessions:
                continue
            cwd = next((w["cwd"] for w in self.windows if w["session"] == s), "")
            dir_name = Path(cwd).name if cwd else s
            pane = self.pane_text.get(s, "")
            last_line = next((l.strip() for l in reversed(pane.splitlines()) if l.strip()), "")
            self._approvals.append({"session": s, "cwd": cwd, "dir": dir_name, "question": last_line, "kind": "error"})

        self._approvals.sort(key=lambda a: self.waiting_since.get(a["session"], 0), reverse=True)

        lv.clear()
        for item in self._approvals:
            q = escape(item["question"])[:100]
            if item["kind"] == "error":
                label = f"[bold red]✗ {item['dir']}[/bold red] [dim]· {item['session']}[/dim]\n[dim]{q}[/dim]"
            else:
                label = f"[bold]✦ {item['dir']}[/bold] [dim]· {item['session']}[/dim]\n[dim]{q}[/dim]"
            lv.append(ListItem(Label(label)))

        is_visible = bool(self._approvals)
        if is_visible:
            strip.add_class("visible")
            if not was_visible:
                lv.focus()
        else:
            strip.remove_class("visible")
            if was_visible:
                try:
                    self.query_one("#explorer-folder-list", ListView).focus()
                except Exception:
                    pass

    async def _update_todo_strip(self, cwd: str) -> None:
        todos = await asyncio.to_thread(parse_todo_items, cwd)
        self._todo_items = todos
        try:
            strip = self.query_one("#todo-strip")
            log = self.query_one("#todo-log", RichLog)
        except Exception:
            return
        log.clear()
        if todos:
            strip.add_class("visible")
            for line in fmt_todo_lines(todos):
                log.write(line)
        else:
            strip.remove_class("visible")

    async def _send_approval(self, session_name: str, key: str) -> None:
        tmux = shutil.which("tmux") or "tmux"
        await asyncio.to_thread(
            subprocess.run,
            [tmux, "send-keys", "-t", session_name, key, "Enter"],
            check=False, capture_output=True,
        )
        self.waiting_status[session_name] = False
        self._approvals = [i for i in self._approvals if i["session"] != session_name]
        self._update_approvals_strip()

    def new_session(self) -> None:
        lv = self.query_one("#explorer-folder-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self.list_items):
            self.app.notify("Select a folder first", severity="warning")
            return
        asyncio.create_task(self._do_new_session(Path(self.list_items[idx]["path"])))

    async def _do_new_session(self, path: Path) -> None:
        all_sessions = await asyncio.to_thread(get_tmux_sessions)
        base = sanitize_session_name(path.name)
        session_name = next_session_name(base, all_sessions)
        self._current_session = None
        await self._mount_session(session_name, str(path))
        self.windows = await asyncio.to_thread(get_tmux_windows)
        self.list_items = self._build_list_items()
        self._rebuild_list()
        self.app.notify(f"Session: {session_name}")

    async def _open_pinned_ctx(self) -> None:
        self.windows = await asyncio.to_thread(get_tmux_windows)
        live = HANABI_CTX_SESSION in {w["session"] for w in self.windows}
        if live:
            prev_session = self._current_session  # capture before _kill_session clears it
            await self._kill_session(HANABI_CTX_SESSION)
            if prev_session and prev_session != HANABI_CTX_SESSION:
                cwd = next((w["cwd"] for w in self.windows if w["session"] == prev_session), "")
                asyncio.create_task(self._mount_session(prev_session, cwd))
        else:
            await asyncio.to_thread(ensure_hanabi_ctx, _HANABI_DIR)
            await self._mount_session(HANABI_CTX_SESSION, _HANABI_DIR)
            self.windows = await asyncio.to_thread(get_tmux_windows)
        self._update_hanabi_btn()

    def _update_hanabi_btn(self) -> None:
        live = HANABI_CTX_SESSION in {w["session"] for w in self.windows}
        try:
            btn = self.query_one("#hanabi-ctx-btn", Button)
            btn.label = "(=^･ω･^=) hanabi helper" if live else "≽^- ˕ -^≼ hanabi helper"
        except Exception:
            pass

    async def _add_folder(self) -> None:
        path_str = await pick_folder_dialog()
        if path_str is None:
            return
        path = Path(path_str)
        if not path.is_dir():
            self.app.notify(f"Not a directory: {path_str}", severity="error")
            return
        if path_str in self.folders:
            self.app.notify("Already in list", severity="warning")
            return
        self.folders.append(path_str)
        await asyncio.to_thread(save_folders, self.folders)
        self.list_items = self._build_list_items()
        self._rebuild_list()
        self.app.notify(f"Added: {path.name}")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if self._rebuilding:
            return
        lv = self.query_one("#explorer-folder-list", ListView)
        if event.list_view is not lv:
            return
        idx = lv.index
        if idx is None or idx >= len(self.list_items):
            return
        item = self.list_items[idx]
        if item["type"] in ("session", "worker"):
            asyncio.create_task(self._mount_session(item["session"], item["path"]))
        elif item["type"] == "folder" and item.get("sessions"):
            asyncio.create_task(self._mount_session(item["sessions"][0], item["path"]))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "explorer-add-btn":
            asyncio.create_task(self._add_folder())
        elif event.button.id == "hanabi-ctx-btn":
            asyncio.create_task(self._open_pinned_ctx())

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._search_query = event.value.strip().lower()
            self._search_mode = bool(self._search_query)
            if self._search_task and not self._search_task.done():
                self._search_task.cancel()
            self._search_results = {}
            self.list_items = self._build_list_items()
            self._rebuild_list()
            if self._search_mode:
                self._search_task = asyncio.create_task(self._run_search())

    async def _run_search(self) -> None:
        sessions_info = []
        for folder_path in self.folders:
            for s in self._sessions_by_folder.get(folder_path, []):
                cwd = next((w["cwd"] for w in self.windows if w["session"] == s), "")
                if cwd:
                    sessions_info.append((s, cwd))
        query = self._search_query
        results = await asyncio.to_thread(search_session_logs, sessions_info, query)
        if query != self._search_query:
            return
        self._search_results = results
        self.list_items = self._build_list_items()
        self._rebuild_list()

    def _enter_watch_mode(self, session_name: str) -> None:
        self._watch_mode = True
        self._watch_session = session_name
        self._watch_view = 0
        try:
            self.query_one("#watch-overlay").add_class("visible")
        except Exception:
            pass
        asyncio.create_task(self._refresh_watch_overlay())

    def _exit_watch_mode(self) -> None:
        self._watch_mode = False
        self._watch_session = None
        self._watch_view = 0
        try:
            overlay = self.query_one("#watch-overlay", Static)
            overlay.remove_class("visible")
            overlay.update("")
        except Exception:
            pass

    async def _refresh_watch_overlay(self) -> None:
        if not self._watch_mode or not self._watch_session:
            return
        try:
            overlay = self.query_one("#watch-overlay", Static)
        except Exception:
            return
        session = self._watch_session
        cwd = next((w["cwd"] for w in self.windows if w["session"] == session), "")
        view_labels = ["todos", "workers", "output"]
        header = (
            f"[cyan]● {escape(session)}[/cyan]  "
            f"[dim]↓/↑ cycle · esc exit[/dim]  "
            f"[bold cyan][{view_labels[self._watch_view]}][/bold cyan]"
        )
        if self._watch_view == 0:
            todos = await asyncio.to_thread(parse_todo_items, cwd) if cwd else []
            body = "\n".join(fmt_todo_lines(todos)) if todos else "[dim]no active todos[/dim]"
        elif self._watch_view == 1:
            folder = next((f for f in self.folders if cwd == f or cwd.startswith(f + "/")), "") if cwd else ""
            if folder:
                orch_workers = load_orch_workers(folder)
                sessions = self._sessions_by_folder.get(folder, [])
                workers_for_session: list[tuple[str, str]] = []
                for parent, workers in group_worker_sessions(sessions, orch_workers):
                    if parent == session:
                        workers_for_session = workers
                        break
                if workers_for_session:
                    body = "\n".join(
                        fmt_worker_row(ws, wi, self.live_status.get(ws, False),
                                       self.waiting_status.get(ws, False), self.error_status.get(ws, False))
                        for ws, wi in workers_for_session
                    )
                else:
                    body = "[dim]no workers — solo session[/dim]"
            else:
                body = "[dim]session not in a known folder[/dim]"
        else:
            pane = self.pane_text.get(session, "")
            body = escape(extract_last_assistant_message(pane)) if pane else "[dim]no output captured[/dim]"
        overlay.update(f"{header}\n[dim]{'─' * 28}[/dim]\n{body}")

    def on_key(self, event) -> None:
        approvals_lv = self.query_one("#approvals-list", ListView)
        folder_lv = self.query_one("#explorer-folder-list", ListView)
        search_input = self.query_one("#search-input", Input)

        if self._watch_mode:
            if event.key == "down":
                self._watch_view = (self._watch_view + 1) % 3
                asyncio.create_task(self._refresh_watch_overlay())
                event.stop(); return
            if event.key == "up":
                self._watch_view = (self._watch_view - 1) % 3
                asyncio.create_task(self._refresh_watch_overlay())
                event.stop(); return
            self._exit_watch_mode()
            event.stop(); return

        if event.key == "ctrl+f":
            if "visible" in search_input.classes:
                search_input.remove_class("visible")
                search_input.value = ""
                self._search_mode = False
                self._search_query = ""
                self._search_results = {}
                self.list_items = self._build_list_items()
                self._rebuild_list()
                folder_lv.focus()
            else:
                search_input.add_class("visible")
                search_input.focus()
            event.stop(); return

        if event.key == "escape" and "visible" in search_input.classes:
            search_input.remove_class("visible")
            search_input.value = ""
            self._search_mode = False
            self._search_query = ""
            self._search_results = {}
            self.list_items = self._build_list_items()
            self._rebuild_list()
            folder_lv.focus()
            event.stop(); return

        if approvals_lv.has_focus:
            idx = approvals_lv.index
            if idx is not None and idx < len(self._approvals):
                item = self._approvals[idx]
                if event.key == "y":
                    asyncio.create_task(self._send_approval(item["session"], "y"))
                    event.stop(); return
                elif event.key == "n":
                    asyncio.create_task(self._send_approval(item["session"], "n"))
                    event.stop(); return
                elif event.key == "enter":
                    asyncio.create_task(self._mount_session(item["session"], item["cwd"] or str(Path.home())))
                    event.stop(); return
            if event.key == "escape":
                folder_lv.focus(); event.stop(); return

        if event.key == "a":
            strip = self.query_one("#approvals-strip")
            if "visible" in strip.classes:
                approvals_lv.focus(); event.stop(); return

        if event.key == "ctrl+backslash":
            asyncio.create_task(self._toggle_split())
            event.stop(); return

        if event.character == "S":
            asyncio.create_task(self._swap_pane_sides())
            event.stop(); return

        if event.character in ("1", "2", "3", "4") and len(self._pane_slots) > 1:
            slot = int(event.character) - 1
            if slot < len(self._pane_slots):
                self._target_slot = slot
                self._update_slot_indicator()
            event.stop(); return

        if event.character == "+":
            asyncio.create_task(self._add_folder())
            event.stop(); return

        if folder_lv.has_focus:
            idx = folder_lv.index
            if idx is not None and idx < len(self.list_items):
                item = self.list_items[idx]
                if event.key == "ctrl+d":
                    if item["type"] in ("session", "worker"):
                        asyncio.create_task(self._kill_session(item["session"]))
                    else:
                        self._remove_folder(item["path"])
                    event.stop(); return
                if event.key == "r" and item["type"] in ("session", "worker"):
                    self.run_worker(self._do_rename(item["session"]), exclusive=False)
                    event.stop(); return
                if event.character == "w" and item["type"] in ("session", "worker"):
                    self._enter_watch_mode(item["session"])
                    event.stop(); return
            if event.key == "enter":
                event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if event.button != 3:
            return
        lv = self.query_one("#explorer-folder-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self.list_items):
            return
        item = self.list_items[idx]
        self.run_worker(self._show_context_menu(item), exclusive=False)

    async def _show_context_menu(self, item: dict) -> None:
        if item["type"] == "pinned":
            return
        action = await self.app.push_screen_wait(ContextMenuScreen(item))
        if action == "ctx-open":
            asyncio.create_task(self._mount_session(item["session"], item["path"]))
        elif action == "ctx-new":
            asyncio.create_task(self._do_new_session(Path(item["path"])))
        elif action == "ctx-kill":
            asyncio.create_task(self._kill_session(item["session"]))
        elif action == "ctx-remove":
            self._remove_folder(item["path"])

    async def _toggle_split(self) -> None:
        modes = ["1", "2V", "2H", "4"]
        next_mode = modes[(modes.index(self._split_mode) + 1) % len(modes)]
        await self._apply_split_mode(next_mode)

    async def _swap_pane_sides(self) -> None:
        right_pane = os.environ.get("TUI_RIGHT_PANE", "")
        if not right_pane:
            return
        tmux = shutil.which("tmux") or "tmux"
        outer_session = right_pane.split(":")[0]
        all_right = await self._get_right_panes(outer_session)
        if not all_right:
            self.app.notify("No right pane to swap", severity="warning")
            return
        base_idx = min(all_right.keys())
        await asyncio.to_thread(subprocess.run,
            [tmux, "swap-pane", "-s", f"{outer_session}:0.0", "-t", f"{outer_session}:0.{base_idx}"],
            capture_output=True)
        self.app.notify("✿ swapped pane sides")
        self.query_one("#explorer-folder-list", ListView).focus()

    async def _kill_session(self, session_name: str) -> None:
        tmux = shutil.which("tmux") or "tmux"
        await asyncio.to_thread(
            subprocess.run,
            [tmux, "kill-session", "-t", session_name],
            check=False, capture_output=True,
        )
        if self._current_session == session_name:
            self._current_session = None
        for slot in self._pane_slots:
            if slot.get("session") == session_name:
                slot["session"] = None
        self.windows = await asyncio.to_thread(get_tmux_windows)
        self.list_items = self._build_list_items()
        self._rebuild_list()
        self.app.notify(f"Killed: {session_name}")

    async def _do_rename(self, session_name: str) -> None:
        new_name = await self.app.push_screen_wait(RenameScreen(session_name))
        if not new_name:
            return
        new_name = sanitize_session_name(new_name)
        if not new_name:
            self.app.notify("Invalid name", severity="warning")
            return
        tmux = shutil.which("tmux") or "tmux"
        result = await asyncio.to_thread(
            subprocess.run,
            [tmux, "rename-session", "-t", session_name, new_name],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.app.notify(f"Rename failed: {result.stderr.strip()}", severity="error")
            return
        if self._current_session == session_name: self._current_session = new_name
        self.windows = await asyncio.to_thread(get_tmux_windows)
        self.list_items = self._build_list_items()
        self._rebuild_list()
        self.app.notify(f"✿ renamed: {session_name} → {new_name}")

    def _remove_folder(self, path: str) -> None:
        if path in self.folders:
            self.folders.remove(path)
            asyncio.create_task(asyncio.to_thread(save_folders, self.folders))
            self.list_items = self._build_list_items()
            self._rebuild_list()
            self.app.notify(f"Removed: {Path(path).name}")

    def refresh_data(self) -> None:
        self.agents_by_cwd = load_agents_by_cwd()
        self.folders = init_folders()
        asyncio.create_task(self._poll_status())
