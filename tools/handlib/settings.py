"""The small JSON files claude-hand keeps or edits: reading and writing them, and the permission rules it adds to
this folder's .claude/settings.local.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def read_json_file(path: Path) -> dict:
    """{} for a missing file; refuses to touch a file that is not valid JSON."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError as exc:
        raise SystemExit(f"{path} is not valid JSON ({exc}); fix or move it, then run again.")
    if not isinstance(data, dict):
        raise SystemExit(f"{path} does not hold a JSON object.")
    return data


AGENT_ALLOW_RE = re.compile(r"^mcp__(unity|blender)_[a-z0-9_]+__\*$")


def plan_tool_allow(root: Path, server_keys: list[str], prune: bool = False) -> dict[Path, dict]:
    """sync --allow-tools: let the supervisor's subagents use their Unity/Blender tools without a
    prompt each time (rules live in this folder's settings.local.json, so only the supervisor gets them)."""
    path = root / ".claude" / "settings.local.json"
    settings = read_json_file(path)
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    wanted = [f"mcp__{key}__*" for key in server_keys]
    new = [rule for rule in allow if not (prune and AGENT_ALLOW_RE.match(rule) and rule not in wanted)]
    new += [rule for rule in wanted if rule not in new]
    if new == allow:
        return {}
    settings["permissions"]["allow"] = new
    return {path: settings}


def permission_path(path: str) -> str:
    """An absolute path as permission rules spell it: //abs/path, and on Windows C:/x becomes //c/x."""
    path = path.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", path):
        return f"//{path[0].lower()}{path[2:]}"
    return f"/{path}" if path.startswith("/") else path


def plan_notes_allow(root: Path, notes_paths: list[str]) -> dict[Path, dict]:
    """Let the supervisor keep, and its subagents read, the windows' notes files without a prompt: those files
    sit in the user's project folders, outside this one, where Read and Edit otherwise ask every time."""
    path = root / ".claude" / "settings.local.json"
    settings = read_json_file(path)
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    new = [f"{tool}({permission_path(p)})" for p in notes_paths if p for tool in ("Read", "Edit")]
    new = [rule for rule in dict.fromkeys(new) if rule not in allow]
    if not new:
        return {}
    allow += new
    return {path: settings}


def write_json_files(plan: dict[Path, dict]) -> None:
    for path, data in plan.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
