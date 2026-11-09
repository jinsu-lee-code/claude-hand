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
but stays passive, so a session without the channel cannot swallow panel messages. Every start
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
from claude_hand import GENERATED_MARKER, refresh_agents, slug  # noqa: E402

SERVER_NAME = "claude-hand-chat"
SERVER_VERSION = "0.1.0"
DEFAULT_PORT = 8765
PORT_ATTEMPTS = 10
MAX_TEXT = 8000
MAX_CONTEXT = 3000
MAX_WAIT_SECONDS = 30
LOG_LIMIT = 500  # items kept per chat
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
    "more than a few seconds, send a short reply first saying what you are doing, then the result."
)

REPLY_TOOL = {
    "name": "reply",
    "description": "Send a message to a Unity or Blender chat panel. Pass the chat_id from the <channel> tag.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "The chat (window) to reply in, from the channel tag"},
            "text": {"type": "string", "description": "The message to show in that panel"},
        },
        "required": ["chat_id", "text"],
    },
}


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
        self.initialized = False

    def _append(self, chat_id: str, item: dict) -> dict:
        self.seq += 1
        item = {"seq": self.seq, "ts": time.time(), **item}
        log_items = self.logs.setdefault(chat_id, [])
        log_items.append(item)
        del log_items[:-LOG_LIMIT]
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
            ready = self.initialized
        self.emit({"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {"content": content, "meta": meta}})
        return {"chat_id": chat_id, "agent": agent, "delivered": ready}

    def reply(self, chat_id: str, text: str) -> bool:
        with self.cond:
            known = chat_id in self.logs
            if known:
                self._append(chat_id, {"kind": "reply", "text": text})
            return known

    @staticmethod
    def _permission_item(pending: dict) -> dict:
        return {"kind": "permission", "request_id": pending["request_id"], "tool_name": pending["tool_name"],
                "description": pending["description"], "input_preview": pending["input_preview"]}

    def permission_request(self, params: dict) -> None:
        """Claude Code asks for a tool approval; show it in every chat panel that is open."""
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
        deadline = time.time() + max(0.0, min(wait, MAX_WAIT_SECONDS))
        with self.cond:
            if chat_id and chat_id not in self.logs:
                self.logs[chat_id] = []  # an open panel that has not sent anything yet still gets permission prompts
            while True:
                items = [i for i in self.logs.get(chat_id, []) if i["seq"] > after]
                remaining = deadline - time.time()
                if items or remaining <= 0:
                    return items
                self.cond.wait(remaining)

    def status(self) -> dict:
        with self.cond:
            return {"ok": True, "server": SERVER_NAME, "version": SERVER_VERSION, "claude_connected": self.initialized,
                    "chats": sorted(self.logs), "open_permissions": sum(not p["resolved"] for p in self.permissions.values())}


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
            with self.hub.cond:
                self.hub.initialized = True
        elif method == "ping":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method == "tools/list":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [REPLY_TOOL]}})
        elif method == "tools/call":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": self.call_tool(params)})
        elif method == "notifications/claude/channel/permission_request":
            self.hub.permission_request(params)
        elif msg_id is not None:
            self.send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
        # other notifications (cancelled, roots changed, ...) need no answer

    def call_tool(self, params: dict) -> dict:
        if params.get("name") != "reply":
            return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool {params.get('name')!r}"}]}
        args = params.get("arguments") or {}
        chat_id, text = str(args.get("chat_id", "")), str(args.get("text", ""))
        if not text.strip():
            return {"isError": True, "content": [{"type": "text", "text": "text is empty"}]}
        if not self.hub.reply(chat_id, text[:MAX_TEXT]):
            return {"isError": True, "content": [{"type": "text", "text": f"No chat panel has used chat_id {chat_id!r}. Use the chat_id from the <channel> tag."}]}
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
            except ValueError:
                self._json(400, {"ok": False, "error": "after and wait must be numbers"})
                return
            items = self.hub.updates(q.get("chat_id", ""), after, wait)
            self._json(200, {"ok": True, "items": items, "next": max([after] + [i["seq"] for i in items])})
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
            result = self.hub.post_user_message(app, str(body.get("window", ""))[:200], text[:MAX_TEXT],
                                                pin=str(body.get("pin", ""))[:200], context=str(body.get("context", ""))[:MAX_CONTEXT])
            self._json(200, {"ok": True, **result})
        elif path == "/v1/permission":
            ok = self.hub.permission_verdict(str(body.get("request_id", "")), str(body.get("behavior", "")))
            self._json(200 if ok else 409, {"ok": ok})
        else:
            self._json(404, {"ok": False, "error": "not found"})


def start_http(hub: ChatHub, token: str, port: int | None = None, attempts: int = PORT_ATTEMPTS) -> ThreadingHTTPServer:
    first = port or DEFAULT_PORT
    last_error = None
    for candidate in range(first, first + attempts):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), PanelHandler)
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
    """Where the panels find this process. Newest supervisor wins; run one supervisor at a time."""
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


def channel_ancestor(table: dict[int, tuple[int, str]], pid: int, max_depth: int = 12) -> str | None:
    """Command line of the Claude Code process that started us, if it loads this server as a channel.
    Only the nearest Claude Code ancestor counts; uv, shells and other wrappers in between are skipped."""
    seen = set()
    current = table.get(pid, (0, ""))[0]
    for _ in range(max_depth):
        if not current or current in seen or current not in table:
            return None
        seen.add(current)
        parent, cmdline = table[current]
        if is_claude_code(cmdline):
            has_channel = f"server:{SERVER_NAME}" in cmdline and CHANNEL_FLAG_RE.search(cmdline)
            return cmdline if has_channel else None
        current = parent
    return None


def supervisor_decision(env=os.environ, table_fn=process_table, pid: int | None = None) -> tuple[bool, str]:
    if is_supervisor(env):
        return True, "CLAUDE_HAND_SUPERVISOR=1 (start-supervisor)"
    try:
        found = channel_ancestor(table_fn(), pid or os.getpid())
    except Exception as exc:  # never let the check stop the server from starting
        return False, f"채널 플래그 없이 연 세션으로 보임 (부모 프로세스 확인 실패: {exc})"
    if found:
        return True, "Claude Code가 이 서버를 채널로 불러옴"
    return False, "채널 플래그 없이 연 Claude Code 세션 (감독 세션은 start-supervisor.cmd로 여세요)"


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
    if not serving:
        # Same tools, no panels: messages only reach the session that has the channel.
        log("passive:", reason)
        try:
            write_status(home, "passive", reason)
        except OSError:
            pass
        mcp.serve()
        return 0
    mcp.hub.auto_sync = lambda agents_dir: refresh_agents(agents_dir=agents_dir)
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
