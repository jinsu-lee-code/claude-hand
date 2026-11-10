"""One subagent per window: the definition files `sync` writes into ~/.claude/agents (unity-<project>.md,
blender-<file>.md), the prompts in them, and the supervisor skill.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from handlib import constants, discovery, settings


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "project")[:40].rstrip("-")


def agent_names(instances: list[discovery.Instance]) -> dict[str, str]:
    """instance id -> subagent name; projects that share a folder name get a hash suffix."""
    counts: dict[str, int] = {}
    for inst in instances:
        counts[slug(inst.name)] = counts.get(slug(inst.name), 0) + 1
    return {
        inst.id: f"unity-{slug(inst.name)}" + (f"-{inst.hash[:4]}" if counts[slug(inst.name)] > 1 else "")
        for inst in instances
    }


def server_key(agent_name: str) -> str:
    """MCP server key inside the subagent; tools then appear as mcp__<key>__<tool>."""
    return agent_name.replace("-", "_")


def pinned_args(args: list[str], pin: str) -> list[str]:
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--default-instance":
            skip = True
            continue
        if a.startswith("--default-instance="):
            continue
        out.append(a)
    return out + ["--default-instance", pin]


def q(value: str) -> str:
    """A JSON string is a valid YAML double-quoted scalar."""
    return json.dumps(value, ensure_ascii=False)


def resolved_runner(runner: str, which=shutil.which) -> str:
    """The runner with its program as a full path, so the hook does not depend on the PATH of the shell Claude Code
    runs hooks in (on Windows that is Git Bash, which need not have uv's folder). A hook that cannot start does not
    stop the call (see tools/guard.py). A path with spaces stays the bare name: no quoting works in both Git Bash and
    PowerShell."""
    head, sep, rest = runner.partition(" ")
    found = which(head) if head and not re.search(r"[/\\]", head) else None
    if found and " " not in found:
        head = Path(found).as_posix()
    return head + sep + rest


def hook_command(runner: str, pin: str, key: str, transport: str, app: str = "unity") -> str:
    """Works in Git Bash (Claude Code's default hook shell on Windows) and in PowerShell."""
    return (f'{resolved_runner(runner)} "{constants.GUARD_SCRIPT.as_posix()}" --pin "{pin}" --server {key} '
            f'--transport {transport} --app {app}')


def hook_block(command: str) -> list[str]:
    return [
        "hooks:",
        "  PreToolUse:",
        '    - matcher: "mcp__.*"',
        "      hooks:",
        "        - type: command",
        "          command: " + q(command),
    ]


def render_agent(inst: discovery.Instance, name: str, server: discovery.ServerConfig, runner: str, allowlist: bool, focus_nudge: bool) -> str:
    key = server_key(name)
    lines = [
        "---",
        f"name: {name}",
        "description: " + q(
            f"Unity 프로젝트 {inst.name} 전담 작업자 ({inst.id}). 이 프로젝트의 씬·오브젝트·에셋을 보거나 고칠 때, "
            f"또는 다른 Unity 프로젝트와 오브젝트를 주고받을 때 맡긴다. 이 프로젝트 말고는 건드리지 못한다."
        ),
    ]
    bridge_key = f"{key}_ch"
    if allowlist:
        lines.append("tools: " + q(f"mcp__{key}__*, mcp__{bridge_key}__*, Read, Glob, Grep"))
    else:
        lines.append("disallowedTools: " + q(f"mcp__{server.key}__*"))
    lines += ["mcpServers:", f"  - {key}:"]
    if server.transport == "http":
        lines += ["      type: http", f"      url: {q(server.url)}"]
    else:
        env = dict(server.env)
        if not focus_nudge:
            env.setdefault("UNITY_MCP_DISABLE_FOCUS_NUDGE", "1")
        lines += [
            "      type: stdio",
            f"      command: {q(server.command)}",
            "      args: " + json.dumps(pinned_args(server.args, inst.id), ensure_ascii=False),
        ]
        if env:
            lines.append("      env:")
            lines += [f"        {k}: {q(v)}" for k, v in sorted(env.items())]
    # claude-hand's own server for the ch_* tools (talks to ChBridge in the editor; does not depend on
    # MCP for Unity exposing custom tools).
    runner_parts = runner.split()
    bridge_args = [*runner_parts[1:], constants.UNITY_BRIDGE_SCRIPT.as_posix(), "--hash", inst.hash, "--project-name", inst.name]
    lines += [f"  - {bridge_key}:", "      type: stdio", f"      command: {q(runner_parts[0])}",
              "      args: " + json.dumps(bridge_args, ensure_ascii=False)]
    lines += hook_block(hook_command(runner, inst.id, key, server.transport, "unity"))
    lines += ["---", constants.GENERATED_MARKER, "", render_agent_prompt(inst, key, server.transport)]
    return "\n".join(lines) + "\n"


def unity_notes_path(project_path: str) -> str:
    """Where a Unity project's lasting rules live (next to Assets/, so Unity does not import it)."""
    root = project_path.rstrip("/\\")
    return f"{root}/ClaudeHand/NOTES.md" if root else ""


def blender_notes_path(filepath: str) -> str:
    """Where a .blend file's lasting rules live: <name>.claude-hand.md beside it."""
    return Path(filepath).with_suffix(".claude-hand.md").as_posix() if filepath else ""


def prompt_template(name: str) -> str:
    """A subagent prompt as it is written in prompts/<name>.md."""
    return (constants.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def fill(template: str, flags: dict[str, bool] | None = None, **values: str) -> str:
    """`template` with every {{NAME}} replaced by values["NAME"]. A line that starts with {{?FLAG}} is kept (without
    the mark) only when flags["FLAG"] is true."""
    lines = []
    for line in template.split("\n"):
        mark = re.match(r"\{\{\?(\w+)\}\}", line)
        if mark:
            if not (flags or {}).get(mark.group(1)):
                continue
            line = line[mark.end():]
        lines.append(line)
    return re.sub(r"\{\{(\w+)\}\}", lambda found: values[found.group(1)], "\n".join(lines))


def notes_section(path: str, what: str) -> str:
    if not path:
        return ""
    return "\n" + fill(prompt_template("notes-section"), WHAT=what, PATH=path)


def render_agent_prompt(inst: discovery.Instance, key: str, transport: str) -> str:
    return fill(prompt_template("unity-subagent"), flags={"HTTP": transport == "http"},
                NAME=inst.name, LOCATION=f" (경로: `{inst.path}`)" if inst.path else "", PIN=inst.id, KEY=key,
                NOTES=notes_section(unity_notes_path(inst.path), "프로젝트"))


def blender_agent_names(instances: list[discovery.BlenderInstance]) -> dict[str, str]:
    """instance id -> subagent name; files with the same name get the port appended."""
    counts: dict[str, int] = {}
    for inst in instances:
        counts[slug(inst.name)] = counts.get(slug(inst.name), 0) + 1
    return {
        inst.id: f"blender-{slug(inst.name)}" + (f"-{inst.port}" if counts[slug(inst.name)] > 1 else "")
        for inst in instances
    }


def with_port(args: list[str], port: int) -> list[str]:
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--port":
            skip = True
            continue
        if a.startswith("--port="):
            continue
        out.append(a)
    return out + ["--port", str(port)]


def render_blender_agent(inst: discovery.BlenderInstance, name: str, server: discovery.ServerConfig, runner: str, allowlist: bool) -> str:
    key = server_key(name)
    shown = inst.filepath or "(저장 안 된 파일)"
    lines = [
        "---",
        f"name: {name}",
        "description: " + q(
            f"Blender 창 전담 작업자: {inst.name} ({shown}, MCP for Blender 포트 {inst.port}). 이 Blender의 오브젝트·머티리얼·씬을 "
            f"보거나 고칠 때, 또는 다른 Blender나 Unity로 모델을 보내고 받을 때 맡긴다. 다른 Blender 창은 건드리지 못한다."
        ),
        "tools: " + q(f"mcp__{key}__*, Read, Glob, Grep") if allowlist else "disallowedTools: " + q(f"mcp__{server.key}__*"),
        "mcpServers:",
        f"  - {key}:",
        "      type: stdio",
        f"      command: {q(server.command)}",
        "      args: " + json.dumps(with_port(server.args, inst.port), ensure_ascii=False),
        "      env:",
    ]
    env = dict(server.env)
    env["BLENDER_PORT"] = str(inst.port)
    lines += [f"        {k}: {q(v)}" for k, v in sorted(env.items())]
    lines += hook_block(hook_command(runner, inst.id, key, "stdio", "blender"))
    lines += ["---", constants.GENERATED_MARKER, "", render_blender_prompt(inst, key)]
    return "\n".join(lines) + "\n"


def render_blender_prompt(inst: discovery.BlenderInstance, key: str) -> str:
    return fill(prompt_template("blender-subagent"), NAME=inst.name, FILE=inst.filepath or "저장 안 된 파일",
                PORT=str(inst.port), KEY=key, NOTES=notes_section(blender_notes_path(inst.filepath), "파일"))


@dataclass
class SyncResult:
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)


def build_agent_files(unity: list[discovery.Instance], unity_server: discovery.ServerConfig | None,
                      blender: list[discovery.BlenderInstance], blender_server: discovery.ServerConfig | None,
                      runner: str, allowlist: bool = True, focus_nudge: bool = False) -> dict[str, str]:
    """file name -> contents for every subagent to write."""
    files: dict[str, str] = {}
    if unity:
        names = agent_names(unity)
        for inst in unity:
            files[f"{names[inst.id]}.md"] = render_agent(inst, names[inst.id], unity_server or discovery.ServerConfig(transport="stdio"),
                                                         runner, allowlist, focus_nudge)
    if blender:
        names = blender_agent_names(blender)
        server = blender_server or discovery.ServerConfig(transport="stdio", command="uvx", args=list(constants.DEFAULT_BLENDER_ARGS), key="blender")
        for inst in blender:
            files[f"{names[inst.id]}.md"] = render_blender_agent(inst, names[inst.id], server, runner, allowlist)
    return files


def write_agent_files(files: dict[str, str], agents_dir: Path, prune: bool = False, dry_run: bool = False) -> SyncResult:
    result = SyncResult()
    for file_name in files:
        path = agents_dir / file_name
        if path.exists() and constants.GENERATED_MARKER not in path.read_text(encoding="utf-8"):
            raise SystemExit(f"{path} exists and was not written by claude-hand; rename it or remove it first.")
    if not dry_run:
        agents_dir.mkdir(parents=True, exist_ok=True)
    for file_name, text in files.items():
        path = agents_dir / file_name
        if path.exists() and path.read_text(encoding="utf-8") == text:
            result.unchanged.append(path)
            continue
        if not dry_run:
            path.write_text(text, encoding="utf-8", newline="\n")
        result.written.append(path)
    if prune and agents_dir.exists():
        for pattern in ("unity-*.md", "blender-*.md"):
            for path in agents_dir.glob(pattern):
                if path.name not in files and constants.GENERATED_MARKER in path.read_text(encoding="utf-8"):
                    if not dry_run:
                        path.unlink()
                    result.removed.append(path)
    return result


def sync_agents(instances: list[discovery.Instance], agents_dir: Path, server: discovery.ServerConfig, runner: str,
                allowlist: bool = True, focus_nudge: bool = False, prune: bool = False, dry_run: bool = False,
                blender: list[discovery.BlenderInstance] | None = None, blender_server: discovery.ServerConfig | None = None,
                notes_root: Path | None = None) -> SyncResult:
    """notes_root: also pre-allow each window's notes file in that folder's settings.local.json."""
    files = build_agent_files(instances, server, blender or [], blender_server, runner, allowlist, focus_nudge)
    if notes_root is not None and not dry_run:
        paths = [unity_notes_path(i.path) for i in instances] + [blender_notes_path(b.filepath) for b in blender or []]
        settings.write_json_files(settings.plan_notes_allow(notes_root, paths))
    return write_agent_files(files, agents_dir, prune=prune, dry_run=dry_run)


def rendered_skill(root: Path = constants.REPO_ROOT) -> str:
    """The supervisor skill as installed: {{CLAUDE_HAND}} becomes this checkout's folder, so the skill can run
    claude_hand.py itself from any working folder (the Desktop app's Code tab has no start script)."""
    return constants.SKILL_SOURCE.read_text(encoding="utf-8").replace("{{CLAUDE_HAND}}", root.as_posix())


def install_skill(skills_dir: Path, dry_run: bool = False) -> Path:
    target = skills_dir / "claude-hand" / "SKILL.md"
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered_skill(), encoding="utf-8", newline="\n")
    return target


def refresh_agents(agents_dir: Path | None = None, runner: str = "uv run -q --no-project",
                   blender_ports: str = constants.DEFAULT_BLENDER_PORTS) -> SyncResult:
    """Make or refresh a subagent for every Unity/Blender window open right now; keep the others.
    Used by preflight and by the chat channel when a panel's window has no subagent yet."""
    agents_dir = agents_dir or Path.home() / ".claude" / "agents"
    server = discovery.detect_server_config()
    unity = discovery.gather_instances(server, [], include_dead=False)
    blender = discovery.discover_blender(discovery.parse_ports(blender_ports))
    if not unity and not blender:
        return SyncResult()
    return sync_agents(unity, agents_dir, server, runner, blender=blender,
                       blender_server=discovery.detect_blender_config() if blender else None, notes_root=constants.REPO_ROOT)


def parse_generated_agent(text: str) -> discovery.BlenderInstance | discovery.Instance | None:
    """Rebuild the instance a generated subagent file was made for (to re-render it after an update)."""
    pin = re.search(r'--pin \\"(.+?)\\"', text)
    app = re.search(r"--app (unity|blender)\b", text)
    if not pin:
        return None
    name, _, tail = pin.group(1).rpartition("@")
    if (app.group(1) if app else "unity") == "blender":
        path = re.search(r"파일 `([^`]*)`, MCP for Blender 포트", text)
        filepath = path.group(1) if path and path.group(1) != "저장 안 된 파일" else ""
        return discovery.BlenderInstance(port=int(tail), filepath=filepath) if tail.isdigit() else None
    path = re.search(r"\(경로: `([^`]*)`\)", text)
    return discovery.Instance(name=name, hash=tail, path=path.group(1) if path else "")
