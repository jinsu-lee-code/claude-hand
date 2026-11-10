#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""claude-hand's own MCP server for one Unity project: exposes the ch_* tools of Claude Hand Tools.

MCP for Unity (CoplayDev) does not reliably expose custom C# tools (its stdio server in 10.3.0
lists none of them), so the Unity package opens its own loopback socket (ChBridge.cs) and records
where in ~/.claude-hand/unity/<hash>.json. Each unity-<project> subagent runs this file next to
MCP for Unity, pinned to its project:

  unity_bridge.py --hash 49ac508c --project-name hand_test       MCP server (stdio) for that project
  unity_bridge.py --list                                          the Unity editors that registered

The tool list comes from the registry file (the C# attributes of the ch_* tools), so it is there
even while Unity reloads scripts. Calls go to the socket; polled tools (ch_import_package) are
polled here until they finish. No third-party packages.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handlib import eventlog  # noqa: E402
from handlib.constants import VERSION  # noqa: E402
from handlib.link import claude_hand_home  # noqa: E402

SERVER_VERSION = VERSION
REGISTRY_FORMAT = "claude-hand/unity-bridge@1"
DIALOG_POLL_SECONDS = 1.5  # while a call waits, how often to look for a dialog that stopped the editor


def log(*parts) -> None:
    print("[claude-hand-unity]", *parts, file=sys.stderr, flush=True)


def registry_dir(env=os.environ) -> Path:
    return claude_hand_home(env) / "unity"


def load_registry(directory: Path | None = None) -> list[dict]:
    entries = []
    for path in sorted((directory or registry_dir()).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("format") == REGISTRY_FORMAT:
            data["_file"] = str(path)
            data["_mtime"] = path.stat().st_mtime
            entries.append(data)
    return entries


def matches(entry: dict, hash_: str = "", name: str = "", path: str = "") -> bool:
    """Same project: hash (8 or 16 hex, either way round), else folder path, else name."""
    if hash_:
        mine = {entry.get("hash", ""), entry.get("hash16", "")}
        return any(h and (h.startswith(hash_) or hash_.startswith(h)) for h in mine)
    if path:
        return entry.get("project_path", "").rstrip("/").lower() == path.replace("\\", "/").rstrip("/").lower()
    return bool(name) and entry.get("name") == name


def find_project(hash_: str = "", name: str = "", path: str = "", directory: Path | None = None) -> dict | None:
    found = [e for e in load_registry(directory) if matches(e, hash_, name, path)]
    found.sort(key=lambda e: e.get("_mtime", 0), reverse=True)  # newest registration first
    return found[0] if found else None


class BridgeError(Exception):
    def __init__(self, code: str, message: str, retry: str = "safe"):
        super().__init__(message)
        self.code, self.message, self.retry = code, message, retry


# ----------------------------------------------------------------------------- dialogs (Windows)
#
# A modal dialog ("Save the scene?", "API update required") stops the editor's main thread, so every call waits
# until it times out. Windows shows such a dialog as a standard dialog window (#32770) owned by the editor, with its
# text in Static/Edit controls and its choices as Buttons, so it can be read from outside without the editor's help.


def app_dialogs(pid: int) -> list[dict]:
    """The dialogs a process shows now: [{"title", "text", "buttons", "progress"}]; progress is true for a progress
    bar window (busy, nothing to choose). [] on other systems or when there is none."""
    if sys.platform != "win32" or not pid:
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")  # its own handle: argtypes set here must not change ctypes.windll.user32 for others
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT,
                                           wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]

    def class_of(hwnd):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def text_of(hwnd):  # WM_GETTEXT reads other processes' Edit controls too, where GetWindowText does not
        buf, done = ctypes.create_unicode_buffer(2048), ctypes.c_size_t()
        user32.SendMessageTimeoutW(hwnd, 0x000D, 2048, ctypes.cast(buf, ctypes.c_void_p).value, 0x0002, 500, ctypes.byref(done))
        return " ".join(buf.value.split())

    tops = []

    def each_top(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and class_of(hwnd) == "#32770":
            tops.append(hwnd)
        return True
    user32.EnumWindows(enum_proc(each_top), 0)
    dialogs = []
    for hwnd in tops:
        kids = []
        user32.EnumChildWindows(hwnd, enum_proc(lambda h, _: kids.append(h) or True), 0)
        shown = [(class_of(k), text_of(k)) for k in kids if user32.IsWindowVisible(k)]
        dialogs.append({"title": text_of(hwnd), "text": " ".join(t for c, t in shown if c in ("Static", "Edit") and t)[:500],
                        "buttons": [t for c, t in shown if c == "Button" and t],
                        "progress": any(c == "msctls_progress32" for c, _ in shown)})
    return dialogs


def waiting_dialogs(entry: dict | None, read=None) -> list[dict]:
    """The dialogs a registered editor waits on for the user (progress bars left out)."""
    if not entry:
        return []
    try:
        return [d for d in (read or app_dialogs)(int(entry.get("pid") or 0)) if not d.get("progress")]
    except Exception:  # reading windows is a nicety: never let it break a call
        return []


def describe_dialogs(dialogs: list[dict]) -> str:
    """"'Save Scene' 창: Do you want to save? [버튼: Save / Don't Save / Cancel]" for each dialog."""
    parts = []
    for d in dialogs:
        part = f"'{d.get('title') or '제목 없음'}' 창" + (f": {d['text']}" if d.get("text") else "")
        if d.get("buttons"):
            part += " [버튼: " + " / ".join(d["buttons"]) + "]"
        parts.append(part)
    return "; ".join(parts)


def dialog_error(entry: dict, dialogs: list[dict]) -> BridgeError:
    return BridgeError("DIALOG", f"Unity {entry.get('name')}가 대화상자 때문에 멈춰 있습니다: {describe_dialogs(dialogs)}. "
                                 "누를 버튼은 사용자가 정합니다. 그대로 감독에게 보고하세요. 대화상자가 닫히면 이 호출이 "
                                 "그때 실행될 수 있으니 다시 하기 전에 상태부터 확인하세요.", "check_first")


def request(entry: dict, tool: str, params: dict | None = None, timeout: float = 130.0, connect_timeout: float = 1.0,
            dialogs=None) -> dict:
    """One call over the editor's socket: line JSON in, line JSON out. Returns the tool's result. While it waits it
    looks for a dialog that stopped the editor and gives up at once with DIALOG instead of running into the timeout."""
    payload = json.dumps({"token": entry.get("token", ""), "tool": tool, "params": params or {},
                          "timeout": int(max(5, timeout - 10))}) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", int(entry["port"])), connect_timeout) as sock:
            sock.sendall(payload.encode("utf-8"))
            deadline, buffer = time.time() + timeout, b""
            while not buffer.endswith(b"\n"):
                sock.settimeout(max(0.05, min(DIALOG_POLL_SECONDS, deadline - time.time())))
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    if time.time() >= deadline:
                        raise
                    waiting = waiting_dialogs(entry, dialogs)
                    if waiting:
                        raise dialog_error(entry, waiting) from None
                    continue
                if not chunk:
                    break
                buffer += chunk
    except (OSError, KeyError, ValueError) as exc:
        raise BridgeError("UNREACHABLE", f"Unity {entry.get('name')} does not answer ({exc}).", "safe") from exc
    try:
        reply = json.loads(buffer.decode("utf-8") or "{}")
    except ValueError as exc:
        raise BridgeError("BAD_REPLY", "Unity sent an unreadable answer.", "check_first") from exc
    if not reply.get("ok"):
        error = reply.get("error") or {}
        raise BridgeError(error.get("code", "FAILED"), error.get("message", "Unity reported an error."), error.get("retry", "check_first"))
    return reply.get("result")


def call_with_retry(locate, tool: str, params: dict, attempts: int = 8, wait: float = 2.5) -> dict:
    """Re-read the registry and retry while Unity is reloading scripts (socket closed, port may change)."""
    last: BridgeError | None = None
    for attempt in range(attempts):
        entry = locate()
        if entry is None:
            last = BridgeError("NOT_RUNNING", "This Unity project is not open, or Claude Hand Tools is not installed in it.", "after_fix")
        else:
            try:
                return request(entry, tool, params)
            except BridgeError as exc:
                if exc.code not in ("UNREACHABLE", "EDITOR_BUSY", "UNAUTHORIZED"):
                    raise
                last = exc
        if attempt < attempts - 1:
            time.sleep(wait)
    raise last  # type: ignore[misc]


def run_tool(locate, meta: dict, params: dict, sleep=time.sleep, now=time.time) -> dict:
    """Call a tool; for polled tools (RequiresPolling) keep asking with action=<poll action> until done."""
    result = call_with_retry(locate, meta["name"], params)
    if not meta.get("requires_polling"):
        return result
    deadline = now() + (meta.get("max_poll_seconds") or 600)
    poll = {**params, "action": meta.get("poll_action") or "status"}
    while isinstance(result, dict) and result.get("_mcp_status") == "pending":
        if now() > deadline:
            raise BridgeError("TIMEOUT", f"{meta['name']} did not finish in time.", "check_first")
        sleep(min(5.0, max(0.2, float(result.get("_mcp_poll_interval") or 1.0))))
        result = call_with_retry(locate, meta["name"], poll)
    return result


# --------------------------------------------------------------------------- MCP server


STATUS_TOOL = {
    "name": "ch_bridge_status",
    "description": "Check that this subagent's Unity project is open and its Claude Hand Tools answer. Changes nothing.",
    "inputSchema": {"type": "object", "properties": {}},
}


def tool_definition(meta: dict) -> dict:
    properties, required = {}, []
    for param in meta.get("parameters", []):
        schema = {"type": param.get("type") or "string", "description": param.get("description", "")}
        if schema["type"] == "array":
            schema["items"] = {"type": "string"}
        properties[param["name"]] = schema
        if param.get("required"):
            required.append(param["name"])
    definition = {"name": meta["name"], "description": meta.get("description", ""),
                  "inputSchema": {"type": "object", "properties": properties}}
    if required:
        definition["inputSchema"]["required"] = required
    return definition


class BridgeServer:
    def __init__(self, stdin, stdout, hash_: str = "", name: str = "", path: str = "", directory: Path | None = None):
        self.stdin, self.stdout = stdin, stdout
        self.hash, self.name, self.path, self.directory = hash_, name, path, directory
        self.log_event = None  # callable(event, **fields) in place of the event log (tests)

    def locate(self) -> dict | None:
        return find_project(self.hash, self.name, self.path, self.directory)

    def tools(self) -> list[dict]:
        entry = self.locate()
        return [tool_definition(m) for m in (entry or {}).get("tools", [])]

    def send(self, message: dict) -> None:
        self.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.stdout.flush()

    def call(self, name: str, arguments: dict) -> dict:
        def text(obj, error=False):
            body = {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}
            if error:
                body["isError"] = True
            return body

        if name == STATUS_TOOL["name"]:
            entry = self.locate()
            if entry is None:
                return text({"connected": False, "why": "No registration from this Unity project in ~/.claude-hand/unity. "
                                                        "Open the project in Unity and make sure Claude Hand Tools compiled."}, True)
            try:
                pong = request(entry, "__ping__", timeout=10)
            except BridgeError as exc:
                return text({"connected": False, "project": entry.get("name"), "why": exc.message, "retry": exc.retry}, True)
            return text({"connected": True, "project": pong.get("project"), "hash": pong.get("hash"),
                         "unity_version": entry.get("unity_version"), "tools": [m["name"] for m in entry.get("tools", [])]})
        entry = self.locate()
        meta = next((m for m in (entry or {}).get("tools", []) if m.get("name") == name), None)
        started = time.time()
        if meta is None:
            self.note(name, started, "NOT_FOUND")
            return text({"code": "NOT_FOUND", "message": f"Unknown tool {name!r} for this project.", "retry": "after_fix"}, True)
        try:
            result = run_tool(self.locate, meta, arguments or {})
        except BridgeError as exc:
            self.note(name, started, exc.code)
            return text({"code": exc.code, "message": exc.message, "retry": exc.retry}, True)
        failed = isinstance(result, dict) and (result.get("success") is False or result.get("_mcp_status") == "error")
        self.note(name, started, str(result.get("code") or result.get("error") or "FAILED") if failed else "")
        return text(result)

    def note(self, tool: str, started: float, code: str) -> None:
        """How a ch_* call ended and how long it took, for the event log (`claude_hand.py trace`). Best effort."""
        try:
            (self.log_event or eventlog.write)("call", pin=f"{self.name}@{self.hash}" if self.name and self.hash else self.name or self.hash,
                                               tool=tool, ok=not code, code=code[:80], ms=round((time.time() - started) * 1000))
        except Exception:
            pass

    def handle(self, message: dict) -> None:
        method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method == "initialize":
            label = self.name or self.hash or self.path
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "claude-hand-unity", "version": SERVER_VERSION},
                "instructions": f"Claude Hand Tools (ch_*) for the Unity project {label}. If a call says the project does not "
                                "answer, use ch_bridge_status to see why.",
            }})
        elif method == "tools/list":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [STATUS_TOOL, *self.tools()]}})
        elif method == "tools/call":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": self.call(params.get("name", ""), params.get("arguments") or {})})
        elif method == "ping":
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif msg_id is not None:
            self.send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})

    def serve(self) -> None:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            try:
                self.handle(message)
            except Exception as exc:  # keep serving whatever one message does
                log("error:", exc)
                if isinstance(message, dict) and message.get("id") is not None:
                    self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": str(exc)}})


def alive(entry: dict) -> bool:
    try:
        request(entry, "__ping__", timeout=3, connect_timeout=0.5)
        return True
    except BridgeError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hash", default="")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--project-path", default="")
    parser.add_argument("--list", action="store_true", help="show the Unity editors that registered and whether they answer")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdin.reconfigure(encoding="utf-8")
    if args.list:
        for entry in load_registry():
            print(f"{entry.get('name')}@{entry.get('hash')}  {'answers' if alive(entry) else 'no answer'}  "
                  f"port {entry.get('port')}  {entry.get('project_path')}")
        return 0
    BridgeServer(sys.stdin, sys.stdout, args.hash, args.project_name, args.project_path).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
