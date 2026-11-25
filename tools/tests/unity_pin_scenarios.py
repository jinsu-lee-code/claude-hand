"""Where does a MCP for Unity server (stdio) send a command when the editor it is for does not answer?

Two fake editors stand in a temporary folder (no Unity is touched) and the server's own code does the choosing, so
this runs with the server's Python, not claude-hand's (test_unity_pin.py finds it and skips when it is not installed):

    uvx --offline --from mcpforunityserver==10.3.0 python tools/tests/unity_pin_scenarios.py [--quick]

Started plainly it shows what the server does by itself; started the way claude-hand starts a subagent's server
(PYTHONPATH=tools/unity_pin and CLAUDE_HAND_UNITY_PIN=ProjA@<hash of C:/Proj/ProjA/Assets>) it shows what the pin
changes. The last line is "RESULT " and JSON: {"pinned": bool, "reached_other": [scenario, ...], "where": {scenario:
"own"|"other"|"nowhere"}, "connected_to_other": [scenario, ...]} (the last: where the server opened a socket to the
other editor at all, which cuts whoever is working there).
"""

import asyncio
import hashlib
import json
import logging
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUICK = "--quick" in sys.argv


def assets(name: str) -> str:
    return f"C:/Proj/{name}/Assets"


def project_hash(name: str) -> str:
    """As MCP for Unity names a project: the first 8 hex of SHA1(Application.dataPath)."""
    return hashlib.sha1(assets(name).encode("utf-8")).hexdigest()[:8]


PIN = f"ProjA@{project_hash('ProjA')}"
sys.stdout.reconfigure(encoding="utf-8")
HOME = Path(tempfile.mkdtemp(prefix="claude_hand_pin_")) / "home"
REGISTRY = HOME / ".unity-mcp"
REGISTRY.mkdir(parents=True)
os.environ.update(USERPROFILE=str(HOME), HOME=str(HOME), UNITY_MCP_STATUS_DIR=str(REGISTRY), UNITY_MCP_DEFAULT_INSTANCE=PIN,
                  UNITY_MCP_RELOAD_MAX_WAIT_S="1")
if QUICK:
    os.environ["UNITY_MCP_COMMAND_TOTAL_TIMEOUT"] = "3"  # a call to an editor that is not there gives up sooner
logging.disable(logging.CRITICAL)

from transport.legacy import unity_connection  # noqa: E402  (the server's own modules)
from transport.legacy.port_discovery import PortDiscovery  # noqa: E402
from transport.legacy.stdio_port_registry import stdio_port_registry  # noqa: E402
from transport.unity_instance_middleware import UnityInstanceMiddleware  # noqa: E402

PINNED = getattr(PortDiscovery.discover_unity_port, "__name__", "") == "my_port"


def frame(obj) -> bytes:
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">Q", len(data)) + data


def receive(conn, count):
    buf = bytearray()
    while len(buf) < count:
        chunk = conn.recv(count - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class FakeEditor:
    """What the server sees of one Unity editor: its status and port files, and the framed socket behind them."""

    def __init__(self, name):
        self.name, self.hash, self.port = name, project_hash(name), 0
        self.received, self.conns, self.listener, self.slow, self.drop, self.accepted = [], [], None, 0.0, False, 0

    def status(self, reloading=False, age_s=0.0, reason=None):
        beat = datetime.now(timezone.utc) - timedelta(seconds=age_s)
        body = {"unity_port": self.port, "reloading": reloading, "reason": reason or ("reloading" if reloading else "ready"),
                "project_path": assets(self.name), "last_heartbeat": beat.isoformat().replace("+00:00", "Z")}
        (REGISTRY / f"unity-mcp-status-{self.hash}.json").write_text(json.dumps(body))
        (REGISTRY / f"unity-mcp-port-{self.hash}.json").write_text(json.dumps({"unity_port": self.port, "project_path": body["project_path"]}))

    def forget(self):
        for kind in ("status", "port"):
            (REGISTRY / f"unity-mcp-{kind}-{self.hash}.json").unlink(missing_ok=True)

    def up(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", self.port))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(16)
        threading.Thread(target=self._accept, args=(self.listener,), daemon=True).start()
        self.status()

    def down(self):
        """The listener goes and open connections close, as when the editor reloads scripts or quits."""
        if self.listener:
            self.listener.close()
            self.listener = None
        for conn in self.conns:
            try:
                conn.close()
            except OSError:
                pass
        self.conns = []

    def _accept(self, listener):
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            self.accepted += 1
            for other in self.conns:  # as the real bridge does: "only ever one active Python server"
                try:
                    other.close()
                except OSError:
                    pass
            self.conns = [conn]
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            if self.slow:
                time.sleep(self.slow)
            conn.sendall(b"WELCOME UNITY-MCP 1 FRAMING=1\n")
            while True:
                header = receive(conn, 8)
                payload = receive(conn, struct.unpack(">Q", header)[0]) if header else None
                if payload is None:
                    return
                if payload == b"ping":
                    conn.sendall(frame({"status": "success", "result": {"message": "pong"}}))
                    continue
                command = json.loads(payload)
                if command.get("type") == "get_project_info":  # what the pin asks a new connection
                    conn.sendall(frame({"status": "success", "result": {"success": True, "message": "Retrieved project info.", "data": {
                        "projectRoot": assets(self.name)[:-len("/Assets")], "projectName": self.name, "assetsPath": assets(self.name)}}}))
                    continue
                if self.drop:  # scripts reload while the command is on its way: no answer, the listener goes
                    self.drop = False
                    self.down()
                    return
                self.received.append(command["params"].get("name"))
                conn.sendall(frame({"status": "success", "result": {"success": True, "message": f"done in {self.name}"}}))
        except OSError:
            return


OWN, OTHER = FakeEditor("ProjA"), FakeEditor("ProjB")


class Session:
    """One subagent's server session: the middleware decides the editor, the tool then sends to it."""

    def __init__(self):
        unity_connection._unity_connection_pool = None
        stdio_port_registry.clear()
        self.state, self.middleware = {}, UnityInstanceMiddleware()

    async def get_state(self, key):
        return self.state.get(key)

    async def set_state(self, key, value):
        self.state[key] = value

    def call(self, name):
        context = types.SimpleNamespace(fastmcp_context=self, message=types.SimpleNamespace(arguments={}))
        self.state.pop("unity_instance", None)  # kept per request in the real server
        try:
            asyncio.run(self.middleware._inject_unity_instance(context))
            reply = unity_connection.send_command_with_retry("manage_gameobject", {"action": "create", "name": name},
                                                             instance_id=self.state.get("unity_instance"))
            said = reply.get("message") if isinstance(reply, dict) else str(getattr(reply, "error", reply))
        except Exception as error:  # what the tool call would answer
            said = f"{type(error).__name__}: {str(error)[:90]}"
        where = "own" if name in OWN.received else "other" if name in OTHER.received else "nowhere"
        print(f"    {name:<24} -> {where:<8} | {said}")
        return where


def fresh():
    for editor in (OWN, OTHER):
        editor.down()
        editor.slow, editor.drop, editor.port = 0.0, False, 0
        editor.up()
    OTHER.accepted = 0
    return Session()


def both_answer(where):
    where["both answer"] = fresh().call("one")


def stops_between_calls(where):
    session = fresh()
    session.call("two-before")
    OWN.down()
    where["stops answering between two calls"] = session.call("two")
    OWN.up()
    where["stops answering between two calls, later"] = session.call("two-once-it-is-back")


def long_reload(where):
    session = fresh()
    OWN.down()
    OWN.status(reloading=True, age_s=90)
    where["reloading for over a minute"] = session.call("three")
    OWN.up()
    where["reloading for over a minute, later"] = session.call("three-once-it-is-back")


def busy(where):
    session = fresh()
    OWN.slow = 1.0  # the server's probe waits 0.3 s
    where["slow to answer"] = session.call("four")
    OWN.slow = 0.0
    where["slow to answer, later"] = session.call("four-once-it-is-quick")


def closed(where):
    session = fresh()
    OWN.down()
    OWN.forget()
    where["closed"] = session.call("five")


def reload_with_fresh_heartbeat(where):
    session = fresh()
    OWN.down()
    OWN.status(reloading=True, age_s=2)
    where["reloading, heartbeat fresh"] = session.call("six")


def reload_during_a_command(where):
    session = fresh()
    session.call("seven-before")
    OWN.drop = True
    where["reload while a command is on its way"] = session.call("seven")
    OWN.up()
    where["reload while a command is on its way, later"] = session.call("seven-once-it-is-back")


def port_taken_over(where):
    """Its own editor's bridge stopped and its file still names the port; the other editor, unable to bind its own
    port after a reload, moved to the lowest free one: that one."""
    session = fresh()
    session.call("eight-before")
    OWN.down()
    OWN.status(reason="stopped")
    OTHER.down()
    OTHER.port = OWN.port
    OTHER.up()
    where["port taken over"] = session.call("eight")
    OWN.port = 0
    OWN.up()
    time.sleep(5.2)  # past the server's 5 s memory of who is where
    where["port taken over, later"] = session.call("eight-once-it-is-back")


def ports_swapped(where):
    """Both editors reloaded and came back on each other's ports, inside the server's 5 s memory of who is where."""
    session = fresh()
    session.call("nine-before")
    session.call("nine-before-again")
    OWN.down()
    OTHER.down()
    OWN.port, OTHER.port = OTHER.port, OWN.port
    OTHER.up()
    OWN.up()
    where["ports swapped"] = session.call("nine")


SCENARIOS = [both_answer, stops_between_calls, long_reload, busy, closed, reload_with_fresh_heartbeat, reload_during_a_command,
             port_taken_over, ports_swapped]
if QUICK:
    SCENARIOS = [both_answer, long_reload, closed, reload_during_a_command, port_taken_over, ports_swapped]

print(f"server code: {Path(unity_connection.__file__).parents[2]}   pinned by claude-hand: {PINNED}")
where: dict[str, str] = {}
connected_to_other = []
for scenario in SCENARIOS:
    print(scenario.__name__.replace("_", " "))
    scenario(where)
    if OTHER.accepted:
        connected_to_other.append(scenario.__name__.replace("_", " "))
print("RESULT " + json.dumps({"pinned": PINNED, "reached_other": [key for key, value in where.items() if value == "other"], "where": where,
                              "connected_to_other": connected_to_other}))
