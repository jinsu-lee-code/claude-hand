"""Finding what is open: Unity editors (MCP for Unity's status files or HTTP server, and Claude Hand Tools' own
bridge registry), Blender windows (MCP for Blender's port), and how the user's Claude Code starts those MCP
servers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from handlib import constants, link


@dataclass
class Instance:
    """One running Unity editor as MCP for Unity identifies it: Name@hash."""

    name: str
    hash: str
    path: str = ""
    port: int | None = None
    unity_version: str = ""
    alive: bool = True
    source: str = ""

    @property
    def id(self) -> str:
        return f"{self.name}@{self.hash}"


@dataclass
class BlenderInstance:
    """One Blender window running the MCP for Blender add-on, identified by its socket port."""

    port: int
    filepath: str = ""
    blender_version: str = ""
    alive: bool = True
    source: str = "probe"

    @property
    def name(self) -> str:
        stem = re.split(r"[/\\]", self.filepath)[-1] if self.filepath else ""
        stem = stem[:-6] if stem.lower().endswith(".blend") else stem
        return stem or f"untitled-{self.port}"

    @property
    def id(self) -> str:
        return f"{self.name}@{self.port}"


@dataclass
class ServerConfig:
    """How the user's existing Unity MCP entry starts the server; copied into each subagent."""

    transport: str  # "stdio" | "http"
    command: str = "uvx"
    args: list[str] = field(default_factory=lambda: list(constants.DEFAULT_STDIO_ARGS))
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    key: str = "unityMCP"
    origin: str = "default"
    warning: str = ""


def status_dir() -> Path:
    env = os.environ.get("UNITY_MCP_STATUS_DIR")
    return Path(env) if env else Path.home() / ".unity-mcp"


def project_name_from_assets_path(project_path: str) -> str:
    """Same rule as MCP for Unity: '.../MyGame/Assets' -> 'MyGame'."""
    path = project_path.rstrip("/\\")
    if path.endswith("Assets"):
        path = path[: -len("Assets")].rstrip("/\\")
    name = re.split(r"[/\\]", path)[-1] if path else ""
    return name or "Unknown"


def probe_port(port: int, timeout: float = 0.4) -> bool:
    """MCP for Unity's listener greets every connection with a handshake containing FRAMING=1."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout) as s:
            s.settimeout(timeout)
            return b"FRAMING=1" in s.recv(512)
    except OSError:
        return False


def discover_stdio(directory: Path | None = None, probe=probe_port) -> list[Instance]:
    """Read ~/.unity-mcp/unity-mcp-status-<hash>.json files written by the Unity side (stdio bridge)."""
    directory = directory or status_dir()
    by_port: dict[object, tuple[Instance, float]] = {}
    entries = []
    for file in sorted(directory.glob("unity-mcp-status-*.json")):
        try:
            entries.append((file, json.loads(file.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    ports = sorted({d.get("unity_port") for _, d in entries if isinstance(d.get("unity_port"), int)})
    with ThreadPoolExecutor(max_workers=max(1, min(16, len(ports)))) as pool:  # stale files cost a timeout each
        answering = dict(zip(ports, pool.map(probe, ports)))
    for file, data in entries:
        project_path = data.get("project_path") or ""
        port = data.get("unity_port")
        inst = Instance(
            name=project_name_from_assets_path(project_path),
            hash=file.stem.replace("unity-mcp-status-", ""),
            path=project_path[: -len("/Assets")] if project_path.endswith(("/Assets", "\\Assets")) else project_path,
            port=port if isinstance(port, int) else None,
            unity_version=data.get("unity_version") or "",
            alive=bool(isinstance(port, int) and answering.get(port)) or bool(data.get("reloading")),
            source=str(file),
        )
        mtime = file.stat().st_mtime
        key = inst.port if inst.port is not None else inst.hash
        if key not in by_port or by_port[key][1] < mtime:
            by_port[key] = (inst, mtime)
    return [entry[0] for entry in sorted(by_port.values(), key=lambda e: e[0].name.lower())]


def http_base(url: str) -> str:
    """'http://localhost:8080/mcp' -> 'http://localhost:8080'."""
    match = re.match(r"^(https?://[^/]+)", url.strip())
    return match.group(1) if match else url.rstrip("/")


def discover_http(url: str, fetch=None) -> list[Instance]:
    """Ask a running MCP for Unity HTTP server which editors are connected (GET /api/instances)."""
    fetch = fetch or _fetch_json
    try:
        payload = fetch(http_base(url) + "/api/instances")
    except (OSError, ValueError, urllib.error.URLError):
        return []
    instances = []
    for item in payload.get("instances", []) if isinstance(payload, dict) else []:
        project, hash_ = item.get("project"), item.get("hash")
        if project and hash_:
            instances.append(Instance(name=project, hash=hash_, unity_version=item.get("unity_version") or "", source="http"))
    return sorted(instances, key=lambda i: i.name.lower())


def _fetch_json(url: str):
    # "localhost" can try ::1 first and wait on Windows; the MCP for Unity server listens on IPv4.
    url = re.sub(r"^(https?://)localhost\b", r"\g<1>127.0.0.1", url)
    with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - localhost only
        return json.loads(response.read().decode("utf-8"))


def parse_ports(spec: str) -> list[int]:
    """'9876-9879,9900' -> [9876, 9877, 9878, 9879, 9900]."""
    ports: list[int] = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        low, sep, high = part.partition("-")
        values = range(int(low), int(high) + 1) if sep else [int(low)]
        ports += [p for p in values if 1024 <= p <= 65535 and p not in ports]
    return ports


def discover_blender(ports: list[int], request=link.blender_request) -> list[BlenderInstance]:
    """Ping the ports in parallel; for every MCP for Blender add-on that answers, read its .blend path (read-only)."""
    def probe(port):
        try:
            pong = request(port, {"type": "ping", "params": {}}, 2.0)
        except OSError:
            return None  # nothing listening
        if not (isinstance(pong, dict) and pong.get("status") == "success"):
            return None  # something else owns the port
        return describe(port)

    def describe(port):
        inst = BlenderInstance(port=port)
        try:
            info = request(port, {"type": "get_addon_info", "params": {}}, 3.0)
            inst.blender_version = str((info or {}).get("result", {}).get("blender_version", ""))
        except (OSError, AttributeError):
            pass
        try:
            code = "import bpy\nprint(bpy.data.filepath)"
            res = request(port, {"type": "execute_code", "params": {"code": code}}, 5.0)
            inst.filepath = str((res or {}).get("result", {}).get("result", "")).strip()
        except (OSError, AttributeError):
            pass
        return inst

    if not ports:
        return []
    with ThreadPoolExecutor(max_workers=min(16, len(ports))) as pool:
        return [inst for inst in pool.map(probe, ports) if inst is not None]


def candidate_config_files(cwd: Path | None = None) -> list[Path]:
    cwd = cwd or Path.cwd()
    home = Path.home()
    files = [cwd / ".mcp.json", home / ".claude.json"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        files.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    files.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    files.append(home / ".config" / "Claude" / "claude_desktop_config.json")
    return files


def _server_maps(data: dict) -> list[dict]:
    maps = []
    if isinstance(data.get("mcpServers"), dict):
        maps.append(data["mcpServers"])
    for project in (data.get("projects") or {}).values():  # ~/.claude.json keeps per-project servers here
        if isinstance(project, dict) and isinstance(project.get("mcpServers"), dict):
            maps.append(project["mcpServers"])
    return maps


def is_unity_ai_relay(entry: dict) -> bool:
    """Unity's own AI Assistant server (~/.unity/relay/relay_<os> --mcp, often registered as "unity-mcp").
    It is not MCP for Unity: it takes no --default-instance and has none of MCP for Unity's tools."""
    command = str(entry.get("command", "")).replace("\\", "/").lower()
    return "/.unity/relay/" in command or command.rsplit("/", 1)[-1].startswith("relay_")


def looks_like_unity_server(key: str, entry: dict) -> bool:
    if not isinstance(entry, dict) or is_unity_ai_relay(entry):
        return False
    blob = " ".join([key, str(entry.get("command", "")), " ".join(map(str, entry.get("args", []) or [])), str(entry.get("url", ""))]).lower()
    return "unity" in key.lower() or any(h in blob for h in constants.UNITY_PACKAGE_HINTS)


def detect_server_config(files: list[Path] | None = None) -> ServerConfig:
    """Find the user's Unity MCP entry so every subagent starts the server the same way."""
    for file in files if files is not None else candidate_config_files():
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for servers in _server_maps(data):
            # Prefer the canonical key, then anything that looks like MCP for Unity.
            for key in sorted(servers, key=lambda k: (k != "unityMCP", k)):
                entry = servers[key]
                if not looks_like_unity_server(key, entry):
                    continue
                if entry.get("url") or entry.get("type") == "http":
                    return ServerConfig(transport="http", url=entry.get("url", "http://localhost:8080/mcp"), key=key, origin=str(file))
                if entry.get("command"):
                    return ServerConfig(
                        transport="stdio",
                        command=entry["command"],
                        args=[str(a) for a in entry.get("args", [])],
                        env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
                        key=key,
                        origin=str(file),
                    )
    return ServerConfig(transport="stdio")


def _entry_blob(key: str, entry: dict) -> str:
    return " ".join([key, str(entry.get("command", "")), " ".join(map(str, entry.get("args", []) or []))]).lower()


def looks_like_blender_server(key: str, entry: dict) -> bool:
    return isinstance(entry, dict) and ("blender" in key.lower() or any(h in _entry_blob(key, entry) for h in constants.BLENDER_PACKAGE_HINTS))


def detect_blender_config(files: list[Path] | None = None) -> ServerConfig:
    """Find the user's MCP for Blender entry. Anything that is not the GitHub add-on's server
    (for example the official Blender Lab server) is not wired up yet, so fall back to the default."""
    for file in files if files is not None else candidate_config_files():
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for servers in _server_maps(data):
            for key in sorted(servers):
                entry = servers[key]
                if not looks_like_blender_server(key, entry) or not entry.get("command"):
                    continue
                if not any(h in _entry_blob(key, entry) for h in constants.BLENDER_PACKAGE_HINTS):
                    return ServerConfig(
                        transport="stdio", command="uvx", args=list(constants.DEFAULT_BLENDER_ARGS), key=key, origin="default",
                        warning=f"'{key}' ({file}) is not MCP for Blender (GitHub); subagents use 'uvx mcp-for-blender' instead.",
                    )
                return ServerConfig(
                    transport="stdio",
                    command=entry["command"],
                    args=[str(a) for a in entry.get("args", [])],
                    env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
                    key=key,
                    origin=str(file),
                )
    return ServerConfig(transport="stdio", command="uvx", args=list(constants.DEFAULT_BLENDER_ARGS), key="blender")


def discover_bridge(directory: Path | None = None, alive_fn=None) -> list[Instance]:
    """Unity editors whose Claude Hand Tools registered in ~/.claude-hand/unity (ChBridge). The hash is the one
    MCP for Unity uses (first 8 hex of SHA1 of the Assets path), so the ids line up with its instances."""
    import unity_bridge  # same folder
    alive_fn = alive_fn or unity_bridge.alive
    entries = unity_bridge.load_registry(directory)
    with ThreadPoolExecutor(max_workers=max(1, min(16, len(entries)))) as pool:
        answering = list(pool.map(alive_fn, entries))
    return [Instance(name=e.get("name", ""), hash=e.get("hash", ""), path=e.get("project_path", ""),
                     unity_version=e.get("unity_version", ""), alive=ok, source="bridge")
            for e, ok in zip(entries, answering) if e.get("name") and e.get("hash")]


def merge_instances(primary: list[Instance], extra: list[Instance]) -> list[Instance]:
    """Add editors only the bridge knows about (e.g. MCP for Unity on HTTP, where no status files exist)."""
    merged = list(primary)
    for inst in extra:
        same = next((p for p in merged if p.name == inst.name and (p.hash.startswith(inst.hash) or inst.hash.startswith(p.hash))), None)
        if same is None:
            merged.append(inst)
        elif not same.path:
            same.path = inst.path
    return merged


def gather_instances(server: ServerConfig, extra: list[str], include_dead: bool) -> list[Instance]:
    if server.transport == "http":
        found = discover_http(server.url) or [i for i in discover_stdio() if i.alive]
    else:
        found = discover_stdio()
    try:
        found = merge_instances(found, [i for i in discover_bridge() if i.alive])
    except Exception:  # the bridge is extra information; never let it break discovery
        pass
    if not include_dead:
        found = [i for i in found if i.alive]
    known = {i.id for i in found}
    for raw in extra:
        name, sep, hash_ = raw.partition("@")
        if not sep or not name or not hash_:
            raise SystemExit(f"--instance needs Name@hash, got {raw!r}")
        if raw not in known:
            found.append(Instance(name=name, hash=hash_, source="--instance"))
    return found


def gather_blender(args: argparse.Namespace) -> list[BlenderInstance]:
    if getattr(args, "no_blender", False):
        return []
    return discover_blender(parse_ports(args.blender_ports))
