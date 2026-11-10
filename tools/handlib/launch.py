"""Starting the supervisor session: the user's config.json, the conversation to resume, the extra `claude`
arguments, Remote Control, the shortcuts.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from handlib import constants, link, settings


def write_user_config(home: Path | None = None, root: Path = constants.REPO_ROOT, start_script: Path = constants.START_SCRIPT) -> Path:
    """~/.claude-hand/config.json: where this claude-hand lives, so the Unity and Blender panels can
    start the supervisor with one button."""
    home = home or link.claude_hand_home()
    path = home / "config.json"
    data = settings.read_json_file(path)
    wanted = {**data, "repo": root.as_posix(), "start_script": start_script.as_posix()}
    wanted.pop("mode", None)  # "mode desktop" from an earlier version: there is one way to use Claude Hand now
    if wanted != data:
        settings.write_json_files({path: wanted})
    return path


def read_user_config(home: Path | None = None) -> dict:
    return settings.read_json_file((home or link.claude_hand_home()) / "config.json")


EXCHANGE_KEEP_DAYS = 30
EXCHANGE_PATTERNS = ("*.unitypackage", "*.blend", "*.blend1", "*.fbx", "*.claudehand.json", "capture-*.png")


def exchange_keep_days(config: dict) -> int:
    """config.json "exchange_keep_days": how long transfer files stay in the exchange folder (0: never cleaned)."""
    value = config.get("exchange_keep_days", EXCHANGE_KEEP_DAYS)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else EXCHANGE_KEEP_DAYS


def old_exchange_files(days: int, folder: Path | None = None, now: float | None = None) -> list[Path]:
    """Transfer files in the exchange folder older than `days`. They are copies: what was sent is in the source
    project or .blend, and what was received was copied into the target. Only the kinds claude-hand writes there, and
    only files directly in the folder, so a folder the user shares with other things is left alone otherwise."""
    folder = folder or link.exchange_dir()
    if days <= 0 or not folder.is_dir():
        return []
    cutoff = (now or time.time()) - days * 86400
    found = []
    for pattern in EXCHANGE_PATTERNS:
        for path in folder.glob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    found.append(path)
            except OSError:
                continue
    return sorted(set(found))


def clean_exchange(days: int, folder: Path | None = None, now: float | None = None, dry_run: bool = False) -> tuple[int, int]:
    """Delete them. (files, bytes) deleted, or that would be with dry_run."""
    count = size = 0
    for path in old_exchange_files(days, folder, now):
        try:
            length = path.stat().st_size
            if not dry_run:
                path.unlink()
        except OSError:
            continue
        count, size = count + 1, size + length
    return count, size


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def remote_control_wanted(config: dict) -> bool:
    """Remote Control is on unless the user turned it off (`remote-control off`)."""
    return config.get("remote_control", True) is not False


def persistent_env_names(platform=sys.platform) -> set[str]:
    """Names in the user's or the machine's saved environment (Windows): set on purpose, so kept."""
    if platform != "win32":
        return set()
    import winreg
    names = set()
    for root, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(root, path) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    names.add(winreg.EnumValue(key, index)[0].upper())
        except OSError:
            continue
    return names


def inherited_session_env(env=os.environ, persistent: set[str] | None = None) -> list[str]:
    """Claude Code session variables this process inherited (none when started from Explorer or a plain shell)."""
    persistent = persistent_env_names() if persistent is None else persistent
    return sorted(name for name in env
                  if (name.upper() in constants.SESSION_ENV_NAMES or name.upper().startswith(constants.SESSION_ENV_PREFIXES))
                  and not name.upper().startswith(constants.USER_ENV_PREFIXES) and name.upper() not in persistent)


def claude_config_dir(env=os.environ) -> Path:
    return Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def session_transcript(session_id: str, config_dir: Path | None = None) -> Path | None:
    """The transcript Claude Code keeps for `session_id`, or None if that conversation never got a message."""
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", session_id or ""):
        return None
    return next(iter(((config_dir or claude_config_dir()) / "projects").glob(f"*/{session_id}.jsonl")), None)


def latest_supervisor_session(root: Path = constants.REPO_ROOT, config_dir: Path | None = None) -> str | None:
    """The newest earlier Claude Hand conversation started in this folder (Remote Control names it), to carry on."""
    folder = (config_dir or claude_config_dir()) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))
    marker = f'"customTitle":"{constants.REMOTE_CONTROL_NAME}"'
    paths = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
    for path in paths:
        try:
            with path.open(encoding="utf-8", errors="replace") as transcript:
                if marker in transcript.read(65536):
                    return path.stem
        except OSError:
            continue
    return None


def supervisor_session(config: dict, transcript=session_transcript, latest=latest_supervisor_session,
                       new_id=lambda: str(uuid.uuid4())) -> tuple[list[str], str, bool]:
    """(claude arguments, session id, resumed?): every start opens the same supervisor conversation, so the work
    and the Claude desktop app's "Claude Hand" carry on across restarts and days; Claude Code compacts it as it
    grows. A first start with this version carries on from the newest earlier one. `new-session` starts afresh."""
    session = config.get("supervisor_session") or ""
    if session and transcript(session):
        return ["--resume", session], session, True
    if not session:
        earlier = latest()
        if earlier:
            return ["--resume", earlier], earlier, True
        session = new_id()
    return ["--session-id", session], session, False  # chosen but never used yet (no message, so no transcript)


def launch_args(config: dict, env=os.environ) -> str:
    """Extra arguments for the supervisor's `claude` command, one line, as start-supervisor pastes it.
    Remote Control is left out when this login cannot use it (it would only print an error at start)."""
    if remote_control_wanted(config) and not remote_control_problems(env):
        # Remote Control: the session (and the subagents it runs) shows up in the Claude desktop app's Code tab,
        # claude.ai/code and the mobile app under this name.
        return f'--remote-control --name "{constants.REMOTE_CONTROL_NAME}"'
    return ""


def remote_control_problems(env=os.environ) -> list[str]:
    """Documented reasons Remote Control cannot work for this login."""
    problems = []
    provider = [name for name in constants.THIRD_PARTY_PROVIDER_ENV if env.get(name, "").lower() in ("1", "true")]
    if provider:
        problems.append("Bedrock/Vertex/Foundry(" + ", ".join(provider) + ")에서는 Remote Control을 쓸 수 없습니다")
    base_url = env.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    if base_url and base_url != "https://api.anthropic.com":  # the desktop app sets the official address itself
        problems.append("ANTHROPIC_BASE_URL이 다른 서버를 가리키고 있으면 Remote Control을 쓸 수 없습니다")
    if env.get("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY가 설정돼 있습니다. Remote Control은 API 키로는 안 되고 claude.ai 로그인(Pro·Max·Team 등)이 필요합니다")
    return problems


BANNER = """\
────────────────────────────────────────────────────────
 Claude Hand 감독 세션
   Unity   : Window > Claude Hand > Chat
   Blender : 3D 뷰포트에서 N > Claude Hand 탭
 이 창은 닫지 마세요. 닫으면 채팅이 멈춥니다. 일을 시작하면 저절로 최소화됩니다.
 켤 때마다 개발용 채널(claude-hand-chat) 경고가 뜹니다: I am using this for local development 를 고르세요.
────────────────────────────────────────────────────────"""


FIRST_RUN_GUIDE = """\
처음에는 Claude Code가 몇 가지를 확인합니다. 화살표 키로 고르고 Enter를 누르세요.
  - 이 폴더(claude-hand)를 믿는지: 믿는다(Yes)를 고르세요.
  - 개발용 채널(claude-hand-chat) 경고(켤 때마다 뜸): I am using this for local development 를 고르세요.
  - (뜨면) MCP 서버 claude-hand-chat 사용: 사용하는 쪽을 고르세요.
  - 로그인을 묻으면 화면 안내대로 로그인하세요.
  - 데스크톱 앱에서 보려면 구독(Pro·Max 등) 계정 로그인이 필요합니다. /status 로 확인하고, 아니면 /login 하세요."""


SHORTCUT_PS = r"""
$ws = New-Object -ComObject WScript.Shell
$folders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
if ($env:CH_STARTUP -eq '1') { $folders += [Environment]::GetFolderPath('Startup') }
foreach ($folder in $folders) {
  $path = Join-Path $folder ($env:CH_NAME + '.lnk')
  if ($env:CH_REMOVE -eq '1') { if (Test-Path $path) { Remove-Item $path; Write-Output "removed $path" }; continue }
  $lnk = $ws.CreateShortcut($path)
  $lnk.TargetPath = $env:CH_TARGET
  $lnk.WorkingDirectory = $env:CH_DIR
  $lnk.Description = 'Unity, Blender chat panels: start the Claude Hand supervisor'
  $lnk.Save()
  Write-Output "created $path"
}
"""


def shortcut_files(platform=sys.platform, home: Path | None = None) -> dict[Path, str]:
    """Non-Windows launchers: {path: contents}. Windows uses .lnk files made through PowerShell."""
    home = home or Path.home()
    script = constants.START_SCRIPT.as_posix()
    if platform == "darwin":
        return {home / "Desktop" / f"{constants.SHORTCUT_NAME}.command": f"#!/bin/sh\nexec '{script}'\n"}
    return {home / "Desktop" / "claude-hand.desktop": (
        "[Desktop Entry]\nType=Application\nName=Claude Hand\n"
        f"Exec={script}\nTerminal=true\nComment=Start the Claude Hand supervisor\n")}


def open_start_window(script: Path = constants.START_SCRIPT, platform=sys.platform, popen=subprocess.Popen) -> list[str]:
    """Open start-supervisor in a new, visible terminal window (the first-start questions are answered there)."""
    if platform == "win32":
        cmd = ["cmd.exe", "/c", "start", "Claude Hand", str(script)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    elif platform == "darwin":
        cmd, flags = ["open", "-a", "Terminal", str(script)], 0
    else:
        cmd, flags = ["x-terminal-emulator", "-e", str(script)], 0
    popen(cmd, cwd=str(constants.REPO_ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          creationflags=flags)
    return cmd
