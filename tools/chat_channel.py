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
from handlib import eventlog, overview, prefs, protect, usage  # noqa: E402
from handlib.agents import AgentInfo, glossary, installed_agents, prompt_template, refresh_agents, rewrite_installed, slug  # noqa: E402
from handlib.capture import capture_window  # noqa: E402
from handlib.constants import VERSION  # noqa: E402
from handlib.lang import N_, T  # noqa: E402
from handlib.launch import read_user_config, session_transcript  # noqa: E402
from handlib.link import blender_request, claude_hand_home, supervisor_running  # noqa: E402
from handlib.looks import LOOKING_TOOLS, tool_only_looks as only_looks  # noqa: E402,F401  (tools that leave a window as it was)
from unity_bridge import BridgeError, describe_dialogs, find_project, waiting_dialogs  # noqa: E402
from unity_bridge import request as unity_request  # noqa: E402

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
INSTRUCTIONS = prompt_template("chat-rules") + glossary()
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
# How long a request's line of time and tokens waits for the supervisor's turn to end (see check_costs). In a real
# session the end showed 6 seconds after the answer as a rule, and after 60 when the turn went on with another request.
COST_WAIT = 90.0
# A message for the supervisor waits while its turn goes on (see deliver). REST: how long its transcript must have
# shown the turn over, so that a turn which starts right behind it (a subagent's report that was waiting) shows
# first. ARRIVAL: how long something on its way to the supervisor (a message just handed over, the report of a
# subagent that just stopped) counts as a turn going on before the transcript shows it. HOLD: the longest a message
# waits whatever the transcript says; late and maybe mid-turn is better than never.
REST_SECONDS = 1.5
ARRIVAL_SECONDS = 20.0
HOLD_SECONDS = 45.0
OFFER_SECONDS = 24 * 3600.0  # how long a panel offers to undo, in the other windows too, a request that changed them
UNDO_SECONDS = 90.0    # how long one window may take to undo a request (a Unity project reads its restored files again)

UNDO_TOOL = {
    "name": "undo_request",
    "description": N_("Undo a chat panel's request in every window it changed: what the panel's 되돌리기 button does. Use it when "
                   "the user asks in a chat panel to cancel or undo what was just done; do not hand the undo to subagents "
                   "(a subagent undoes its window's newest request, which need not be the one meant). Each window undoes "
                   "that request only when nothing newer changed it since. Returns what happened window by window; tell "
                   "the user exactly that."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "The chat the request was asked in, from the channel tag"},
            "request": {"type": "string", "description": "The request id to undo (the `request` of that message's channel tag). "
                                                          "Leave out for the newest request of that chat that changed something."},
        },
        "required": ["chat_id"],
    },
}

PROTECT_TOOL = {
    "name": "propose_protection",
    "description": N_("Propose a protection rule for one window: something Claude must not change there from now on. Use it when "
                   "the user says so in words ('Hero는 건드리지 마', 'Assets/Art는 손대지 마', '이 창은 보기만 해', '씬은 저장하지 마'). "
                   "The rule holds only once the user presses [추가] under the proposal in that window's chat panel: say so in "
                   "your reply. You cannot add, change or remove a rule yourself, and nothing loosens one but the user in the "
                   "panel (보호 → 풀기): when asked to lift a rule, tell the user where that button is. A rule is checked by "
                   "the hooks and by the window itself, whatever a subagent does; a note in NOTES.md is only read."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "The window the rule is for (its chat_id)"},
            "kind": {"type": "string", "enum": ["window", "path", "object", "action"],
                     "description": "window: the window as a whole. path: Unity assets by project path. object: scene objects "
                                    "(Unity) or objects (Blender) by name. action: something not to be done there at all"},
            "value": {"type": "string",
                      "description": "window: 'look' (only look, change nothing) or 'locked' (no work there at all). path: "
                                     "'Assets/Art/**', 'Assets/Scenes/Main.unity' (* within a name, ** any depth; a folder covers "
                                     "all in it). object: 'Player' (that object wherever it is, and all below it), "
                                     "'Environment/**', 'UI/Canvas' (that one object); in Blender also 'collection:Rig'. action: "
                                     "'save_scene', 'delete_asset', 'play_mode' (Unity), 'save_file' (Blender)"},
            "lasting": {"type": "boolean", "description": N_("true: kept with the project or the .blend. false: only until Claude Hand "
                                                           "is turned off (the user said 'for now', '이번만', '오늘은')")},
            "why": {"type": "string", "description": "One line for the user: what they said that this rule answers"},
        },
        "required": ["chat_id", "kind", "value", "lasting"],
    },
}

UNDO_WORDS = {  # what became of a request in one window, for the people reading it in a chat panel
    "undone": N_("되돌렸어요"),
    "nothing": N_("되돌릴 것이 없었어요"),
    "blocked": N_("그 뒤에 다른 요청이 이 창을 바꿔서 건너뛰었어요. 그 창에서 먼저 되돌려 주세요"),
    "busy": N_("지금 작업 중이라 건너뛰었어요. 끝난 뒤 다시 눌러 주세요"),
    "unreachable": N_("창이 응답하지 않아 건너뛰었어요"),
    "failed": N_("되돌리지 못했어요"),
}


def undo_in_window(app: str, pin: str, key: str) -> dict:
    """Ask one window to undo the request `key` (its id): Unity through Claude Hand Tools' bridge (ch_checkpoint
    undo_request), Blender through MCP for Blender's execute_code into the chat add-on (undo_request). Each undoes
    only when that request's point is its newest one. Returns {"state": one of UNDO_WORDS, "top", "summary",
    "detail"}; a package or add-on from before this existed answers with an error, which is "failed", never an undo
    of whatever happens to be on top."""
    if app == "unity":
        entry = find_project(hash_=pin.rpartition("@")[2])
        if entry is None:
            return {"state": "unreachable"}
        try:
            result = unity_request(entry, "ch_checkpoint", {"action": "undo_request", "request": key}, timeout=UNDO_SECONDS) or {}
        except BridgeError as exc:
            return {"state": "unreachable" if exc.code in ("UNREACHABLE", "DIALOG") else "failed", "detail": exc.message[:300]}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if result.get("success") is not True or data.get("state") not in UNDO_WORDS:
            return {"state": "failed", "detail": str(data.get("message") or result.get("message") or result.get("code") or
                                                      T("Claude Hand Tools가 예전 버전입니다"))[:300]}
        return {"state": data["state"], "top": str(data.get("top") or ""), "summary": str(data.get("summary") or "")}
    code = f"import claude_hand_chat, json\nprint('CH_UNDO ' + json.dumps(claude_hand_chat.undo_request({key!r})))"
    try:
        reply = blender_request(int(pin.rpartition("@")[2]), {"type": "execute_code", "params": {"code": code}}, UNDO_SECONDS)
    except (OSError, ValueError):
        return {"state": "unreachable"}
    printed = reply.get("result") if isinstance(reply, dict) else None  # {"executed": True, "result": what the code printed}
    printed = printed.get("result") if isinstance(printed, dict) else printed
    found = re.search(r"CH_UNDO (\{.*\})", str(printed or ""))
    try:
        data = json.loads(found.group(1)) if found else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or data.get("state") not in UNDO_WORDS:
        why = reply.get("message") if isinstance(reply, dict) else None  # no undo_request in the add-on: an AttributeError
        return {"state": "failed", "detail": str(why or T("Claude Hand Chat 애드온이 예전 버전입니다"))[:300]}
    return {"state": data["state"], "top": str(data.get("top") or ""), "summary": "", "detail": str(data.get("detail") or "")}

# A reply has to say whether the work still goes on. The tool definition asks for it, but Claude Code does not hold
# a call to that, and a supervisor that has answered without it for days goes on doing so (a hint in the result
# changed nothing; being refused once did, as with the request tag on hand-outs). So the first few replies without
# it are sent back, with this.
WORKING_ASKS = 5
WORKING_ASK = (N_("Not sent: say whether the work for this request still goes on. Send the same text again with working: true "
               "(a status while the work goes on, such as 'on it') or working: false (the result, or an answer that needed "
               "no work). The panel's work line, its 중지 button and the alert that an answer came depend on it. Say it on "
               "every reply from now on."))


def said(tool):
    """A tool as Claude is told of it: its descriptions with the panels' button names as the language in use has them
    (the descriptions are English; the names in them are the Korean ones until T finds the text in its table)."""
    if isinstance(tool, dict):
        return {key: T(value) if key == "description" and isinstance(value, str) else said(value) for key, value in tool.items()}
    return [said(value) for value in tool] if isinstance(tool, list) else tool


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
    note = T("\n…(너무 길어 앞의 {limit}자만 보냅니다)", limit=limit)
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
        # Undoing a request in every window it changed (undo_request). Which windows those are is written on the
        # request's own panel message: item["points"], the chat_ids of the windows that got an undo point for it.
        self.undo_window = None  # callable(app, pin, request id) -> what became of it there; None: undo_in_window
        self.undoing: set[str] = set()  # request ids being undone right now (a second press waits for the first)
        self.undo_notes: dict[str, str] = {}  # chat_id -> what was undone from its panel, told to the supervisor with its next message
        # What a request took: the time the channel sees itself, the tokens are in Claude Code's transcripts
        # (handlib/usage.py). Said in the panel under the result and written to the event log (check_costs).
        self.transcript: Path | None = None  # the supervisor's transcript, as its hook says when it hands work out (assign)
        self.find_transcript = None  # callable() -> Path | None: where it is when no hook has said so yet
        self.ledger = None           # usage.Ledger over that transcript, from when this server started
        self.costs: dict[str, dict] = {}  # request id -> {"chat", "asked", "answered", "actions"}: answered, tokens still to come
        self.show_cost = True        # the line in the panel (config.json "show_cost": false leaves it to trace and usage)
        self.show_previews = True    # pictures of the other windows a request changed, under its result
        # The user's settings as they are now: callable(key) -> value (handlib/prefs.py reads config.json anew whenever it
        # changes, so a settings window's change holds without a restart). None: this hub's own attributes above.
        self.setting = None
        self.language_seen: str | None = None  # the interface's language when the watcher last looked (check_language)
        self.on_language = None      # callable(): the language changed (the subagents' files and the skill are written anew)
        # Messages for the supervisor that wait for its turn to end (deliver, check_held), oldest first.
        # Protection rules (handlib/protect.py). The channel writes none: it shows a rule the supervisor proposes in
        # the window's panel, where the user's press writes it, and says in the panels what the guard hook refused.
        self.home: Path | None = None  # claude-hand's home folder (this run's rules lie in it); None: the usual one
        self.offers: dict[str, dict] = {}  # offer id -> {"chat_id", "text"}: proposed rules the user has not answered
        self.offer_count = 0
        self.held: list[dict] = []   # {"since", "notification"}
        self.handed_at = 0.0         # when a message last went to the supervisor
        self.agent_stop_at = 0.0     # when a subagent last stopped: its report is on its way to the supervisor
        self.reading = threading.Lock()  # one reader of the transcripts at a time (the watcher, and whoever delivers)

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

    def assign(self, target: str, request: str | None, agent: str = "", transcript: str = "") -> dict:
        """The supervisor is about to hand work to a subagent (its hook on Agent and SendMessage says so, see
        tools/supervisor_hook.py). target: the subagent's name, or the id of one that worked before (SendMessage);
        agent: for such an id, the subagent's name when the hook could tell it.
        request: the id from the prompt's `[요청 …]` tag, "" for `[요청 없음]` (asked in the terminal or the Claude
        app), None when the prompt has no tag. From here until that subagent stops, the work in its window belongs
        to that request: previews, 중지 and the event log need not guess. The answer tells the hook what to do:
        ours false (not a window's subagent: none of its business), missing or unknown (refuse, the tag is wrong),
        stop (refuse: 중지 was pressed on that request), busy (refuse: the window is at work for another request).
        transcript: the supervisor's own transcript file, which the hook knows; the tokens of each request are read
        from it (check_costs)."""
        agents = {a.name for a in installed_agents(self.agents_dir)}
        with self.cond:
            now = time.time()
            if transcript.endswith(".jsonl") and Path(transcript) != self.transcript:
                self.transcript = Path(transcript)
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
                    self._log("refused", request=request, chat_id=window, tool=T("(맡기기)"), reason=T("사용자가 채팅창에서 중지를 누름"))
                    return {"ours": True, "chat_id": window, "stop": True}
            if self.protection(window).window == "locked":
                self._log("refused", request=request, chat_id=window, tool=T("(맡기기)"), reason=T("사용자가 잠근 창 (보호 규칙: 잠금)"))
                return {"ours": True, "chat_id": window, "locked": True}
            # A window has one place for "whose request is this work": handing it work for another request while it is
            # at work would put the rest of that work, its undo point and its 중지 down for the new request. Refused
            # until the subagent there stops, or shows no sign of life for as long as two subagents are kept apart.
            given = self._assignment(window, now)
            if given is not None and given["item"] is not item and now - given["ts"] <= LOCK_SECONDS:
                other = " ".join(str((given["item"] or {}).get("text", "")).split())[:40].rstrip()
                self._log("refused", request=request, chat_id=window, tool=T("(맡기기)"), reason=T("그 창이 다른 요청으로 일하는 중") +
                          (f" ({other})" if other else ""))
                return {"ours": True, "chat_id": window, "busy": True, "other": other}
            self.assigned[window] = {"asker": asker, "item": item, "ts": now}
            self._log("assigned", request=request, chat_id=window, text="" if asker else T("채팅창 요청이 아님"))
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

    def _on(self, key: str) -> bool:
        """A switch of the settings as it stands now."""
        if self.setting is not None:
            try:
                return self.setting(key) is not False
            except Exception as exc:  # a setting that cannot be read is as it was made
                log("could not read the settings:", exc)
        return getattr(self, key, True) is not False

    def use_history(self, directory: Path, keep_days: int = 0, now: float | None = None) -> int:
        """Keep each chat's recent messages in `directory` and show the saved ones again, so a panel still has its
        conversation after Claude Hand restarts. Numbered afresh: panels start over when the server changes.
        keep_days (the setting "history_keep_days"): messages older than that many days are not shown again and go
        from the files, and so do the pictures of that age; 0 keeps them whatever their age (LOG_LIMIT of each chat).
        Returns how many messages went."""
        self.history_dir = directory
        cutoff = (now or time.time()) - keep_days * 86400 if keep_days > 0 else None
        gone, thinned = 0, []
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
                fresh = [item for item in kept if cutoff is None or not isinstance(item.get("ts"), (int, float)) or item["ts"] >= cutoff]
                if len(fresh) < len(kept):
                    gone += len(kept) - len(fresh)
                    thinned.append(chat_id)
                for item in fresh[-LOG_LIMIT:]:
                    self.seq += 1
                    item["seq"] = self.seq
                    self.logs.setdefault(chat_id, []).append(item)
            for chat_id in thinned:  # the file keeps what is still shown (and whose subagent the window's was)
                self.logs.setdefault(chat_id, [])
                self._save_history(chat_id)
        if cutoff is not None and self.images_dir is not None:
            try:
                for old in [p for p in self.images_dir.glob("*.png") if p.stat().st_mtime < cutoff]:
                    old.unlink()
            except OSError:
                pass
        return gone

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
        content = text if not context else f"{text}\n\n" + T("[{app} 창 정보: {window}]", app=app, window=window) + f"\n{context}"
        meta = {"chat_id": chat_id, "app": app, "window": window, "agent": agent or "none"}
        with self.cond:
            undone = self.undo_notes.pop(chat_id, "")
            if undone:  # the panel undid a request in other windows since: the supervisor would still count on that work
                content = f"{content}\n\n{undone}"
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
            self.deliver(notification)
        return {"chat_id": chat_id, "agent": agent, "delivered": ready}

    def mark_initialized(self) -> None:
        """Claude Code finished connecting: send what panels posted while it was starting (lazy start)."""
        with self.cond:
            self.initialized = True
            backlog, self.backlog = self.backlog, []
        for notification in backlog:
            self.deliver(notification)

    def _supervisor_resting(self, now: float) -> bool:
        """Whether a message handed to the supervisor now would start a turn of its own. Read from its transcript
        (handlib/usage.py, Ledger.resting), plus what the channel knows is on its way there and the transcript does
        not show yet. True when there is no transcript to go by. Called without the lock: it reads files."""
        try:
            ledger = self._ledger()
            if ledger is None:
                return True
            with self.reading:
                ledger.update()
                over, at = ledger.resting()
        except Exception as exc:  # a transcript that cannot be read must not keep the user's messages back
            log("could not read the supervisor's transcript:", exc)
            return True
        coming = max(self.handed_at, self.agent_stop_at)
        if not over or (coming > at and now - coming < ARRIVAL_SECONDS):
            return False
        return now - at >= REST_SECONDS

    def deliver(self, notification: dict) -> bool:
        """Hand a channel message to the supervisor: now when it is at rest, else once its turn is over (check_held).
        Claude Code slips a message that arrives during a turn into that turn, next to whatever else arrives there
        (a subagent's report, marked as no input of the user's), and the supervisor then does not take it for the
        user's word: it answered "보낸 것으로 확인되지 않았다, 다시 보내 달라" to a request sent right after a result
        (2026-11-24). Arriving between turns, the message starts a turn as the panel's own. Returns whether it went
        now."""
        now = time.time()
        with self.cond:
            waiting = bool(self.held)
        resting = not waiting and self._supervisor_resting(now)
        meta = notification["params"]["meta"]
        with self.cond:
            if self.held or not resting:  # behind the ones already waiting: in the order they were sent
                self.held.append({"since": now, "notification": notification})
                self._log("held", request=meta.get("request", ""), chat_id=meta.get("chat_id", ""),
                          text=T("감독이 차례 중이라, 차례가 끝나면 넘김"))
                return False
            self.handed_at = now
        self.emit(notification)
        return True

    def check_held(self, now: float | None = None) -> int:
        """Hand over the oldest message that waits for the supervisor's turn to end, once it has ended, or once the
        message has waited HOLD_SECONDS. One at a time: the next waits for the turn this one starts. One pass (watch
        repeats). Returns how many went (0 or 1)."""
        now = now or time.time()
        with self.cond:
            if not self.held or not self.initialized:
                return 0
            waited = now - self.held[0]["since"]
        if waited < HOLD_SECONDS and not self._supervisor_resting(now):
            return 0
        with self.cond:
            if not self.held:
                return 0
            entry = self.held.pop(0)
            self.handed_at = now
            meta = entry["notification"]["params"]["meta"]
            self._log("released", request=meta.get("request", ""), chat_id=meta.get("chat_id", ""),
                      text=T("{seconds:.1f}초 기다림", seconds=now - entry['since']) + ("" if waited < HOLD_SECONDS else T(" (차례가 끝나지 않아 그냥 넘김)")))
        self.emit(entry["notification"])
        return 1

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
            if asked is not None and asked.get("request"):
                # What it took is said once the supervisor's turn is over (check_costs). A later answer to the same
                # request takes this one's place: then this was "on it" sent as if it were the result.
                self.costs[asked["request"]] = {"chat": chat_id, "asked": asked["ts"], "answered": now, "actions": asked.get("actions", 0),
                                                "item": asked}
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
        if self.capture is None or self.images_dir is None or not self._on("show_previews"):
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
                                       "caption": T("{window} ({app}) 지금 모습", window=window, app='Unity' if agent.app == 'unity' else 'Blender')})
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
                text = T("{window}({app})가 대화상자 때문에 멈춰 있어요. {app}에서 직접 골라 주세요: {dialogs}",
                         window=window, app=app, dialogs=describe_dialogs(dialogs))
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
                    "content": T("[claude-hand] 이 채팅창의 요청이 아직 답을 받지 못했습니다: \"{asked}\". 일이 끝났으면 reply로 결과를, "
                                 "아직 하는 중이면 reply에 working: true로 지금 상황을 한 줄 보내세요.", asked=asked),
                    "meta": {"chat_id": chat_id, "app": str(window.get("app", "")), "window": str(window.get("window", "")),
                             "agent": window.get("agent") or "none", "request": str(request.get("request", "")),
                             "reminder": "unanswered"}}})
                self._append(chat_id, {"kind": "notice", "text": T("답이 늦어져 Claude Hand에 다시 알렸어요.")})
                self._log("reminder", request=request.get("request", ""), chat_id=chat_id,
                          text=T("하위 세션이 멈춘 뒤 답이 없음") if worked else T("하위 세션이 일한 흔적이 없음"))
        for notification in reminders:
            self.deliver(notification)
        return [n["params"]["meta"]["chat_id"] for n in reminders]

    def _ledger(self):
        """The tokens of this run's requests, read from the supervisor's transcript and its subagents' (None when no
        transcript is known). Called without the lock: it reads files."""
        path = self.transcript
        if path is None and self.find_transcript is not None:
            path = self.find_transcript()
        if path is None:
            return None
        if self.ledger is None or self.ledger.transcript != Path(path):
            self.ledger = usage.Ledger(Path(path), since=self.started - 60.0)
        return self.ledger

    def check_costs(self, now: float | None = None) -> list[str]:
        """Say what each answered request took: under its result in the panel ("이 요청: 32초 · 동작 2번 · 토큰 …") and
        in the event log, where `trace` and `usage` read it. The time is the channel's own (panel message to result);
        the tokens are in Claude Code's transcripts (handlib/usage.py), and the last answer of the supervisor's turn
        is written there only when the turn is over. So a request waits until the transcript shows that, COST_WAIT at
        most; without a transcript to read, the line says the time alone. One pass (watch repeats). Returns the
        requests told."""
        now = now or time.time()
        with self.cond:
            due = {}
            for request, entry in self.costs.items():
                early = self.early.get(entry["chat"])
                # Answered, but the channel still takes it for "on it" with work under way: the result is yet to come
                # (for as long as _asking waits for it).
                if early is not None and early["item"] is entry["item"] and self._asking(entry["chat"], now) is early["item"] and (
                        early["worked"] or any(given["item"] is early["item"] for given in self.assigned.values())):
                    continue
                due[request] = entry
        if not due:
            return []
        try:
            ledger = self._ledger()
            if ledger is not None:
                with self.reading:
                    ledger.update()
        except Exception as exc:  # a transcript that cannot be read costs the tokens, not the line
            log("could not read the transcripts:", exc)
            ledger = None
        told = []
        for request, entry in due.items():
            if ledger is not None and not ledger.settled(request, entry["answered"]) and now - entry["answered"] < COST_WAIT:
                continue
            cost = ledger.cost(request) if ledger is not None else {}
            seconds = max(0.0, entry["answered"] - entry["asked"])
            with self.cond:
                if self.costs.get(request) is not entry:
                    continue  # answered anew meanwhile: that answer's turn is the one to wait for
                del self.costs[request]
                if self._on("show_cost"):
                    # The panel has moved on to its next message meanwhile: the line says which request it is about.
                    item = entry["item"]
                    newest = next((i for i in reversed(self.logs.get(entry["chat"], [])) if i.get("kind") == "user"), item)
                    which = T("이 요청") if newest is item else T("앞 요청 “{asked}”", asked=eventlog.short(item.get('text', ''), 20))
                    # "cost": the panels draw it as a quiet line (a clock in Blender), not as the box their other notices get
                    self._append(entry["chat"], {"kind": "notice", "cost": True, "text": f"{which}: " + usage.line(seconds, entry["actions"], cost)})
                self._log("cost", request=request, chat_id=entry["chat"], seconds=round(seconds, 1), actions=entry["actions"],
                          tokens=cost or None, text=usage.detail(cost))
            told.append(request)
        return told

    def check_language(self) -> bool:
        """The user chose another language in a settings window while Claude Hand is on. The panels, the commands and
        the hooks read the setting where they say something, so they follow at once; what was written out earlier in
        the other language does not: the subagents' definition files and the supervisor skill are written anew
        (on_language), and the supervisor, whose instructions were given when it started, is told in a message what
        the buttons are called from now on. One pass (watch repeats). Returns whether the language had changed."""
        now = prefs.language(home=self.home) if self.setting is not None else None
        if now is None or now == self.language_seen:
            return False
        first, self.language_seen = self.language_seen is None, now
        if first:
            return False  # as this server found it when it started: what was written out is in it
        if self.on_language is not None:
            try:
                self.on_language()
            except (Exception, SystemExit) as exc:  # the files stay as they were until the next start
                log("could not write the subagents' files in the new language:", exc)
        notification = {"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {
            "content": T("[claude-hand] 사용자가 화면 언어를 {language}로 바꿨습니다. 이제부터 채팅창의 버튼 이름과 도구가 돌려주는 글(막힌 호출의 "
                         "이유 포함)이 그 언어로 나옵니다. 사용자에게 버튼이나 칸을 가리킬 때 그 언어의 이름으로 말하세요. 답은 지금처럼 사용자가 쓴 "
                         "언어로 합니다. 이 알림에는 답하지 않습니다.", language=prefs.LANGUAGE_NAMES[now]) + glossary(),
            "meta": {"chat_id": "", "app": "", "window": "", "agent": "none", "notice": "language"}}}
        self._log("language", text=now)
        with self.cond:
            self.overview_count += 1  # the panels fetch the other windows' lines again: they are said in the language now
            self.cond.notify_all()
            ready = self.initialized
            if not ready:
                self.backlog.append(notification)
        if ready:
            self.deliver(notification)
        return True

    def watch(self, interval: float = 2.0) -> None:
        while True:
            for check in (self.check_held, self.check_dialogs, self.check_unanswered, self.check_costs, self.check_language):
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
            rules = self.protection(str(w.get("chat_id") or ""))
            if not rules.empty():
                w["protection"] = {key: getattr(rules, key) for key in ("window", "paths", "objects", "actions") if getattr(rules, key) != []}
        return {"windows": windows,
                "note": "Most recently used first. A window that is not listed has not reported since this session "
                        "started (its Claude Hand Tools / chat add-on may be missing). state: working (its subagent is "
                        "at it; asker: the chat whose request it is, when not its own), waiting (its panel has a request "
                        "and nothing is at work: your turn), dialog (a dialog stops that window), idle. protection: the "
                        "user's rules for that window (window look: only look there; locked: hand it no work at all; "
                        "paths, objects, actions: not to be changed or done). Calls that break them are refused."}

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
                            "others": self._others(chat_id, now), "others_rev": others, "undo": self._undo_offer(chat_id, now)}
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
        with label: the panel message it answers, if any. point names the request the window works for now (its id,
        or when nobody knows whose it is, the start of its work line): the guard keeps the files this call may write
        over under that name, and the undo point carries it. busy is true when another subagent is still at work in
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
                          reason=T("사용자가 채팅창에서 중지를 누름") + ("" if stopper == agent.name else f" ({stopper})"))
                return {"chat_id": agent.name, "stop": True, "by": stopper}
            worker = self.workers.get(agent.name)
            if agent_id and worker and worker["agent_id"] not in ("", agent_id) and not worker["stopped"] \
                    and now - worker["last"] <= LOCK_SECONDS:
                self._log("refused", request=self._request_for(agent.name), chat_id=agent.name, pin=pin, tool=tool[:80], agent_id=agent_id,
                          reason=T("다른 하위 세션({agent})이 이 창에서 작업 중", agent=worker["agent_id"]))
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
            if asked is not None:
                asked["actions"] = asked.get("actions", 0) + 1  # for the line that says what the request took
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
                if known and agent.name not in asked.setdefault("points", []):
                    # Written on the request itself: the windows to undo it in (undo_request), also after a restart.
                    asked["points"].append(agent.name)
                    asked.setdefault("point_names", {})[agent.name] = \
                        f"{agent.pin.rpartition('@')[0]} ({'Unity' if agent.app == 'unity' else 'Blender'})"
                    if self.history_dir is not None:
                        self._save_history(asker)
                    if asker != agent.name:
                        self._bump_progress(asker)  # the asking panel now offers to undo this window as well
            entry.update(tool=tool[:80], detail=detail[:120], actions=entry["actions"] + 1, updated=now)
            self._bump_progress(agent.name)
            label = " ".join(str(asked.get("text", "")).split())[:40].rstrip() if asked is not None and first else ""
            key = str(asked.get("request") or f"item-{asked['seq']}") if known else f"bar-{int(entry['started'] * 1000)}"
            self._log("action", request=self._request_for(agent.name), chat_id=agent.name, pin=pin, tool=tool[:80], detail=detail[:120],
                      agent_id=agent_id, first=first or None)
        return {"chat_id": agent.name, "new_request": first, "label": label, "point": key}

    def agent_stopped(self, app: str, pin: str, agent_id: str = "") -> dict | None:
        """A window's subagent finished its turn (its Stop hook says so): the window is free for another, and the
        supervisor now owes the panel an answer. None for a pin no generated subagent has."""
        agent = next((a for a in installed_agents(self.agents_dir) if a.app == app and a.pin == pin), None)
        if agent is None:
            return None
        with self.cond:
            self.agent_stop_at = time.time()  # the supervisor is about to be told: a turn of its own is starting
            worker = self.workers.get(agent.name)
            if worker and worker["agent_id"] in ("", agent_id) and not worker["stopped"]:
                worker["stopped"] = time.time()
                self._log("stopped", request=self._request_for(agent.name), chat_id=agent.name, pin=pin, agent_id=agent_id)
                if self.stops.get(agent.name, {}).get("seq") == 0:  # 중지 on work asked elsewhere: it has stopped
                    del self.stops[agent.name]
            # Also a subagent that never touched the window (the worker on record is an earlier one, long stopped):
            # left standing, what it was handed would keep the window shut to the next request (see assign).
            if worker is None or worker["stopped"] or worker["agent_id"] in ("", agent_id):
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
            self._append(chat_id, {"kind": "notice", "text": T("중지를 요청했어요. 지금 하던 동작 하나는 끝까지 갈 수 있어요.")})
            meta = {"chat_id": chat_id, "app": app, "window": window, "agent": agent or "none", "stop": "requested"}
            if item and item.get("request"):
                meta["request"] = item["request"]  # the supervisor's turn that sees to the stop is that request's doing too
            worker = self.workers.get(chat_id)
            if worker and worker["agent_id"] and not worker["stopped"]:
                meta["agent_id"] = worker["agent_id"]
            asked = " ".join(str((item or {}).get("text", "")).split())[:80]
            notification = {"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {
                "content": T("[claude-hand] 사용자가 이 채팅창에서 [중지]를 눌렀습니다{asked}"
                             ". 이 요청으로 일하는 하위 세션의 다음 Unity·Blender 호출은 채널이 막습니다. 이 요청의 일을 더 맡기지 말고, "
                             "아직 돌고 있는 하위 세션이 있으면 멈춘 뒤, 어디까지 했고 무엇이 남았는지 reply로 알리세요.",
                             asked=f': "{asked}"' if asked else ""),
                "meta": meta}}
            self._log("stop", request=(item or {}).get("request", ""), chat_id=chat_id)
            ready = self.initialized
            if not ready:
                self.backlog.append(notification)
        if ready:
            self.deliver(notification)
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

    # ------------------------------------------------------------------ undoing a request in every window it changed

    def _window_name(self, chat_id: str) -> str:
        """"hand_test (Unity)" for a window's chat, as the other-windows lines name it. Called with the lock held."""
        known = self.names.get(chat_id) or self.windows.get(chat_id) or self.contexts.get(chat_id) or {}
        app, _, rest = chat_id.partition("-")
        return f"{known.get('window') or rest} ({'Unity' if (known.get('app') or app) == 'unity' else 'Blender'})"

    def _undo_item(self, chat_id: str, request: str = "") -> dict | None:
        """The request of a chat to undo: the one with this id, else the newest it asked that still has a window to
        go back in. Called with the lock held."""
        for item in reversed(self.logs.get(chat_id, [])):
            if item.get("kind") == "user" and item.get("points") and (not request or item.get("request") == request):
                return item
        return None

    def _undo_offer(self, chat_id: str, now: float) -> dict | None:
        """What a panel is told so that its 되돌리기 can take a request back in the other windows too: the newest
        request asked in this chat that set an undo point in another window. The panel knows its own window's undo
        points and decides with that: this request first, unless its own newest point is of a later request. Called
        with the lock held."""
        for item in reversed(self.logs.get(chat_id, [])):
            if now - item["ts"] > OFFER_SECONDS:
                break
            others = [w for w in item.get("points") or [] if w != chat_id] if item.get("kind") == "user" else []
            if others:
                names = item.get("point_names") or {}
                return {"request": str(item.get("request", "")), "label": " ".join(str(item.get("text", "")).split())[:40].rstrip(),
                        "ts": item["ts"], "windows": [names.get(w) or self._window_name(w) for w in others]}
        return None

    @staticmethod
    def _undo_text(label: str, results: list[dict]) -> str:
        """How undoing a request went, window by window, as one line for a chat panel."""
        if not results:
            return T("'{label}' 요청은 다른 창에서 되돌릴 것이 없었어요.", label=label)
        parts = []
        for result in results:
            words = T(UNDO_WORDS.get(result["state"], UNDO_WORDS["failed"]))
            if result["state"] == "blocked" and result.get("top"):
                words = T("그 뒤의 요청('{top}')이 이 창을 바꿔서 건너뛰었어요. 그 창에서 먼저 되돌려 주세요", top=result['top'])
            more = result.get("summary") if result["state"] == "undone" else result.get("detail") if result["state"] == "failed" else ""
            parts.append(f"{result['window']}: {words}" + (f" ({str(more).rstrip('.')})" if more else ""))
        return T("'{label}' 요청 되돌리기: ", label=label) + " / ".join(parts)

    def undo_request(self, chat_id: str, request: str = "", skip=(), notify: bool = True) -> dict:
        """Undo a request in every window it changed: the panel's 되돌리기 for a request that reached other windows
        (the panel undoes its own window itself and passes it as skip), and the supervisor's undo_request tool for
        "cancel that" (notify false: the supervisor tells the panel itself). Each window is asked to undo that
        request by its id, and does so only when nothing newer changed it since (undo_in_window); a window at work
        is passed by. The windows that were undone get a line in their own chat. request "": the newest request of
        this chat that still has a window to go back in. Returns {"ok", "request", "label", "results": [{"chat_id",
        "window", "state", ...}], "text"}, or ok false with reason "none" / "running"."""
        agents = {a.name: a for a in installed_agents(self.agents_dir)}
        with self.cond:
            item = self._undo_item(chat_id, request)
            if item is None:
                return {"ok": False, "reason": "none"}
            key = str(item.get("request") or "")
            if key in self.undoing:
                return {"ok": False, "reason": "running"}
            self.undoing.add(key)
            label = " ".join(str(item.get("text", "")).split())[:40].rstrip()
            now, names = time.time(), item.get("point_names") or {}
            targets = [(window, names.get(window) or self._window_name(window),
                        self._state(window, now)["state"] in ("working", "stopping", "dialog"))
                       for window in item["points"] if window not in skip]
        results = []
        try:
            for window, name, busy in targets:  # outside the lock: a window may take a while
                agent = agents.get(window)
                if busy:
                    outcome = {"state": "busy"}
                elif agent is None:
                    outcome = {"state": "unreachable"}
                else:
                    try:
                        outcome = (self.undo_window or undo_in_window)(agent.app, agent.pin, key) or {"state": "failed"}
                    except Exception as exc:  # one window's trouble must not keep the others from going back
                        outcome = {"state": "failed", "detail": str(exc)[:300]}
                results.append({"chat_id": window, "window": name, **outcome})
        finally:
            with self.cond:
                self.undoing.discard(key)
                text = self._undo_text(label, results)
                item["points"][:] = [window for window in item["points"] if window not in skip]  # the caller saw to those itself
                for result in results:
                    if result["state"] in ("undone", "nothing") and result["chat_id"] in item["points"]:
                        item["points"].remove(result["chat_id"])
                    if result["state"] == "undone" and result["chat_id"] != chat_id:  # that window's own record
                        more = str(result.get("summary") or "")
                        self._append(result["chat_id"], {"kind": "notice", "text": T("{window} 채팅창의 요청 '{label}'을 여기서도 되돌렸어요.",
                                                                                     window=self._window_name(chat_id), label=label) + (f" {more}" if more else "")})
                if self.history_dir is not None:
                    self._save_history(chat_id)
                if notify:
                    self._append(chat_id, {"kind": "notice", "text": text})
                    if any(result["state"] == "undone" for result in results):  # the supervisor still counts on what is gone
                        self.undo_notes[chat_id] = T("[사용자가 채팅창에서 되돌렸음: {text}]", text=text)
                self._bump_progress(chat_id)  # the panel's button no longer offers what went back
                self._log("undo", request=key, chat_id=chat_id, text=eventlog.short(text))
        return {"ok": True, "request": key, "label": label, "results": results, "text": text}

    def start_undo(self, app: str, window: str, pin: str = "", request: str = "", skip_own: bool = False) -> dict:
        """되돌리기 in a panel, for a request that changed other windows: undo it there in the background (a window
        may take a while). The panel hears how it went as a notice in its chat."""
        chat_id, _ = resolve_window(installed_agents(self.agents_dir), app, window, pin)
        with self.cond:
            if self._undo_item(chat_id, request) is None:
                return {"chat_id": chat_id, "started": False}
        threading.Thread(target=self.undo_request, args=(chat_id, request, {chat_id} if skip_own else (), True), daemon=True).start()
        return {"chat_id": chat_id, "started": True}

    # ------------------------------------------------------------------ protection rules

    def protection(self, chat_id: str) -> protect.Rules:
        """The rules of a window's subagent's window as they stand in their files now (the project's or the .blend's,
        and this run's); none for a chat without a subagent, or when they cannot be looked up."""
        agent = next((a for a in installed_agents(self.agents_dir) if a.name == chat_id), None)
        if agent is None:
            return protect.Rules()
        try:
            where = agent.path
            if not where and agent.app == "unity":
                where = (find_project(hash_=agent.pin.rpartition("@")[2]) or {}).get("project_path") or ""
            return protect.window_rules(agent.app, where or None, self.home or claude_hand_home(), protect.session_key(agent.app, agent.pin))
        except Exception as exc:  # the guard hook holds every call against the rules anyway
            log("could not read a window's protection rules:", exc)
            return protect.Rules()

    def propose_protection(self, chat_id: str, kind: str, value: str, lasting: bool = True, why: str = "") -> dict:
        """The supervisor proposes a rule for a window (the user said in words what must not be touched). Shown in
        that window's panel with [추가] and [취소]; the panel writes the rule when the user presses [추가]. Returns
        {"ok": True, "offer_id"} or {"ok": False, "reason": what is wrong with the proposal}."""
        agent = next((a for a in installed_agents(self.agents_dir) if a.name == chat_id), None)
        if agent is None:
            return {"ok": False, "reason": f"Unknown chat_id {chat_id!r}: a rule is for one window (current_context lists them)."}
        kind, value = str(kind or "").strip().lower(), " ".join(str(value or "").split())
        problem = protect.proposal_problem(agent.app, kind, value)
        if problem:
            return {"ok": False, "reason": problem}
        text = protect.proposal_words(kind, value, lasting) + (f"\n{clip(' '.join(why.split()), 300)}" if why.strip() else "")
        with self.cond:
            self.offer_count += 1
            offer_id = f"p{self.offer_count}-{int(time.time())}"
            self.offers[offer_id] = {"chat_id": chat_id, "text": text}
            self._append(chat_id, {"kind": "protect_offer", "offer_id": offer_id, "text": text,
                                   "rule": {"kind": kind, "value": value, "lasting": bool(lasting)}})
            self._log("protect", chat_id=chat_id, request=self._request_for(chat_id), text=T("제안: ") + eventlog.short(text.splitlines()[0]))
        return {"ok": True, "offer_id": offer_id}

    def protection_verdict(self, offer_id: str, behavior: str) -> bool:
        """The user pressed [추가] or [취소] under a proposed rule (the panel has written the rule by then, or left
        it). The supervisor hears of it with that panel's next message."""
        if behavior not in ("added", "declined"):
            return False
        with self.cond:
            offer = self.offers.pop(offer_id, None)
            if offer is None:
                return False
            chat_id, rule = offer["chat_id"], offer["text"].splitlines()[0]
            self._append(chat_id, {"kind": "protect_resolved", "offer_id": offer_id, "behavior": behavior})
            note = T("[사용자가 제안된 보호 규칙을 추가했음: {rule}]", rule=rule) if behavior == "added" else \
                T("[사용자가 제안된 보호 규칙을 추가하지 않았음: {rule}]", rule=rule)
            self.undo_notes[chat_id] = "\n".join(filter(None, [self.undo_notes.get(chat_id, ""), note]))
            self._log("protect", chat_id=chat_id, text=(T("추가함: ") if behavior == "added" else T("추가 안 함: ")) + eventlog.short(rule))
        return True

    def protected(self, app: str, pin: str, tool: str, text: str, agent_id: str = "") -> dict | None:
        """A subagent's guard hook says the window's protection rules stopped a call, or that the window took a
        request's work back because something protected had changed: a line in that window's panel, and in the panel
        the request was asked in. None for a pin no generated subagent has."""
        agent = next((a for a in installed_agents(self.agents_dir) if a.app == app and a.pin == pin), None)
        if agent is None or not text.strip():
            return None
        text = clip(" ".join(text.split()), 600)
        with self.cond:
            now = time.time()
            self.names.setdefault(agent.name, {"app": agent.app, "window": agent.pin.rpartition("@")[0]})
            asker, _ = self._work_for(agent.name, now)
            self._append(agent.name, {"kind": "notice", "text": text, "alert": True})
            if asker and asker != agent.name:
                self._append(asker, {"kind": "notice", "text": f"{self._window_name(agent.name)}: {text}", "alert": True})
            self._log("protected", request=self._request_for(agent.name), chat_id=agent.name, pin=pin, tool=tool[:80], agent_id=agent_id,
                      reason=eventlog.short(text, 300))
        return {"chat_id": agent.name}

    def window_undone(self, app: str, window: str, pin: str, request: str) -> dict:
        """A panel undid this request in its own window with its own button: no other panel need offer that window
        along with the request any more. A nicety: asked again, the window would just say there is nothing left."""
        chat_id, _ = resolve_window(installed_agents(self.agents_dir), app, window, pin)
        with self.cond:
            for chat, items in self.logs.items():
                for item in items:
                    if item.get("kind") == "user" and request and item.get("request") == request and chat_id in (item.get("points") or []):
                        item["points"].remove(chat_id)
                        if self.history_dir is not None:
                            self._save_history(chat)
                        self._bump_progress(chat)
                        return {"chat_id": chat_id, "known": True}
        return {"chat_id": chat_id, "known": False}

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
        return " ".join(str(item.get("text", "")).split())[:40].rstrip() if item else ""

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
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [said(tool) for tool in (REPLY_TOOL, CONTEXT_TOOL, UNDO_TOOL, PROTECT_TOOL)]}})
        elif method == "tools/call":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": self.call_tool(params)})
        elif method == "notifications/claude/channel/permission_request":
            self.hub.permission_request(params)
        elif msg_id is not None:
            self.send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
        # other notifications (cancelled, roots changed, ...) need no answer

    def call_tool(self, params: dict) -> dict:
        name = params.get("name")
        if name not in ("reply", CONTEXT_TOOL["name"], UNDO_TOOL["name"], PROTECT_TOOL["name"]):
            return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool {name!r}"}]}
        self.hub.mark_in_use()
        if name == CONTEXT_TOOL["name"]:
            return {"content": [{"type": "text", "text": json.dumps(self.hub.current_context(), ensure_ascii=False)}]}
        args = params.get("arguments") or {}
        if name == PROTECT_TOOL["name"]:
            offered = self.hub.propose_protection(str(args.get("chat_id", "")), str(args.get("kind", "")), str(args.get("value", "")),
                                                  lasting=args.get("lasting") not in (False, "false"), why=str(args.get("why") or ""))
            if not offered["ok"]:
                return {"isError": True, "content": [{"type": "text", "text": "Not proposed: " + offered["reason"]}]}
            return {"content": [{"type": "text", "text": T("Proposed in that window's chat panel. It is not a rule yet: it holds once the "
                                                         "user presses [추가] there. Tell the user so in your reply; you will hear "
                                                         "what they chose with that panel's next message.")}]}
        if name == UNDO_TOOL["name"]:
            done = self.hub.undo_request(str(args.get("chat_id", "")), str(args.get("request") or ""), notify=False)
            if not done["ok"]:
                why = ("that request is being undone right now" if done["reason"] == "running" else
                       "Claude Hand knows of no request of that chat with something left to undo. Work asked in the terminal or "
                       "the Claude app has no undo point by request: hand that undo to the window's subagent instead")
                return {"isError": True, "content": [{"type": "text", "text": f"Nothing undone: {why}."}]}
            windows = [{k: v for k, v in result.items() if v not in ("", None)} for result in done["results"]]
            return {"content": [{"type": "text", "text": json.dumps({"request": done["request"], "asked": done["label"], "windows": windows,
                                                                    "in_words": done["text"]}, ensure_ascii=False)}]}
        chat_id, text = str(args.get("chat_id", "")), str(args.get("text", ""))
        origin = "desktop" if args.get("origin") == "desktop" else "panel"
        if not text.strip():
            return {"isError": True, "content": [{"type": "text", "text": "text is empty"}]}
        changed = args.get("changed_windows")  # None from a session that knows the tool without it: the channel then guesses
        changed = [str(c) for c in changed] if isinstance(changed, list) else None
        if origin == "panel" and args.get("working") is None and self.hub.known_chat(chat_id) and self.hub.wants_working():
            return {"isError": True, "content": [{"type": "text", "text": T(WORKING_ASK)}]}
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
                                                           str(body.get("agent") or "")[:200], str(body.get("transcript") or "")[:1000])})
        elif path == "/v1/stopped":  # a subagent's Stop hook: it finished its turn
            result = self.hub.agent_stopped(str(body.get("app", "")), str(body.get("pin", ""))[:200], str(body.get("agent_id", ""))[:80])
            self._json(200 if result else 404, {"ok": result is not None, **(result or {})})
        elif path == "/v1/protect":  # [추가] or [취소] under a protection rule the supervisor proposed
            ok = self.hub.protection_verdict(str(body.get("offer_id", ""))[:80], str(body.get("behavior", "")))
            self._json(200 if ok else 409, {"ok": ok})
        elif path == "/v1/protected":  # a subagent's guard hook: the window's protection rules stopped a call, or took work back
            result = self.hub.protected(str(body.get("app", "")), str(body.get("pin", ""))[:200], str(body.get("tool", "")),
                                        str(body.get("text", "")), str(body.get("agent_id", ""))[:80])
            self._json(200 if result else 404, {"ok": result is not None, **(result or {})})
        elif path in ("/v1/undo", "/v1/undone"):  # 되돌리기 in a panel: the other windows of a request / its own window, done
            app, request = str(body.get("app", "")), str(body.get("request") or "")[:80]
            if app not in ("unity", "blender"):
                self._json(400, {"ok": False, "error": "app must be unity or blender"})
                return
            window, pin = str(body.get("window", ""))[:200], str(body.get("pin", ""))[:200]
            if path == "/v1/undone":
                self._json(200, {"ok": True, **self.hub.window_undone(app, window, pin, request)})
                return
            result = self.hub.start_undo(app, window, pin, request, skip_own=body.get("skip_own") is True)
            self._json(200, {"ok": result["started"], **result, **({} if result["started"] else {"error": T("그 요청이 바꾼 다른 창이 없습니다")})})
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
HEALTH_CHECK_REASON = N_("claude mcp get/list가 상태 확인용으로 띄운 실행")


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
        return False, T("채널 플래그 없이 연 세션으로 보임 (부모 프로세스 확인 실패: {exc})", exc=exc)
    if claude and HEALTH_CHECK_RE.search(claude):
        # Run inside the supervisor, `claude mcp get` passes CLAUDE_HAND_SUPERVISOR=1 on to this copy too.
        return False, HEALTH_CHECK_REASON
    if is_supervisor(env):
        return True, "CLAUDE_HAND_SUPERVISOR=1 (start-supervisor)"
    if claude and f"server:{SERVER_NAME}" in claude and CHANNEL_FLAG_RE.search(claude):
        return True, T("Claude Code가 이 서버를 채널로 불러옴")
    return False, T("채널 플래그 없이 연 Claude Code 세션 (감독 세션은 start-supervisor.cmd로 여세요)")


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
        # What is said on stderr holds Korean too. Left to the system's code page (cp949 here), whoever reads it as
        # UTF-8 (Claude Code's log, the tests) gets bytes it cannot read.
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    if "--check" in argv:
        result = self_check()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    mcp = StdioMcp(sys.stdin, sys.stdout, default_agents_dir())
    home = claude_hand_home()
    serving, reason = supervisor_decision()
    live = supervisor_running(home)
    if serving and live:
        serving, reason = False, T("다른 감독 세션이 이미 채팅창을 받고 있음")
    if not serving:
        # Same tools, no panels: messages only reach the session that has the channel.
        log("passive:", T(reason))
        if reason != HEALTH_CHECK_REASON and not live:  # a status check or a second session keeps the real status
            try:
                write_status(home, "passive", T(reason))
            except OSError:
                pass
        mcp.serve()
        return 0
    mcp.hub.auto_sync = lambda agents_dir: refresh_agents(agents_dir=agents_dir)
    mcp.hub.events = eventlog.write
    mcp.hub.home = home
    protect.clear_session(home)  # the protection rules that were for the last run only ("이번만") are over
    # The settings (the gear button of a chat panel, `claude_hand.py settings`) are read where they are used, so a
    # change holds at once: the line of time and tokens, the pictures of other windows, the language.
    mcp.hub.setting = lambda key: prefs.get(key, home=home)
    mcp.hub.on_language = lambda: rewrite_installed(mcp.hub.agents_dir)
    mcp.hub.capture = lambda app, pin, path: capture_window(app, pin, path, PREVIEW_SIZE)
    mcp.hub.images_dir = home / "chat" / "images"
    gone = mcp.hub.use_history(home / "chat" / "history", prefs.get("history_keep_days", home=home) or 0)
    if gone:
        log(f"chat history: {gone} messages older than the days kept were removed")
    mcp.hub.find_dialogs = lambda app, pin: waiting_dialogs(find_project(hash_=pin.rpartition("@")[2])) if app == "unity" else []
    # The tokens of each request are read from the supervisor's transcript: the one start-supervisor opened, until
    # the supervisor's hook names it.
    mcp.hub.find_transcript = lambda: session_transcript(str(read_user_config(home).get("supervisor_session") or ""))
    threading.Thread(target=mcp.hub.watch, name="watch", daemon=True).start()
    if sys.platform == "win32":
        # Not at start: Claude Code may still be showing its development-channel confirmation in that window.
        # The first tool call or permission request means the session is past it. Whether to is asked then.
        def minimize() -> None:
            if not minimize_after_start(home):
                return
            table = process_table()
            claude_pid = claude_ancestor_pid(table, os.getpid())
            if claude_pid and not minimize_console(console_owners(table, claude_pid)):
                log(T("Claude Hand 창을 찾지 못해 최소화하지 않았습니다 (Windows Terminal 탭이면 그대로 둡니다)"))
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
            write_status(home, "stopped", T("감독 세션이 종료됨"))
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
