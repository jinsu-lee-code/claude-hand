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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from handlib import agents, chatsetup, constants, discovery, install, launch, link


@dataclass
class Check:
    area: str
    item: str
    ok: bool | None  # None = not applicable / informational
    detail: str = ""
    fix: str = ""


def doctor_checks(projects: list[Path], blender_ports: list[int], version=install.tool_version, progress=None) -> list[Check]:
    checks: list[Check] = []
    progress = progress or (lambda message: None)

    def add(area, item, ok, detail="", fix=""):
        checks.append(Check(area, item, ok, detail, fix))

    progress("프로그램 버전 확인 중...")
    tools = {"claude": ("claude", "--version"), "uv": ("uv", "--version"), "git": ("git", "--version"),
             "node": ("node", "--version"), "agent-desktop": ("agent-desktop", "version")}
    with ThreadPoolExecutor(max_workers=len(tools)) as pool:  # each CLI start can take a second or two on Windows
        futures = {name: pool.submit(version, *cmd) for name, cmd in tools.items()}
        versions = {name: future.result() for name, future in futures.items()}
    claude = versions["claude"]
    add("기본 도구", "Claude Code CLI (claude 명령)", claude is not None, claude or "없음",
        "" if claude else ("winget install Anthropic.ClaudeCode" if sys.platform == "win32" else "curl -fsSL https://claude.ai/install.sh | bash"))
    uv = versions["uv"]
    add("기본 도구", "uv", uv is not None, uv or "없음", "" if uv else
        "winget install --id astral-sh.uv -e  (winget이 없으면 https://docs.astral.sh/uv/getting-started/installation/ 안내대로)")
    git = versions["git"]
    add("기본 도구", "git (Unity가 MCP for Unity를 받을 때 필요)", git is not None, git or "없음",
        "" if git else ("winget install --id Git.Git -e  (winget이 없으면 https://git-scm.com/download/win 에서 받아 설치)"
                        if sys.platform == "win32" else "https://git-scm.com/downloads 에서 설치"))
    node = versions["node"]
    node_ok = (install.node_major(node) or 0) >= 18
    add("기본 도구", "Node.js 18 이상 (agent-desktop용)", node_ok, node or "없음",
        "" if node_ok else "winget install --id OpenJS.NodeJS.LTS -e  (winget이 없으면 https://nodejs.org 에서 LTS 설치)")

    agent_desktop = versions["agent-desktop"]
    add("agent-desktop", "agent-desktop CLI", agent_desktop is not None, agent_desktop or "없음",
        "" if agent_desktop else "npm install -g agent-desktop")

    exes = install.find_blender_executables()
    add("Blender", "Blender 실행 파일", bool(exes), ", ".join(map(str, exes[:3])) or "못 찾음",
        "" if exes else "Blender를 설치하거나 blender-open --blender-exe 로 경로를 알려주세요")
    addons = install.find_blender_addon_installs()
    add("Blender", "MCP for Blender 애드온", bool(addons), ", ".join(map(str, addons[:3])) or "설치 안 됨",
        "" if addons else "uvx mcp-for-blender setup --dry-run  (확인 후 --yes)")
    blender_cfg = discovery.detect_blender_config()
    configured = blender_cfg.origin != "default"  # a warning means some other Blender MCP is configured, not ours
    add("Blender", "Claude 설정에 MCP for Blender 등록", configured,
        blender_cfg.origin if configured else (blender_cfg.warning or "없음"),
        "" if configured else "uvx mcp-for-blender setup --dry-run  (확인 후 --yes)")
    progress("Blender 창 찾는 중...")
    running = discovery.discover_blender(blender_ports)
    add("Blender", "응답하는 Blender 창", bool(running) if exes else None,
        ", ".join(f"{b.name}@{b.port}" for b in running) or "없음",
        "" if running else "Blender를 열고 애드온 서버를 켜세요 (blender-open --port 9876)")

    progress("Unity 에디터 찾는 중...")
    unity_cfg = discovery.detect_server_config()
    unity_configured = unity_cfg.origin != "default"
    add("Unity", "Claude 설정에 MCP for Unity 등록", unity_configured,
        f"{unity_cfg.transport} ({unity_cfg.origin})" if unity_configured else "없음",
        "" if unity_configured else constants.UNITY_MCP_ADD)
    unity_running = discovery.gather_instances(unity_cfg, [], include_dead=False)
    add("Unity", "연결된 Unity 에디터", bool(unity_running) or None,
        ", ".join(i.id for i in unity_running) or "없음",
        "" if unity_running else "Unity 프로젝트를 열고 Window > MCP for Unity 가 Connected인지 확인")
    try:
        via_bridge = [i for i in discovery.discover_bridge() if i.alive]
    except Exception:
        via_bridge = []
    via_mcp = [i for i in (discovery.discover_http(unity_cfg.url) if unity_cfg.transport == "http" else discovery.discover_stdio()) if i.alive]
    same = lambda a, b: a.name == b.name and (a.hash.startswith(b.hash) or b.hash.startswith(a.hash))
    only_bridge = [b for b in via_bridge if not any(same(b, m) for m in via_mcp)]
    only_mcp = [m for m in via_mcp if not any(same(m, b) for b in via_bridge)]
    if only_bridge:
        add("Unity", "MCP for Unity 연결", False, "열려 있지만 MCP for Unity에 안 보임: " + ", ".join(i.name for i in only_bridge),
            "Unity의 Window > MCP for Unity에서 Transport를 Stdio로 하고 Connected인지 확인하세요. Claude Code 등록 방식(" +
            unity_cfg.transport + ")이 그 창의 Transport와 달라도 이렇게 됩니다. 바꿨다면 그 창에서 Claude Code의 Configure를 "
            "다시 누르세요. 이 상태로도 ch_* 도구는 쓸 수 있지만 MCP for Unity 기본 도구는 하위 세션에서 안 됩니다")
    if only_mcp:
        add("Unity", "Claude Hand Tools 연결", False, "MCP for Unity에는 보이지만 Claude Hand Tools가 응답 안 함: " +
            ", ".join(i.name for i in only_mcp),
            "그 프로젝트에 uv run tools/claude_hand.py unity-install 을 했는지, Unity Console에 컴파일 오류가 없는지 확인하세요")
    for project in projects:
        label = f"프로젝트 {project.name}"
        if not install.is_unity_project(project):
            add("Unity", label, False, f"{project}: Unity 프로젝트가 아님", "경로를 확인하세요")
            continue
        try:
            plan, _ = install.plan_unity_install(project)
        except SystemExit as exc:
            add("Unity", f"{label}: 패키지", False, str(exc), "Packages/manifest.json을 확인하세요")
            continue
        add("Unity", f"{label}: 패키지", not plan.changes, "; ".join(plan.changes or plan.notes),
            "" if not plan.changes else f'uv run tools/claude_hand.py unity-install "{project}"')
        editor_version = install.read_project_version(project)
        editor = install.find_unity_editor(editor_version) if editor_version else None
        add("Unity", f"{label}: 에디터 {editor_version or '?'}", editor is not None if editor_version else None,
            str(editor) if editor else "Unity Hub 기본 위치에서 못 찾음",
            "" if editor else "Unity Hub에서 해당 버전을 설치하거나 Hub로 직접 여세요")

    claude_v = chatsetup.parse_claude_version(claude)
    relay_ok = claude_v is not None and claude_v >= constants.MIN_CLAUDE_FOR_PERMISSION_RELAY
    add("채팅", "Claude Code 2.1.234 이상 (채팅창의 허용/거부 버튼)", relay_ok if claude else None, claude or "없음",
        "" if relay_ok else "claude update")
    provider = [name for name in constants.THIRD_PARTY_PROVIDER_ENV if os.environ.get(name, "").lower() in ("1", "true")]
    add("채팅", "Channels를 쓸 수 있는 로그인 (claude.ai 또는 Console API 키)", not provider,
        ("이 환경은 " + ", ".join(provider) + " 사용 중 - Channels를 쓸 수 없음") if provider else "Bedrock/Vertex/Foundry 아님",
        "" if not provider else "claude.ai 로그인이나 Console API 키로 Claude Code를 쓰세요")
    try:
        chat_plan = chatsetup.plan_chat_config(constants.REPO_ROOT)
    except SystemExit as exc:
        chat_plan = {Path("?"): {"error": str(exc)}}
    add("채팅", "채팅 채널 등록과 답장 허용 (claude-hand 폴더)", not chat_plan,
        "됨" if not chat_plan else "빠짐: " + ", ".join(p.name for p in chat_plan),
        "" if not chat_plan else "uv run tools/claude_hand.py chat-setup")
    chat_addons = install.find_chat_addon_installs()
    add("채팅", "Blender 채팅 애드온", bool(chat_addons) if exes else None, ", ".join(map(str, chat_addons[:3])) or "설치 안 됨",
        "" if chat_addons else "uv run tools/claude_hand.py blender-install-chat")

    agents_dir = Path.home() / ".claude" / "agents"
    generated = sorted(p.name for p in agents_dir.glob("*.md") if constants.GENERATED_MARKER in p.read_text(encoding="utf-8", errors="replace")) \
        if agents_dir.is_dir() else []
    add("claude-hand", "하위 세션 정의 파일", bool(generated) or None, ", ".join(generated) or "없음",
        "" if generated else "uv run tools/claude_hand.py sync")
    skill = Path.home() / ".claude" / "skills" / "claude-hand" / "SKILL.md"
    add("claude-hand", "감독 스킬", skill.exists(), str(skill) if skill.exists() else "없음",
        "" if skill.exists() else "uv run tools/claude_hand.py sync")
    if launch.remote_control_wanted(launch.read_user_config()):
        problems = launch.remote_control_problems()
        add("데스크톱 앱에서 보기", "Remote Control을 쓸 수 있는 로그인", not problems, "; ".join(problems) or "문제 없음",
            "" if not problems else "claude.ai 계정(Pro·Max·Team 등)으로 /login 하세요")
    progress("이전 버전 흔적 확인 중...")
    entry = chatsetup.claude_mcp_get() if claude else None
    if entry is not None and entry.exists and entry.scope == "local":
        add("갱신", "이전 버전의 local 범위 채팅 등록 (.mcp.json보다 먼저 쓰임)", False, entry.scope, "uv run tools/claude_hand.py update")
    checks.extend(stale_checks(agents_dir, Path.home() / ".claude" / "skills"))
    return checks


def stale_checks(agents_dir: Path, skills_dir: Path) -> list[Check]:
    """Things an earlier claude-hand left behind that `update` refreshes."""
    checks = []
    installed = skills_dir / "claude-hand" / "SKILL.md"
    if installed.exists() and installed.read_text(encoding="utf-8") != agents.rendered_skill():
        checks.append(Check("갱신", "설치된 감독 스킬이 저장소 버전과 다름", False, str(installed), "uv run tools/claude_hand.py update"))
    outdated = []
    for path in sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        inst = agents.parse_generated_agent(text) if constants.GENERATED_MARKER in text else None
        if inst is None:
            continue
        files = agents.build_agent_files([] if isinstance(inst, discovery.BlenderInstance) else [inst], discovery.detect_server_config(),
                                  [inst] if isinstance(inst, discovery.BlenderInstance) else [], discovery.detect_blender_config(), "uv run -q --no-project")
        if path.name in files and files[path.name] != text:
            outdated.append(path.name)
    if outdated:
        checks.append(Check("갱신", "이전 버전으로 만든 하위 세션 정의", False, ", ".join(outdated), "uv run tools/claude_hand.py update"))
    for addon in install.find_chat_addon_installs():
        if addon.read_bytes() != constants.CHAT_ADDON_SOURCE.read_bytes():
            checks.append(Check("갱신", "설치된 Blender 채팅 애드온이 저장소 버전과 다름", False, str(addon), "uv run tools/claude_hand.py update"))
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
            print(f"       고치기: {c.fix}")
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
        return False, "실패: " + ", ".join(failed[:5]) + (f" 외 {len(failed) - 5}개" if len(failed) > 5 else "")
    ran, skipped = re.search(r"^Ran (\d+) tests?", output, re.M), re.search(r"skipped=(\d+)", output)
    if not passed:
        return False, "끝까지 돌지 못함: " + (output.strip().splitlines() or ["출력 없음"])[-1][:200]
    return True, f"{ran.group(1) if ran else '?'}개 통과" + (f" (건너뜀 {skipped.group(1)})" if skipped else "")


def hook_shell(env=os.environ, platform=sys.platform, which=shutil.which) -> list[str] | None:
    """The shell Claude Code runs hook commands in: sh, or Git Bash on Windows (None when it is not found).
    Not `bash` from PATH on Windows: that can be WSL's."""
    if platform != "win32":
        return ["sh", "-c"]
    candidates = [env.get("CLAUDE_CODE_GIT_BASH_PATH") or ""]
    git = which("git")
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
    fix = "uv run tools/claude_hand.py update (그래도 안 되면 Git Bash에서 uv가 실행되는지 보세요)"
    for path in sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        found = HOOK_COMMAND_RE.search(text) if constants.GENERATED_MARKER in text else None
        if not found:
            continue
        item = f"{path.stem}: 안전장치 훅"
        if not shell:
            checks.append(Check("안전장치", item, None, "Git Bash를 찾지 못해 훅 명령을 돌려 보지 못함"))
            continue
        try:
            proc = run([*shell, json.loads(found.group(1))], input=json.dumps(HOOK_PROBE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
            decision = (json.loads(proc.stdout).get("hookSpecificOutput") or {}).get("permissionDecision") if proc.stdout.strip() else ""
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            checks.append(Check("안전장치", item, False, f"훅 명령을 돌리지 못함: {exc}", fix))
            continue
        if proc.returncode == 0 and decision == "deny":
            checks.append(Check("안전장치", item, True, "다른 대상의 호출을 막음"))
        else:
            said = ((proc.stderr or proc.stdout).strip().splitlines() or ["출력 없음"])[-1][:160]
            checks.append(Check("안전장치", item, False, f"막지 못함 (종료 코드 {proc.returncode}): {said}", fix))
    return checks


def selftest_checks(blender_ports: list[int], run_tests: bool = True, run=subprocess.run, progress=None,
                    registry=None, unity_request=None, blender=None, blenders=None, chat_lines=None,
                    supervisor_on=None, stale=None, hooks=None) -> list[Check]:
    checks: list[Check] = []
    progress = progress or (lambda message: None)
    add = lambda *a: checks.append(Check(*a))
    tests = constants.REPO_ROOT / "tools" / "tests"

    if run_tests:
        progress("Python 테스트 실행 중...")
        proc = run([sys.executable, "-m", "unittest", "discover", "-s", str(tests)], cwd=str(constants.REPO_ROOT), capture_output=True,
                   text=True, encoding="utf-8", errors="replace", timeout=600)
        ok, detail = unittest_summary((proc.stdout or "") + (proc.stderr or ""))
        add("테스트", "Python 테스트", ok, detail, "" if ok else "uv run --no-project python -m unittest discover -s tools/tests -v")
        exes = install.find_blender_executables()
        if exes:
            progress("Blender 안 애드온 테스트 실행 중...")
            proc = run([str(exes[0]), "--background", "--factory-startup", "--python-expr", BLENDER_TESTS_EXPR.format(tests=str(tests))],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            ok, detail = unittest_summary((proc.stdout or "") + (proc.stderr or ""))
            add("테스트", f"Blender 안 애드온 테스트 ({exes[0].parent.name})", ok, detail,
                "" if ok else "selftest 출력의 실패한 테스트 이름으로 tools/tests/test_blender_chat_addon.py를 보세요")
        else:
            add("테스트", "Blender 안 애드온 테스트", None, "Blender 실행 파일을 못 찾아 건너뜀")

    progress("Unity 확인 중...")
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
        add("Unity", f"{name}: Claude Hand Tools", not missing, f"ch_* 도구 {len(wanted)}개 응답" if not missing else
            "저장소에 있는데 Unity에 없는 도구: " + ", ".join(missing),
            "" if not missing else "Unity 창을 앞으로 가져와 새 패키지를 컴파일하게 하고, Console에 컴파일 오류가 없는지 보세요")
        shot = Path(tempfile.gettempdir()) / f"claude-hand-selftest-{entry.get('hash')}.png"
        try:
            call(entry, "ch_capture", {"path": str(shot), "max_size": 128}, timeout=20)
            captured = shot.exists()
            detail = "씬 뷰를 찍음" if captured else "그림 파일이 생기지 않음"
        except unity_bridge.BridgeError as exc:
            captured, detail = False, exc.message
        finally:
            shot.unlink(missing_ok=True)
        add("Unity", f"{name}: 결과 미리보기 찍기", captured, detail, "" if captured else "Unity에 씬 뷰가 열려 있는지 보세요")
    if not seen:
        add("Unity", "열린 Unity", None, "Claude Hand Tools가 응답하는 Unity가 없어 건너뜀")

    progress("Blender 확인 중...")
    want = addon_version(constants.CHAT_ADDON_SOURCE.read_text(encoding="utf-8"))
    open_blenders = blenders if blenders is not None else discovery.discover_blender(blender_ports)
    for inst in open_blenders:
        label = f"{inst.name}@{inst.port}: 채팅 애드온"
        try:
            res = (blender or link.blender_request)(inst.port, {"type": "execute_code", "params": {"code": BLENDER_ADDON_STATE_CODE}}, 10.0)
            state = json.loads(str(((res or {}).get("result") or {}).get("result", "")).strip().splitlines()[-1])
        except (OSError, ValueError, IndexError, AttributeError) as exc:
            add("Blender", label, False, f"상태를 읽지 못함: {exc}", "MCP for Blender 서버가 켜져 있는지 보세요")
            continue
        version = ".".join(map(str, state.get("version") or []))
        if not state.get("enabled") or not version:
            add("Blender", label, False, "켜져 있지 않음", "uv run tools/claude_hand.py blender-install-chat")
        else:
            add("Blender", label, version == want, version + ("" if version == want else f" (저장소 {want})"),
                "" if version == want else "uv run tools/claude_hand.py update 뒤 Blender를 다시 여세요")
    if not open_blenders:
        add("Blender", "열린 Blender", None, "응답하는 Blender가 없어 건너뜀")

    progress("채팅 확인 중...")
    running = link.supervisor_running() if supervisor_on is None else supervisor_on
    if not running:
        add("채팅", "감독 세션", None, "Claude Hand가 꺼져 있어 채널 확인은 건너뜀 (켠 뒤 다시 돌리면 확인)")
    else:
        for line in chat_lines if chat_lines is not None else chatsetup.chat_status_report():
            if line.startswith(("OK ", "!! ")):
                add("채팅", "채널 서버", line.startswith("OK "), line[3:].strip())

    progress("하위 세션의 안전장치 훅 확인 중...")
    checks += hooks if hooks is not None else hook_checks(Path.home() / ".claude" / "agents")

    found = stale if stale is not None else stale_checks(Path.home() / ".claude" / "agents", Path.home() / ".claude" / "skills")
    checks += found or [Check("갱신", "하위 세션 정의·감독 스킬·Blender 애드온 파일", True, "저장소 버전과 같음")]
    return checks
