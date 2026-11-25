"""Keeps a MCP for Unity server (stdio) on the one Unity editor its subagent is for.

claude-hand starts each Unity subagent's server with this folder on PYTHONPATH and CLAUDE_HAND_UNITY_PIN=<name>@<hash>
(handlib/agents.py). Python imports a module of this name when it starts, before the server's own code runs.

Why. --default-instance is only a default, and a port is not an identity (seen in server and package 10.3.0):
- When the pinned editor is not in the server's list for a moment (reloading for over a minute, slow to answer, its
  bridge stopped), the server takes whatever other editor is: its middleware selects "the sole instance" and keeps it
  for the session, and a send that fails falls back to a scan of every editor's port.
- An editor that cannot bind its port again after a reload moves to the lowest free one, which may be the port another
  editor had a moment ago. The server reconnects to the port it remembers and gets no word on whose it is.
- An editor closes all its other connections whenever a new one arrives, and the server looks for editors by
  connecting to each of them: every server keeps cutting the connections of every other window's server.
The work then lands in another project, or is cut off half way.

So here the server (1) is shown its own editor and no other, (2) never connects to another editor's port, nor a
second time to its own while it holds a connection, and (3) uses a new connection only after the editor behind it
has said which project it is. A call to an editor that is not there fails or waits.

Standard library only, and silent in any Python that is not the server's (uv's own helpers inherit the environment).
"""

import hashlib
import importlib
import importlib.util
import json
import os
import struct
import sys

DISCOVERY_MODULE = "transport.legacy.port_discovery"
CONNECTION_MODULE = "transport.legacy.unity_connection"
WHO = {"type": "get_project_info", "params": {}}  # answered by the editor itself with its Assets path


def say(message: str) -> None:
    print(f"claude-hand: {message}", file=sys.stderr, flush=True)


def project_hash(assets_path: str) -> str:
    """The hash MCP for Unity names a project by: the first 8 hex of SHA1(Application.dataPath)."""
    return hashlib.sha1(assets_path.encode("utf-8")).hexdigest()[:8]


def pin_discovery(discovery, pin_hash: str, connected=lambda: False) -> None:
    """Make `discovery` (the server's PortDiscovery class) see only the editor whose project hash is `pin_hash`,
    and connect to no other. `connected` says whether the server holds a connection to its editor right now."""
    everything = discovery.discover_all_unity_instances
    probe = discovery._try_probe_unity_mcp
    folder = discovery.get_registry_dir

    def only_mine():
        return [inst for inst in everything() if getattr(inst, "hash", None) == pin_hash]

    def my_port() -> int:
        """The pinned editor's port as its own files give it, answering or not: a retry waits for that editor."""
        for kind in ("status", "port"):
            try:
                port = json.loads((folder() / f"unity-mcp-{kind}-{pin_hash}.json").read_text(encoding="utf-8")).get("unity_port")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(port, int) and not isinstance(port, bool):
                return port
        raise ConnectionError(f"claude-hand: the Unity editor this server is pinned to ({pin_hash}) is not open.")

    def probe_mine(port) -> bool:
        """An editor drops every other connection when a new one arrives, so: none to another editor's port (that
        would cut the server working there), and none to its own while one is open (that would cut its own call)."""
        try:
            if port != my_port():
                return False
        except ConnectionError:
            return False
        return True if connected() else bool(probe(port))

    discovery.discover_all_unity_instances = staticmethod(only_mine)
    discovery.discover_unity_port = staticmethod(my_port)
    discovery._try_probe_unity_mcp = staticmethod(probe_mine)


def editor_behind(conn, wait: float) -> str:
    """The project hash of the editor a new connection reached, asked of that editor itself."""
    sock = conn.sock
    payload = json.dumps(WHO).encode("utf-8")
    before = sock.gettimeout()
    sock.settimeout(wait)
    try:
        with conn._io_lock:
            sock.sendall(struct.pack(">Q", len(payload)) + payload if conn.use_framing else payload)
            answer = json.loads(conn.receive_full_response(sock).decode("utf-8"))
    finally:
        try:
            sock.settimeout(before)
        except OSError:
            pass
    result = answer.get("result") if isinstance(answer, dict) else None
    data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        return f"unknown: {str(answer)[:80]}"
    assets = data.get("assetsPath") or (f"{data['projectRoot']}/Assets" if data.get("projectRoot") else "")
    return project_hash(assets) if assets else f"unknown: {str(answer)[:80]}"


def pin_connection(connection, pin_hash: str) -> None:
    """Make `connection` (the server's UnityConnection class) keep a new socket only when the editor behind it is
    the project `pin_hash`. A socket stays with the editor it reached, so asking once per socket is enough."""
    connect = connection.connect

    def connect_to_mine(self, *args, **kwargs):
        if getattr(self, "sock", None):
            return True
        if not connect(self, *args, **kwargs):
            return False
        given = args[0] if args else kwargs.get("connect_timeout")
        wait = min(30.0, max(2.0, float(given))) if isinstance(given, (int, float)) and not isinstance(given, bool) else 30.0
        try:
            found = editor_behind(self, wait)
        except Exception as error:
            found = f"no answer ({type(error).__name__}: {error})"
        if found == pin_hash:
            return True
        say(f"port {getattr(self, 'port', '?')} is not the Unity editor this server is for ({pin_hash}): it is {found}. Not used.")
        try:
            self.disconnect()
        except Exception:
            self.sock = None
        return False

    connection.connect = connect_to_mine


def main() -> None:
    pin_hash = os.environ.get("CLAUDE_HAND_UNITY_PIN", "").rpartition("@")[2].strip()
    if not pin_hash:
        return
    try:
        if importlib.util.find_spec(DISCOVERY_MODULE) is None:
            return
    except (ImportError, ValueError):
        return  # not the server's Python
    try:
        discovery = importlib.import_module(DISCOVERY_MODULE).PortDiscovery
        connections = importlib.import_module(CONNECTION_MODULE)
        for owner, names in ((discovery, ("discover_all_unity_instances", "discover_unity_port", "get_registry_dir", "_try_probe_unity_mcp")),
                             (connections.UnityConnection, ("connect", "disconnect", "receive_full_response"))):
            for name in names:  # all of it or none: half a pin would look like one
                getattr(owner, name)

        def connected() -> bool:
            try:
                pool = getattr(connections, "_unity_connection_pool", None)
                return any(getattr(conn, "sock", None) is not None for conn in list(getattr(pool, "_connections", {}).values()))
            except Exception:
                return False

        pin_discovery(discovery, pin_hash, connected)
        pin_connection(connections.UnityConnection, pin_hash)
        state = "ok"
    except Exception as error:  # a server laid out differently: it runs as it would without claude-hand
        state = f"failed ({type(error).__name__}: {error})"
        say(f"this MCP for Unity server is not kept to its editor ({pin_hash}): {state}")
    if os.environ.get("CLAUDE_HAND_UNITY_PIN_CHECK"):  # `claude_hand.py doctor` asks, and the server does not start
        print(f"claude-hand unity pin {state}")
        sys.stdout.flush()
        os._exit(0 if state == "ok" else 1)


main()
