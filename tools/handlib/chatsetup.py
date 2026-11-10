"""The chat channel's registration with Claude Code (.mcp.json, the reply tool's permission) and working out why
a panel does or does not reach the supervisor.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from handlib import constants, discovery, link, settings


def direct_app_servers(files: list[Path] | None = None) -> list[str]:
    """MCP servers in the user's Claude config that operate Unity or Blender directly: MCP for Unity, Unity AI
    Assistant's relay, MCP for Blender. The supervisor must not use them (it would skip its subagents and their
    guard); the subagents run their own pinned copies under other names, so denying these does not touch them."""
    keys: set[str] = set()
    for file in files if files is not None else discovery.candidate_config_files():
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for servers in discovery._server_maps(data):
            for key, entry in servers.items():
                if key == constants.CHAT_SERVER_NAME or not isinstance(entry, dict):
                    continue
                if discovery.looks_like_unity_server(key, entry) or discovery.is_unity_ai_relay(entry) or discovery.looks_like_blender_server(key, entry):
                    keys.add(key)
    return sorted(keys)


def plan_chat_config(root: Path = constants.REPO_ROOT, runner: str = "uv run -q --no-project",
                     direct_servers: list[str] | None = None) -> dict[Path, dict]:
    """Files chat-setup writes on this machine, as {path: new contents}; only files that change.
    .mcp.json registers the channel for this folder (the channels docs' way). settings.local.json
    pre-allows the reply tool, so answering a panel never stops for a permission prompt. Both stay
    out of git: they hold this machine's paths and this user's permission choices."""
    plan: dict[Path, dict] = {}
    mcp_path = root / ".mcp.json"
    mcp = settings.read_json_file(mcp_path)
    parts = runner.split()
    entry = {
        "command": parts[0],
        "args": [*parts[1:], (root / "tools" / "chat_channel.py").as_posix()],
        # Passed on explicitly: Claude Code is not documented to hand its whole environment to MCP servers.
        "env": {"CLAUDE_HAND_SUPERVISOR": "${CLAUDE_HAND_SUPERVISOR:-0}"},
    }
    if mcp.get("mcpServers", {}).get(constants.CHAT_SERVER_NAME) != entry:
        mcp.setdefault("mcpServers", {})[constants.CHAT_SERVER_NAME] = entry
        plan[mcp_path] = mcp
    settings_path = root / ".claude" / "settings.local.json"
    local = settings.read_json_file(settings_path)
    changed = False
    allow = local.setdefault("permissions", {}).setdefault("allow", [])
    for rule in (constants.CHAT_REPLY_RULE, constants.CHAT_CONTEXT_RULE):
        if rule not in allow:
            allow.append(rule)
            changed = True
    # Approves this folder's .mcp.json entry up front so the first start asks one question fewer.
    # Claude Code applies approvals from settings.local.json only once the folder is trusted.
    enabled = local.setdefault("enabledMcpjsonServers", [])
    if constants.CHAT_SERVER_NAME not in enabled:
        enabled.append(constants.CHAT_SERVER_NAME)
        changed = True
    # The supervisor works through its subagents only: no direct Unity/Blender tools in this folder's sessions.
    deny = local["permissions"].setdefault("deny", [])
    for key in direct_servers if direct_servers is not None else direct_app_servers():
        rule = f"mcp__{key}__*"
        if rule not in deny:
            deny.append(rule)
            changed = True
    if changed:
        plan[settings_path] = local
    return plan


def prewarm_chat_channel(root: Path = constants.REPO_ROOT, runner: str = "uv run -q --no-project", run=subprocess.run) -> dict:
    """Start the channel once the way Claude Code will, with --check: uv fetches Python now (not inside
    Claude Code's 30-second MCP startup window) and we learn whether the port and home folder work."""
    parts = runner.split()
    exe = shutil.which(parts[0]) or parts[0]
    try:
        proc = run([exe, *parts[1:], str(root / "tools" / "chat_channel.py"), "--check"],
                   capture_output=True, text=True, errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"could not run {parts[0]}: {exc}"}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {"ok": False, "error": (proc.stderr or proc.stdout or "no output").strip()[-500:]}


def parse_claude_version(text: str | None) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in match.groups()) if match else None


def chat_add_command(runner: str = "uv run -q --no-project") -> list[str]:
    """Fallback for chat-setup --register-local: the same server via `claude mcp add` (local scope)
    instead of .mcp.json, in case a Claude Code version does not load .mcp.json servers as channels."""
    return ["claude", "mcp", "add", "--scope", "local", constants.CHAT_SERVER_NAME, "--", *runner.split(), str(constants.CHAT_CHANNEL_SCRIPT)]


@dataclass
class McpEntry:
    exists: bool
    scope: str = ""        # "local" / "project" / "user" as `claude mcp get` reports it
    pending: bool = False  # .mcp.json server waiting for the user's approval
    raw: str = ""


def claude_mcp_get(name: str = constants.CHAT_SERVER_NAME, cwd: Path = constants.REPO_ROOT, run=subprocess.run, which=shutil.which) -> McpEntry | None:
    """What Claude Code itself resolves for this folder (None when the claude CLI is missing)."""
    exe = which("claude")
    if not exe:
        return None
    try:
        proc = run([exe, "mcp", "get", name], cwd=str(cwd), capture_output=True, text=True, errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or "No MCP server named" in out:
        return McpEntry(False, raw=out)
    scope = ""
    for line in out.splitlines():
        if line.strip().lower().startswith("scope:"):
            text = line.lower()
            scope = next((s for s in ("local", "project", "user") if s in text), text.split(":", 1)[1].strip())
            break
    return McpEntry(True, scope=scope, pending="pending approval" in out.lower(), raw=out)


def chat_server_registered(run=subprocess.run, which=shutil.which) -> bool | None:
    entry = claude_mcp_get(run=run, which=which)
    return None if entry is None else entry.exists


def chat_status_report(home: Path | None = None, mcp_entry=claude_mcp_get, fetch=None, root: Path = constants.REPO_ROOT) -> list[str]:
    """Plain-language diagnosis of why a panel does or does not reach the supervisor."""
    lines: list[str] = []
    server, status = link.read_chat_files(home)
    entry = mcp_entry()
    config = settings.read_json_file(root / ".mcp.json").get("mcpServers", {}).get(constants.CHAT_SERVER_NAME)
    if config is None:
        lines.append("!! 이 폴더의 .mcp.json에 claude-hand-chat이 없습니다 → uv run tools/claude_hand.py chat-setup")
    elif "CLAUDE_HAND_SUPERVISOR" not in (config.get("env") or {}):
        lines.append("!! .mcp.json이 이전 버전 형식입니다 → uv run tools/claude_hand.py chat-setup 으로 고치세요")
    if entry is not None and entry.exists and entry.scope == "local":
        lines.append("!! 이전 버전이 local 범위로 등록한 claude-hand-chat이 남아 있어 .mcp.json보다 먼저 쓰입니다 "
                     "→ uv run tools/claude_hand.py update (또는 claude mcp remove claude-hand-chat -s local)")
    if entry is not None and entry.pending:
        lines.append("!! .mcp.json의 claude-hand-chat이 승인 대기 중입니다 → start-supervisor.cmd로 연 Claude Code에서 승인하세요 "
                     "(거절했었다면 claude mcp reset-project-choices 후 다시)")
    if server:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{server['port']}/v1/status",
                                         headers={"X-Claude-Hand-Token": server.get("token", "")})
            state = (fetch or (lambda r: json.loads(urllib.request.urlopen(r, timeout=3).read().decode("utf-8"))))(req)
        except (OSError, ValueError, KeyError) as exc:
            lines.append(f"!! server.json은 있지만 채널 서버가 응답하지 않습니다({exc}). 감독 세션이 비정상 종료됐을 수 있습니다 "
                         "→ start-supervisor.cmd로 다시 여세요")
        else:
            if state.get("claude_connected"):
                lines.append(f"OK 감독 세션이 채팅을 받고 있습니다 (포트 {server['port']}, 채팅 {', '.join(state.get('chats', [])) or '아직 없음'})")
            else:
                lines.append("!! 채널 서버는 떴지만 Claude Code와의 연결이 아직 끝나지 않았습니다. 잠시 뒤 다시 확인하세요")
    elif status and status.get("mode") == "passive":
        lines.append(f"!! 채널 서버가 대기 모드로 떴습니다: {status.get('reason')}")
        lines.append("   → 감독 세션은 claude-hand 폴더의 start-supervisor.cmd로 여세요. 데스크톱 앱이나 그냥 연 claude는 채팅을 받지 않습니다")
    elif status and status.get("mode") == "stopped":
        lines.append("!! 감독 세션이 종료됐습니다 → start-supervisor.cmd로 다시 여세요")
    else:
        lines.append("!! 채널 서버가 아직 한 번도 시작되지 않았습니다")
        lines.append("   → start-supervisor.cmd로 연 Claude Code에서 /mcp 를 입력해 claude-hand-chat 상태를 보세요 "
                     "(⏸ 승인 대기면 승인, failed면 uv가 PATH에 있는지와 chat-setup 점검 결과를 확인)")
    if os.environ.get("CLAUDE_HAND_HOME"):
        lines.append(f"-- CLAUDE_HAND_HOME={os.environ['CLAUDE_HAND_HOME']} 가 설정돼 있습니다. Unity·Blender도 같은 값을 써야 합니다")
    return lines
