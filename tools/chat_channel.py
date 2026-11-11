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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handlib import eventlog, overview  # noqa: E402
from handlib.agents import AgentInfo, installed_agents, prompt_template, refresh_agents, slug  # noqa: E402
from handlib.capture import capture_window  # noqa: E402
from handlib.constants import VERSION  # noqa: E402
from handlib.link import claude_hand_home, supervisor_running  # noqa: E402
from unity_bridge import describe_dialogs, find_project, waiting_dialogs  # noqa: E402

SERVER_NAME = "claude-hand-chat"
SERVER_VERSION = VERSION
DEFAULT_PORT = 8765
PORT_ATTEMPTS = 10
MAX_TEXT = 8000
MAX_CONTEXT = 3000
MAX_WAIT_SECONDS = 30
MAX_CONTEXT_ITEMS = 20  # selected objects / assets kept per window
LOG_LIMIT = 100  # items kept per chat: panels show recent history; the conversation itself lives in Claude Code
AUTO_SYNC_INTERVAL = 20.0  # seconds between automatic subagent refreshes for the same window

# What the supervisor is told about the panels. The same text goes into the supervisor skill when it is installed,
# so the two cannot drift apart (prompts/chat-rules.md).
INSTRUCTIONS = prompt_template("chat-rules")
HISTORY_KINDS = ("user", "reply", "image")  # what a panel shows again after a restart; permission prompts do not outlive it
PREVIEW_SIZE = 640  # longest side of a result preview, in pixels
IMAGES_KEPT = 60    # preview files kept on disk; older ones are deleted

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
            "working": {"type": "boolean", "description": "true on a short message sent while the work for the request "
                                                          "still goes on ('working on it'); false on the result, and on an "
                                                          "answer that needed no work. Say which on every reply (not "
                                                          "needed with origin desktop)."},
            "changed_windows": {"type": "array", "items": {"type": "string"},
                                "description": "The chat_ids of the OTHER windows this request changed, e.g. the Unity project "
                                               "a model from this Blender window was sent to. The panel shows a picture of each "
                                               "under the result. [] when only this chat's own window was touched, on a working "
                                               "message and with origin desktop. Never a window that worked for another request."},
        },
        "required": ["chat_id", "text", "working", "changed_windows"],
    },
}

PROGRESS_TTL = 300.0  # seconds without news after which a panel stops showing a bar (a long import is one action)
LOCK_SECONDS = 90.0    # a subagent that reported a tool call this recently and has not stopped is still at work in its window
ANSWER_GRACE = 60.0    # once the subagents stopped: how long the supervisor may take to answer the panel before it is reminded
STALL_SECONDS = 300.0  # when no subagent work was seen at all: how long a panel request may wait before the reminder
STOP_SECONDS = 180.0   # a panel's 중지 holds until its request is answered, but never longer than this
EARLY_SECONDS = 120.0  # an answer sent before any work was seen: how long that work may still take to start (see _asking)
SEEN_SECONDS = 90.0    # a panel asks for news every MAX_WAIT_SECONDS at the latest: one silent for this long has been closed

# Tools that leave a window as it was. A window that did nothing else for a request is not one the request changed:
# its picture under the answer would show nothing new (see report_action).
LOOKING_TOOLS = {"ch_scene_snapshot", "ch_editor_status", "ch_capture", "ch_export_model", "ch_export_objects"}
# A reply has to say whether the work still goes on. The tool definition asks for it, but Claude Code does not hold
# a call to that, and a supervisor that has answered without it for days goes on doing so (a hint in the result
# changed nothing; being refused once did, as with the request tag on hand-outs). So the first few replies without
# it are sent back, with this.
WORKING_ASKS = 5
WORKING_ASK = ("Not sent: say whether the work for this request still goes on. Send the same text again with working: true "
               "(a status while the work goes on, such as 'on it') or working: false (the result, or an answer that needed "
               "no work). The panel's work line, its 중지 button and the alert that an answer came depend on it. Say it on "
               "every reply from now on.")


def only_looks(tool: str, detail: str = "") -> bool:
    return tool in LOOKING_TOOLS or tool.startswith(("get_", "read_", "find_", "list_")) or \
        (tool == "ch_checkpoint" and detail.strip() == "list")

CONTEXT_TOOL = {
    "name": "current_context",
    "description": "What the user has open in Unity and Blender right now, most recently used window first: per window its "
                   "chat_id, subagent, active scene or file, selected objects and assets, whether it has focus, when it "
                   "was last used, and what goes on there now (state: working, waiting for an answer, stopped by a dialog, "
                   "idle). Use it to tell which window 'this', 'the selected one' or 'here' means, and to say what each "
                   "window is busy with.",
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


def default_agents_dir(env=os.environ) -> Path:
    return Path(env.get("CLAUDE_HAND_AGENTS_DIR") or Path.home() / ".claude" / "agents")


# --------------------------------------------------------------------------- window -> subagent


def resolve_window(agents: list[AgentInfo], app: str, window: str, pin: str = "") -> tuple[str, str | None]:
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
        # "Before" and "after" below are counted in items (self.seq), not timed: on Windows before Python 3.13 the
        # clock moves in steps of about 15 ms, and a message and the work it starts can fall into the same step.
        # chat_id -> {asking chat: the item count when this window's subagent last worked for that chat's request}
        self.acted: dict[str, dict[str, int]] = {}
        self.worked: dict[str, int] = {}  # chat_id -> the item count when work was last seen for its request, in any window
        # chat_id -> its request, answered before any work for it was seen (see _asking): {"item", "worked": whether
        # work was seen since, "ts": of that answer or of the last work}
        self.early: dict[str, dict] = {}
        # chat_id -> the request its window's subagent was handed work for, as the supervisor's hook told it (see assign):
        # {"asker": the chat that asked ("": the terminal or the Claude app), "item": that request, "ts": of the last sign of life}
        self.assigned: dict[str, dict] = {}
        self.previewed: dict[str, int] = {}  # chat_id -> the item count when its replies last looked for other windows to show
        self.seen: dict[str, float] = {}  # chat_id -> when its panel last asked for news: the window is open
        self.names: dict[str, dict] = {}  # chat_id -> {"app", "window"}, for the overview of all windows
        self._overview_sig: tuple | None = None  # the windows' states when the overview last changed
        self.overview_count = 0  # bumped on every change of that, so a waiting panel wakes up and shows the other windows anew
        self.capture = None  # callable(app, pin, path) -> path or None: a picture of what a window shows now
        self.images_dir: Path | None = None  # where result previews are kept (None: no previews)
        self.find_dialogs = None  # callable(app, pin) -> the dialogs that stop that window now ([] for none)
        self.dialog_seen: dict[str, str] = {}  # chat_id -> the dialogs already told about, so each is told once
        self.workers: dict[str, dict] = {}  # chat_id -> the subagent at work in that window: {"agent_id", "last", "stopped"}
        # chat_id -> the id of the subagent that last worked in its window. Kept with the chat's history: a supervisor
        # that is resumed goes on with the subagents of before (SendMessage to their ids), and a channel that has just
        # started has to know whose window such an id is (see assign).
        self.agent_ids: dict[str, str] = {}
        self.working_asked = 0  # replies sent back for not saying `working` (see wants_working)
        self.pointed: dict[str, tuple] = {}  # chat_id -> the request its window's newest undo point was set for (see report_action)
        self.reminded: dict[str, int] = {}  # chat_id -> seq of the request the supervisor was reminded of (once each)
        self.stops: dict[str, dict] = {}  # chat_id -> its panel's 중지: {"seq": of the request it stops (0: work asked elsewhere), "ts"}
        self.started = time.time()  # requests older than this server (shown again from history) are not its to chase
        self.events = None  # callable(event, **fields): the event log (handlib/eventlog.py); None: nothing is logged

    def _log(self, event: str, **fields) -> None:
        """One line in the event log, for `claude_hand.py trace`. Never in the way of the chat."""
        if self.events is not None:
            try:
                self.events(event, **fields)
            except Exception as exc:
                log("could not write the event log:", exc)

    def _request_for(self, chat_id: str) -> str:
        """The id of the request a window's work belongs to: the one its own panel waits on, else the newest one
        another panel waits on (the work was asked there). "" when no panel waits: it was asked in the terminal or
        the Claude app, or before this server started. Called with the lock held."""
        now = time.time()
        given = self._assignment(chat_id, now)
        if given is not None:  # the supervisor said which request: nothing to guess
            return str((given["item"] or {}).get("request", ""))
        item = self._asking(chat_id, now)
        if item is None:
            waiting = [i for i in (self._asking(chat, now) for chat in self.logs if chat != chat_id) if i]
            item = max(waiting, key=lambda i: i["seq"], default=None)
        return str((item or {}).get("request", ""))

    def _assignment(self, chat_id: str, now: float) -> dict | None:
        """What the supervisor's hook said this window's subagent works for, while that still holds: until the
        subagent stops, or PROGRESS_TTL after the last sign of it (a Stop that never arrived). Called with the lock
        held."""
        given = self.assigned.get(chat_id)
        if given is not None and now - given["ts"] > PROGRESS_TTL:
            del self.assigned[chat_id]
            return None
        return given

    def _work_for(self, chat_id: str, now: float) -> tuple[str, dict | None]:
        """Whose request the work in this window is for now: (the asking chat, its request), ("", None) for nobody's
        or when it cannot be told. The supervisor's hook says so when it hands the work out (assign). Without that (a
        supervisor started without the hook) the channel goes by who waits: the window's own panel first; else the
        work was asked in another window, and whose, the channel can only tell when a single panel waits. Called with
        the lock held."""
        given = self._assignment(chat_id, now)
        if given is not None:
            return given["asker"], given["item"]
        own = self._asking(chat_id, now)
        if own is not None:
            return chat_id, own
        waiting = [(chat, item) for chat in self.logs if chat != chat_id for item in [self._asking(chat, now)] if item is not None]
        return waiting[0] if len(waiting) == 1 else ("", None)

    def assign(self, target: str, request: str | None, agent: str = "") -> dict:
        """The supervisor is about to hand work to a subagent (its hook on Agent and SendMessage says so, see
        tools/supervisor_hook.py). target: the subagent's name, or the id of one that worked before (SendMessage);
        agent: for such an id, the subagent's name when the hook could tell it.
        request: the id from the prompt's `[요청 …]` tag, "" for `[요청 없음]` (asked in the terminal or the Claude
        app), None when the prompt has no tag. From here until that subagent stops, the work in its window belongs
        to that request: previews, 중지 and the event log need not guess. The answer tells the hook what to do:
        ours false (not a window's subagent: none of its business), missing or unknown (refuse, the tag is wrong),
        stop (refuse: 중지 was pressed on that request)."""
        agents = {a.name for a in installed_agents(self.agents_dir)}
        with self.cond:
            now = time.time()
            window = next((name for name in (target, agent) if name in agents), "") or next(
                (chat for chat, known in self.agent_ids.items() if target and known == target and chat in agents), "")
            if not window:
                return {"ours": False}
            if request is None:
                return {"ours": True, "chat_id": window, "missing": True}
            asker, item = "", None
            if request:
                asker, item = next(((chat, i) for chat, items in self.logs.items() for i in reversed(items)
                                    if i.get("kind") == "user" and i.get("request") == request), ("", None))
                if item is None:
                    return {"ours": True, "chat_id": window, "unknown": True}
                stop = self.stops.get(asker)
                if stop and stop["seq"] == item["seq"] and now - stop["ts"] <= STOP_SECONDS and self._asking(asker, now) is item:
                    self._log("refused", request=request, chat_id=window, tool="(맡기기)", reason="사용자가 채팅창에서 중지를 누름")
                    return {"ours": True, "chat_id": window, "stop": True}
            self.assigned[window] = {"asker": asker, "item": item, "ts": now}
            self._log("assigned", request=request, chat_id=window, text="" if asker else "채팅창 요청이 아님")
            self.cond.notify_all()  # the other panels show the asking window: its request is under way (see _state)
        return {"ours": True, "chat_id": window, "asker": asker}

    def _asking(self, chat_id: str, now: float) -> dict | None:
        """The request this chat still waits on, when the question is whose request a window's work belongs to: its
        panel message with no answer yet, or one answered before any work for it was seen. Such an answer is as a
        rule "on it" sent without `working`. Taking it for the result would make the work that follows look asked
        elsewhere, and with two projects worked on side by side each would get the other's picture (and the other's
        중지). So the request stays this chat's while the work it announced may still start (EARLY_SECONDS; after that
        it was a plain answer that needed no work), and once work was seen, until the result is in: the next answer,
        or PROGRESS_TTL without any more work. Requests from before this server started are nobody's now. Called
        with the lock held."""
        item = self._request_item(chat_id)
        if item is not None:
            return item if item["ts"] >= self.started else None
        early = self.early.get(chat_id)
        if early is None:
            return None
        if now - early["ts"] > (PROGRESS_TTL if early["worked"] else EARLY_SECONDS):
            del self.early[chat_id]
            return None
        return early["item"]

    def _note_work(self, chat_id: str, now: float) -> None:
        """A window's subagent worked for this chat's request. Called with the lock held."""
        self.worked[chat_id] = self.seq
        if chat_id in self.early:
            self.early[chat_id].update(worked=True, ts=now)

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
                if isinstance(saved.get("agent_id"), str) and saved["agent_id"]:
                    self.agent_ids[chat_id] = saved["agent_id"]
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
            saved = {"chat_id": chat_id, "items": items}
            if self.agent_ids.get(chat_id):
                saved["agent_id"] = self.agent_ids[chat_id]
            tmp.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
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
        chat_id, agent = resolve_window(installed_agents(self.agents_dir), app, window, pin)
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
                chat_id, agent = resolve_window(installed_agents(self.agents_dir), app, window, pin)
        with self.cond:
            self.names[chat_id] = {"app": app, "window": window}
        return chat_id, agent

    # ------------------------------------------------------------------ all windows at a glance

    def _state(self, chat_id: str, now: float) -> dict:
        """What goes on in one window, from what the channel already knows (nothing is asked of anybody): working
        (a subagent is at it; `asker` names the chat whose request it is, when not its own), waiting (its panel has a
        request and no subagent is at work: the supervisor's turn), dialog (a dialog stops the window), idle. Called
        with the lock held."""
        name = self.names.get(chat_id) or self.windows.get(chat_id) or self.contexts.get(chat_id) or {}
        app, _, rest = chat_id.partition("-")
        state = {"chat_id": chat_id, "app": name.get("app") or app, "window": name.get("window") or rest, "state": "idle"}
        entry = self.progress.get(chat_id)
        worker = self.workers.get(chat_id)
        working = entry is not None and now - entry["updated"] <= PROGRESS_TTL and not (worker and worker["stopped"])
        own = self._request_item(chat_id)
        early = self.early.get(chat_id) if own is None else None
        if early is not None and self._asking(chat_id, now) is not None and (
                early["worked"] or any(given["item"] is early["item"] for given in self.assigned.values())):
            # Answered "on it" without saying so (see _asking), and work for it was handed out or seen since: the
            # panel still waits for the result, between one subagent's turn and the next as well.
            own = early["item"]
        if working:
            asker, item = self._work_for(chat_id, now)
            state.update(state="stopping" if entry.get("stopping") else "working", seconds=round(now - entry["started"]),
                         action=f"{entry.get('tool', '')} {entry.get('detail', '')}".strip(), actions=entry.get("actions", 0))
            if item is not None:
                state.update(request=" ".join(str(item.get("text", "")).split())[:60], request_seq=item["seq"])
            if asker and asker != chat_id:
                state["asker"] = asker
            dialogs = self.dialog_seen.get(chat_id)
            if dialogs:
                state.update(state="dialog", dialog=describe_dialogs(json.loads(dialogs)))
        elif own is not None and own["ts"] >= self.started:
            state.update(state="waiting", seconds=round(now - own["ts"]), request=" ".join(str(own.get("text", "")).split())[:60],
                         request_seq=own["seq"])
        return state

    def overview(self, now: float | None = None) -> list[dict]:
        """Every window that is open or at work, busy ones first: a window whose panel asked for news lately, whose
        subagent is at work, or whose panel waits for an answer. A project that is closed is not in it."""
        now = now or time.time()
        with self.cond:
            return self._overview(now)

    def _overview(self, now: float) -> list[dict]:
        chats = {chat for chat, when in self.seen.items() if now - when <= SEEN_SECONDS}
        chats |= {chat for chat, entry in self.progress.items() if now - entry["updated"] <= PROGRESS_TTL}
        waiting = {chat: self._request_item(chat) for chat in self.logs}
        chats |= {chat for chat, item in waiting.items() if item is not None and item["ts"] >= self.started}
        states = [self._state(chat, now) for chat in chats if chat]
        return sorted(states, key=lambda s: (s["state"] == "idle", s["window"].lower(), s["chat_id"]))

    def _overview_rev(self, now: float) -> int:
        """A number that changes when a window's state does (not with every tool call or second: those the panel of
        that window shows itself). Called with the lock held."""
        sig = tuple((s["chat_id"], s["state"], s.get("request_seq"), s.get("asker")) for s in self._overview(now))
        if sig != self._overview_sig:
            self._overview_sig = sig
            self.overview_count += 1
        return self.overview_count

    def _others(self, chat_id: str, now: float) -> list[str]:
        """The other windows as lines for a panel to show under its own (handlib/overview.py). Called with the lock held."""
        return overview.lines(self._overview(now), skip=chat_id)

    def post_user_message(self, app: str, window: str, text: str, pin: str = "", context: str = "") -> dict:
        chat_id, agent = self.resolve(app, window, pin)
        content = text if not context else f"{text}\n\n[{app} 창 정보: {window}]\n{context}"
        meta = {"chat_id": chat_id, "app": app, "window": window, "agent": agent or "none"}
        with self.cond:
            worker = self.workers.get(chat_id)
            if agent and worker and worker["agent_id"]:
                meta["agent_id"] = worker["agent_id"]  # to resume with SendMessage, whatever the supervisor still remembers
            self.windows[chat_id] = {"app": app, "window": window, "agent": agent, "last_seen": time.time()}
            request = f"{time.strftime('%m%d-%H%M%S')}-{self.seq + 1}"  # what the event log knows this request by
            meta["request"] = request  # the supervisor puts it on the work it hands out for this message: [요청 <request>]
            self._append(chat_id, {"kind": "user", "text": text, "request": request})
            self.early.pop(chat_id, None)  # whatever was still going on, this is the panel's request now
            for pending in self.permissions.values():  # a window that joins late still sees open prompts
                if not pending.get("resolved") and chat_id not in pending["chats"]:
                    pending["chats"].add(chat_id)
                    self._append(chat_id, self._permission_item(pending))
            self._touch(chat_id, app, window, agent)
            notification = {"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {"content": content, "meta": meta}}
            ready = self.initialized
            if not ready:
                self.backlog.append(notification)
        self._log("message", request=request, chat_id=chat_id, app=app, window=window, agent=agent or "none",
                  text=eventlog.short(text), delivered=ready)
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
        return chat_id in self.logs or any(a.name == chat_id for a in installed_agents(self.agents_dir))

    def reply(self, chat_id: str, text: str, origin: str = "panel", request: str = "", working: bool = False,
              changed: list[str] | None = None) -> bool:
        """working: a short message while the work goes on ("작업 중이에요"). It is not the answer: the request stays
        open, the work line and its undo point go on, and pictures of other windows wait for the result.
        changed: the other windows this request changed, as the supervisor tells it (their chat_ids); the result gets
        a picture of each. None: it did not say, and the channel goes by what it saw (see _changed_elsewhere)."""
        if not self.known_chat(chat_id):
            return False
        item = {"kind": "reply", "text": text}
        if origin == "desktop":
            item.update(origin="desktop", request=request[:500])
        if working:
            item["working"] = True
        with self.cond:
            now = time.time()
            asked = self._asking(chat_id, now) if origin != "desktop" else None
            stop = None if working or origin == "desktop" else self.stops.get(chat_id)
            if stop is not None and stop["seq"] != (asked["seq"] if asked else 0):
                stop = None  # pressed on an earlier request
            # The windows that stop covered (see _stop_for), told before the answer changes what the chat asks: those at
            # work for this chat's request, and its own unless that one works for another chat's request meanwhile.
            halted = set()
            if stop is not None:
                halted = {w for w in set(self.progress) | set(self.assigned) if self._work_for(w, now)[0] == chat_id}
                if not stop["seq"] or self._work_for(chat_id, now)[0] in (chat_id, ""):
                    halted.add(chat_id)
            self._append(chat_id, item)
            self._log("reply", request=(asked or {}).get("request", ""), chat_id=chat_id, text=eventlog.short(text),
                      working=working or None, origin="desktop" if origin == "desktop" else None)
            if working:
                return True
            # The answer is in: the bar goes away. Not while the window is at work for somebody else's request (its
            # own panel was told "after this one"): the bar would flicker, and the work going on would look like the
            # start of a new request and get a second undo point.
            given = self._assignment(chat_id, now)
            if (given is None or given["asker"] == ("" if origin == "desktop" else chat_id)) and self.progress.pop(chat_id, None) is not None:
                self._bump_progress(chat_id)
            if origin == "desktop":
                return True
            if stop is not None:  # the answer to 중지: whatever worked for the request is over, said or not
                for window in sorted(halted):
                    self._halt(window, now)
                del self.stops[chat_id]
                self.early.pop(chat_id, None)
            elif asked is not None and self.worked.get(chat_id, 0) < asked["seq"] and not changed:
                self.early[chat_id] = {"item": asked, "worked": False, "ts": now}  # nothing was done for it yet: see _asking
            else:
                self.early.pop(chat_id, None)
            others = self._changed_elsewhere(chat_id, changed)
        if others:
            threading.Thread(target=self._attach_previews, args=(chat_id, others), daemon=True).start()
        return True

    def _halt(self, window: str, now: float) -> None:
        """The work in this window is over without its subagent saying so: the supervisor ended it after 중지, and a
        subagent that is killed runs no Stop hook. Left alone, the window would show as at work, and stay shut to
        another subagent, until the timeouts ran out. Called with the lock held."""
        worker = self.workers.get(window)
        if worker and not worker["stopped"]:
            worker["stopped"] = now
        self.assigned.pop(window, None)
        if self.progress.pop(window, None) is not None:
            self._bump_progress(window)

    def wants_working(self) -> bool:
        """A reply came without saying whether it is a status or the result: whether to send it back for that. The
        first few times only (WORKING_ASKS): a supervisor that keeps leaving it out is not made to send every answer
        twice, and the channel copes as before (see _asking)."""
        with self.cond:
            if self.working_asked >= WORKING_ASKS:
                return False
            self.working_asked += 1
            return True

    def _changed_elsewhere(self, chat_id: str, declared: list[str] | None = None) -> list[str]:
        """The other windows to show under this chat's result: the user asked here and cannot see them. The
        supervisor knows which windows it changed for this request and says so (declared). When it does not, only a
        window whose work was taken down for this chat's request while it happened counts (see report_action), since
        this chat's request or its previous reply. Called with the lock held."""
        if self.capture is None or self.images_dir is None:
            return []
        asked = max((i["seq"] for i in self.logs.get(chat_id, []) if i.get("kind") == "user"), default=0)
        since = max(asked, self.previewed.get(chat_id, 0))
        self.previewed[chat_id] = self.seq
        if declared is not None:
            return sorted({other for other in declared if other != chat_id})
        return sorted(other for other, by in self.acted.items() if other != chat_id and by.get(chat_id, -1) >= since)

    def _attach_previews(self, chat_id: str, others: list[str]) -> None:
        """Background thread: capture each window and add the picture to the chat (no Claude involved, no tokens)."""
        agents = {a.name: a for a in installed_agents(self.agents_dir)}
        for other in others:
            agent = agents.get(other)
            if agent is None:
                continue
            safe_name = re.sub(r"[^\w.-]", "_", other)
            path = self.images_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_name}.png"
            try:
                self.images_dir.mkdir(parents=True, exist_ok=True)
                captured = self.capture(agent.app, agent.pin, str(path))
            except Exception as exc:  # a preview is a nicety
                log("could not capture", other, exc)
                captured = None
            if not captured:
                continue
            with self.cond:
                window = (self.contexts.get(other) or self.windows.get(other) or {}).get("window") or agent.pin.rpartition("@")[0]
                self._append(chat_id, {"kind": "image", "path": Path(captured).as_posix(), "app": agent.app, "window": window,
                                       "caption": f"{window} ({'Unity' if agent.app == 'unity' else 'Blender'}) 지금 모습"})
        self._prune_images()

    def check_dialogs(self) -> None:
        """While a window's subagent works, look for a dialog that stopped that window ("Save the scene?") and tell the
        chats whose request waits on it: their user may be looking at another window. One pass (watch repeats)."""
        if self.find_dialogs is None:
            return
        now = time.time()
        with self.cond:
            busy = [chat for chat, entry in self.progress.items() if now - entry["updated"] <= PROGRESS_TTL]
            for chat in [c for c in self.dialog_seen if c not in busy]:
                del self.dialog_seen[chat]
        agents = {a.name: a for a in installed_agents(self.agents_dir)} if busy else {}
        for chat in busy:
            agent = agents.get(chat)
            dialogs = self.find_dialogs(agent.app, agent.pin) if agent else []
            key = json.dumps(dialogs, ensure_ascii=False, sort_keys=True) if dialogs else ""
            with self.cond:
                if key == self.dialog_seen.get(chat, ""):
                    continue
                self.dialog_seen[chat] = key
                self.cond.notify_all()  # the other panels show this window: stopped by a dialog, or going again
                if not dialogs:
                    continue
                window = (self.contexts.get(chat) or self.windows.get(chat) or {}).get("window") or agent.pin.rpartition("@")[0]
                app = "Unity" if agent.app == "unity" else "Blender"
                text = f"{window}({app})가 대화상자 때문에 멈춰 있어요. {app}에서 직접 골라 주세요: {describe_dialogs(dialogs)}"
                self._log("dialog", request=self._request_for(chat), chat_id=chat, pin=agent.pin, text=eventlog.short(describe_dialogs(dialogs)))
                for target in [c for c in self.logs if c != chat and self._open_request(c)]:
                    self._append(target, {"kind": "notice", "text": text, "alert": True})

    def check_unanswered(self, now: float | None = None) -> list[str]:
        """Remind the supervisor of a panel request that got no answer. Answering with the reply tool is a rule the
        supervisor follows, not something that happens by itself; when it writes its answer to the terminal instead,
        the user in that window waits for nothing. Once per request: a minute after the subagents stopped, or five
        minutes after the request when no subagent work was seen at all. One pass (watch repeats). Returns the chats
        reminded."""
        now = now or time.time()
        reminders = []
        with self.cond:
            if not self.initialized:
                return []
            if any(not w["stopped"] and now - w["last"] <= PROGRESS_TTL for w in self.workers.values()):
                return []  # someone is still at work
            last_work = max((w["stopped"] or w["last"] for w in self.workers.values()), default=0.0)
            for chat_id in list(self.logs):
                request = self._request_item(chat_id)
                if request is None or request["ts"] < self.started or self.reminded.get(chat_id) == request["seq"]:
                    continue
                heard = max(i["ts"] for i in self.logs[chat_id] if i["seq"] >= request["seq"] and i.get("kind") in ("user", "reply"))
                worked = last_work >= request["ts"]
                if now - max(heard, last_work if worked else 0.0) < (ANSWER_GRACE if worked else STALL_SECONDS):
                    continue
                self.reminded[chat_id] = request["seq"]
                window = self.windows.get(chat_id) or {}
                asked = " ".join(str(request.get("text", "")).split())[:80]
                reminders.append({"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {
                    "content": f'[claude-hand] 이 채팅창의 요청이 아직 답을 받지 못했습니다: "{asked}". 일이 끝났으면 reply로 결과를, '
                               "아직 하는 중이면 reply에 working: true로 지금 상황을 한 줄 보내세요.",
                    "meta": {"chat_id": chat_id, "app": str(window.get("app", "")), "window": str(window.get("window", "")),
                             "agent": window.get("agent") or "none", "request": str(request.get("request", "")),
                             "reminder": "unanswered"}}})
                self._append(chat_id, {"kind": "notice", "text": "답이 늦어져 Claude Hand에 다시 알렸어요."})
                self._log("reminder", request=request.get("request", ""), chat_id=chat_id,
                          text="하위 세션이 멈춘 뒤 답이 없음" if worked else "하위 세션이 일한 흔적이 없음")
        for notification in reminders:
            self.emit(notification)
        return [n["params"]["meta"]["chat_id"] for n in reminders]

    def watch(self, interval: float = 2.0) -> None:
        while True:
            for check in (self.check_dialogs, self.check_unanswered):
                try:
                    check()
                except Exception as exc:  # never let the watcher die
                    log(f"{check.__name__} failed:", exc)
            time.sleep(interval)

    def _prune_images(self) -> None:
        try:
            files = sorted(self.images_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[IMAGES_KEPT:]:
                old.unlink()
        except OSError:
            pass

    def _touch(self, chat_id: str, app: str, window: str, agent: str | None) -> None:
        entry = self.contexts.setdefault(chat_id, {"chat_id": chat_id})
        entry.update(app=app, window=window, agent=agent or "none", last_used=time.time())

    def update_context(self, app: str, window: str, pin: str, context: dict) -> str:
        """A Unity or Blender window reports what is selected and open (no chat message needed)."""
        chat_id, agent = resolve_window(installed_agents(self.agents_dir), app, window, pin)
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
            states = {s["chat_id"]: s for s in self._overview(now)}
        windows.sort(key=lambda w: (bool(w.get("focused")), w.get("last_used", 0)), reverse=True)
        for w in windows:
            for key in ("last_used", "reported"):
                if key in w:
                    w[key + "_seconds_ago"] = round(now - w.pop(key))
            state = states.get(w.get("chat_id"))
            if state is not None:  # what goes on there now, as the panels and `claude_hand.py status` show it
                w["state"] = state["state"]
                w.update({key: state[key] for key in ("request", "asker", "action", "dialog", "seconds") if key in state})
        return {"windows": windows,
                "note": "Most recently used first. A window that is not listed has not reported since this session "
                        "started (its Claude Hand Tools / chat add-on may be missing). state: working (its subagent is "
                        "at it; asker: the chat whose request it is, when not its own), waiting (its panel has a request "
                        "and nothing is at work: your turn), dialog (a dialog stops that window), idle."}

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
        self._log("permission", tool=pending["tool_name"], text=eventlog.short(pending["description"]))

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
        self._log("verdict", tool=pending["tool_name"], behavior=behavior)
        self.emit({"jsonrpc": "2.0", "method": "notifications/claude/channel/permission",
                   "params": {"request_id": request_id, "behavior": behavior}})
        return True

    def updates(self, chat_id: str, after: int, wait: float) -> list[dict]:
        return self.poll(chat_id, after, wait)["items"]

    def poll(self, chat_id: str, after: int, wait: float, progress_rev: int | None = None, others_rev: int | None = None) -> dict:
        """New items after `after`, the progress bar, and what the other windows are busy with; returns early when
        any of them changes (the bar and the other windows only for a panel that says which revision it has)."""
        deadline = time.time() + max(0.0, min(wait, MAX_WAIT_SECONDS))
        with self.cond:
            if chat_id and chat_id not in self.logs:
                self.logs[chat_id] = []  # an open panel that has not sent anything yet still gets permission prompts
            if chat_id and time.time() - self.seen.get(chat_id, 0.0) > SEEN_SECONDS:
                self.cond.notify_all()  # a window that just opened: the other panels list it
            while True:
                now = time.time()
                if chat_id:
                    self.seen[chat_id] = now  # a panel is asking: its window is open
                items = [i for i in self.logs.get(chat_id, []) if i["seq"] > after]
                rev = self.progress_rev.get(chat_id, 0)
                others = self._overview_rev(now)
                remaining = deadline - now
                if items or (progress_rev is not None and rev != progress_rev) or (others_rev is not None and others != others_rev) \
                        or remaining <= 0:
                    return {"items": items, "progress": self._current_progress(chat_id), "progress_rev": rev,
                            "others": self._others(chat_id, now), "others_rev": others}
                self.cond.wait(remaining)

    def _progress_entry(self, chat_id: str, now: float) -> dict:
        entry = self.progress.get(chat_id)
        if entry is None or now - entry["updated"] > PROGRESS_TTL:  # a new request
            entry = self.progress[chat_id] = {"agent": chat_id, "started": now, "actions": 0, "updated": now}
        return entry

    def _bump_progress(self, chat_id: str) -> None:
        self.progress_rev[chat_id] = self.progress_rev.get(chat_id, 0) + 1
        self.cond.notify_all()

    def report_action(self, app: str, pin: str, tool: str, detail: str, agent_id: str = "", looks: bool = False) -> dict | None:
        """A window's subagent is about to run a Unity/Blender tool (its guard hook says so): show it in that
        window's panel. new_request is true for the first action of a request (the guard sets an undo point then),
        with label: the panel message it answers, if any. busy is true when another subagent is still at work in
        that window: two at once overwrite each other, so the guard refuses the call. looks: the guard read the
        code this call runs and it can only look (a tool that runs any code does not show it in its name). None for
        a pin no generated subagent has."""
        looks = looks or only_looks(tool, detail)
        agent = next((a for a in installed_agents(self.agents_dir) if a.app == app and a.pin == pin), None)
        if agent is None:
            return None
        with self.cond:
            now = time.time()
            stopper = self._stop_for(agent.name, now)
            if stopper:
                self._log("refused", request=self._request_for(agent.name), chat_id=agent.name, pin=pin, tool=tool[:80], agent_id=agent_id,
                          reason="사용자가 채팅창에서 중지를 누름" + ("" if stopper == agent.name else f" ({stopper})"))
                return {"chat_id": agent.name, "stop": True, "by": stopper}
            worker = self.workers.get(agent.name)
            if agent_id and worker and worker["agent_id"] not in ("", agent_id) and not worker["stopped"] \
                    and now - worker["last"] <= LOCK_SECONDS:
                self._log("refused", request=self._request_for(agent.name), chat_id=agent.name, pin=pin, tool=tool[:80], agent_id=agent_id,
                          reason="다른 하위 세션(" + worker["agent_id"] + ")이 이 창에서 작업 중")
                return {"chat_id": agent.name, "busy": True, "other": worker["agent_id"]}
            if agent_id or worker is None:
                self.workers[agent.name] = {"agent_id": agent_id, "last": now, "stopped": 0.0}
            else:  # a guard from before agent ids were reported: the same window, whoever it is
                worker.update(last=now, stopped=0.0)
            if agent_id and self.agent_ids.get(agent.name) != agent_id:
                self.agent_ids[agent.name] = agent_id
                if self.history_dir is not None:
                    self._save_history(agent.name)
            self.names.setdefault(agent.name, {"app": agent.app, "window": agent.pin.rpartition("@")[0]})
            asker, asked = self._work_for(agent.name, now)
            if agent.name in self.assigned:
                self.assigned[agent.name]["ts"] = now
            if asker:
                if asker != agent.name and not looks:  # asked in another window, whose reply may show this one
                    self.acted.setdefault(agent.name, {})[asker] = self.seq
                self._note_work(asker, now)
            entry = self._progress_entry(agent.name, now)
            # One undo point per request and window, in front of the first action that may change something: a request
            # that only looked (or was stopped while it still looked) leaves no point behind with nothing to undo.
            # Which request it is, the channel knows for sure when the supervisor said so or the window's own panel
            # waits; then the point holds through "on it" answers and the subagent's several turns. Otherwise (asked
            # in the Claude app, or a guess) a request lasts as long as its work line does.
            known = asked is not None and (agent.name in self.assigned or asker == agent.name)
            point = ("request", asker, asked["seq"]) if known else ("bar", entry["started"])
            first = self.pointed.get(agent.name) != point and not looks
            if first:
                self.pointed[agent.name] = point
            entry.update(tool=tool[:80], detail=detail[:120], actions=entry["actions"] + 1, updated=now)
            self._bump_progress(agent.name)
            label = " ".join(str(asked.get("text", "")).split())[:40] if asked is not None and first else ""
            self._log("action", request=self._request_for(agent.name), chat_id=agent.name, pin=pin, tool=tool[:80], detail=detail[:120],
                      agent_id=agent_id, first=first or None)
        return {"chat_id": agent.name, "new_request": first, "label": label}

    def agent_stopped(self, app: str, pin: str, agent_id: str = "") -> dict | None:
        """A window's subagent finished its turn (its Stop hook says so): the window is free for another, and the
        supervisor now owes the panel an answer. None for a pin no generated subagent has."""
        agent = next((a for a in installed_agents(self.agents_dir) if a.app == app and a.pin == pin), None)
        if agent is None:
            return None
        with self.cond:
            worker = self.workers.get(agent.name)
            if worker and worker["agent_id"] in ("", agent_id) and not worker["stopped"]:
                worker["stopped"] = time.time()
                self._log("stopped", request=self._request_for(agent.name), chat_id=agent.name, pin=pin, agent_id=agent_id)
                if self.stops.get(agent.name, {}).get("seq") == 0:  # 중지 on work asked elsewhere: it has stopped
                    del self.stops[agent.name]
            if worker is None or worker["agent_id"] in ("", agent_id):
                given = self.assigned.pop(agent.name, None)  # what it was handed is done; the next hand-out says anew whose it is
                if given is not None and given["asker"] != agent.name and self.progress.pop(agent.name, None) is not None:
                    # It worked for a request asked elsewhere: the answer goes there, and none comes to this window's
                    # panel to take its work line away (the line also hides the panel's 되돌리기).
                    self._bump_progress(agent.name)
            self.cond.notify_all()  # the other panels show this window: it is no longer at work
        return {"chat_id": agent.name}

    def request_stop(self, app: str, window: str, pin: str = "") -> dict:
        """중지 in a panel: stop the work its request started. Two things happen. The channel refuses the next
        Unity/Blender call of the subagents working for it (through their guards, see _stop_for), so the work ends
        whatever the supervisor does; and the supervisor is told, so it hands out no more of that request and tells
        the panel how far things got. Nothing is undone: that is the panel's 되돌리기."""
        chat_id, agent = resolve_window(installed_agents(self.agents_dir), app, window, pin)
        with self.cond:
            now = time.time()
            item = self._asking(chat_id, now)  # also a request answered "on it" without the flag: its work goes on
            entry = self.progress.get(chat_id)
            working = entry is not None and now - entry["updated"] <= PROGRESS_TTL
            if item is None and not working:
                return {"chat_id": chat_id, "stopping": False}  # nothing is going on for this window
            self.stops[chat_id] = {"seq": item["seq"] if item else 0, "ts": now}
            if working:
                entry["stopping"] = True
                self._bump_progress(chat_id)
            self._append(chat_id, {"kind": "notice", "text": "중지를 요청했어요. 지금 하던 동작 하나는 끝까지 갈 수 있어요."})
            meta = {"chat_id": chat_id, "app": app, "window": window, "agent": agent or "none", "stop": "requested"}
            worker = self.workers.get(chat_id)
            if worker and worker["agent_id"] and not worker["stopped"]:
                meta["agent_id"] = worker["agent_id"]
            asked = " ".join(str((item or {}).get("text", "")).split())[:80]
            notification = {"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {
                "content": "[claude-hand] 사용자가 이 채팅창에서 [중지]를 눌렀습니다" + (f': "{asked}"' if asked else "") +
                           ". 이 요청으로 일하는 하위 세션의 다음 Unity·Blender 호출은 채널이 막습니다. 이 요청의 일을 더 맡기지 말고, "
                           "아직 돌고 있는 하위 세션이 있으면 멈춘 뒤, 어디까지 했고 무엇이 남았는지 reply로 알리세요.",
                "meta": meta}}
            self._log("stop", request=(item or {}).get("request", ""), chat_id=chat_id)
            ready = self.initialized
            if not ready:
                self.backlog.append(notification)
        if ready:
            self.emit(notification)
        return {"chat_id": chat_id, "stopping": True}

    def _stop_for(self, chat_id: str, now: float) -> str | None:
        """The chat whose 중지 covers work in this window now, or None. A stop belongs to the request it was pressed
        on: it holds until that request is answered or its panel sends something new, and STOP_SECONDS at most, so a
        supervisor that never answers cannot leave a window shut. It covers the panel's own window, and the windows
        that work for that request (see _work_for). Called with the lock held."""
        asker, _ = self._work_for(chat_id, now)
        for chat, stop in list(self.stops.items()):
            item = self._asking(chat, now)
            if now - stop["ts"] > STOP_SECONDS or (item["seq"] if item else 0) != stop["seq"]:
                del self.stops[chat]
            elif chat == chat_id or (stop["seq"] and asker == chat):
                return chat
        return None

    def _request_item(self, chat_id: str) -> dict | None:
        """The panel message not answered yet (a "working on it" reply does not answer it)."""
        for item in reversed(self.logs.get(chat_id, [])):
            if item.get("kind") == "reply" and not item.get("working"):
                return None
            if item.get("kind") == "user":
                return item
        return None

    def _open_request(self, chat_id: str) -> str:
        """The panel message still waiting for its result, in a few words ("" when the request came from elsewhere)."""
        item = self._asking(chat_id, time.time())
        return " ".join(str(item.get("text", "")).split())[:40] if item else ""

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
        changed = args.get("changed_windows")  # None from a session that knows the tool without it: the channel then guesses
        changed = [str(c) for c in changed] if isinstance(changed, list) else None
        if origin == "panel" and args.get("working") is None and self.hub.known_chat(chat_id) and self.hub.wants_working():
            return {"isError": True, "content": [{"type": "text", "text": WORKING_ASK}]}
        if not self.hub.reply(chat_id, clip(text, MAX_TEXT), origin, clip(str(args.get("request", "")), MAX_CONTEXT),
                              working=args.get("working") in (True, "true"), changed=changed):
            return {"isError": True, "content": [{"type": "text", "text": f"Unknown chat_id {chat_id!r}. Use the chat_id from the "
                                                  "<channel> tag, or a window's chat_id from current_context."}]}
        notes = []
        if len(text) > MAX_TEXT:
            notes.append(f"cut at {MAX_TEXT} characters: the panel shows only the start. Send long results as a short summary, "
                         "or in several replies.")
        unknown = [c for c in changed or [] if c != chat_id and not self.hub.known_chat(c)]
        if unknown:
            notes.append(f"changed_windows names no window Claude Hand knows: {', '.join(unknown)}. Use the chat_id of each "
                         "window (as a rule its subagent's name; current_context lists them), so its picture can be shown.")
        return {"content": [{"type": "text", "text": "sent" + "".join(f", but {note}" for note in notes)}]}

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
                others = int(q["orev"]) if "orev" in q else None  # and the revision of the other windows' states it shows
            except ValueError:
                self._json(400, {"ok": False, "error": "after, wait, prev and orev must be numbers"})
                return
            result = self.hub.poll(q.get("chat_id", ""), after, wait, prev, others)
            self._json(200, {"ok": True, **result, "next": max([after] + [i["seq"] for i in result["items"]])})
        elif url.path == "/v1/overview":  # `claude_hand.py status`: every open window and what goes on in it
            self._json(200, {"ok": True, "windows": self.hub.overview()})
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
                                            str(body.get("tool", "")), str(body.get("detail", "")), str(body.get("agent_id", ""))[:80],
                                            looks=body.get("looks") is True)
            self._json(200 if result else 404, {"ok": result is not None, **(result or {})})
        elif path == "/v1/stop":  # 중지 in a panel
            app = str(body.get("app", ""))
            if app not in ("unity", "blender"):
                self._json(400, {"ok": False, "error": "app must be unity or blender"})
                return
            self._json(200, {"ok": True, **self.hub.request_stop(app, str(body.get("window", ""))[:200], str(body.get("pin", ""))[:200])})
        elif path == "/v1/assign":  # the supervisor's hook, just before it hands work to a subagent
            request = body.get("request")
            self._json(200, {"ok": True, **self.hub.assign(str(body.get("target", ""))[:200], None if request is None else str(request)[:80],
                                                           str(body.get("agent") or "")[:200])})
        elif path == "/v1/stopped":  # a subagent's Stop hook: it finished its turn
            result = self.hub.agent_stopped(str(body.get("app", "")), str(body.get("pin", ""))[:200], str(body.get("agent_id", ""))[:80])
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
    checks["agents"] = [a.name for a in installed_agents(default_agents_dir(env))]
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
    mcp.hub.events = eventlog.write
    mcp.hub.use_history(home / "chat" / "history")
    mcp.hub.capture = lambda app, pin, path: capture_window(app, pin, path, PREVIEW_SIZE)
    mcp.hub.images_dir = home / "chat" / "images"
    mcp.hub.find_dialogs = lambda app, pin: waiting_dialogs(find_project(hash_=pin.rpartition("@")[2])) if app == "unity" else []
    threading.Thread(target=mcp.hub.watch, name="watch", daemon=True).start()
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
