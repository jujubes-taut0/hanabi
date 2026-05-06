from __future__ import annotations

import asyncio
import collections
import json
import re
import shutil
import socket
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import os

from rich.markup import escape

BASE = Path(os.environ.get("HANABI_BASE", str(Path.home() / ".config" / "hanabi")))
AGENTS_FILE = BASE / "config" / "agents-status.json"
FOLDERS_FILE = BASE / "config" / "explorer-folders.json"
DASHBOARDS_FILE = BASE / "config" / "dashboards.json"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
SCRIPTS_DIR = BASE / "config" / "scripts"

WAIT_KEYWORDS = [
    "enter to select",
    "esc to cancel",
    "[y/n]",
    "? >",
]

ERROR_KEYWORDS = [
    "traceback (most recent call last)",
    "error:", "api error", "rate limit", "timed out",
    "connection error", "failed to", "cannot ", "exception:",
    "syntaxerror", "typeerror", "valueerror", "keyerror", "attributeerror",
]

SIGNAL_PHRASE = "existence is pain"


def load_agents() -> list[dict]:
    try:
        data = json.loads(AGENTS_FILE.read_text())
        agents = data.get("agents", [])
        agents.sort(key=lambda a: a.get("last_session_ts", ""), reverse=True)
        return agents
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def load_agents_by_cwd() -> dict[str, dict]:
    return {a["cwd"]: a for a in load_agents() if "cwd" in a}



def load_folders() -> list[str]:
    try:
        return json.loads(FOLDERS_FILE.read_text()).get("folders", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_folders(folders: list[str]) -> None:
    FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FOLDERS_FILE.write_text(json.dumps({"folders": folders}, indent=2))


def init_folders() -> list[str]:
    persisted = load_folders()
    agent_cwds = [a["cwd"] for a in load_agents() if "cwd" in a]
    seen = set(agent_cwds)
    extras = [p for p in persisted if p not in seen]
    merged = agent_cwds + extras
    if merged != persisted:
        save_folders(merged)
    return merged


def load_dashboards() -> tuple[list[dict], str | None]:
    try:
        mode = DASHBOARDS_FILE.stat().st_mode
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            return [], "dashboards.json is group/world-writable — refusing to load"
        # dashboards.json is trusted local config; launch commands run verbatim in bash
        data = json.loads(DASHBOARDS_FILE.read_text())
        dashboards = list(data.get("dashboards", []))
        components = [
            c for c in data.get("components", [])
            if c.get("role") != "foundation"
        ]
        result = []
        for dash in dashboards:
            result.append(dash)
            dash_port = dash.get("port", "")
            dash_project = dash.get("project", "")
            for comp in components:
                if comp.get("project") == dash_project and comp.get("port", "") == dash_port:
                    result.append({
                        "type": "component",
                        "project": dash_project,
                        "file": comp["file"],
                        "title": comp["name"],
                        "port": dash_port,
                        "url": comp.get("url", dash.get("url", "")),
                        "launch": comp.get("launch", dash.get("launch", "")),
                    })
        return result, None
    except FileNotFoundError:
        return [], f"dashboards.json not found at {DASHBOARDS_FILE}"
    except json.JSONDecodeError as e:
        return [], f"Invalid JSON in dashboards.json: {e}"


def check_port(port: str) -> bool:
    if not port:
        return False
    try:
        p = int(port)
        if not (1 <= p <= 65535):
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(("127.0.0.1", p)) == 0
    except (OSError, ValueError):
        return False


HANABI_CTX_SESSION = "hanabi-ctx"


def ensure_hanabi_ctx(hanabi_dir: str) -> None:
    tmux = shutil.which("tmux") or "tmux"
    r = subprocess.run([tmux, "has-session", "-t", HANABI_CTX_SESSION], capture_output=True)
    if r.returncode != 0:
        subprocess.run([tmux, "new-session", "-d", "-s", HANABI_CTX_SESSION, "-c", hanabi_dir], capture_output=True)
        hint = (
            "\n"
            "  (=^･ω･^=) hanabi helper — your interactive manual\n"
            "\n"
            "  Ask me anything about hanabi:\n"
            '  "what are all the keybindings?"\n'
            '  "how does split mode work?"\n'
            '  "how do I add a custom widget?"\n'
            '  "what do the status symbols mean?"\n'
            '  "walk me through setting up a new session"\n'
            "\n"
            "  Run:    claude   (or any AI CLI)\n"
            '  Start:  "read USER-MANUAL.md and help me use hanabi"\n'
            "\n"
        )
        pane_tty = subprocess.run(
            [tmux, "display-message", "-t", HANABI_CTX_SESSION, "-p", "#{pane_tty}"],
            capture_output=True, text=True,
        ).stdout.strip()
        if pane_tty:
            try:
                with open(pane_tty, "w") as tty:
                    tty.write(hint)
            except OSError:
                pass


def get_tmux_windows() -> list[dict]:
    fmt = "#{session_name}\t#{window_index}\t#{window_name}\t#{pane_current_path}\t#{pane_current_command}"
    tmux = shutil.which("tmux") or "tmux"
    try:
        result = subprocess.run(
            [tmux, "list-windows", "-a", "-F", fmt],
            capture_output=True, text=True, check=True, timeout=5,
        )
        windows = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 5:
                windows.append({
                    "session": parts[0], "index": parts[1],
                    "name": parts[2], "cwd": parts[3], "command": parts[4],
                })
        return windows
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []


def get_tmux_sessions() -> list[str]:
    tmux = shutil.which("tmux") or "tmux"
    try:
        result = subprocess.run(
            [tmux, "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        return result.stdout.strip().splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []


_RE_CLAUDE_VERSION = re.compile(r"^\d+\.\d+\.\d+")


def session_has_claude(session_name: str, windows: list[dict]) -> bool:
    # pane_current_command may show "claude" or, when the binary is a symlink
    # to a versioned file (e.g. ~/.local/share/claude/versions/2.1.126),
    # the resolved version string.
    return any(
        w["session"] == session_name and (
            "claude" in w["command"].lower()
            or bool(_RE_CLAUDE_VERSION.match(w["command"]))
        )
        for w in windows
    )


def assign_sessions_to_folders(folders: list[str], windows: list[dict]) -> dict[str, list[str]]:
    sorted_folders = sorted(folders, key=lambda f: len(Path(f).parts), reverse=True)
    session_to_folder: dict[str, str] = {}
    for w in windows:
        s = w["session"]
        if s in session_to_folder or s.isdigit():
            continue
        cwd = w["cwd"]
        for folder in sorted_folders:
            if cwd == folder or cwd.startswith(folder + "/"):
                session_to_folder[s] = folder
                break
    result: dict[str, list[str]] = {f: [] for f in folders}
    seen: set[str] = set()
    for s, folder in session_to_folder.items():
        if s not in seen and folder in result:
            result[folder].append(s)
            seen.add(s)
    return result


def load_orch_workers(folder_path: str) -> dict[str, str]:
    """Scan orchestration manifests in folder_path and return {session: item_label}.

    Only includes workers whose session is still active (present in the folder's
    tmux sessions). Stale manifests from finished runs are silently skipped.
    """
    result: dict[str, str] = {}
    orch_dir = Path(folder_path) / "orchestration"
    if not orch_dir.is_dir():
        return result
    for manifest_path in orch_dir.glob("*/manifest.json"):
        try:
            data = json.loads(manifest_path.read_text())
            for w in data.get("workers", []):
                session = w.get("session", "")
                item = w.get("item", w.get("slug", session))
                if session:
                    result[session] = item
        except (json.JSONDecodeError, OSError):
            continue
    return result


def group_worker_sessions(
    sessions: list[str], orch_workers: dict[str, str]
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Return sessions grouped as (parent, [(worker_session, item_label)]).

    Workers are identified from manifest data. All workers nest under the first
    non-worker session in the folder (the orchestrator). If no non-worker session
    exists, workers appear as top-level entries with no children.
    """
    worker_set = set(orch_workers) & set(sessions)
    non_workers = [s for s in sessions if s not in worker_set]
    workers_list = [(s, orch_workers[s]) for s in sessions if s in worker_set]

    if not workers_list:
        return [(s, []) for s in sessions]

    result = []
    orchestrator_assigned = False
    for s in non_workers:
        if not orchestrator_assigned and workers_list:
            result.append((s, workers_list))
            orchestrator_assigned = True
        else:
            result.append((s, []))
    # Workers with no orchestrator session present — show ungrouped
    if not orchestrator_assigned:
        for s, item in workers_list:
            result.append((s, []))
    return result


def next_session_name(base: str, all_sessions: list[str]) -> str:
    pat = re.compile(rf"^{re.escape(base)}-(\d+)$")
    nums = [int(m.group(1)) for s in all_sessions if (m := pat.match(s))]
    return f"{base}-{max(nums) + 1 if nums else 1}"


def sanitize_session_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", name.lower().replace(" ", "_"))


def capture_pane(session_name: str, lines: int = 30) -> str:
    tmux = shutil.which("tmux") or "tmux"
    try:
        result = subprocess.run(
            [tmux, "capture-pane", "-p", "-t", session_name],
            capture_output=True, text=True, timeout=5,
        )
        return "\n".join(result.stdout.splitlines()[-lines:])
    except (Exception, subprocess.TimeoutExpired):
        return ""


def pane_is_waiting(pane_text: str) -> bool:
    lower = pane_text.lower()
    return any(k in lower for k in WAIT_KEYWORDS)


def pane_is_error_looping(pane_text: str) -> bool:
    lower = pane_text.lower()
    matches = sum(1 for k in ERROR_KEYWORDS if k in lower)
    return matches >= 2


def pane_has_signal_phrase(pane_text: str) -> bool:
    return SIGNAL_PHRASE in pane_text.lower()


def extract_approval_question(pane_text: str) -> str:
    lines = [l.strip() for l in pane_text.splitlines() if l.strip()]
    # Claude Code menu: question is the line immediately before "❯ 1." or "1."
    for i, line in enumerate(lines):
        if re.match(r"^[❯>]?\s*1\.", line) and i > 0:
            return lines[i - 1]
    # Fallback: last non-navigation, non-numbered line
    skip = re.compile(r"enter to select|esc to cancel|[↑↓]|^\d+\.|^[❯>]")
    for line in reversed(lines):
        if not skip.search(line.lower()):
            return line
    return lines[-1] if lines else ""


def _latest_jsonl(cwd: str) -> Path | None:
    encoded = Path(cwd).as_posix().lstrip("/").replace("/", "-")
    for candidate in (CLAUDE_PROJECTS / f"-{encoded}", CLAUDE_PROJECTS / encoded):
        if candidate.exists():
            files = sorted(candidate.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
            return files[0] if files else None
    return None


def jsonl_last_mtime(cwd: str) -> str:
    """Return ISO timestamp of the most-recently-modified JSONL file for cwd, or ''."""
    f = _latest_jsonl(cwd)
    if f is None:
        return ""
    return datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()


def load_chat_messages(cwd: str, max_messages: int = 60) -> list[dict]:
    jsonl = _latest_jsonl(cwd)
    if jsonl is None:
        return []
    messages = []
    try:
        with open(jsonl, errors="replace") as f:
            lines = collections.deque(f, maxlen=max_messages * 4)
        for line in lines:
            try:
                event = json.loads(line)
                role = event.get("type")
                if role not in ("user", "assistant"):
                    continue
                content = event.get("message", {}).get("content", "")
                ts = event.get("timestamp", "")
                if isinstance(content, list):
                    for b in content:
                        btype = b.get("type", "")
                        if btype == "text":
                            text = b.get("text", "").strip()
                            if text:
                                messages.append({"role": role, "text": text, "ts": ts})
                        elif btype == "thinking":
                            text = b.get("thinking", "").strip()
                            if text:
                                messages.append({"role": "thinking", "text": text, "ts": ts})
                else:
                    text = str(content).strip()
                    if text:
                        messages.append({"role": role, "text": text, "ts": ts})
            except (json.JSONDecodeError, KeyError, AttributeError):
                continue
    except OSError:
        return []
    return messages[-max_messages:]


async def pick_folder_dialog() -> str | None:
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", 'POSIX path of (choose folder with prompt "Add project folder")'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip().rstrip("/")
    except FileNotFoundError:
        return None


def fmt_bar(pct: float, width: int = 20) -> str:
    EIGHTH_BLOCKS = " ▏▎▍▌▋▊▉█"
    color = "red" if pct >= 90 else "dark_orange" if pct >= 75 else "yellow" if pct >= 50 else "green"
    filled_cells = pct / 100 * width
    full = int(filled_cells)
    partial_idx = round((filled_cells - full) * 8)
    if full >= width:
        bar_colored = "█" * width
        bar_dim = ""
    else:
        partial = EIGHTH_BLOCKS[partial_idx]
        bar_colored = "█" * full + partial
        bar_dim = "░" * (width - full - 1)
    return f"[{color}]{bar_colored}[/{color}][dim]{bar_dim}[/dim] {pct:.0f}%"


def agent_status(ts: str) -> tuple[str, str]:
    if not ts:
        return "sleeping", "dim"
    try:
        h = (datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 3600
    except ValueError:
        return "sleeping", "dim"
    if h < 2:    return "active ✨", "green"
    if h < 12:   return "active",    "green"
    if h < 48:   return "recent",    "yellow"
    if h < 168:  return "this week", "magenta"
    return "idle", "dim"


def fmt_ago(ts: str) -> str:
    if not ts:
        return "never"
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
        if secs < 60:    return f"{int(secs)}s ago"
        if secs < 3600:  return f"{int(secs / 60)}m ago"
        if secs < 86400: return f"{int(secs / 3600)}h ago"
        return f"{int(secs / 86400)}d ago"
    except ValueError:
        return "?"


def fmt_tok(n: int) -> str:
    if n >= 1_000_000: return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:     return f"{n / 1_000:.0f}k"
    return str(n)


def fmt_resets_in(ts: str) -> str:
    if not ts:
        return ""
    try:
        target = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        secs = (target - datetime.now(timezone.utc)).total_seconds()
        if secs <= 0:    return "now"
        if secs < 60:    return f"{int(secs)}s"
        if secs < 3600:  return f"{int(secs / 60)}m"
        if secs < 86400: return f"{int(secs / 3600)}h"
        return f"{int(secs / 86400)}d"
    except ValueError:
        return ""


def fmt_dash_row(d: dict, running: bool) -> str:
    if d.get("type") == "component":
        fname = Path(d.get("file", "")).name
        dot = "[dim]·[/dim]"
        return f"    {dot} [dim]{escape(fname)}[/dim]"
    dot = "✿" if running else "✧"
    title = d.get("title") or d.get("project", "?")
    port = d.get("port", "")
    port_str = f"[dim]:{port}[/dim]" if port else "[dim](file)[/dim]"
    return f"{dot} {escape(title)}  {port_str}"


def fmt_folder_row(name: str, agent: dict | None, live: bool, waiting: bool) -> str:
    dot = "[green]●[/green]" if live else "[dim]○[/dim]"
    suffix = ""
    if agent:
        cost = agent.get("cost_usd", 0) or 0
        if cost:
            suffix = f"  [dim]${cost:.2f}[/dim]"
    return f"{dot} [bold]{name}[/bold]{suffix}"


def fmt_worker_row(session: str, item: str, live: bool, waiting: bool, erroring: bool = False, exited: bool = False) -> str:
    if exited:
        label = escape(item) if item else escape(session)
        return f"    [dim]↳[/dim] (˶˃ᆺ ˂˶) [dim]{label}[/dim]"
    if waiting:
        dot = "[bold yellow]！[/bold yellow]"
    elif erroring:
        dot = "[bold red]✗[/bold red]"
    elif live:
        dot = "[#86efac]✿[/#86efac]"
    else:
        dot = "[dim]✧[/dim]"
    label = escape(item) if item else escape(session)
    return f"    [dim]↳[/dim] {dot} [dim]{label}[/dim]"


def fmt_session_row(session: str, live: bool, waiting: bool, ctx_pct: float | None = None, erroring: bool = False, exited: bool = False) -> str:
    if exited:
        return f"  (˶˃ᆺ ˂˶) {session}"
    if waiting:
        dot = "[bold yellow]！[/bold yellow]"
    elif erroring:
        dot = "[bold red]✗[/bold red]"
    elif live:
        dot = "[#86efac]✿[/#86efac]"
    else:
        dot = "[dim]✧[/dim]"
    suffix = f"  [dim]{ctx_pct:.0f}%[/dim]" if ctx_pct else ""
    return f"  {dot} {session}{suffix}"


def fmt_search_result_row(session: str, snippet: str) -> str:
    return f"  [dim]⌕[/dim] {escape(session)}  [dim]{escape(snippet[:50])}[/dim]"


def parse_todo_items(cwd: str) -> list[dict]:
    jsonl = _latest_jsonl(cwd)
    if jsonl is None:
        return []
    todos: list = []
    try:
        with open(jsonl, errors="replace") as f:
            for line in f:
                try:
                    event = json.loads(line)
                    if event.get("type") != "assistant":
                        continue
                    content = event.get("message", {}).get("content", [])
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if block.get("type") == "tool_use" and block.get("name") == "TodoWrite":
                            raw = block.get("input", {}).get("todos", [])
                            if raw:
                                todos = raw
                except (json.JSONDecodeError, KeyError, AttributeError):
                    continue
    except OSError:
        return []
    return [{"text": t.get("content", ""), "status": t.get("status", "pending")} for t in todos]


def search_session_logs(sessions_info: list[tuple[str, str]], query: str) -> dict[str, str]:
    results = {}
    query_lower = query.lower()
    for session_name, cwd in sessions_info:
        messages = load_chat_messages(cwd, max_messages=100)
        for msg in reversed(messages):
            if query_lower in msg["text"].lower():
                text = msg["text"]
                idx = text.lower().find(query_lower)
                start = max(0, idx - 15)
                snippet = text[start:start + 60].replace("\n", " ").strip()
                results[session_name] = snippet
                break
    return results


def extract_last_assistant_message(pane_text: str) -> str:
    lines = pane_text.splitlines()
    result = []
    for line in reversed(lines):
        stripped = line.rstrip()
        if stripped or result:
            result.append(stripped)
        if len(result) >= 8:
            break
    if not result:
        return "(no output)"
    return "\n".join(reversed(result))


def fmt_todo_lines(todos: list[dict]) -> list[str]:
    lines = []
    for t in todos:
        s = t["status"]
        if s == "completed":
            dot, txt = "[dim]✓[/dim]", f"[dim]{escape(t['text'])}[/dim]"
        elif s == "in_progress":
            dot, txt = "[#86efac]●[/#86efac]", escape(t["text"])
        else:
            dot, txt = "[dim]○[/dim]", f"[dim]{escape(t['text'])}[/dim]"
        lines.append(f" {dot} {txt}")
    return lines
