#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""claude-hand chat channel: Unity / Blender chat panels <-> the supervisor Claude Code session.

Claude Code starts this file as an MCP server (stdio, registered in the repo's .mcp.json) and,
when the session is launched with start-supervisor.cmd / .sh, i.e.
  claude --dangerously-load-development-channels server:claude-hand-chat
treats it as a *channel*: a message typed in a Unity or Blender chat panel is pushed into the
running supervisor session (notifications/claude/channel), tagged with the window it came from
and the claude-hand subagent that owns that window. The supervisor answers with the `reply`
tool, and the answer shows up in the same panel. Tool-permission prompts are relayed to the
panels too (Allow / Deny buttons).

The panels talk to this process over HTTP on 127.0.0.1 only. Every request must carry the
token this process writes to ~/.claude-hand/chat/server.json, so only programs running as you
can post into the session (an open port would be a prompt-injection hole).

Only the supervisor session serves the panels. It counts as the supervisor when
  - CLAUDE_HAND_SUPERVISOR=1 reaches it (start-supervisor sets it; chat-setup's .mcp.json passes it
    on explicitly through `env`, since Claude Code is not documented to pass its whole environment), or
  - a parent process is Claude Code started with this server as a channel
    (`--dangerously-load-development-channels server:claude-hand-chat`), however it was launched.
Any other Claude Code session opened in this folder also starts this server (it is in .mcp.json)
but stays passive, so a session without the channel cannot swallow panel messages. So does a copy
started by `claude mcp get/list` to show its status (chat-status, doctor and update run that, and
inside the supervisor it inherits CLAUDE_HAND_SUPERVISOR=1), and so does any start while another
supervisor still answers: replacing a live server.json would cut the panels off. Every start
writes ~/.claude-hand/chat/status.json (serving / passive and why) so panels and
`claude_hand.py chat-status` can say what is wrong.

  python tools/chat_channel.py --check   checks that this machine can run the channel (used by setup)

No third-party packages; run with `uv run` or any Python 3.10+.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_hand import GENERATED_MARKER, refresh_agents, slug, supervisor_running  # noqa: E402

SERVER_NAME = "claude-hand-chat"
SERVER_VERSION = "0.1.0"
DEFAULT_PORT = 8765
PORT_ATTEMPTS = 10
MAX_TEXT = 8000
MAX_CONTEXT = 3000
MAX_WAIT_SECONDS = 30
MAX_CONTEXT_ITEMS = 20  # selected objects / assets kept per window
LOG_LIMIT = 100  # items kept per chat: panels show recent history; the conversation itself lives in Claude Code
AUTO_SYNC_INTERVAL = 20.0  # seconds between automatic subagent refreshes for the same window

INSTRUCTIONS = (
    "This channel connects chat panels inside Unity and Blender windows to you. A message arrives as "
    '<channel source="claude-hand-chat" chat_id="..." app="unity|blender" window="..." agent="...">. '
    "The text is from the user, who is sitting in that window and cannot see your terminal output, so always "
    "answer with the reply tool, passing the chat_id from the tag; reply in the user's language. "
    "Routing: the agent attribute names the claude-hand subagent that owns that window (unity-* / blender-*). "
    "Hand the request to that subagent. For the same chat_id keep resuming the same subagent with SendMessage "
    "instead of starting a new one, so each window keeps its own conversation. Run subagents in the background so you "
    "keep receiving other windows' messages (and the Claude app shows them under Remote Control); reply when they finish. When a request involves other "
    "windows (moving objects between projects, Blender to Unity), coordinate the other subagents as the claude-hand "
    "skill describes, and report back in the chat_id the request came from. If agent is \"none\", the window's subagent could "
    "not be made automatically: reply that MCP for Unity should show Connected (Window > MCP for Unity) or the "
    "MCP for Blender server should be on, then ask them to send the message again. For work that takes "
    "more than a few seconds, send a short reply first saying what you are doing, then the result. "
    "Messages without a <channel> tag come from the terminal or the Claude app (Remote Control). When such a request "
    "is about a Unity or Blender window and says 'this', 'the selected one' or 'here' without naming it, call "
    "current_context to see what is selected where and which window was used last. After work requested that way "
    "finishes, also call reply for each window it changed, with that window's chat_id from current_context, "
    "origin \"desktop\" and request set to a one-line summary of what was asked, so the window's chat panel keeps its whole history. "
    "Claude Hand resumes this same conversation when it is restarted, but subagents started before a restart may be gone: "
    "if SendMessage to one fails, hand the window's request to its subagent anew."
)
HISTORY_KINDS = ("user", "reply")  # what a panel shows again after a restart; permission prompts do not outlive it

REPLY_TOOL = {
    "name": "reply",
    "description": "Send a message to a Unity or Blender chat panel. Pass the chat_id from the <channel> tag.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "The chat (window) to reply in, from the channel tag"},
            "text": {"type": "string", "description": "The message to show in that panel"},
            "origin": {"type": "string", "enum": ["panel", "desktop"],
                       "description": "desktop: the request came from the terminal or the Claude app, not from this panel"},
            "request": {"type": "string", "description": "With origin desktop: one line saying what was asked"},
        },
        "required": ["chat_id", "text"],
    },
}

PROGRESS_TTL = 300.0  # seconds without news after which a panel stops showing a bar (a long import is one action)

CONTEXT_TOOL = {
    "name": "current_context",
    "description": "What the user has open in Unity and Blender right now, most recently used window first: per window its "
                   "chat_id, subagent, active scene or file, selected objects and assets, whether it has focus, and when "
                   "it was last used. Use it to tell which window 'this', 'the selected one' or 'here' means.",
    "inputSchema": {"type": "object", "properties": {}},
}


def clip(text: str, limit: int) -> str:
    """`text` cut to `limit` characters, saying so, so neither the panel nor Claude mistakes it for the whole."""
    if len(text) <= limit:
        return text
    note = f"\n…(너무 길어 앞의 {limit}자만 보냅니다)"
    return text[:limit - len(note)] + note


def log(*parts) -> None:
    print("[claude-hand-chat]", *parts, file=sys.stderr, flush=True)  # stdout is the MCP protocol


def claude_hand_home(env=os.environ) -> Path:
    return Path(env.get("CLAUDE_HAND_HOME") or Path.home() / ".claude-hand")


def default_agents_dir(env=os.environ) -> Path:
    return Path(env.get("CLAUDE_HAND_AGENTS_DIR") or Path.home() / ".claude" / "agents")


# --------------------------------------------------------------------------- window -> subagent


@dataclass
class AgentEntry:
    name: str
    app: str
    pin: str


def load_agents(directory: Path) -> list[AgentEntry]:
    """Read the subagents `claude_hand.py sync` generated (name, app, pinned instance)."""
    entries = []
    if not directory.is_dir():
        return entries
    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if GENERATED_MARKER not in text:
            continue
        name = re.search(r"^name:\s*(\S+)\s*$", text, re.M)
        pin = re.search(r'--pin \\"(.+?)\\"', text)
        app = re.search(r"--app (unity|blender)\b", text)
        if name and pin:
            entries.append(AgentEntry(name.group(1), app.group(1) if app else "unity", pin.group(1)))
    return entries


def resolve_window(agents: list[AgentEntry], app: str, window: str, pin: str = "") -> tuple[str, str | None]:
    """(chat_id, agent name or None). One chat per window; chat_id is the owning subagent's name when known."""
    candidates = [a for a in agents if a.app == app]
    exact = [a for a in candidates if pin and a.pin == pin]
    if not exact and app == "unity":
        exact = [a for a in candidates if a.pin.partition("@")[0] == window]
    if len(exact) == 1:
        return exact[0].name, exact[0].name
    return f"{app}-{slug(window or 'window')}", None


# --------------------------------------------------------------------------- shared state


class ChatHub:
    """Chats, replies and permission requests, shared by the MCP loop and the HTTP threads."""

    def __init__(self, emit, agents_dir: Path, auto_sync=None):
        self.emit = emit  # callable(dict): writes one JSON-RPC message to Claude Code
        self.agents_dir = agents_dir
        self.auto_sync = auto_sync  # callable(agents_dir): makes subagents for open windows (None = never)
        self._last_sync: dict[tuple[str, str], float] = {}
        self.cond = threading.Condition()
        self.seq = 0
        self.logs: dict[str, list[dict]] = {}
        self.windows: dict[str, dict] = {}
        self.permissions: dict[str, dict] = {}
        self.contexts: dict[str, dict] = {}
        self.backlog: list[dict] = []  # channel messages that arrived before Claude Code finished connecting
        self.initialized = False
        self.in_use = False  # Claude has called a tool or asked a permission: the session is past its start-up prompts
        self.on_first_use = None  # callable(): run once when in_use becomes true (minimizes the Claude Hand window)
        self.history_dir: Path | None = None  # where each chat's recent messages are kept across restarts
        self.progress: dict[str, dict] = {}  # chat_id -> what its subagent is doing now (shown as a bar in the panel)
        self.progress_rev: dict[str, int] = {}  # bumped on every change, so a waiting panel wakes up

    def use_history(self, directory: Path) -> None:
        """Keep each chat's recent messages in `directory` and show the saved ones again, so a panel still has its
        conversation after Claude Hand restarts. Numbered afresh: panels start over when the server changes."""
        self.history_dir = directory
        with self.cond:
            for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
                try:
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    chat_id, items = str(saved["chat_id"]), saved["items"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                kept = [dict(item) for item in items if isinstance(item, dict) and item.get("kind") in HISTORY_KINDS]
                for item in kept[-LOG_LIMIT:]:
                    self.seq += 1
                    item["seq"] = self.seq
                    self.logs.setdefault(chat_id, []).append(item)

    def _save_history(self, chat_id: str) -> None:
        items = [item for item in self.logs.get(chat_id, []) if item.get("kind") in HISTORY_KINDS]
        name = re.sub(r"[^\w.-]", "_", chat_id) or "chat"
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.history_dir / f"{name}.{os.getpid()}.tmp"
            tmp.write_text(json.dumps({"chat_id": chat_id, "items": items}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.history_dir / f"{name}.json")
        except OSError as exc:  # a full disk or a locked file must not stop the chat
            log("could not save the chat history:", exc)

    def _append(self, chat_id: str, item: dict) -> dict:
        self.seq += 1
        item = {"seq": self.seq, "ts": time.time(), **item}
        log_items = self.logs.setdefault(chat_id, [])
        log_items.append(item)
        del log_items[:-LOG_LIMIT]
        if self.history_dir is not None and item.get("kind") in HISTORY_KINDS:
            self._save_history(chat_id)
        self.cond.notify_all()
        return item

    def resolve(self, app: str, window: str, pin: str = "") -> tuple[str, str | None]:
        chat_id, agent = resolve_window(load_agents(self.agents_dir), app, window, pin)
        if agent is None and self.auto_sync is not None:
            # A window opened after the supervisor started: make its subagent now instead of asking
            # the user to run `sync`. Claude Code picks up new files in ~/.claude/agents by itself.
            with self.cond:
                now = time.time()
                due = now - self._last_sync.get((app, window), 0.0) >= AUTO_SYNC_INTERVAL
                if due:
                    self._last_sync[(app, window)] = now
            if due:
                try:
                    self.auto_sync(self.agents_dir)
                except (Exception, SystemExit) as exc:  # sync refuses to overwrite hand-written files
                    log("automatic subagent refresh failed:", exc)
                chat_id, agent = resolve_window(load_agents(self.agents_dir), app, window, pin)
        return chat_id, agent

    def post_user_message(self, app: str, window: str, text: str, pin: str = "", context: str = "") -> dict:
        chat_id, agent = self.resolve(app, window, pin)
        content = text if not context else f"{text}\n\n[{app} 창 정보: {window}]\n{context}"
        meta = {"chat_id": chat_id, "app": app, "window": window, "agent": agent or "none"}
        with self.cond:
            self.windows[chat_id] = {"app": app, "window": window, "agent": agent, "last_seen": time.time()}
            self._append(chat_id, {"kind": "user", "text": text})
            for pending in self.permissions.values():  # a window that joins late still sees open prompts
                if not pending.get("resolved") and chat_id not in pending["chats"]:
                    pending["chats"].add(chat_id)
                    self._append(chat_id, self._permission_item(pending))
            self._touch(chat_id, app, window, agent)
            notification = {"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {"content": content, "meta": meta}}
            ready = self.initialized
            if not ready:
                self.backlog.append(notification)
        if ready:
            self.emit(notification)
        return {"chat_id": chat_id, "agent": agent, "delivered": ready}

    def mark_initialized(self) -> None:
        """Claude Code finished connecting: send what panels posted while it was starting (lazy start)."""
        with self.cond:
            self.initialized = True
            backlog, self.backlog = self.backlog, []
        for notification in backlog:
            self.emit(notification)

    def known_chat(self, chat_id: str) -> bool:
        """A chat a panel has used, or a window's subagent (so work asked in the Claude app can be recorded there)."""
        return chat_id in self.logs or any(a.name == chat_id for a in load_agents(self.agents_dir))

    def reply(self, chat_id: str, text: str, origin: str = "panel", request: str = "") -> bool:
        if not self.known_chat(chat_id):
            return False
        item = {"kind": "reply", "text": text}
        if origin == "desktop":
            item.update(origin="desktop", request=request[:500])
        with self.cond:
            self._append(chat_id, item)
            if self.progress.pop(chat_id, None) is not None:  # the answer is in: the bar goes away
                self._bump_progress(chat_id)
        return True

    def _touch(self, chat_id: str, app: str, window: str, agent: str | None) -> None:
        entry = self.contexts.setdefault(chat_id, {"chat_id": chat_id})
        entry.update(app=app, window=window, agent=agent or "none", last_used=time.time())

    def update_context(self, app: str, window: str, pin: str, context: dict) -> str:
        """A Unity or Blender window reports what is selected and open (no chat message needed)."""
        chat_id, agent = resolve_window(load_agents(self.agents_dir), app, window, pin)
        clean = {}
        for key in ("scene", "file", "active", "mode"):
            if isinstance(context.get(key), str):
                clean[key] = context[key][:300]
        for key in ("selection", "assets"):
            if isinstance(context.get(key), list):
                clean[key] = [str(v)[:300] for v in context[key][:MAX_CONTEXT_ITEMS]]
        if isinstance(context.get("focused"), bool):
            clean["focused"] = context["focused"]
        with self.cond:
            self.logs.setdefault(chat_id, [])
            entry = self.contexts.setdefault(chat_id, {"chat_id": chat_id})
            previous = {k: entry.get(k) for k in ("scene", "file", "active", "selection", "assets")}
            entry.update(app=app, window=window, agent=agent or "none", reported=time.time())
            for key in ("scene", "file", "active", "mode", "selection", "assets", "focused"):
                entry.pop(key, None)
            entry.update(clean)
            changed = previous != {k: entry.get(k) for k in ("scene", "file", "active", "selection", "assets")}
            if changed or clean.get("focused"):
                entry["last_used"] = time.time()
        return chat_id

    def current_context(self, now: float | None = None) -> dict:
        now = now or time.time()
        with self.cond:
            windows = [dict(w) for w in self.contexts.values()]
        windows.sort(key=lambda w: (bool(w.get("focused")), w.get("last_used", 0)), reverse=True)
        for w in windows:
            for key in ("last_used", "reported"):
                if key in w:
                    w[key + "_seconds_ago"] = round(now - w.pop(key))
        return {"windows": windows,
                "note": "Most recently used first. A window that is not listed has not reported since this session "
                        "started (its Claude Hand Tools / chat add-on may be missing)."}

    @staticmethod
    def _permission_item(pending: dict) -> dict:
        return {"kind": "permission", "request_id": pending["request_id"], "tool_name": pending["tool_name"],
                "description": pending["description"], "input_preview": pending["input_preview"]}

    def mark_in_use(self) -> None:
        with self.cond:
            first = not self.in_use
            self.in_use = True
        if first and self.on_first_use is not None:
            try:
                self.on_first_use()
            except Exception as exc:  # a window that will not minimize is no reason to fail a tool call
                log("could not minimize the Claude Hand window:", exc)

    def permission_request(self, params: dict) -> None:
        """Claude Code asks for a tool approval; show it in every chat panel that is open."""
        self.mark_in_use()
        request_id = str(params.get("request_id", ""))
        if not request_id:
            return
        pending = {
            "request_id": request_id,
            "tool_name": str(params.get("tool_name", "")),
            "description": str(params.get("description", "")),
            "input_preview": str(params.get("input_preview", "")),
            "resolved": False,
            "chats": set(),
        }
        with self.cond:
            self.permissions[request_id] = pending
            for chat_id in self.logs:
                pending["chats"].add(chat_id)
                self._append(chat_id, self._permission_item(pending))

    def permission_verdict(self, request_id: str, behavior: str) -> bool:
        if behavior not in ("allow", "deny"):
            return False
        with self.cond:
            pending = self.permissions.get(request_id)
            if pending is None or pending["resolved"]:
                return False
            pending["resolved"] = True
            for chat_id in pending["chats"]:
                self._append(chat_id, {"kind": "permission_resolved", "request_id": request_id, "behavior": behavior})
        self.emit({"jsonrpc": "2.0", "method": "notifications/claude/channel/permission",
                   "params": {"request_id": request_id, "behavior": behavior}})
        return True

    def updates(self, chat_id: str, after: int, wait: float) -> list[dict]:
        return self.poll(chat_id, after, wait)["items"]

    def poll(self, chat_id: str, after: int, wait: float, progress_rev: int | None = None) -> dict:
        """New items after `after`, and the progress bar; returns early when either changes (the bar only for
        a panel that says which revision it has)."""
        deadline = time.time() + max(0.0, min(wait, MAX_WAIT_SECONDS))
        with self.cond:
            if chat_id and chat_id not in self.logs:
                self.logs[chat_id] = []  # an open panel that has not sent anything yet still gets permission prompts
            while True:
                items = [i for i in self.logs.get(chat_id, []) if i["seq"] > after]
                rev = self.progress_rev.get(chat_id, 0)
                remaining = deadline - time.time()
                if items or (progress_rev is not None and rev != progress_rev) or remaining <= 0:
                    return {"items": items, "progress": self._current_progress(chat_id), "progress_rev": rev}
                self.cond.wait(remaining)

    def _progress_entry(self, chat_id: str, now: float) -> dict:
        entry = self.progress.get(chat_id)
        if entry is None or now - entry["updated"] > PROGRESS_TTL:  # a new request
            entry = self.progress[chat_id] = {"agent": chat_id, "started": now, "actions": 0, "updated": now}
        return entry

    def _bump_progress(self, chat_id: str) -> None:
        self.progress_rev[chat_id] = self.progress_rev.get(chat_id, 0) + 1
        self.cond.notify_all()

    def report_action(self, app: str, pin: str, tool: str, detail: str) -> dict | None:
        """A window's subagent is about to run a Unity/Blender tool (its guard hook says so): show it in that
        window's panel. new_request is true for the first action of a request (the guard sets an undo point then),
        with label: the panel message it answers, if any. None for a pin no generated subagent has."""
        agent = next((a for a in load_agents(self.agents_dir) if a.app == app and a.pin == pin), None)
        if agent is None:
            return None
        with self.cond:
            entry = self._progress_entry(agent.name, time.time())
            first = not entry.get("marked")
            entry.update(tool=tool[:80], detail=detail[:120], actions=entry["actions"] + 1, updated=time.time(), marked=True)
            self._bump_progress(agent.name)
            label = self._open_request(agent.name) if first else ""
        return {"chat_id": agent.name, "new_request": first, "label": label}

    def _open_request(self, chat_id: str) -> str:
        """The panel message not answered yet, in a few words ("" when the request came from elsewhere)."""
        for item in reversed(self.logs.get(chat_id, [])):
            if item.get("kind") == "reply":
                return ""
            if item.get("kind") == "user":
                return " ".join(str(item.get("text", "")).split())[:40]
        return ""

    def _current_progress(self, chat_id: str) -> dict | None:
        entry = self.progress.get(chat_id)
        now = time.time()
        if entry is None or now - entry["updated"] > PROGRESS_TTL:
            return None
        return {**entry, "elapsed": round(now - entry["started"])}

    def status(self) -> dict:
        with self.cond:
            return {"ok": True, "server": SERVER_NAME, "version": SERVER_VERSION, "claude_connected": self.initialized,
                    "in_use": self.in_use, "chats": sorted(self.logs),
                    "open_permissions": sum(not p["resolved"] for p in self.permissions.values())}


# --------------------------------------------------------------------------- MCP over stdio


class StdioMcp:
    def __init__(self, stdin, stdout, agents_dir: Path):
        self.stdin, self.stdout = stdin, stdout
        self.write_lock = threading.Lock()
        self.hub = ChatHub(self.send, agents_dir)

    def send(self, message: dict) -> None:
        line = json.dumps(message, ensure_ascii=False)
        with self.write_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def handle(self, message: dict) -> None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {
                    "experimental": {"claude/channel": {}, "claude/channel/permission": {}},
                    "tools": {},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            }})
        elif method == "notifications/initialized":
            self.hub.mark_initialized()
        elif method == "ping":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method == "tools/list":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [REPLY_TOOL, CONTEXT_TOOL]}})
        elif method == "tools/call":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": self.call_tool(params)})
        elif method == "notifications/claude/channel/permission_request":
            self.hub.permission_request(params)
        elif msg_id is not None:
            self.send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
        # other notifications (cancelled, roots changed, ...) need no answer

    def call_tool(self, params: dict) -> dict:
        name = params.get("name")
        if name not in ("reply", CONTEXT_TOOL["name"]):
            return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool {name!r}"}]}
        self.hub.mark_in_use()
        if name == CONTEXT_TOOL["name"]:
            return {"content": [{"type": "text", "text": json.dumps(self.hub.current_context(), ensure_ascii=False)}]}
        args = params.get("arguments") or {}
        chat_id, text = str(args.get("chat_id", "")), str(args.get("text", ""))
        origin = "desktop" if args.get("origin") == "desktop" else "panel"
        if not text.strip():
            return {"isError": True, "content": [{"type": "text", "text": "text is empty"}]}
        if not self.hub.reply(chat_id, clip(text, MAX_TEXT), origin, clip(str(args.get("request", "")), MAX_CONTEXT)):
            return {"isError": True, "content": [{"type": "text", "text": f"Unknown chat_id {chat_id!r}. Use the chat_id from the "
                                                  "<channel> tag, or a window's chat_id from current_context."}]}
        if len(text) > MAX_TEXT:
            return {"content": [{"type": "text", "text": f"sent, but cut at {MAX_TEXT} characters: the panel shows only the "
                                                         "start. Send long results as a short summary, or in several replies."}]}
        return {"content": [{"type": "text", "text": "sent"}]}

    def serve(self) -> None:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                log("ignoring a line that is not JSON")
                continue
            try:
                self.handle(message)
            except Exception as exc:  # keep the channel alive whatever one message does
                log("error handling message:", exc)
                if isinstance(message, dict) and message.get("id") is not None:
                    self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": str(exc)}})


# --------------------------------------------------------------------------- HTTP for the panels


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "claude-hand-chat/" + SERVER_VERSION

    def log_message(self, fmt, *args):  # quiet; stdout is reserved for MCP
        pass

    @property
    def hub(self) -> ChatHub:
        return self.server.hub  # type: ignore[attr-defined]

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        token = self.headers.get("X-Claude-Hand-Token", "")
        if secrets.compare_digest(token, self.server.token):  # type: ignore[attr-defined]
            return True
        self._json(401, {"ok": False, "error": "missing or wrong X-Claude-Hand-Token"})
        return False

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}

    def do_GET(self):
        if not self._authorized():
            return
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/v1/status":
            self._json(200, self.hub.status())
        elif url.path == "/v1/resolve":
            chat_id, agent = self.hub.resolve(q.get("app", ""), q.get("window", ""), q.get("pin", ""))
            self._json(200, {"ok": True, "chat_id": chat_id, "agent": agent})
        elif url.path == "/v1/updates":
            try:
                after, wait = int(q.get("after", "0")), float(q.get("wait", "0"))
                prev = int(q["prev"]) if "prev" in q else None  # the progress revision the panel already shows
            except ValueError:
                self._json(400, {"ok": False, "error": "after, wait and prev must be numbers"})
                return
            result = self.hub.poll(q.get("chat_id", ""), after, wait, prev)
            self._json(200, {"ok": True, **result, "next": max([after] + [i["seq"] for i in result["items"]])})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        try:
            body = self._body()
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        path = urlparse(self.path).path
        if path == "/v1/messages":
            app, text = str(body.get("app", "")), str(body.get("text", "")).strip()
            if app not in ("unity", "blender") or not text:
                self._json(400, {"ok": False, "error": "app must be unity or blender, text must not be empty"})
                return
            result = self.hub.post_user_message(app, str(body.get("window", ""))[:200], clip(text, MAX_TEXT),
                                                pin=str(body.get("pin", ""))[:200], context=str(body.get("context", ""))[:MAX_CONTEXT])
            self._json(200, {"ok": True, **result})
        elif path == "/v1/permission":
            ok = self.hub.permission_verdict(str(body.get("request_id", "")), str(body.get("behavior", "")))
            self._json(200 if ok else 409, {"ok": ok})
        elif path == "/v1/context":
            app, context = str(body.get("app", "")), body.get("context")
            if app not in ("unity", "blender") or not isinstance(context, dict):
                self._json(400, {"ok": False, "error": "app must be unity or blender, context must be an object"})
                return
            chat_id = self.hub.update_context(app, str(body.get("window", ""))[:200], str(body.get("pin", ""))[:200], context)
            self._json(200, {"ok": True, "chat_id": chat_id})
        elif path == "/v1/progress":  # a subagent's guard hook, just before each Unity/Blender tool call
            result = self.hub.report_action(str(body.get("app", "")), str(body.get("pin", ""))[:200],
                                            str(body.get("tool", "")), str(body.get("detail", "")))
            self._json(200 if result else 404, {"ok": result is not None, **(result or {})})
        else:
            self._json(404, {"ok": False, "error": "not found"})


class PanelServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind a port that is already listening, so two
    # channel servers could share 8765 and answer each other's panels. Take the next free port instead.
    allow_reuse_address = os.name != "nt"


def start_http(hub: ChatHub, token: str, port: int | None = None, attempts: int = PORT_ATTEMPTS) -> ThreadingHTTPServer:
    first = port or DEFAULT_PORT
    last_error = None
    for candidate in range(first, first + attempts):
        try:
            httpd = PanelServer(("127.0.0.1", candidate), PanelHandler)
        except OSError as exc:
            last_error = exc
            continue
        httpd.daemon_threads = True
        httpd.hub = hub  # type: ignore[attr-defined]
        httpd.token = token  # type: ignore[attr-defined]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    raise OSError(f"no free port in {first}-{first + attempts - 1}: {last_error}")


def write_server_file(home: Path, port: int, token: str) -> Path:
    """Where the panels find this process. main() only gets here when no other supervisor answers."""
    chat_dir = home / "chat"
    chat_dir.mkdir(parents=True, exist_ok=True)
    target = chat_dir / "server.json"
    tmp = chat_dir / f"server.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid(), "started": time.time()}), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return target


def remove_server_file(path: Path) -> None:
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def is_supervisor(env=os.environ) -> bool:
    return env.get("CLAUDE_HAND_SUPERVISOR", "").strip().lower() in ("1", "true", "yes", "on")


CHANNEL_FLAG_RE = re.compile(r"--(dangerously-load-development-channels|channels)\b")
HEALTH_CHECK_RE = re.compile(r"\smcp\s+(get|list)\b")  # `claude mcp get/list` starts each server once to show its status
HEALTH_CHECK_REASON = "claude mcp get/list가 상태 확인용으로 띄운 실행"


def process_table(run=subprocess.run, platform=sys.platform) -> dict[int, tuple[int, str]]:
    """{pid: (parent pid, command line)} for every process, with no third-party packages."""
    if platform == "win32":
        script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine "
                  "| ConvertTo-Json -Compress")
        proc = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                   text=True, errors="replace", timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = json.loads(proc.stdout or "[]")
        rows = rows if isinstance(rows, list) else [rows]
        return {int(r["ProcessId"]): (int(r.get("ParentProcessId") or 0), r.get("CommandLine") or "") for r in rows}
    proc = run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "args="], capture_output=True, text=True,
               errors="replace", timeout=30)
    table = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def executable_of(cmdline: str) -> str:
    """Lower-case file name of the program in a command line ("C:/x/claude.exe" -> "claude.exe")."""
    cmdline = cmdline.strip()
    if cmdline.startswith('"'):
        exe = cmdline[1:].split('"', 1)[0]
    else:
        exe = cmdline.split(None, 1)[0] if cmdline else ""
    return re.split(r"[/\\]", exe)[-1].lower()


def is_claude_code(cmdline: str) -> bool:
    exe = executable_of(cmdline)
    if "claude" in exe:
        return True
    return exe.removesuffix(".exe") in ("node", "bun") and "claude" in cmdline.lower()  # npm-installed CLI


def claude_ancestor_pid(table: dict[int, tuple[int, str]], pid: int, max_depth: int = 12) -> int | None:
    """Pid of the nearest Claude Code process above us; uv, shells and other wrappers are skipped."""
    seen = set()
    current = table.get(pid, (0, ""))[0]
    for _ in range(max_depth):
        if not current or current in seen or current not in table:
            return None
        seen.add(current)
        parent, cmdline = table[current]
        if is_claude_code(cmdline):
            return current
        current = parent
    return None


def claude_ancestor(table: dict[int, tuple[int, str]], pid: int, max_depth: int = 12) -> str | None:
    """Command line of the nearest Claude Code process above us."""
    found = claude_ancestor_pid(table, pid, max_depth)
    return table[found][1] if found else None


def channel_ancestor(table: dict[int, tuple[int, str]], pid: int, max_depth: int = 12) -> str | None:
    """Command line of the Claude Code process that started us, if it loads this server as a channel.
    Only the nearest Claude Code ancestor counts."""
    cmdline = claude_ancestor(table, pid, max_depth)
    if cmdline and f"server:{SERVER_NAME}" in cmdline and CHANNEL_FLAG_RE.search(cmdline):
        return cmdline
    return None


def supervisor_decision(env=os.environ, table_fn=process_table, pid: int | None = None) -> tuple[bool, str]:
    try:
        claude = claude_ancestor(table_fn(), pid or os.getpid())
    except Exception as exc:  # never let the check stop the server from starting
        if is_supervisor(env):
            return True, "CLAUDE_HAND_SUPERVISOR=1 (start-supervisor)"
        return False, f"채널 플래그 없이 연 세션으로 보임 (부모 프로세스 확인 실패: {exc})"
    if claude and HEALTH_CHECK_RE.search(claude):
        # Run inside the supervisor, `claude mcp get` passes CLAUDE_HAND_SUPERVISOR=1 on to this copy too.
        return False, HEALTH_CHECK_REASON
    if is_supervisor(env):
        return True, "CLAUDE_HAND_SUPERVISOR=1 (start-supervisor)"
    if claude and f"server:{SERVER_NAME}" in claude and CHANNEL_FLAG_RE.search(claude):
        return True, "Claude Code가 이 서버를 채널로 불러옴"
    return False, "채널 플래그 없이 연 Claude Code 세션 (감독 세션은 start-supervisor.cmd로 여세요)"


SW_MINIMIZE = 6


def console_owners(table: dict[int, tuple[int, str]], claude_pid: int, levels: int = 2) -> set[int]:
    """Claude Code and the processes above it. Windows reports a console window as belonging to the first
    process of that console, i.e. the cmd.exe running start-supervisor, not Claude Code itself."""
    owners, current = set(), claude_pid
    for _ in range(levels):
        if not current:
            break
        owners.add(current)
        current = table.get(current, (0, ""))[0]
    return owners


def console_windows(owners: set[int]) -> list[int]:
    """Visible top-level classic console windows (not Windows Terminal, whose window holds other tabs too) owned
    by one of `owners`. Hidden ones are skipped: minimizing a hidden console would put it on the taskbar.
    Only calls that never wait on the window's thread."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == "ConsoleWindowClass" and user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in owners:
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def minimize_after_start(home: Path) -> bool:
    """config.json "minimize_after_start" (default on): tuck the Claude Hand window away once it is in use."""
    try:
        return json.loads((home / "config.json").read_text(encoding="utf-8")).get("minimize_after_start", True) is not False
    except (OSError, ValueError):
        return True


def minimize_console(owners: set[int], find=console_windows, show=None, platform=sys.platform) -> bool:
    """Minimize the console window Claude Code runs in (Windows only; elsewhere the terminal is left alone).
    ShowWindowAsync only posts the request: attaching to that console and calling ShowWindow from a helper
    process hung for the full timeout under Claude Code on Windows 10 and never minimized anything."""
    if platform != "win32" or not owners:
        return False
    windows = find(owners)
    if show is None:
        import ctypes
        show = lambda hwnd: ctypes.windll.user32.ShowWindowAsync(hwnd, SW_MINIMIZE)
    for hwnd in windows:
        show(hwnd)
    return bool(windows)


def write_status(home: Path, mode: str, reason: str, port: int | None = None) -> Path:
    """Last start of the channel, for panels and `claude_hand.py chat-status`."""
    chat_dir = home / "chat"
    chat_dir.mkdir(parents=True, exist_ok=True)
    target = chat_dir / "status.json"
    tmp = chat_dir / f"status.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"mode": mode, "reason": reason, "pid": os.getpid(), "port": port, "time": time.time(),
                               "version": SERVER_VERSION}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)
    return target


def self_check(env=os.environ) -> dict:
    """What setup runs once: proves Python, the home folder and a loopback port all work here.
    Running it through `uv run` also makes uv fetch Python now instead of during Claude Code's
    30-second MCP startup window."""
    checks = {"python": sys.version.split()[0], "python_ok": sys.version_info >= (3, 10)}
    home = claude_hand_home(env) / "chat"
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / f"check.{os.getpid()}.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["home_ok"] = True
    except OSError as exc:
        checks["home_ok"] = False
        checks["home_error"] = str(exc)
    checks["home"] = str(home)
    try:
        hub = ChatHub(lambda message: None, default_agents_dir(env))
        port = int(env["CLAUDE_HAND_CHAT_PORT"]) if env.get("CLAUDE_HAND_CHAT_PORT") else None
        httpd = start_http(hub, secrets.token_urlsafe(8), port)
        checks["port"] = httpd.server_address[1]
        httpd.shutdown()
        httpd.server_close()
        checks["port_ok"] = True
    except OSError as exc:
        checks["port_ok"] = False
        checks["port_error"] = str(exc)
    checks["agents"] = [a.name for a in load_agents(default_agents_dir(env))]
    checks["ok"] = checks["python_ok"] and checks["home_ok"] and checks["port_ok"]
    return checks


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdin.reconfigure(encoding="utf-8")
    if "--check" in argv:
        result = self_check()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    mcp = StdioMcp(sys.stdin, sys.stdout, default_agents_dir())
    home = claude_hand_home()
    serving, reason = supervisor_decision()
    live = supervisor_running(home)
    if serving and live:
        serving, reason = False, "다른 감독 세션이 이미 채팅창을 받고 있음"
    if not serving:
        # Same tools, no panels: messages only reach the session that has the channel.
        log("passive:", reason)
        if reason != HEALTH_CHECK_REASON and not live:  # a status check or a second session keeps the real status
            try:
                write_status(home, "passive", reason)
            except OSError:
                pass
        mcp.serve()
        return 0
    mcp.hub.auto_sync = lambda agents_dir: refresh_agents(agents_dir=agents_dir)
    mcp.hub.use_history(home / "chat" / "history")
    if sys.platform == "win32" and minimize_after_start(home):
        # Not at start: Claude Code may still be showing its development-channel confirmation in that window.
        # The first tool call or permission request means the session is past it.
        def minimize() -> None:
            table = process_table()
            claude_pid = claude_ancestor_pid(table, os.getpid())
            if claude_pid and not minimize_console(console_owners(table, claude_pid)):
                log("Claude Hand 창을 찾지 못해 최소화하지 않았습니다 (Windows Terminal 탭이면 그대로 둡니다)")
        mcp.hub.on_first_use = lambda: threading.Thread(target=minimize, daemon=True).start()
    token = secrets.token_urlsafe(32)
    env_port = os.environ.get("CLAUDE_HAND_CHAT_PORT")
    httpd = start_http(mcp.hub, token, int(env_port) if env_port else None)
    port = httpd.server_address[1]
    server_file = write_server_file(home, port, token)
    write_status(home, "serving", reason, port)
    log(f"serving panels on http://127.0.0.1:{port} ({reason}); token in {server_file}")
    try:
        mcp.serve()
    finally:
        httpd.shutdown()
        remove_server_file(server_file)
        try:
            write_status(home, "stopped", "감독 세션이 종료됨")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
