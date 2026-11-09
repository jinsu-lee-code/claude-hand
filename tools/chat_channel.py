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

Only the supervisor session serves the panels: start-supervisor sets CLAUDE_HAND_SUPERVISOR=1.
Any other Claude Code session opened in this folder also starts this server (it is in .mcp.json)
but stays passive, so it cannot capture the panels with a session that has no channel.

  python tools/chat_channel.py --check   checks that this machine can run the channel (used by setup)

No third-party packages; run with `uv run` or any Python 3.10+.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_hand import GENERATED_MARKER, slug  # noqa: E402

SERVER_NAME = "claude-hand-chat"
SERVER_VERSION = "0.1.0"
DEFAULT_PORT = 8765
PORT_ATTEMPTS = 10
MAX_TEXT = 8000
MAX_CONTEXT = 3000
MAX_WAIT_SECONDS = 30
LOG_LIMIT = 500  # items kept per chat

INSTRUCTIONS = (
    "This channel connects chat panels inside Unity and Blender windows to you. A message arrives as "
    '<channel source="claude-hand-chat" chat_id="..." app="unity|blender" window="..." agent="...">. '
    "The text is from the user, who is sitting in that window and cannot see your terminal output, so always "
    "answer with the reply tool, passing the chat_id from the tag; reply in the user's language. "
    "Routing: the agent attribute names the claude-hand subagent that owns that window (unity-* / blender-*). "
    "Hand the request to that subagent. For the same chat_id keep resuming the same subagent with SendMessage "
    "instead of starting a new one, so each window keeps its own conversation. When a request involves other "
    "windows (moving objects between projects, Blender to Unity), coordinate the other subagents as the claude-hand "
    "skill describes, and report back in the chat_id the request came from. If agent is \"none\", reply that this "
    "window has no subagent yet and that `uv run tools/claude_hand.py sync` creates one. For work that takes "
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

    def __init__(self, emit, agents_dir: Path):
        self.emit = emit  # callable(dict): writes one JSON-RPC message to Claude Code
        self.agents_dir = agents_dir
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
        return resolve_window(load_agents(self.agents_dir), app, window, pin)

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
    return env.get("CLAUDE_HAND_SUPERVISOR", "").lower() in ("1", "true", "yes", "on")


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
    if not is_supervisor():
        # Same tools, no panels: messages only reach a session started with start-supervisor.
        log("passive: not started by start-supervisor (CLAUDE_HAND_SUPERVISOR unset); panels are not served")
        mcp.serve()
        return 0
    token = secrets.token_urlsafe(32)
    env_port = os.environ.get("CLAUDE_HAND_CHAT_PORT")
    httpd = start_http(mcp.hub, token, int(env_port) if env_port else None)
    server_file = write_server_file(claude_hand_home(), httpd.server_address[1], token)
    log(f"panels: http://127.0.0.1:{httpd.server_address[1]} (token in {server_file})")
    try:
        mcp.serve()
    finally:
        httpd.shutdown()
        remove_server_file(server_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
