"""The low-level ways to things that are already running: where claude-hand keeps its files, the chat channel's
server file and whether it answers, and MCP for Blender's socket.

Kept small on purpose: the guard hook loads this on every tool call of a subagent.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.request
from pathlib import Path


def claude_hand_home(env=os.environ) -> Path:
    return Path(env.get("CLAUDE_HAND_HOME") or Path.home() / ".claude-hand")


def chat_home(env=os.environ) -> Path:
    return claude_hand_home(env) / "chat"


def exchange_dir(env=os.environ) -> Path:
    """Where Unity and Blender windows leave files for each other. The same rule as Claude Hand Tools (ChPaths) and
    the Blender add-on: CLAUDE_HAND_EXCHANGE_DIR, else exchange/ in claude-hand's home folder."""
    return Path(env.get("CLAUDE_HAND_EXCHANGE_DIR") or claude_hand_home(env) / "exchange")


def read_chat_files(home: Path | None = None) -> tuple[dict | None, dict | None]:
    """(server.json, status.json) of the chat channel; None for one that is missing or unreadable."""
    home = home or chat_home()

    def load(name):
        try:
            return json.loads((home / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return load("server.json"), load("status.json")


def supervisor_running(home: Path | None = None, fetch=None) -> bool:
    """A supervisor session's chat channel answers right now (a server.json left behind by a closed one does not)."""
    server, _ = read_chat_files((home or claude_hand_home()) / "chat")
    if not server:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server['port']}/v1/status",
                                     headers={"X-Claude-Hand-Token": server.get("token", "")})
        state = (fetch or (lambda r: json.loads(urllib.request.urlopen(r, timeout=2).read().decode("utf-8"))))(req)
    except (OSError, ValueError, KeyError):
        return False
    return bool(state.get("ok"))


def channel_get(path: str, home: Path | None = None, fetch=None) -> dict | None:
    """What the running supervisor's chat channel answers to a GET; None when there is none or it does not answer."""
    server, _ = read_chat_files((home or claude_hand_home()) / "chat")
    if not server:
        return None
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server['port']}{path}", headers={"X-Claude-Hand-Token": server.get("token", "")})
        return (fetch or (lambda r: json.loads(urllib.request.urlopen(r, timeout=3).read().decode("utf-8"))))(req)
    except (OSError, ValueError, KeyError):
        return None


def blender_request(port: int, command: dict, timeout: float = 2.0, connect_timeout: float = 0.5):
    """MCP for Blender's add-on speaks bare JSON over TCP: one object in, one object out.
    The connect timeout is short on purpose: Windows takes about two seconds to refuse a closed
    local port, and an add-on that is listening accepts at once even while Blender is busy."""
    with socket.create_connection(("127.0.0.1", port), min(timeout, connect_timeout)) as s:
        s.settimeout(timeout)
        s.sendall(json.dumps(command).encode("utf-8"))
        buffer = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buffer += chunk
            try:
                return json.loads(buffer.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue  # partial message
    return None
