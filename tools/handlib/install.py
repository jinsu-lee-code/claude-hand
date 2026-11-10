"""Setting up Unity and Blender from the command line: finding the programs, a Unity project's package manifest,
opening Blender on a port, where Blender add-ons go.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from handlib import constants, link


def tool_version(*cmd: str, which=shutil.which) -> str | None:
    """First output line of `<cmd> --version`-style calls, or None when the tool is missing."""
    exe = which(cmd[0])
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or proc.stderr).strip()
    return text.splitlines()[0].strip() if text else ""


def node_major(version: str | None) -> int | None:
    match = re.match(r"v?(\d+)", version or "")
    return int(match.group(1)) if match else None


def find_blender_executables(env=os.environ, platform=sys.platform, which=shutil.which) -> list[Path]:
    found: list[Path] = []
    if exe := which("blender"):
        found.append(Path(exe))
    if platform == "win32":
        for base in filter(None, (env.get("ProgramFiles"), env.get("ProgramFiles(x86)"))):
            found += sorted(Path(base, "Blender Foundation").glob("Blender*/blender.exe"), reverse=True)
            steam = Path(base, "Steam", "steamapps", "common", "Blender", "blender.exe")
            if steam.exists():
                found.append(steam)
    elif platform == "darwin":
        app = Path("/Applications/Blender.app/Contents/MacOS/Blender")
        if app.exists():
            found.append(app)
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def blender_config_base(env=os.environ, platform=sys.platform) -> Path | None:
    if platform == "win32":
        return Path(env["APPDATA"], "Blender Foundation", "Blender") if env.get("APPDATA") else None
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Blender"
    return Path.home() / ".config" / "blender"


def find_blender_addon_installs(base: Path | None = None) -> list[Path]:
    """MCP for Blender add-on files in every Blender version's user folder."""
    base = base if base is not None else blender_config_base()
    if not base or not base.is_dir():
        return []
    found = []
    for version_dir in sorted(base.iterdir(), reverse=True):
        if not re.match(r"^\d+\.\d+", version_dir.name):
            continue
        for folder in (version_dir / "scripts" / "addons", version_dir / "extensions" / "user_default"):
            candidate = folder / constants.BLENDER_ADDON_FILE
            if candidate.exists():
                found.append(candidate)
    return found


def read_project_version(project: Path) -> str | None:
    try:
        text = (project / "ProjectSettings" / "ProjectVersion.txt").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"m_EditorVersion:\s*(\S+)", text)
    return match.group(1) if match else None


def unity_hub_editor_dirs(env=os.environ, platform=sys.platform) -> list[Path]:
    dirs: list[Path] = []
    if platform == "win32":
        if env.get("ProgramFiles"):
            dirs.append(Path(env["ProgramFiles"], "Unity", "Hub", "Editor"))
        if env.get("APPDATA"):  # a custom install location chosen in Unity Hub
            try:
                custom = json.loads(Path(env["APPDATA"], "UnityHub", "secondaryInstallPath.json").read_text(encoding="utf-8"))
                if isinstance(custom, str) and custom:
                    dirs.insert(0, Path(custom))
            except (OSError, ValueError):
                pass
    elif platform == "darwin":
        dirs.append(Path("/Applications/Unity/Hub/Editor"))
    else:
        dirs.append(Path.home() / "Unity" / "Hub" / "Editor")
    return dirs


def find_unity_editor(version: str, dirs: list[Path] | None = None, platform=sys.platform) -> Path | None:
    for base in dirs if dirs is not None else unity_hub_editor_dirs():
        exe = base / version / ("Unity.app/Contents/MacOS/Unity" if platform == "darwin" else
                                "Editor/Unity.exe" if platform == "win32" else "Editor/Unity")
        if exe.exists():
            return exe
    return None


def is_unity_project(project: Path) -> bool:
    return (project / "Assets").is_dir() and (project / "ProjectSettings").is_dir()


@dataclass
class ManifestPlan:
    project: str
    changes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    backup: str = ""


def follows_a_branch(git_url: str) -> bool:
    """A git package address that names no release or commit ("...git#main", or no "#" at all): Unity then takes
    whatever is newest whenever the project's lock file is made anew."""
    if not re.match(r"(https?|git|ssh)://|git@", git_url):
        return False
    ref = git_url.partition("#")[2]
    return not re.fullmatch(r"v?\d+(\.\d+)+\S*|[0-9a-f]{7,40}", ref)


def find_git(env=os.environ, platform=sys.platform, which=shutil.which) -> str | None:
    """git, also where the Windows installer puts it when it is not on this process's PATH."""
    found = which("git")
    if found or platform != "win32":
        return found
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        candidate = Path(env.get(var) or "") / ("Programs/Git/cmd/git.exe" if var == "LOCALAPPDATA" else "Git/cmd/git.exe")
        if env.get(var) and candidate.is_file():
            return str(candidate)
    return None


def released_checkout(root: Path = constants.REPO_ROOT, run=subprocess.run, git=None) -> bool:
    """This checkout is exactly the release its VERSION names (git tag v<VERSION>). Projects can then take Claude
    Hand Tools from GitHub at that tag; from any other checkout that address would give them different code."""
    git = git if git is not None else find_git()
    if not git:
        return False
    try:
        proc = run([git, "-C", str(root), "describe", "--exact-match", "--tags", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == f"v{constants.VERSION}"


def plan_unity_install(project: Path, mcp_for_unity: bool = True, claude_hand: bool = True,
                       package_dir: Path = constants.CLAUDE_HAND_PACKAGE_DIR, pin_mcp_for_unity: bool = False,
                       source: str = "auto", released=released_checkout) -> tuple[ManifestPlan, dict | None]:
    """What unity-install would change, plus the new manifest (None when nothing changes).
    pin_mcp_for_unity: also move an MCP for Unity that follows a branch to the release claude-hand was tested with.
    source: where Claude Hand Tools comes from. "release": GitHub at this version's tag, which opens on any PC.
    "folder": this checkout by its path, for working on claude-hand itself; the project's manifest then names a
    folder of this PC. "auto": release when this checkout is that release, else folder."""
    plan = ManifestPlan(project=str(project))
    if not is_unity_project(project):
        raise SystemExit(f"{project} is not a Unity project (needs Assets/ and ProjectSettings/).")
    manifest_path = project / "Packages" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Could not read {manifest_path}: {exc}")
    deps = manifest.setdefault("dependencies", {})
    if mcp_for_unity:
        current = deps.get(constants.MCP_FOR_UNITY_PACKAGE)
        if current and pin_mcp_for_unity and follows_a_branch(current):
            deps[constants.MCP_FOR_UNITY_PACKAGE] = constants.MCP_FOR_UNITY_GIT_URL
            plan.changes.append(f"change {constants.MCP_FOR_UNITY_PACKAGE}: {current} -> {constants.MCP_FOR_UNITY_GIT_URL}")
        elif current:
            plan.notes.append(f"MCP for Unity is already installed ({current})." + (
                f" It follows a branch; `unity-install --pin-mcp-for-unity` fixes it to {constants.MCP_FOR_UNITY_TESTED}, the release "
                "claude-hand was tested with." if follows_a_branch(current) else ""))
        elif (project / "Assets" / "MCPForUnity").is_dir():
            plan.notes.append("MCP for Unity is already installed in Assets/MCPForUnity (Asset Store copy).")
        else:
            deps[constants.MCP_FOR_UNITY_PACKAGE] = constants.MCP_FOR_UNITY_GIT_URL
            plan.changes.append(f"add {constants.MCP_FOR_UNITY_PACKAGE}: {constants.MCP_FOR_UNITY_GIT_URL} (Unity fetches it with git)")
    if claude_hand:
        from_release = source == "release" or (source == "auto" and released())
        wanted = constants.CLAUDE_HAND_PACKAGE_GIT_URL if from_release else "file:" + package_dir.resolve().as_posix()
        current = deps.get(constants.CLAUDE_HAND_PACKAGE)
        if current == wanted:
            plan.notes.append(f"Claude Hand Tools is already installed ({'release ' + constants.VERSION if from_release else 'from this folder'}).")
        else:
            deps[constants.CLAUDE_HAND_PACKAGE] = wanted
            plan.changes.append(f"{'change' if current else 'add'} {constants.CLAUDE_HAND_PACKAGE}: {wanted}")
        if not from_release:
            plan.notes.append("Claude Hand Tools comes from this folder, so the project's manifest names a path on this PC and "
                              "the project will not open elsewhere. For a project you share, install from a released "
                              f"checkout (git tag v{constants.VERSION}) or with --release once that tag is on GitHub.")
    return plan, (manifest if plan.changes else None)


def apply_unity_install(project: Path, manifest: dict, plan: ManifestPlan) -> ManifestPlan:
    manifest_path = project / "Packages" / "manifest.json"
    backup = manifest_path.with_name("manifest.json.claude-hand.bak")
    shutil.copy2(manifest_path, backup)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    plan.backup = str(backup)
    return plan


def blender_port_expr(port: int) -> str:
    """Runs inside Blender before its event loop starts, so the add-on's auto-start sees the new port."""
    return (
        "import bpy\n"
        "s = bpy.context.scene\n"
        "if hasattr(s, 'blendermcp_port'):\n"
        f"    s.blendermcp_port = {int(port)}\n"
        "    s.blendermcp_auto_start_server = True\n"
        f"    print('claude-hand: MCP for Blender will listen on port {int(port)}')\n"
        "else:\n"
        "    print('claude-hand: the MCP for Blender add-on is not enabled; run: uvx mcp-for-blender setup')\n"
    )


def blender_open_command(exe: Path, blend_file: Path | None, port: int) -> list[str]:
    cmd = [str(exe)]
    if blend_file is not None:
        cmd.append(str(blend_file))
    return cmd + ["--python-expr", blender_port_expr(port)]


def launch_detached(cmd: list[str]) -> None:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)  # noqa: S603 - fixed argv, no shell


def blender_answers(port: int) -> bool:
    try:
        pong = link.blender_request(port, {"type": "ping", "params": {}}, 2.0)
    except OSError:
        return False
    return isinstance(pong, dict) and pong.get("status") == "success"


def blender_chat_targets(base: Path | None = None) -> list[Path]:
    """Add-on folders to install the chat add-on into: every Blender version that has MCP for Blender,
    else the newest Blender version folder."""
    with_mcp = [p.parent for p in find_blender_addon_installs(base)]
    if with_mcp:
        return list(dict.fromkeys(with_mcp))
    base = base if base is not None else blender_config_base()
    if not base or not base.is_dir():
        return []
    versions = sorted((d for d in base.iterdir() if re.match(r"^\d+\.\d+", d.name)),
                      key=lambda d: [int(x) for x in re.findall(r"\d+", d.name)[:2]], reverse=True)
    return [versions[0] / "scripts" / "addons"] if versions else []


def find_chat_addon_installs(base: Path | None = None) -> list[Path]:
    base = base if base is not None else blender_config_base()
    if not base or not base.is_dir():
        return []
    return sorted(p for p in base.glob(f"*/scripts/addons/{constants.CHAT_ADDON_MODULE}.py"))


ENABLE_CHAT_ADDON = (
    "import addon_utils, bpy\n"
    f"mod = addon_utils.enable('{constants.CHAT_ADDON_MODULE}', default_set=True)\n"
    "if mod is not None:\n"
    "    bpy.ops.wm.save_userpref()\n"
    "print('CLAUDE_HAND_CHAT ' + ('enabled' if mod is not None else 'failed'))\n"
)
