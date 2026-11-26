"""doctor (is it installed?) and selftest (does it run?).

selftest is the checks done by hand so far that need no eyes on the screen: the test suites, and whether the
open Unity and Blender windows, the chat channel and the subagents' hooks actually answer with this checkout's
version. It changes nothing in them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from handlib import agents, chatsetup, constants, discovery, install, keeps, launch, link, prefs, usage
from handlib.lang import N_, T

# The Unity self-test writes over this file directly, the way MCP for Unity writes a script: Unity is told nothing
# beforehand, so only the copy the guard hook makes can bring it back (ChSelfTest.FileChecks has the same two names).
SELFTEST_REQUEST = "selftest-files"
SELFTEST_DIRECT = "Assets/ClaudeHand/SelfTest/ChSelfTestDirect.txt"


@dataclass
class Check:
    area: str
    item: str
    ok: bool | None  # None = not applicable / informational
    detail: str = ""
    fix: str = ""


def unity_server_starts(cfg, run=subprocess.run, contained=launch.inside_app_container) -> tuple[bool, str, str]:
    """Whether the MCP for Unity server comes up at all: (ok, what was seen, what to do). Each Unity subagent starts
    one for itself with the registered command; when that cannot start, the subagent is left with the ch_* tools
    only and nothing says why (a supervisor spent five minutes finding out, 2026-11-24). Asked with --help, which
    sets the server's environment up as a real start does and talks to no Unity."""
    command = [cfg.command, *[a for a in cfg.args if a != "--help"], "--help"]
    shown = " ".join(command)
    try:
        done = run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, T("실행하지 못함: {exc}", exc=exc), T("터미널에서 직접 실행해 오류를 보세요: {shown}", shown=shown)
    if done.returncode == 0:
        return True, T("뜸"), ""
    seen = ((done.stderr or done.stdout or "").strip().splitlines() or [T("오류 내용 없음")])[-1].strip()[:200]
    if contained():
        return False, seen, (T("이 세션은 Claude 데스크톱 앱이 띄운 것이라 uv가 실행 환경을 새로 만들지 못합니다(앱이 AppData를 따로 보여 줍니다). 시작 메뉴에서 연 터미널에서 한 번 실행하세요: "
                               "{shown}  (한 번 만들어지면 여기서도 됩니다)",
                               shown=shown))
    return False, seen, T("터미널에서 직접 실행해 오류를 보세요: {shown}", shown=shown)


UNPINNED_ADVICE = N_("Unity 창을 둘 이상 열어 두면, 맡은 창이 잠깐 응답하지 않을 때(스크립트를 오래 다시 읽음 · 바쁨 · 닫힘) 하위 세션의 작업이 "
                      "다른 창으로 갈 수 있습니다. MCP for Unity 서버가 uvx로 등록돼 있는지 확인하세요: {command}"
                      "  그 전까지는 Unity 창을 하나만 열고 쓰세요.")


def unpinned_advice() -> str:
    return T(UNPINNED_ADVICE, command=constants.UNITY_MCP_ADD)


def unity_server_pinned(cfg, run=subprocess.run, environ=None) -> tuple[bool, str, str]:
    """Whether the server a Unity subagent starts is kept to that subagent's editor (tools/unity_pin): (ok, what was
    seen, what to do). Without that the server takes another open editor whenever its own does not answer for a
    moment, and the work lands in the wrong project. Started the way a subagent starts it, with a mark that makes the
    pin say whether it holds and end before the server itself runs: it talks to no Unity."""
    env = {**(os.environ if environ is None else environ), **agents.pinned_env(cfg.env, "check@00000000"),
           constants.UNITY_PIN_CHECK_ENV: "1", "UNITY_MCP_SKIP_STARTUP_CONNECT": "1"}
    try:
        done = run([cfg.command, *cfg.args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                   stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, T("확인하지 못함: {exc}", exc=exc), unpinned_advice()
    said = [line.strip() for line in (done.stdout or "").splitlines() if line.startswith("claude-hand unity pin")]
    if constants.UNITY_PIN_OK in said:
        return True, T("맡은 창만 봄"), ""
    return False, (said[0] if said else T("서버가 고정 장치를 읽지 않음"))[:200], unpinned_advice()


def doctor_checks(projects: list[Path], blender_ports: list[int], version=install.tool_version, progress=None,
                  server_starts=None, server_pinned=None) -> list[Check]:
    """server_starts: unity_server_starts when the MCP for Unity server is to be started for real (the doctor
    command); left out, that check is skipped. server_pinned: unity_server_pinned, asked once the server starts."""
    checks: list[Check] = []
    progress = progress or (lambda message: None)

    def add(area, item, ok, detail="", fix=""):
        checks.append(Check(area, item, ok, detail, fix))

    progress(T("프로그램 버전 확인 중..."))
    tools = {"claude": ("claude", "--version"), "uv": ("uv", "--version"), "git": ("git", "--version"),
             "node": ("node", "--version"), "agent-desktop": ("agent-desktop", "version")}
    with ThreadPoolExecutor(max_workers=len(tools)) as pool:  # each CLI start can take a second or two on Windows
        futures = {name: pool.submit(version, *cmd) for name, cmd in tools.items()}
        versions = {name: future.result() for name, future in futures.items()}
    claude = versions["claude"]
    add(T("기본 도구"), T("Claude Code CLI (claude 명령)"), claude is not None, claude or T("없음"),
        "" if claude else ("winget install Anthropic.ClaudeCode" if sys.platform == "win32" else "curl -fsSL https://claude.ai/install.sh | bash"))
    uv = versions["uv"]
    add(T("기본 도구"), "uv", uv is not None, uv or T("없음"), "" if uv else
        T("winget install --id astral-sh.uv -e  (winget이 없으면 https://docs.astral.sh/uv/getting-started/installation/ 안내대로)"))
    git = versions["git"]
    add(T("기본 도구"), T("git (Unity가 MCP for Unity를 받을 때 필요)"), git is not None, git or T("없음"),
        "" if git else (T("winget install --id Git.Git -e  (winget이 없으면 https://git-scm.com/download/win 에서 받아 설치)")
                        if sys.platform == "win32" else T("https://git-scm.com/downloads 에서 설치")))
    node = versions["node"]
    node_ok = (install.node_major(node) or 0) >= 18
    add(T("기본 도구"), T("Node.js 18 이상 (agent-desktop용)"), node_ok, node or T("없음"),
        "" if node_ok else T("winget install --id OpenJS.NodeJS.LTS -e  (winget이 없으면 https://nodejs.org 에서 LTS 설치)"))

    agent_desktop = versions["agent-desktop"]
    add("agent-desktop", "agent-desktop CLI", agent_desktop is not None, agent_desktop or T("없음"),
        "" if agent_desktop else "npm install -g agent-desktop")

    exes = install.find_blender_executables()
    add("Blender", T("Blender 실행 파일"), bool(exes), ", ".join(map(str, exes[:3])) or T("못 찾음"),
        "" if exes else T("Blender를 설치하거나 blender-open --blender-exe 로 경로를 알려주세요"))
    addons = install.find_blender_addon_installs()
    add("Blender", T("MCP for Blender 애드온"), bool(addons), ", ".join(map(str, addons[:3])) or T("설치 안 됨"),
        "" if addons else T("uvx mcp-for-blender setup --dry-run  (확인 후 --yes)"))
    blender_cfg = discovery.detect_blender_config()
    configured = blender_cfg.origin != "default"  # a warning means some other Blender MCP is configured, not ours
    add("Blender", T("Claude 설정에 MCP for Blender 등록"), configured,
        blender_cfg.origin if configured else (blender_cfg.warning or T("없음")),
        "" if configured else T("uvx mcp-for-blender setup --dry-run  (확인 후 --yes)"))
    progress(T("Blender 창 찾는 중..."))
    running = discovery.discover_blender(blender_ports)
    add("Blender", T("응답하는 Blender 창"), bool(running) if exes else None,
        ", ".join(f"{b.name}@{b.port}" for b in running) or T("없음"),
        "" if running else T("Blender를 열고 애드온 서버를 켜세요 (blender-open --port 9876)"))

    progress(T("Unity 에디터 찾는 중..."))
    unity_cfg = discovery.detect_server_config()
    unity_configured = unity_cfg.origin != "default"
    add("Unity", T("Claude 설정에 MCP for Unity 등록"), unity_configured,
        f"{unity_cfg.transport} ({unity_cfg.origin})" if unity_configured else T("없음"),
        "" if unity_configured else constants.UNITY_MCP_ADD)
    unity_running = discovery.gather_instances(unity_cfg, [], include_dead=False)
    add("Unity", T("연결된 Unity 에디터"), bool(unity_running) or None,
        ", ".join(i.id for i in unity_running) or T("없음"),
        "" if unity_running else T("Unity 프로젝트를 열고 Window > MCP for Unity 가 Connected인지 확인"))
    if server_starts is not None and unity_configured and unity_cfg.transport == "stdio" and versions["uv"] is not None:
        progress(T("MCP for Unity 서버가 뜨는지 확인 중..."))
        starts, seen, fix = server_starts(unity_cfg)
        add("Unity", T("MCP for Unity 서버 실행 (하위 세션이 띄우는 것)"), starts, seen, fix)
        if starts and server_pinned is not None:
            pinned, seen, fix = server_pinned(unity_cfg)
            add("Unity", T("MCP for Unity 서버가 맡은 창만 봄 (다른 Unity 창으로 새지 않음)"), pinned, seen, fix)
    try:
        via_bridge = [i for i in discovery.discover_bridge() if i.alive]
    except Exception:
        via_bridge = []
    via_mcp = [i for i in (discovery.discover_http(unity_cfg.url) if unity_cfg.transport == "http" else discovery.discover_stdio()) if i.alive]
    same = lambda a, b: a.name == b.name and (a.hash.startswith(b.hash) or b.hash.startswith(a.hash))
    only_bridge = [b for b in via_bridge if not any(same(b, m) for m in via_mcp)]
    only_mcp = [m for m in via_mcp if not any(same(m, b) for b in via_bridge)]
    if only_bridge:
        add("Unity", T("MCP for Unity 연결"), False, T("열려 있지만 MCP for Unity에 안 보임: ") + ", ".join(i.name for i in only_bridge),
            T("Unity의 Window > MCP for Unity에서 Transport를 Stdio로 하고 Connected인지 확인하세요. Claude Code 등록 방식({transport})"
              "이 그 창의 Transport와 달라도 이렇게 됩니다. 바꿨다면 그 창에서 Claude Code의 Configure를 "
              "다시 누르세요. 이 상태로도 ch_* 도구는 쓸 수 있지만 MCP for Unity 기본 도구는 하위 세션에서 안 됩니다", transport=unity_cfg.transport))
    if only_mcp:
        add("Unity", T("Claude Hand Tools 연결"), False, T("MCP for Unity에는 보이지만 Claude Hand Tools가 응답 안 함: ") +
            ", ".join(i.name for i in only_mcp),
            T("그 프로젝트에 uv run tools/claude_hand.py unity-install 을 했는지, Unity Console에 컴파일 오류가 없는지 확인하세요"))
    for project in projects:
        label = T("프로젝트 {name}", name=project.name)
        if not install.is_unity_project(project):
            add("Unity", label, False, T("{project}: Unity 프로젝트가 아님", project=project), T("경로를 확인하세요"))
            continue
        try:
            plan, _ = install.plan_unity_install(project)
        except SystemExit as exc:
            add("Unity", T("{label}: 패키지", label=label), False, str(exc), T("Packages/manifest.json을 확인하세요"))
            continue
        add("Unity", T("{label}: 패키지", label=label), not plan.changes, "; ".join(plan.changes or plan.notes),
            "" if not plan.changes else f'uv run tools/claude_hand.py unity-install "{project}"')
        editor_version = install.read_project_version(project)
        editor = install.find_unity_editor(editor_version) if editor_version else None
        add("Unity", T("{label}: 에디터 {version}", label=label, version=editor_version or '?'), editor is not None if editor_version else None,
            str(editor) if editor else T("Unity Hub 기본 위치에서 못 찾음"),
            "" if editor else T("Unity Hub에서 해당 버전을 설치하거나 Hub로 직접 여세요"))

    claude_v = chatsetup.parse_claude_version(claude)
    relay_ok = claude_v is not None and claude_v >= constants.MIN_CLAUDE_FOR_PERMISSION_RELAY
    add(T("채팅"), T("Claude Code 2.1.234 이상 (채팅창의 허용/거부 버튼)"), relay_ok if claude else None, claude or T("없음"),
        "" if relay_ok else "claude update")
    provider = [name for name in constants.THIRD_PARTY_PROVIDER_ENV if os.environ.get(name, "").lower() in ("1", "true")]
    add(T("채팅"), T("Channels를 쓸 수 있는 로그인 (claude.ai 또는 Console API 키)"), not provider,
        T("이 환경은 {names} 사용 중 - Channels를 쓸 수 없음", names=", ".join(provider)) if provider else T("Bedrock/Vertex/Foundry 아님"),
        "" if not provider else T("claude.ai 로그인이나 Console API 키로 Claude Code를 쓰세요"))
    try:
        chat_plan = chatsetup.plan_chat_config(constants.REPO_ROOT)
    except SystemExit as exc:
        chat_plan = {Path("?"): {"error": str(exc)}}
    add(T("채팅"), T("채팅 채널 등록과 답장 허용 (claude-hand 폴더)"), not chat_plan,
        T("됨") if not chat_plan else T("빠짐: ") + ", ".join(p.name for p in chat_plan),
        "" if not chat_plan else "uv run tools/claude_hand.py chat-setup")
    chat_addons = install.find_chat_addon_installs()
    add(T("채팅"), T("Blender 채팅 애드온"), bool(chat_addons) if exes else None, ", ".join(map(str, chat_addons[:3])) or T("설치 안 됨"),
        "" if chat_addons else "uv run tools/claude_hand.py blender-install-chat")

    agents_dir = Path.home() / ".claude" / "agents"
    generated = sorted(info.file.name for info in agents.installed_agents(agents_dir))
    add("claude-hand", T("하위 세션 정의 파일"), bool(generated) or None, ", ".join(generated) or T("없음"),
        "" if generated else "uv run tools/claude_hand.py sync")
    skill = Path.home() / ".claude" / "skills" / "claude-hand" / "SKILL.md"
    add("claude-hand", T("감독 스킬"), skill.exists(), str(skill) if skill.exists() else T("없음"),
        "" if skill.exists() else "uv run tools/claude_hand.py sync")
    if launch.remote_control_wanted(launch.read_user_config()):
        problems = launch.remote_control_problems()
        add(T("데스크톱 앱에서 보기"), T("Remote Control을 쓸 수 있는 로그인"), not problems, "; ".join(problems) or T("문제 없음"),
            "" if not problems else T("claude.ai 계정(Pro·Max·Team 등)으로 /login 하세요"))
    progress(T("이전 버전 흔적 확인 중..."))
    entry = chatsetup.claude_mcp_get() if claude else None
    if entry is not None and entry.exists and entry.scope == "local":
        add(T("갱신"), T("이전 버전의 local 범위 채팅 등록 (.mcp.json보다 먼저 쓰임)"), False, entry.scope, "uv run tools/claude_hand.py update")
    checks.extend(stale_checks(agents_dir, Path.home() / ".claude" / "skills"))
    return checks


def stale_checks(agents_dir: Path, skills_dir: Path) -> list[Check]:
    """Things an earlier claude-hand left behind that `update` refreshes."""
    checks = []
    installed = skills_dir / "claude-hand" / "SKILL.md"
    outdated = []
    # The files are in the language config.json names. CLAUDE_HAND_LANG only says how this one command speaks, so
    # what they should hold is worked out without it (as_configured).
    with prefs.as_configured():
        skill_differs = installed.exists() and installed.read_text(encoding="utf-8") != agents.rendered_skill()
        for info in agents.installed_agents(agents_dir):
            inst, path = info.instance(), info.file
            if inst is None:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            files = agents.build_agent_files([] if isinstance(inst, discovery.BlenderInstance) else [inst], discovery.detect_server_config(),
                                      [inst] if isinstance(inst, discovery.BlenderInstance) else [], discovery.detect_blender_config(), "uv run -q --no-project")
            if path.name in files and files[path.name] != text:
                outdated.append(path.name)
    if skill_differs:
        checks.append(Check(T("갱신"), T("설치된 감독 스킬이 저장소 버전과 다름"), False, str(installed), "uv run tools/claude_hand.py update"))
    if outdated:
        checks.append(Check(T("갱신"), T("이전 버전으로 만든 하위 세션 정의"), False, ", ".join(outdated), "uv run tools/claude_hand.py update"))
    for addon in install.find_chat_addon_installs():
        if addon.read_bytes() != constants.CHAT_ADDON_SOURCE.read_bytes():
            checks.append(Check(T("갱신"), T("설치된 Blender 채팅 애드온이 저장소 버전과 다름"), False, str(addon), "uv run tools/claude_hand.py update"))
    return checks


def print_checks(checks: list[Check]) -> int:
    """doctor / selftest output, grouped by area; returns how many failed."""
    area = None
    for c in checks:
        if c.area != area:
            area = c.area
            print(f"[{area}]")
        mark = "OK " if c.ok else ("-- " if c.ok is None else "!! ")
        print(f"  {mark}{c.item}: {c.detail}")
        if c.fix and not c.ok:
            print(T("       고치기: {fix}", fix=c.fix))
    return sum(c.ok is False for c in checks)


BLENDER_TESTS_EXPR = ("import sys, unittest; sys.path.insert(0, {tests!r}); import test_blender_chat_addon as t; "
                      "unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(t))")


BLENDER_ADDON_STATE_CODE = """import bpy, json, sys
module = sys.modules.get("claude_hand_chat")
print(json.dumps({"enabled": "claude_hand_chat" in bpy.context.preferences.addons,
                  "version": list(module.bl_info["version"]) if module else None}))"""


def repo_unity_tools(root: Path = constants.REPO_ROOT) -> list[str]:
    """The ch_* tools the Unity package in this checkout defines."""
    names = set()
    for source in (root / "unity" / "com.claudehand.tools" / "Editor").glob("*.cs"):
        names.update(re.findall(r'\[ChTool\("(ch_\w+)"', source.read_text(encoding="utf-8", errors="replace")))
    return sorted(names)


def addon_version(source: str) -> str:
    match = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', source)
    return ".".join(match.groups()) if match else ""


def unittest_summary(output: str) -> tuple[bool, str]:
    """(passed, "187개 통과 (건너뜀 24)") from unittest's output; on failure, the failing tests' names."""
    failed = re.findall(r"^(?:FAIL|ERROR): (\S+)", output, re.M)
    passed = bool(re.search(r"^OK\b", output, re.M)) and not failed
    if failed:
        return False, T("실패: ") + ", ".join(failed[:5]) + (T(" 외 {count}개", count=len(failed) - 5) if len(failed) > 5 else "")
    ran, skipped = re.search(r"^Ran (\d+) tests?", output, re.M), re.search(r"skipped=(\d+)", output)
    if not passed:
        return False, T("끝까지 돌지 못함: ") + (output.strip().splitlines() or [T("출력 없음")])[-1][:200]
    return True, T("{count}개 통과", count=ran.group(1) if ran else '?') + (T(" (건너뜀 {count})", count=skipped.group(1)) if skipped else "")


def hook_shell(env=os.environ, platform=sys.platform, which=shutil.which) -> list[str] | None:
    """The shell Claude Code runs hook commands in: sh, or Git Bash on Windows (None when it is not found).
    Not `bash` from PATH on Windows: that can be WSL's."""
    if platform != "win32":
        return ["sh", "-c"]
    candidates = [env.get("CLAUDE_CODE_GIT_BASH_PATH") or ""]
    git = install.find_git(env, platform, which)
    if git:  # .../Git/cmd/git.exe -> .../Git/bin/bash.exe
        candidates.append(str(Path(git).resolve().parent.parent / "bin" / "bash.exe"))
    candidates += [str(Path(env[var]) / tail) for var, tail in (("ProgramFiles", "Git/bin/bash.exe"),
                                                                ("ProgramFiles(x86)", "Git/bin/bash.exe"),
                                                                ("LOCALAPPDATA", "Programs/Git/bin/bash.exe")) if env.get(var)]
    found = next((c for c in candidates if c and Path(c).is_file()), None)
    return [found, "-c"] if found else None


HOOK_COMMAND_RE = re.compile(r'- type: command\n\s+command: (".*")')


HOOK_PROBE = {"tool_name": "mcp__claude_hand_selftest__probe", "tool_input": {}}  # no subagent's own server: every guard refuses it


def hook_checks(agents_dir: Path, run=subprocess.run, shell=None) -> list[Check]:
    """Run each generated subagent's guard hook the way Claude Code does, and see that it refuses a call that is not
    for its own window. A hook that cannot start (uv not on the hook shell's PATH, a moved checkout) does not stop any
    call and says nothing, so this is where it shows. The probe is refused before anything is reported or marked."""
    checks = []
    shell = hook_shell() if shell is None else shell
    fix = T("uv run tools/claude_hand.py update (그래도 안 되면 Git Bash에서 uv가 실행되는지 보세요)")
    for path in sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        found = HOOK_COMMAND_RE.search(text) if constants.GENERATED_MARKER in text else None
        if not found:
            continue
        item = T("{stem}: 안전장치 훅", stem=path.stem)
        if not shell:
            checks.append(Check(T("안전장치"), item, None, T("Git Bash를 찾지 못해 훅 명령을 돌려 보지 못함")))
            continue
        try:
            proc = run([*shell, json.loads(found.group(1))], input=json.dumps(HOOK_PROBE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60, env={**os.environ, "CLAUDE_HAND_LOG": "off"})  # a probe is no event
            decision = (json.loads(proc.stdout).get("hookSpecificOutput") or {}).get("permissionDecision") if proc.stdout.strip() else ""
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            checks.append(Check(T("안전장치"), item, False, T("훅 명령을 돌리지 못함: {exc}", exc=exc), fix))
            continue
        if proc.returncode == 0 and decision == "deny":
            checks.append(Check(T("안전장치"), item, True, T("다른 대상의 호출을 막음")))
        else:
            said = ((proc.stderr or proc.stdout).strip().splitlines() or [T("출력 없음")])[-1][:160]
            checks.append(Check(T("안전장치"), item, False, T("막지 못함 (종료 코드 {returncode}): {said}", returncode=proc.returncode, said=said), fix))
    return checks


# Run inside Blender, with no window: one object of a saved .blend, exported for Unity twice by the add-on as it is in
# this checkout. The second export has more geometry and another transfer name: Unity must still know it is the same
# source and replace its model in place.
MODEL_FIXTURES_EXPR = """import bpy, importlib.util, json, os
spec = importlib.util.spec_from_file_location("claude_hand_chat", {addon!r})
addon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon)
bpy.ops.wm.read_factory_settings(use_empty=True)
def mesh_of(quads):
    mesh = bpy.data.meshes.new("ChSelfTestMesh")
    verts = [v for q in range(quads) for v in ((q * 2, 0, 0), (q * 2 + 1, 0, 0), (q * 2 + 1, 1, 0), (q * 2, 1, 0))]
    mesh.from_pydata(verts, [], [tuple(range(q * 4, q * 4 + 4)) for q in range(quads)])
    mesh.materials.append(bpy.data.materials.get("ChSelfTestMat") or bpy.data.materials.new("ChSelfTestMat"))
    return mesh
obj = bpy.data.objects.new("ChSelfTest", mesh_of(1))
bpy.context.scene.collection.objects.link(obj)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join({folder!r}, "ch_selftest.blend"))
first = addon.export_fbx(["ChSelfTest"], "ch_selftest")
obj.data = mesh_of(2)
second = addon.export_fbx(["ChSelfTest"], "ch_selftest_again")
print("FIXTURES " + json.dumps([first["manifest"], second["manifest"]]))
"""


# Run inside Blender: take what Unity's ch_export_model wrote.
MODEL_TAKE_EXPR = """import bpy, importlib.util, json
spec = importlib.util.spec_from_file_location("claude_hand_chat", {addon!r})
addon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon)
bpy.ops.wm.read_factory_settings(use_empty=True)
print("TOOK " + json.dumps(addon.import_model({manifest!r})))
"""


# Run inside Blender, with no window, while a Unity without a window waits with its bridge open (ChSelfTest.Serve):
# the add-on as it is in this checkout sends an object with its panel's button, changes it and saves, then saves once
# more with nothing changed. Each time Unity is asked what it has. Timers do not run without a window, so the add-on
# is ticked by hand.
LIVE_ROUND_TRIP_EXPR = """import bpy, importlib.util, json, os, time
spec = importlib.util.spec_from_file_location("claude_hand_chat", {addon!r})
addon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon)
bpy.ops.wm.read_factory_settings(use_empty=True)
def mesh_of(quads):
    mesh = bpy.data.meshes.new("ChSelfTestLive")
    verts = [v for q in range(quads) for v in ((q * 2, 0, 0), (q * 2 + 1, 0, 0), (q * 2 + 1, 1, 0), (q * 2, 1, 0))]
    mesh.from_pydata(verts, [], [tuple(range(q * 4, q * 4 + 4)) for q in range(quads)])
    return mesh
obj = bpy.data.objects.new("ch_selftest_live", mesh_of(1))
bpy.context.scene.collection.objects.link(obj)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join({folder!r}, "ch_selftest_live.blend"))
addon.register()
idle = lambda: not addon._follow["busy"] and addon._follow["due"] is None and addon._inbox.empty()
def until(done, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        addon.tick()
        if done():
            return True
        time.sleep(0.1)
    return False
out = {{"editors": []}}
if until(lambda: bool(addon._follow["editors"]) and idle()):
    out["editors"] = addon._follow["editors"]
    here = lambda: [m for m in addon.unity_call(addon.unity_projects()[0], "ch_model_follow",
                                               {{"action": "list", "source": bpy.data.filepath}})["models"] if m["here"]]
    out["problem"] = addon.send_to_unity(["ch_selftest_live"], addon._follow["editors"][0][1])
    until(idle)
    out["after_send"] = here()
    obj.data = mesh_of(3)
    bpy.context.view_layer.update()
    bpy.ops.wm.save_mainfile()
    until(idle)
    out["after_save"] = here()
    bpy.ops.wm.save_mainfile()
    until(idle)
    out["after_same_save"] = here()
    out["notices"] = [e["text"] for e in addon._entries if e.get("kind") == "notice"]
addon.unregister()
print("LIVE " + json.dumps(out))
"""
LIVE_WAIT = 240  # seconds a Unity without a window may take to open the project and its bridge


def marked_json(output: str, mark: str):
    """What a script printed after `mark` on a line of its own (Blender prints much else), or None."""
    found = re.search(rf"^{mark} (.+)$", output or "", re.M)
    try:
        return json.loads(found.group(1)) if found else None
    except ValueError:
        return None


def prepare_file_checks(project: Path) -> bool:
    """What the guard hook does before a call that names a file, done with the hook's own code for the file the
    Unity self-test then writes over: Unity without a window runs no hook. True when the copy is made."""
    try:
        shutil.rmtree(keeps.request_folder(project, SELFTEST_REQUEST), ignore_errors=True)  # an earlier run's copy
        direct = project / SELFTEST_DIRECT
        direct.parent.mkdir(parents=True, exist_ok=True)
        direct.write_text("before", encoding="utf-8")
        return bool(keeps.keep(project, SELFTEST_REQUEST, keeps.named_files(project, {"path": SELFTEST_DIRECT})))
    except OSError:
        return False


def clean_file_checks(project: Path) -> None:
    """The self-test deletes its folder when it runs to the end. When it did not, what was put there goes here."""
    direct = project / SELFTEST_DIRECT
    for path in (direct, Path(str(direct) + ".meta")):
        path.unlink(missing_ok=True)
    for folder in (direct.parent, direct.parent.parent):
        try:
            folder.rmdir()  # only when nothing else is in it
        except OSError:
            break
        Path(str(folder) + ".meta").unlink(missing_ok=True)


def live_round_trip(project: Path, editor, blender_exe, folder: Path, run=subprocess.run, start=subprocess.Popen,
                    sleep=time.sleep, clock=time.time) -> list[tuple]:
    """The way a model takes in daily use once it follows its .blend, with both programs real and neither showing a
    window: Unity opens the project with its bridge on (ChSelfTest.Serve), and Blender's add-on sends it an object
    with the panel's button, changes the object and saves, then saves again with nothing changed. Blender talks to
    Unity's socket itself, as it does when the user saves. Both get a home folder of their own, so the list of the
    user's open editors is left alone. Returns (item, ok, detail) for each thing looked at."""
    home, stop, log = folder / "live-home", folder / "live-stop", folder / "live-unity.log"
    env = {**os.environ, "CLAUDE_HAND_HOME": str(home), "CLAUDE_HAND_EXCHANGE_DIR": str(folder / "live-exchange"),
           "CLAUDE_HAND_BRIDGE_IN_BATCH": "1", "CLAUDE_HAND_SELFTEST_STOP": str(stop),
           "CLAUDE_HAND_SELFTEST_OUT": str(folder / "live-result.json"),
           "CLAUDE_HAND_LANG": "ko"}  # what the two say to each other is read below by its Korean word
    last = lambda text: (str(text or "").strip().splitlines() or [T("출력 없음")])[-1][:200]
    unity = start([str(editor), "-batchmode", "-projectPath", str(project), "-executeMethod", "ClaudeHand.Editor.ChSelfTest.Serve",
                   "-logFile", str(log)], env=env)
    try:
        deadline = clock() + LIVE_WAIT
        registered = lambda: (home / "unity").is_dir() and any((home / "unity").glob("*.json"))
        while clock() < deadline and unity.poll() is None and not registered():
            sleep(0.5)
        if not registered():
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            errors = re.findall(r"^.*error CS\d+.*$", text, re.M)
            return [(T("한 바퀴: 창 없는 Unity가 통로를 엶"), False, errors[0][:300] if errors else T("통로 등록 파일이 생기지 않음: ") + last(text))]
        proc = run([str(blender_exe), "--background", "--factory-startup", "--python-expr",
                    LIVE_ROUND_TRIP_EXPR.format(addon=str(constants.CHAT_ADDON_SOURCE), folder=str(folder))], env=env,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        live = marked_json(proc.stdout, "LIVE")
        if not isinstance(live, dict) or not live.get("editors"):
            return [(T("한 바퀴: Blender가 열린 Unity를 찾아 보냄"), False,
                     T("Unity의 통로 등록 파일을 Blender가 읽지 못함") if isinstance(live, dict) else last(proc.stderr or proc.stdout))]
    finally:
        stop.write_text("stop", encoding="utf-8")  # Unity deletes what came in, and ends
        try:
            unity.wait(120)
        except subprocess.TimeoutExpired:
            unity.kill()
    one = lambda key: (live.get(key) or [{}])[0] if len(live.get(key) or []) == 1 else {}
    sent, saved, same = one("after_send"), one("after_save"), one("after_same_save")
    told = sum("반영" in str(notice) for notice in live.get("notices") or [])
    said = json.dumps(live, ensure_ascii=False)[:400]
    found = [
        (T("한 바퀴: Blender 패널에서 보낸 모델이 Unity에 들어와 씬에 놓이고, 저장을 따라가게 됨"),
         not live.get("problem") and sent.get("follow") is True and sent.get("instances") == 1),
        (T("한 바퀴: Blender에서 고쳐 저장하면 Unity의 모델이 제자리에서 바뀜"),
         bool(sent.get("file_hash")) and saved.get("asset_path") == sent.get("asset_path") and
         saved.get("file_hash") not in (None, sent.get("file_hash")) and told >= 1),
        (T("한 바퀴: 바뀐 것 없이 저장하면 Unity의 모델은 그대로"),
         bool(saved.get("file_hash")) and same.get("file_hash") == saved.get("file_hash") and told == 1),
    ]
    return [(item, bool(ok), "" if ok else said) for item, ok in found]


def unity_batch_checks(project: Path, run=subprocess.run, editor=None, blender_exe=None, progress=None, start=None) -> list[Check]:
    """Open a closed Unity project without a window and run Claude Hand Tools' own self-test in it (ChSelfTest.cs): a
    model comes in, comes in again and is replaced in place, takes the project's materials, and goes back out. With
    Blender installed the model is one its add-on really exported, what Unity sends back is read by Blender again,
    and at the end Unity is opened once more, its bridge on, for a Blender that sends it a model and saves
    (live_round_trip; start: how that Unity is started, subprocess.Popen unless `run` is a stand-in). The test works
    in a scene it never saves and deletes the assets it made."""
    area, checks = T("Unity 안 테스트 ({name})", name=project.name), []
    progress = progress or (lambda message: None)
    add = lambda item, ok, detail="", fix="": checks.append(Check(area, item, ok, detail, fix))
    if not install.is_unity_project(project):
        add(T("프로젝트"), False, T("{project}: Unity 프로젝트가 아님", project=project), T("경로를 확인하세요"))
        return checks
    if (project / "Temp" / "UnityLockfile").exists():
        add(T("프로젝트"), None, T("Unity로 열려 있어 건너뜀 (창 없이 한 번 더 열 수 없음). 닫고 다시 돌리면 확인"))
        return checks
    version = install.read_project_version(project)
    editor = editor or (install.find_unity_editor(version) if version else None)
    if editor is None:
        add(T("프로젝트"), None, T("Unity {version} 에디터를 Unity Hub 기본 위치에서 못 찾아 건너뜀", version=version or '?'))
        return checks
    if blender_exe is None:
        blender_exe = next(iter(install.find_blender_executables()), None)

    with tempfile.TemporaryDirectory(prefix="claude-hand-selftest-") as tmp:
        exchange, out, log = Path(tmp) / "exchange", Path(tmp) / "result.json", Path(tmp) / "unity.log"
        exchange.mkdir()
        env = {**os.environ, "CLAUDE_HAND_EXCHANGE_DIR": str(exchange), "CLAUDE_HAND_SELFTEST_OUT": str(out)}
        env.pop("CLAUDE_HAND_LANG", None)  # the self-test says itself which language each of its checks is in (ChText.Forced)
        in_blender = lambda expr: run([str(blender_exe), "--background", "--factory-startup", "--python-expr", expr], env=env,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if blender_exe:
            progress(T("Blender에서 시험용 모델 내보내는 중..."))
            proc = in_blender(MODEL_FIXTURES_EXPR.format(addon=str(constants.CHAT_ADDON_SOURCE), folder=tmp))
            fixtures = marked_json(proc.stdout, "FIXTURES")
            if fixtures:
                env.update(CLAUDE_HAND_SELFTEST_MODEL_A=fixtures[0], CLAUDE_HAND_SELFTEST_MODEL_B=fixtures[1])
            add(T("Blender가 보낸 모델(FBX)로 시험"), bool(fixtures) or None,
                T("export_fbx로 같은 오브젝트를 두 번 내보냄") if fixtures else T("내보내지 못해 OBJ 파일로 대신함: ") +
                ((proc.stderr or proc.stdout).strip().splitlines() or [T("출력 없음")])[-1][:200])
        prepare_file_checks(project)  # without it the self-test says that it skipped the file written over directly
        progress(T("Unity {version}를 창 없이 열어 시험하는 중 (1~2분)...", version=version))
        try:
            proc = run([str(editor), "-batchmode", "-projectPath", str(project), "-executeMethod", "ClaudeHand.Editor.ChSelfTest.Run",
                        "-logFile", str(log)], env=env, capture_output=True, text=True, errors="replace", timeout=900)
        except subprocess.TimeoutExpired:
            add(T("Unity 실행"), False, T("15분 안에 끝나지 않음"), T("Unity가 대화상자를 띄웠는지 작업 관리자에서 확인하세요"))
            return checks
        finally:
            clean_file_checks(project)
        try:
            result = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            errors = re.findall(r"^.*error CS\d+.*$", text, re.M)
            add(T("Unity 실행"), False, (errors[0][:300] if errors else T("결과 파일이 없음 (종료 코드 {returncode}): ", returncode=proc.returncode) +
                                    (text.strip().splitlines() or [T("로그 없음")])[-1][:200]),
                T("Unity로 프로젝트를 열어 Console의 컴파일 오류를 보세요") if errors else T("프로젝트에 Claude Hand Tools가 들어 있는지 보세요 (unity-install)"))
            return checks
        for check in result.get("checks", []):
            add(str(check.get("name")), bool(check.get("ok")), "" if check.get("ok") else str(check.get("detail", ""))[:400])
        sent = result.get("exported_manifest")
        if blender_exe and sent:
            progress(T("Unity가 보낸 모델을 Blender에서 받는 중..."))
            proc = in_blender(MODEL_TAKE_EXPR.format(addon=str(constants.CHAT_ADDON_SOURCE), manifest=sent))
            took = marked_json(proc.stdout, "TOOK")
            add(T("Unity가 보낸 모델을 Blender가 받음 (import_model)"), bool(took and took.get("added")),
                ", ".join(took["added"]) if took and took.get("added") else
                ((proc.stderr or proc.stdout).strip().splitlines() or [T("출력 없음")])[-1][:200])
        start = start or (subprocess.Popen if run is subprocess.run else None)
        if blender_exe and start:
            progress(T("창 없는 Unity에 통로를 열고, Blender가 모델을 보내고 저장하는 중 (30초쯤)..."))
            for item, ok, detail in live_round_trip(project, editor, blender_exe, Path(tmp), run, start):
                add(item, ok, detail)
    return checks


def token_record_check(transcript: Path | None = None, config: dict | None = None) -> Check:
    """Whether the supervisor session's transcript still reads the way handlib/usage.py takes it: the line that says
    what a request took has tokens only then. The format is Claude Code's own and written down nowhere, so a new
    Claude Code may change it; this is where that shows."""
    area, item = T("시간·토큰"), T("감독 세션 기록에서 토큰 읽기")
    if transcript is None:
        config = launch.read_user_config() if config is None else config
        transcript = launch.session_transcript(str(config.get("supervisor_session") or ""))
    if transcript is None:
        return Check(area, item, None, T("감독 세션 기록이 아직 없어 건너뜀 (Claude Hand를 한 번 쓴 뒤 다시 돌리면 확인)"))
    ledger = usage.Ledger(transcript)
    ledger.update()
    answers = sum(one["calls"] for who in ledger.requests.values() for one in who.values())
    if not answers:
        return Check(area, item, False, T("{name}: 토큰이 적힌 응답을 하나도 찾지 못함", name=transcript.name),
                     T("Claude Code의 기록 형식이 바뀐 것 같습니다. 채팅창의 '이 요청' 줄에는 시간만 나옵니다 (tools/handlib/usage.py)"))
    numbered = sum(bool(request) for request in ledger.requests)
    return Check(area, item, True, T("응답 {answers}개", answers=answers) + (T(", 요청 번호로 갈린 요청 {numbered}개", numbered=numbered) if numbered else
                                                         T(", 요청 번호가 붙은 것은 아직 없음")))


def selftest_checks(blender_ports: list[int], run_tests: bool = True, run=subprocess.run, progress=None,
                    registry=None, unity_request=None, blender=None, blenders=None, chat_lines=None,
                    supervisor_on=None, stale=None, hooks=None, unity_projects=(), unity_batch=None, tokens=None) -> list[Check]:
    checks: list[Check] = []
    progress = progress or (lambda message: None)
    add = lambda *a: checks.append(Check(*a))
    tests = constants.REPO_ROOT / "tools" / "tests"

    if run_tests:
        progress(T("Python 테스트 실행 중..."))
        proc = run([sys.executable, "-m", "unittest", "discover", "-s", str(tests)], cwd=str(constants.REPO_ROOT), capture_output=True,
                   text=True, encoding="utf-8", errors="replace", timeout=600)
        ok, detail = unittest_summary((proc.stdout or "") + (proc.stderr or ""))
        add(T("테스트"), T("Python 테스트"), ok, detail, "" if ok else "uv run --no-project python -m unittest discover -s tools/tests -v")
        exes = install.find_blender_executables()
        if exes:
            progress(T("Blender 안 애드온 테스트 실행 중..."))
            proc = run([str(exes[0]), "--background", "--factory-startup", "--python-expr", BLENDER_TESTS_EXPR.format(tests=str(tests))],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            ok, detail = unittest_summary((proc.stdout or "") + (proc.stderr or ""))
            add(T("테스트"), T("Blender 안 애드온 테스트 ({name})", name=exes[0].parent.name), ok, detail,
                "" if ok else T("selftest 출력의 실패한 테스트 이름으로 tools/tests/test_blender_chat_addon.py를 보세요"))
        else:
            add(T("테스트"), T("Blender 안 애드온 테스트"), None, T("Blender 실행 파일을 못 찾아 건너뜀"))

    for project in unity_projects:
        checks += (unity_batch or unity_batch_checks)(Path(project), progress=progress)

    progress(T("Unity 확인 중..."))
    import unity_bridge
    call = unity_request or unity_bridge.request
    wanted, seen = repo_unity_tools(), set()
    for entry in sorted(registry if registry is not None else unity_bridge.load_registry(), key=lambda e: -e.get("_mtime", 0)):
        if entry.get("hash") in seen:
            continue
        try:
            call(entry, "__ping__", timeout=3)
        except unity_bridge.BridgeError:
            continue  # left behind by an editor that closed
        seen.add(entry.get("hash"))
        name = entry.get("name", "?")
        missing = [tool for tool in wanted if tool not in {t.get("name") for t in entry.get("tools", [])}]
        version = str(entry.get("version") or "")  # written by Claude Hand Tools 0.8.0 and later
        behind = bool(version) and version != constants.VERSION
        add("Unity", f"{name}: Claude Hand Tools", not missing and not behind,
            T("저장소에 있는데 Unity에 없는 도구: ") + ", ".join(missing) if missing else
            T("{version} (저장소 {repo})", version=version, repo=constants.VERSION) if behind else
            T("ch_* 도구 {count}개 응답", count=len(wanted)) + (f", {version}" if version else ""),
            "" if not (missing or behind) else T("Unity 창을 앞으로 가져와 새 패키지를 컴파일하게 하고, Console에 컴파일 오류가 없는지 보세요"))
        shot = Path(tempfile.gettempdir()) / f"claude-hand-selftest-{entry.get('hash')}.png"
        try:
            call(entry, "ch_capture", {"path": str(shot), "max_size": 128}, timeout=20)
            captured = shot.exists()
            detail = T("씬 뷰를 찍음") if captured else T("그림 파일이 생기지 않음")
        except unity_bridge.BridgeError as exc:
            captured, detail = False, exc.message
        finally:
            shot.unlink(missing_ok=True)
        add("Unity", T("{name}: 결과 미리보기 찍기", name=name), captured, detail, "" if captured else T("Unity에 씬 뷰가 열려 있는지 보세요"))
    if not seen:
        add("Unity", T("열린 Unity"), None, T("Claude Hand Tools가 응답하는 Unity가 없어 건너뜀"))

    progress(T("Blender 확인 중..."))
    want = addon_version(constants.CHAT_ADDON_SOURCE.read_text(encoding="utf-8"))
    open_blenders = blenders if blenders is not None else discovery.discover_blender(blender_ports)
    for inst in open_blenders:
        label = T("{name}@{port}: 채팅 애드온", name=inst.name, port=inst.port)
        try:
            res = (blender or link.blender_request)(inst.port, {"type": "execute_code", "params": {"code": BLENDER_ADDON_STATE_CODE}}, 10.0)
            state = json.loads(str(((res or {}).get("result") or {}).get("result", "")).strip().splitlines()[-1])
        except (OSError, ValueError, IndexError, AttributeError) as exc:
            add("Blender", label, False, T("상태를 읽지 못함: {exc}", exc=exc), T("MCP for Blender 서버가 켜져 있는지 보세요"))
            continue
        version = ".".join(map(str, state.get("version") or []))
        if not state.get("enabled") or not version:
            add("Blender", label, False, T("켜져 있지 않음"), "uv run tools/claude_hand.py blender-install-chat")
        else:
            add("Blender", label, version == want, version + ("" if version == want else T(" (저장소 {want})", want=want)),
                "" if version == want else T("uv run tools/claude_hand.py update 뒤 Blender를 다시 여세요"))
    if not open_blenders:
        add("Blender", T("열린 Blender"), None, T("응답하는 Blender가 없어 건너뜀"))

    progress(T("채팅 확인 중..."))
    running = link.supervisor_running() if supervisor_on is None else supervisor_on
    if not running:
        add(T("채팅"), T("감독 세션"), None, T("Claude Hand가 꺼져 있어 채널 확인은 건너뜀 (켠 뒤 다시 돌리면 확인)"))
    else:
        for line in chat_lines if chat_lines is not None else chatsetup.chat_status_report():
            if line.startswith(("OK ", "!! ")):
                add(T("채팅"), T("채널 서버"), line.startswith("OK "), line[3:].strip())

    checks += tokens if tokens is not None else [token_record_check()]

    progress(T("하위 세션의 안전장치 훅 확인 중..."))
    checks += hooks if hooks is not None else hook_checks(Path.home() / ".claude" / "agents")

    found = stale if stale is not None else stale_checks(Path.home() / ".claude" / "agents", Path.home() / ".claude" / "skills")
    checks += found or [Check(T("갱신"), T("하위 세션 정의·감독 스킬·Blender 애드온 파일"), True, T("저장소 버전과 같음"))]
    return checks
