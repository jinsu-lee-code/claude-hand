#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""claude-hand: one Claude Code subagent per open Unity project or Blender file.

Unity goes through MCP for Unity (CoplayDev); Blender through MCP for Blender (ahujasid, the
GitHub add-on), one add-on port per Blender window.

  list    Show the Unity editors and Blender windows that can be reached right now.
  sync    Write a subagent definition (unity-<project>.md / blender-<file>.md) for each of
          them, plus the supervisor skill, into your Claude Code folders.
  (the subagents' PreToolUse hook, which blocks any call that would reach a project or Blender other
   than the subagent's own, is tools/guard.py; `guard` here only keeps older subagent files working)

First-time setup helpers (used by the claude-hand-setup skill, safe to run by hand):
  doctor         Check what is installed and configured; prints the command that fixes each gap.
  unity-install  Add MCP for Unity and Claude Hand Tools to a project's Packages/manifest.json.
  unity-open     Open a project with the Unity version it was made with.
  unity-call     Run one ch_* tool in an open Unity project (through Claude Hand Tools' own bridge).
  blender-open   Start Blender with the MCP for Blender add-on listening on a chosen port.
  chat-setup           Register the chat channel for this folder (.mcp.json), allow its reply tool
                       (.claude/settings.local.json) and pre-start it once so uv fetches Python.
  blender-install-chat Install and enable the Blender chat panel add-on.
  chat-status          Explain why a chat panel does or does not reach the supervisor.
  status               Show every open window and what goes on in it: at work and on whose request, waiting
                       for an answer, stopped by a dialog, idle (the chat panels show the same of the others).
  trace                Show what happened to a chat panel request, step by step: the message, each tool
                       call and refusal, the ch_* calls and how long they took, the reply, what it took.
  usage                Show what the chat panel requests took: time, actions and tokens of each, and the sums
                       by who spent them and by day.
  update               After `git pull`: refresh everything an earlier version set up.

Everyday start (so nobody has to open a terminal):
  preflight            Run by start-supervisor each time: refresh subagents, check nothing is
                       already running, explain the first-run prompts.
  start                Open the Claude Hand window (what the icon and the panels' start button do).
                       From any Claude Code session: "Claude Hand 켜줘" runs this.
  shortcut             Put a "Claude Hand" icon on the Desktop and in the Start menu.
  remote-control       on/off/status (default on): show the supervisor and its subagents in the
                       Claude desktop app (Code tab), claude.ai/code and the mobile app.
  minimize             on/off/status (default on, Windows): minimize the Claude Hand window once
                       the session is in use.
  new-session          Start the next Claude Hand as a new conversation (each start otherwise resumes
                       the same one, so work carries over from day to day).
  settings             Show or change the settings and the lab (the gear button of a chat panel shows the
                       same): the language (ko | en), what to show, how long to keep things, features to try.

Run with uv (already installed for MCP for Unity) or any Python 3.10+:
  uv run tools/claude_hand.py list
  uv run tools/claude_hand.py sync
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402
from handlib import agents, chatsetup, checks, constants, discovery, eventlog, install, launch, link, overview, prefs, settings, usage  # noqa: E402
from handlib.lang import N_, T, pick  # noqa: E402


def cmd_chat_setup(args: argparse.Namespace) -> int:
    prefix = "(dry run) " if args.dry_run else ""
    if args.register_local:
        cmd = chatsetup.chat_add_command(args.runner)
        print(prefix + T("등록(대체 방식): ") + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        if not args.dry_run:
            proc = subprocess.run([shutil.which("claude") or "claude", *cmd[1:]], cwd=str(constants.REPO_ROOT))
            if proc.returncode != 0:
                return proc.returncode
    else:
        plan = chatsetup.plan_chat_config(constants.REPO_ROOT, args.runner)
        for path in plan:
            what = T("채널 등록") if path.name == ".mcp.json" else \
                T("답장·현재 상황 도구 자동 허용, 감독이 Unity·Blender를 직접 조작하는 도구 막기")
            print(f"{prefix}{T('고침') if path.exists() else T('만듦')}: {path}  ({what})")
        if not plan:
            print(T("채널 등록과 답장 도구 허용은 이미 돼 있습니다."))
        if not args.dry_run:
            settings.write_json_files(plan)

    if not args.dry_run:
        launch.write_user_config()
    if args.dry_run or args.no_check:
        print(prefix + T("건너뜀: 채널 서버 미리 실행(--check)"))
    else:
        print(T("채널 서버를 한 번 미리 실행합니다(처음이면 uv가 Python을 내려받아 몇 분 걸릴 수 있습니다)..."))
        result = chatsetup.prewarm_chat_channel(constants.REPO_ROOT, args.runner)
        if not result.get("ok"):
            print(T("점검 실패: ") + json.dumps(result, ensure_ascii=False))
            return 1
        print(T("점검 통과: Python {python}, 포트 {port} 사용 가능, 하위 세션 {count}개",
                python=result.get('python'), port=result.get('port'), count=len(result.get('agents', []))))

    print(T("다음: start-supervisor.cmd 로 감독 세션을 여세요. 켤 때마다 개발용 채널 경고가 뜨고(I am using this for\n"
            "      local development), 처음 한 번은 .mcp.json 서버(claude-hand-chat) 사용 승인 창도 뜹니다. 내용을 읽고 승인하세요."))
    return 0


def cmd_blender_install_chat(args: argparse.Namespace) -> int:
    targets = [Path(args.addons_dir)] if args.addons_dir else install.blender_chat_targets()
    if not targets:
        print(T("Blender 사용자 폴더를 찾지 못했습니다. Blender를 한 번 실행한 뒤 다시 하거나 --addons-dir로 알려주세요."))
        return 1
    for target in targets:
        print(T("{dry}복사: {source} -> {target}",
                dry='(dry run) ' if args.dry_run else '', source=constants.CHAT_ADDON_SOURCE, target=target / (constants.CHAT_ADDON_MODULE + '.py')))
        if not args.dry_run:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(constants.CHAT_ADDON_SOURCE, target / (constants.CHAT_ADDON_MODULE + ".py"))
    exes = [Path(args.blender_exe)] if args.blender_exe else install.find_blender_executables()
    if not exes:
        print(T("Blender 실행 파일을 찾지 못해 켜지는 못했습니다. Preferences > Add-ons에서 'Claude Hand Chat'을 켜세요."))
        return 0
    cmd = [str(exes[0]), "--background", "--python-expr", install.ENABLE_CHAT_ADDON]
    print(T("{dry}켜기: {blender} --background (애드온 활성화 후 환경설정 저장)", dry='(dry run) ' if args.dry_run else '', blender=exes[0]))
    if args.dry_run:
        return 0
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(T("Blender를 실행하지 못했습니다: {exc}", exc=exc))
        return 1
    if "CLAUDE_HAND_CHAT enabled" in (proc.stdout or ""):
        print(T("켰습니다. 열려 있던 Blender는 다시 열어야 N 패널에 Claude Hand 탭이 보입니다."))
        return 0
    print(T("켜지 못했습니다. Preferences > Add-ons에서 'Claude Hand Chat'을 직접 켜세요."))
    return 1


def cmd_preflight(args: argparse.Namespace) -> int:
    """What start-supervisor runs before Claude Code, so starting needs nothing else from the user."""
    home = link.claude_hand_home()
    launch.write_user_config(home)
    if link.supervisor_running(home):
        print(T("Claude Hand가 이미 실행 중입니다. 작업 표시줄에서 'Claude Hand' 창을 찾아 그 창을 쓰세요."))
        return 3
    try:
        plan = chatsetup.plan_chat_config(constants.REPO_ROOT, args.runner)
        if plan:
            settings.write_json_files(plan)
            print(T("채팅 설정을 맞췄습니다."))
    except SystemExit as exc:
        print(T("채팅 설정을 고치지 못했습니다: {exc}", exc=exc))
    # Claude Code watches ~/.claude/agents for new subagents, except the first file in a folder that did
    # not exist when the session started; make sure the folder exists before Claude Code starts.
    (Path.home() / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
    try:
        result = agents.refresh_agents(runner=args.runner, blender_ports=args.blender_ports)
        if result.written:
            print(T("열려 있는 창의 하위 세션을 준비했습니다: ") + ", ".join(p.stem for p in result.written))
        # The others too, when the language of the interface or of the instructions was changed since they were written.
        try:
            agents.rewrite_installed(runner=args.runner)
        except Exception:  # they stay as they are; `update` writes them, and says what is in the way
            pass
    except SystemExit as exc:
        print(T("하위 세션을 만들지 못했습니다: {exc}", exc=exc))
    skill = Path.home() / ".claude" / "skills" / "claude-hand" / "SKILL.md"
    if not skill.exists() or skill.read_text(encoding="utf-8") != agents.rendered_skill():
        agents.install_skill(Path.home() / ".claude" / "skills")
    # The subagent prompts just written call functions of this checkout's Blender add-on; an older installed copy
    # does not have them. It is not replaced here: Blender has to be reopened for that, which is the user's call.
    try:
        behind = [str(path) for path in install.find_chat_addon_installs() if path.read_bytes() != constants.CHAT_ADDON_SOURCE.read_bytes()]
    except OSError:
        behind = []
    if behind:
        print(T("Blender 채팅 애드온이 이 버전과 다릅니다. Blender에서 옮기기·되돌리기가 안 될 수 있으니 "
              "`uv run tools/claude_hand.py update`를 한 뒤 Blender를 다시 여세요."))
    config = launch.read_user_config(home)
    cleaned, freed = launch.clean_exchange(launch.exchange_keep_days(config))
    if cleaned:
        print(T("교환 폴더에서 {days}일 넘은 파일 {count}개({mb:.1f}MB)를 지웠습니다.",
                days=launch.exchange_keep_days(config), count=cleaned, mb=freed / 1e6))
    eventlog.clean(home)  # the event log (trace): two weeks are kept
    session_args, session, resumed = launch.supervisor_session(config)
    if config.get("supervisor_session") != session:
        config["supervisor_session"] = session
        settings.write_json_files({home / "config.json": config})
    # The supervisor's own hook (whose request is the work it hands out for), in a settings file only this session gets.
    # "supervisor_hook": false in config.json starts without it: the channel then guesses, as it did before the hook.
    hook_settings = constants.LAUNCH_ARGS_FILE.with_name(".launch-settings.json")
    hooked = config.get("supervisor_hook", True) is not False
    settings.write_json_files({hook_settings: agents.supervisor_settings(args.runner) if hooked else {}})
    constants.LAUNCH_ARGS_FILE.write_text(" ".join(filter(None, [*session_args, launch.launch_args(config),
                                                                 f'--settings "{hook_settings.as_posix()}"' if hooked else ""])) + "\n",
                                          encoding="utf-8")
    inherited = launch.inherited_session_env()
    constants.LAUNCH_ARGS_FILE.with_name(".launch-unset").write_text("".join(f"{name}\n" for name in inherited), encoding="utf-8")
    banner = T(launch.BANNER)
    if config.get("minimize_after_start", True) is False:  # the window stays: do not say that it goes
        banner = banner.replace(T(launch.BANNER_MINIMIZES), "")
    print(banner)
    print(T(" 지난 대화를 이어서 엽니다. 새 대화로 시작하려면: uv run tools/claude_hand.py new-session") if resumed
          else T(" 새 대화로 시작합니다. 다음부터는 켤 때마다 이 대화를 이어서 엽니다."))
    if inherited:
        print(T(" 다른 Claude Code 세션에서 켜져서, 그 세션의 표시 변수 {count}개를 지우고 따로 시작합니다.", count=len(inherited)))
    if launch.remote_control_wanted(config):
        problems = launch.remote_control_problems()
        if problems:
            print(T(" 데스크톱 앱에서 보기(Remote Control)는 이번에는 끄고 시작합니다:"))
            for problem in problems:
                print(f"   - {problem}")
        else:
            print(T(" 진행 보기: Claude 데스크톱 앱의 Code 탭(또는 claude.ai/code, 모바일 앱)에서 '{name}' 세션을 여세요.",
                    name=constants.REMOTE_CONTROL_NAME))
    marker = home / "first-run-done"
    if not marker.exists():
        print(T(launch.FIRST_RUN_GUIDE))
        marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    return 0


def cmd_clean_exchange(args: argparse.Namespace) -> int:
    """Delete old transfer files from the exchange folder now (preflight does it on every start)."""
    config = launch.read_user_config()
    if args.keep_days is not None:
        config["exchange_keep_days"] = args.keep_days
        settings.write_json_files({link.claude_hand_home() / "config.json": config})
        print(T("교환 폴더의 파일을 {keep_days}일 동안 둡니다.", keep_days=args.keep_days) if args.keep_days else T("교환 폴더를 자동으로 정리하지 않습니다."))
    days = args.days if args.days is not None else launch.exchange_keep_days(config)
    folder = link.exchange_dir()
    if days <= 0:
        print(T("{folder}: 자동 정리가 꺼져 있습니다. 지금 지우려면 --days N을 붙이세요.", folder=folder))
        return 0
    count, size = launch.clean_exchange(days, dry_run=args.dry_run)
    print(T("(dry run) {folder}: {days}일 넘은 파일 {count}개({mb:.1f}MB)를 지울 수 있습니다.", folder=folder, days=days, count=count, mb=size / 1e6)
          if args.dry_run else
          T("{folder}: {days}일 넘은 파일 {count}개({mb:.1f}MB)를 지웠습니다.", folder=folder, days=days, count=count, mb=size / 1e6))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """What happened to a request, from the event log the chat channel, the guard and the Unity bridge write."""
    events = eventlog.read(days=args.days)
    listed = eventlog.requests(events)
    if args.request:
        chosen = next((r for r in listed if r["request"] == args.request), None)
    elif args.chat:
        chosen = next((r for r in reversed(listed) if r["chat_id"] == args.chat), None)
    else:
        if args.json:
            print(json.dumps(listed, ensure_ascii=False, indent=2))
            return 0
        if not listed:
            print(T("{log_dir}: 최근 {days}일 동안 채팅창 요청 기록이 없습니다. Claude Hand가 켜져 있을 때 채팅창에서 보낸 요청이 여기에 남습니다.",
                    log_dir=eventlog.log_dir(), days=args.days))
            return 0
        for r in listed[-args.last:]:
            answer = T("답 {seconds:.0f}초 뒤", seconds=r['answered'] - r['ts']) if r["answered"] else T("답 없음")
            refused = T(" · 막힘 {refused}", refused=r['refused']) if r["refused"] else ""
            print(T("{when}  {request:<16} {chat_id:<24} 동작 {actions}{refused} · {answer}  \"{asked}\"",
                    when=time.strftime('%m-%d %H:%M:%S', time.localtime(r['ts'])), request=r['request'], chat_id=r['chat_id'], actions=r['actions'], refused=refused, answer=answer, asked=r['text'][:60]))
        print(T("\n한 요청을 자세히: uv run tools/claude_hand.py trace <채팅 이름>   또는   trace --request <번호>"))
        return 0
    if chosen is None:
        print(T("최근 {days}일 기록에 그런 요청이 없습니다. `trace`만 치면 요청 목록이 나옵니다.", days=args.days))
        return 1
    lines = eventlog.trace(events, chosen["request"])
    if args.json:
        print(json.dumps(lines, ensure_ascii=False, indent=2))
        return 0
    print(T("요청 {request}  ({chat_id}, {day})",
            request=chosen['request'], chat_id=chosen['chat_id'], day=time.strftime('%Y-%m-%d', time.localtime(chosen['ts']))))
    for event in lines:
        print("  " + eventlog.describe(event, since=chosen["ts"]))
    if chosen["answered"] is None:
        print(T("  (아직 답이 없습니다)"))
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    """What the chat panel requests took, from the lines the chat channel writes to the event log when a request is
    answered (the same that a panel shows under the result, here with who spent what)."""
    events = eventlog.read(days=args.days)
    asked = {e["request"]: e for e in events if e["event"] == "message" and e.get("request")}
    rows = sorted({e["request"]: e for e in events if e["event"] == "cost" and e.get("request")}.values(), key=lambda e: e["ts"])
    if args.json:
        print(json.dumps([{**row, "asked": asked.get(row["request"], {}).get("text", "")} for row in rows], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(T("{log_dir}: 최근 {days}일 동안 시간·토큰 기록이 없습니다. Claude Hand가 켜져 있을 때 채팅창 요청에 결과 답이 오면 남습니다.", log_dir=eventlog.log_dir(), days=args.days))
        return 0
    spent = lambda row: {who: {**usage.blank(), **one} for who, one in (row.get("tokens") or {}).items() if isinstance(one, dict)}
    for row in rows[-args.last:]:
        cost = spent(row)
        tokens = usage.tokens_text(usage.together(cost)) if cost else T("토큰 기록 없음")
        print(T("{when}  {chat_id:<24} {duration} · 동작 {actions}번 · {tokens}  \"{asked}\"",
                when=time.strftime('%m-%d %H:%M:%S', time.localtime(row['ts'])), chat_id=row.get('chat_id', ''), duration=usage.duration(row.get('seconds', 0)), actions=row.get('actions', 0), tokens=tokens, asked=asked.get(row['request'], {}).get('text', '')[:40]))
    by_who, by_day = {}, {}
    for row in rows:
        day = by_day.setdefault(time.strftime("%m-%d", time.localtime(row["ts"])), {"requests": 0, "seconds": 0.0, **usage.blank()})
        day["requests"] += 1
        day["seconds"] += row.get("seconds", 0)
        for who, one in spent(row).items():
            for key in ("calls", "output", "input", "cached"):
                by_who.setdefault(who, usage.blank())[key] += one[key]
                day[key] += one[key]
    print(T("\n합계: 요청 {count}개 · 기다린 시간 {duration} · {tokens}",
            count=len(rows), duration=usage.duration(sum(row.get('seconds', 0) for row in rows)), tokens=usage.tokens_text(usage.together(by_who))))
    for who in sorted(by_who, key=lambda who: (who != usage.SUPERVISOR, who)):
        print(T("  {who:<24} API {calls}번 · {tokens}", who=usage.shown(who), calls=by_who[who]['calls'], tokens=usage.tokens_text(by_who[who])))
    if len(by_day) > 1:
        print(T("날짜별:"))
        for name, day in by_day.items():
            print(T("  {day}  요청 {requests}개 · {duration} · {tokens}",
                    day=name, requests=day['requests'], duration=usage.duration(day['seconds']), tokens=usage.tokens_text(day)))
    print(T("\n출력: 써낸 토큰. 새 입력: 새로 읽은 토큰(캐시에 새로 쓴 것 포함). 다시 읽기: 캐시에서 다시 읽은 토큰(대부분이고 쌉니다)."))
    return 0


def cmd_mode(args: argparse.Namespace) -> int:
    """Kept so instructions from an earlier version still run: there is one mode now."""
    home = link.claude_hand_home()
    launch.write_user_config(home)  # drops an old "mode": "desktop"
    print(T("이제 쓰는 방식은 하나입니다. Claude Hand 창(감독 세션)이 일을 하고, Unity·Blender 채팅창과 Claude 데스크톱 앱('{name}' 세션)은 같은 세션을 보는 창입니다.",
            name=constants.REMOTE_CONTROL_NAME))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    home = link.claude_hand_home()
    if launch.is_truthy(os.environ.get("CLAUDE_HAND_SUPERVISOR")):
        print(T("이 세션이 Claude Hand(감독 세션)입니다. 여기서 바로 시키면 됩니다."))
        return 0
    if link.supervisor_running(home):
        print(T("Claude Hand가 이미 켜져 있습니다. Claude 데스크톱 앱의 Code 탭에서 '{name}' 세션을 여세요(작업 표시줄의 'Claude Hand' 창과 같은 대화입니다).",
                name=constants.REMOTE_CONTROL_NAME))
        return 0
    launch.write_user_config(home)
    if args.dry_run:
        print(T("(dry run) 새 창에서 실행: ") + str(constants.START_SCRIPT))
        return 0
    try:
        launch.open_start_window()
    except OSError as exc:
        print(T("Claude Hand 창을 열지 못했습니다: {exc}. 바탕화면의 Claude Hand 아이콘을 누르세요.", exc=exc))
        return 1
    print(T("Claude Hand 창을 열었습니다."))
    print(T("  1. 새로 뜬 'Claude Hand' 창에 개발용 채널 경고가 뜹니다(켤 때마다). I am using this for local development 를 고르고 Enter."))
    config = launch.read_user_config(home)
    print(T("     창은 세션이 일을 시작하면 저절로 최소화됩니다. 닫지는 마세요.") if config.get("minimize_after_start", True) is not False
          else T("     창은 닫지 마세요."))
    if launch.remote_control_wanted(config) and not launch.remote_control_problems():
        print(T("  2. Claude 데스크톱 앱의 Code 탭 세션 목록에서 '{name}'을 여세요. 거기서 대화하면 됩니다.", name=constants.REMOTE_CONTROL_NAME))
    return 0


def cmd_new_session(args: argparse.Namespace) -> int:
    home = link.claude_hand_home()
    config = launch.read_user_config(home)
    config["supervisor_session"] = str(uuid.uuid4())
    settings.write_json_files({home / "config.json": config})
    print(T("다음에 Claude Hand를 켜면 새 대화로 시작합니다. 지금까지의 대화는 지워지지 않고 claude --resume 목록에 남습니다."))
    if link.supervisor_running(home):
        print(T("지금 켜져 있는 Claude Hand는 그대로입니다. 닫고 다시 켜면 새 대화가 됩니다."))
    return 0


def cmd_minimize(args: argparse.Namespace) -> int:
    home = link.claude_hand_home()
    config = launch.read_user_config(home)
    if args.action in ("on", "off"):
        config["minimize_after_start"] = args.action == "on"
        settings.write_json_files({home / "config.json": config})
        launch.write_user_config(home)
    on = config.get("minimize_after_start", True) is not False
    print(T("Claude Hand 창 자동 최소화: {state}", state=T('켜짐') if on else T('꺼짐')))
    if on:
        print(T("  Windows에서 세션이 처음 일을 시작하면(도구 호출이나 허락 요청) Claude Hand 창을 최소화합니다. 시작할 때의 "
              "확인 질문은 창이 보이는 동안 답할 수 있습니다."))
    return 0


def cmd_remote_control(args: argparse.Namespace) -> int:
    home = link.claude_hand_home()
    path = home / "config.json"
    config = launch.read_user_config(home)
    if args.action in ("on", "off"):
        config["remote_control"] = args.action == "on"
        settings.write_json_files({path: config})
        launch.write_user_config(home)
    on = launch.remote_control_wanted(config)
    print(f"Remote Control: {T('켜짐') if on else T('꺼짐')}{'' if 'remote_control' in config else T(' (기본값)')}")
    if on:
        print(T("  다음에 Claude Hand를 켤 때부터 Claude 데스크톱 앱의 Code 탭(또는 claude.ai/code, 모바일 앱)에 '{name}' 세션이 보입니다. 감독의 대화와 도구 실행, "
                "백그라운드로 도는 하위 세션이 보입니다.",
                name=constants.REMOTE_CONTROL_NAME))
        print(T("  필요한 것: Pro·Max·Ultra·Team·Enterprise 구독의 claude.ai 로그인(API 키는 안 됨). Team·Enterprise는 관리자가 켜야 합니다."))
        problems = launch.remote_control_problems()
        for problem in problems:
            print(T("  주의: {problem}", problem=problem))
        if problems:
            print(T("  위 문제가 있는 동안은 Claude Hand를 켤 때 Remote Control을 빼고 시작합니다."))
        if link.supervisor_running(home):
            print(T("  지금 켜져 있는 Claude Hand에는 적용되지 않습니다. 그 창에서 /remote-control 을 입력하거나, 닫고 다시 켜세요."))
    return 0


APPLIES = {"now": N_("바로 적용"), "start": N_("다음에 Claude Hand를 켤 때부터")}


def setting_words(found: dict, value) -> str:
    """A setting's value as the settings windows say it: in words, never by a colour."""
    kind = found.get("kind")
    if kind == "switch":
        return T("켜짐") if value else T("꺼짐")
    if kind == "choice":
        return next((str(c.get("label")) for c in found.get("choices") or [] if c.get("value") == value), str(value))
    if kind == "days":
        return T("{days}일", days=value) if value else T("지우지 않음")
    return str(value)


def setting_row(found: dict, words: str, lab: bool = False) -> str:
    """One setting in one line: its key (what the command takes), its value, its name, when a change holds."""
    when = T(APPLIES.get(found.get("applies"), APPLIES["start"]))
    when = T("{since}부터 · {when}", since=found.get("since", "?"), when=when) if lab else when
    return f"  {found['key']:<22} {words:<14} {pick(found.get('name'))}  [{when}]"  # 14: "never removed" and a space


def cmd_settings(args: argparse.Namespace) -> int:
    """The settings and the lab, as the gear button of a chat panel shows them: list them, or change one. They are in
    config.json, which every part reads where it uses a setting, so most changes hold at once (each row says)."""
    home = link.claude_hand_home()
    listed = prefs.catalog()
    if not listed["settings"]:
        print(T("설정 목록을 읽지 못했습니다: {path}", path=prefs.CATALOG_FILE))
        return 1
    config = prefs.live(home)
    lab = args.name == "lab"
    key, word = (args.value, args.more) if lab else (args.name, args.value)
    if key is None:
        if args.json:
            print(json.dumps({"settings": {e["key"]: prefs.get(e["key"], config) for e in listed["settings"]},
                              "lab": {e["key"]: prefs.lab_on(e["key"], config) for e in prefs.in_lab()}}, ensure_ascii=False, indent=2))
            return 0
        if not lab:
            print(T("설정  ({path})", path=prefs.config_path(home)))
            for found in listed["settings"]:
                print(setting_row(found, setting_words(found, prefs.get(found["key"], config))))
        print(T("실험실  (아직 다듬는 중인 기능입니다. 켜야 쓰입니다)"))
        for found in prefs.in_lab():
            print(setting_row(found, T("켜짐") if prefs.lab_on(found["key"], config) else T("꺼짐"), lab=True))
        if not prefs.in_lab():
            print(T("  지금 실험실에 있는 기능이 없습니다."))
        print(T("\n바꾸기: uv run tools/claude_hand.py settings <이름> <값>      실험실: settings lab <이름> on|off"))
        return 0
    found = prefs.lab_entry(key) if lab else prefs.entry(key)
    if found is None:
        print(T("'{name}'(이)라는 설정이 없습니다. `settings`만 치면 목록이 나옵니다.", name=key))
        return 1
    if lab and found.get("stage") == "stable":
        print(T("'{name}'은(는) 실험실을 나와 이제 기본 기능입니다. 켜고 끄지 않습니다.", name=pick(found.get("name"))))
        return 0
    if word is not None:
        try:
            value = prefs.parse({"kind": "switch"} if lab else found, word)
        except ValueError as exc:
            print(T("'{word}'은(는) 이 설정의 값이 아닙니다. 쓸 수 있는 값: {values}", word=word, values=exc))
            return 1
        try:
            config = (prefs.put_lab if lab else prefs.put)(key, value, home)
        except OSError as exc:  # another window had the file open just then, or the folder cannot be written
            print(T("설정을 적지 못했습니다: {exc}", exc=exc))
            return 1
    words = (T("켜짐") if prefs.lab_on(key, config) else T("꺼짐")) if lab else setting_words(found, prefs.get(key, config))
    print(setting_row(found, words, lab=lab).strip())
    print("  " + pick(found.get("hint")))
    if word is None:
        return 0
    if found.get("applies") != "now":
        print(T("다음에 Claude Hand를 켤 때부터 적용됩니다.") if not link.supervisor_running(home) else
              T("지금 켜져 있는 Claude Hand에는 적용되지 않습니다. 닫고 다시 켜면 적용됩니다."))
    if key in ("language", "english_prompts"):
        # What was written out in the other language: the subagents' definitions and the supervisor skill.
        try:
            written = agents.rewrite_installed(Path(args.agents_dir).expanduser(), Path(args.skills_dir).expanduser()).written
            if written:
                print(T("하위 세션 정의와 감독 스킬을 이 언어로 다시 썼습니다 ({count}개).", count=len(written)))
        except SystemExit as exc:
            print(T("하위 세션 정의를 다시 쓰지 못했습니다: {exc}", exc=exc))
    return 0


def cmd_shortcut(args: argparse.Namespace) -> int:
    if sys.platform == "win32":
        env = {**os.environ, "CH_NAME": constants.SHORTCUT_NAME, "CH_TARGET": str(constants.START_SCRIPT), "CH_DIR": str(constants.REPO_ROOT),
               "CH_STARTUP": "1" if args.startup else "0", "CH_REMOVE": "1" if args.remove else "0"}
        if args.dry_run:
            print(T("(dry run) 바탕화면과 시작 메뉴{startup}에 '{name}' 바로가기 {what} → {script}",
                    startup=T('와 시작프로그램') if args.startup else '', name=constants.SHORTCUT_NAME, what=T('삭제') if args.remove else T('만들기'), script=constants.START_SCRIPT))
            return 0
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", launch.SHORTCUT_PS], env=env,
                              capture_output=True, text=True, errors="replace", timeout=60)
        print((proc.stdout or proc.stderr).strip())
        return proc.returncode
    for path, text in launch.shortcut_files().items():
        if args.dry_run:
            print(f"(dry run) {T('삭제') if args.remove else T('만들기')}: {path}")
            continue
        if args.remove:
            path.unlink(missing_ok=True)
            print(f"removed {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        print(f"created {path}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Every open window and what goes on in it, as the chat channel knows it (the panels show the same of the other
    windows): at work and on what, waiting for an answer, stopped by a dialog, idle."""
    answer = link.channel_get("/v1/overview")
    if not answer or not answer.get("ok"):
        print(T("Claude Hand가 꺼져 있습니다. 켜져 있는 동안 열린 창들이 무엇을 하고 있는지 보여 줍니다."))
        return 1
    windows = answer.get("windows") or []
    if args.json:
        print(json.dumps(windows, ensure_ascii=False, indent=2))
        return 0
    if not windows:
        print(T("채팅창이 열려 있거나 일하고 있는 창이 없습니다."))
        return 0
    for line in overview.lines(windows, detail=True):
        print(line)
    return 0


def cmd_chat_status(args: argparse.Namespace) -> int:
    lines = chatsetup.chat_status_report()
    for line in lines:
        print(line)
    return 0 if any(line.startswith("OK ") for line in lines) and not any(line.startswith("!! ") for line in lines) else 1


def cmd_update(args: argparse.Namespace) -> int:
    """After `git pull`: bring an earlier install up to this version without losing what was set up."""
    prefix = "(dry run) " if args.dry_run else ""
    agents_dir = Path(args.agents_dir).expanduser()
    skills_dir = Path(args.skills_dir).expanduser()

    # 1. Subagents: re-render the ones that exist with the current templates, plus any newly open windows.
    known = [info.instance() for info in agents.installed_agents(agents_dir)]
    unity = [inst for inst in known if isinstance(inst, discovery.Instance)]
    blender = [inst for inst in known if isinstance(inst, discovery.BlenderInstance)]
    server = discovery.detect_server_config()
    live_unity = discovery.gather_instances(server, [], include_dead=False)
    live_blender = discovery.discover_blender(discovery.parse_ports(args.blender_ports))
    unity = list({i.id: i for i in [*unity, *live_unity]}.values())
    blender = list({b.port: b for b in [*blender, *live_blender]}.values())
    blender_server = discovery.detect_blender_config() if blender else None
    result = agents.sync_agents(unity, agents_dir, server, args.runner, prune=False, dry_run=args.dry_run,
                         blender=blender, blender_server=blender_server, notes_root=constants.REPO_ROOT)
    print(T("{prefix}하위 세션: 고침 {written}개, 그대로 {unchanged}개", prefix=prefix, written=len(result.written), unchanged=len(result.unchanged)))

    # 2. Supervisor skill (and the folder name the first version used).
    print(T("{prefix}감독 스킬: {path}", prefix=prefix, path=agents.install_skill(skills_dir, args.dry_run)))
    old_skill = skills_dir / "claude-hand-unity" / "SKILL.md"
    if old_skill.exists() and "name: claude-hand-unity" in old_skill.read_text(encoding="utf-8", errors="replace"):
        print(T("{prefix}지움: 예전 이름의 스킬 {parent}", prefix=prefix, parent=old_skill.parent))
        if not args.dry_run:
            shutil.rmtree(old_skill.parent)

    # 3. Chat: drop the earlier version's local registration (it wins over .mcp.json), then set up again.
    entry = chatsetup.claude_mcp_get()
    chat_used = (entry is not None and entry.exists) or (constants.REPO_ROOT / ".mcp.json").exists() or install.find_chat_addon_installs() \
        or any(link.read_chat_files())
    if entry is not None and entry.exists and entry.scope == "local":
        print(T("{prefix}지움: 이전 버전의 local 범위 claude-hand-chat 등록", prefix=prefix))
        if not args.dry_run:
            subprocess.run([shutil.which("claude") or "claude", "mcp", "remove", constants.CHAT_SERVER_NAME, "-s", "local"], cwd=str(constants.REPO_ROOT))
    if chat_used:
        code = cmd_chat_setup(argparse.Namespace(runner=args.runner, no_check=False, register_local=False, dry_run=args.dry_run))
        if code != 0:
            return code
        for target in sorted({p.parent for p in install.find_chat_addon_installs()}):
            installed = target / (constants.CHAT_ADDON_MODULE + ".py")
            if installed.read_bytes() != constants.CHAT_ADDON_SOURCE.read_bytes():
                print(T("{prefix}고침: Blender 채팅 애드온 {installed} (Blender를 다시 열어야 적용됩니다)", prefix=prefix, installed=installed))
                if not args.dry_run:
                    shutil.copy2(constants.CHAT_ADDON_SOURCE, installed)
        print(T("감독 세션을 열어 두었다면 닫고 start-supervisor.cmd로 다시 여세요."))
    else:
        print(T("채팅은 아직 설정하지 않았습니다(원하면 chat-setup)."))
    return 0


def cmd_unity_call(args: argparse.Namespace) -> int:
    """Run one ch_* tool in an open Unity project from the command line (setup, doctor, scripts)."""
    import unity_bridge
    params = {}
    for pair in args.param:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"parameters are key=value, got {pair!r}")
        try:
            params[key] = json.loads(value)
        except ValueError:
            params[key] = value
    locate = lambda: unity_bridge.find_project(name=args.project) or unity_bridge.find_project(hash_=args.project) \
        or unity_bridge.find_project(path=args.project)
    entry = locate()
    if entry is None:
        print(T("'{project}' Unity 프로젝트를 찾지 못했습니다. Unity로 열려 있고 Claude Hand Tools가 컴파일됐는지 확인하세요.", project=args.project))
        return 1
    meta = next((m for m in entry.get("tools", []) if m.get("name") == args.tool), None)
    if meta is None:
        print(T("{tool} 도구가 없습니다. 있는 도구: {tools}", tool=args.tool, tools=', '.join(m['name'] for m in entry.get('tools', []))))
        return 1
    try:
        result = unity_bridge.run_tool(locate, meta, params)
    except unity_bridge.BridgeError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message, "retry": exc.retry}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    started = time.time()
    progress = None if args.json else (lambda message: print("  " + message, file=sys.stderr, flush=True))
    results = checks.doctor_checks([Path(p).expanduser() for p in args.unity_project], discovery.parse_ports(args.blender_ports),
                           progress=progress, server_starts=checks.unity_server_starts, server_pinned=checks.unity_server_pinned)
    if args.json:
        print(json.dumps([asdict(c) for c in results], ensure_ascii=False, indent=2))
        return 0
    missing = checks.print_checks(results)
    print(T("\n빠진 항목 {missing}개", missing=missing) if missing else T("\n모두 준비됐습니다."))
    print(T("(진단 {seconds:.1f}초)", seconds=time.time() - started))
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    started = time.time()
    progress = None if args.json else (lambda message: print("  " + message, file=sys.stderr, flush=True))
    results = checks.selftest_checks(discovery.parse_ports(args.blender_ports), run_tests=not args.no_tests, progress=progress,
                                     unity_projects=[Path(p).expanduser().resolve() for p in args.unity_project])
    if args.json:
        print(json.dumps([asdict(c) for c in results], ensure_ascii=False, indent=2))
    else:
        failed = checks.print_checks(results)
        print(T("\n문제 {failed}개", failed=failed) if failed else T("\n모두 통과했습니다."))
        print(T("({seconds:.1f}초)", seconds=time.time() - started))
    return 1 if any(c.ok is False for c in results) else 0


def cmd_unity_install(args: argparse.Namespace) -> int:
    project = Path(args.project).expanduser().resolve()
    plan, manifest = install.plan_unity_install(project, mcp_for_unity=not args.no_mcp_for_unity, claude_hand=not args.no_claude_hand,
                                                pin_mcp_for_unity=args.pin_mcp_for_unity,
                                                source="release" if args.release else "folder" if args.folder else "auto")
    for note in plan.notes:
        print(T("그대로: {note}", note=note))
    if manifest is None:
        print(T("바꿀 것이 없습니다."))
        return 0
    for change in plan.changes:
        print(T("{dry}변경: {change}", dry='(dry run) ' if args.dry_run else '', change=change))
    if args.dry_run:
        return 0
    install.apply_unity_install(project, manifest, plan)
    print(T("Packages/manifest.json을 고쳤습니다. 원래 파일: {backup}", backup=plan.backup))
    print(T("Unity가 열려 있으면 Unity 창이 앞에 올 때 패키지를 받아 옵니다. 닫혀 있으면 unity-open으로 여세요."))
    return 0


def cmd_unity_open(args: argparse.Namespace) -> int:
    project = Path(args.project).expanduser().resolve()
    if not install.is_unity_project(project):
        raise SystemExit(f"{project} is not a Unity project.")
    version = install.read_project_version(project)
    exe = Path(args.unity_exe) if args.unity_exe else (install.find_unity_editor(version) if version else None)
    if exe is None or not exe.exists():
        print(T("Unity {version} 에디터를 찾지 못했습니다. --unity-exe로 Unity.exe 경로를 알려주거나 Unity Hub로 여세요.", version=version or '?'))
        return 1
    cmd = [str(exe), "-projectPath", str(project)]
    print(("(dry run) " if args.dry_run else T("실행: ")) + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    if not args.dry_run:
        install.launch_detached(cmd)
    return 0


def cmd_blender_open(args: argparse.Namespace) -> int:
    port = int(args.port)
    if install.blender_answers(port):
        print(T("포트 {port}은(는) 이미 다른 Blender가 쓰고 있습니다. 다른 포트를 고르세요.", port=port))
        return 1
    blend = Path(args.file).expanduser().resolve() if args.file else None
    if blend is not None and not blend.exists():
        raise SystemExit(f"{blend} does not exist.")
    exes = [Path(args.blender_exe)] if args.blender_exe else install.find_blender_executables()
    if not exes or not exes[0].exists():
        print(T("Blender 실행 파일을 찾지 못했습니다. --blender-exe로 blender.exe 경로를 알려주세요."))
        return 1
    cmd = install.blender_open_command(exes[0], blend, port)
    print(("(dry run) " if args.dry_run else T("실행: ")) + T("{blender} {file} (포트 {port})", blender=exes[0], file=blend or T('(새 파일)'), port=port))
    if args.dry_run:
        return 0
    install.launch_detached(cmd)
    deadline = time.time() + args.wait
    while time.time() < deadline:
        if install.blender_answers(port):
            print(T("Blender가 포트 {port}에서 응답합니다. 이 포트를 파일에 남기려면 .blend를 저장하세요.", port=port))
            return 0
        time.sleep(1)
    print(T("{wait}초 안에 포트 {port}에서 응답이 없습니다. 애드온이 켜져 있는지(uvx mcp-for-blender setup) 확인하세요.", wait=args.wait, port=port))
    return 1


def cmd_list(args: argparse.Namespace) -> int:
    server = discovery.detect_server_config()
    instances = discovery.gather_instances(server, args.instance, include_dead=True)
    blender = discovery.gather_blender(args)
    print(T("Unity MCP 설정: {transport} ({origin})", transport=server.transport, origin=server.origin))
    if instances:
        names = agents.agent_names(instances)
        for inst in instances:
            state = T("실행 중") if inst.alive else T("응답 없음")
            print(f"  {names[inst.id]:<28} {inst.id:<36} {state:<6} {inst.unity_version} {inst.path}")
    else:
        print(T("  열려 있는 Unity 프로젝트 없음 (Unity에서 Window > MCP for Unity 상태가 Connected인지 확인)"))
    if not getattr(args, "no_blender", False):
        print(T("Blender (MCP for Blender, 포트 {blender_ports}):", blender_ports=args.blender_ports))
        if blender:
            names = agents.blender_agent_names(blender)
            for inst in blender:
                print(T("  {name:<28} 포트 {port:<6} {version:<8} {file}",
                        name=names[inst.id], port=inst.port, version=inst.blender_version, file=inst.filepath or T('(저장 안 된 파일)')))
        else:
            print(T("  응답한 Blender 없음 (3D 뷰포트 N 패널 > MCP for Blender 탭에서 서버가 켜져 있는지 확인)"))
    return 0 if instances or blender else 1


def cmd_sync(args: argparse.Namespace) -> int:
    server = discovery.detect_server_config()
    instances = discovery.gather_instances(server, args.instance, include_dead=args.include_dead)
    blender = discovery.gather_blender(args)
    if not instances and not blender:
        print(T("열려 있는 Unity 프로젝트도, 응답하는 Blender도 없습니다. 하위 세션을 만들 창을 먼저 여세요."))
        return 1
    blender_server = discovery.detect_blender_config() if blender else None
    agents_dir = Path(args.agents_dir).expanduser()
    result = agents.sync_agents(instances, agents_dir, server, args.runner, allowlist=not args.no_allowlist,
                         focus_nudge=args.keep_focus_nudge, prune=args.prune, dry_run=args.dry_run,
                         blender=blender, blender_server=blender_server, notes_root=constants.REPO_ROOT)
    prefix = "(dry run) " if args.dry_run else ""
    if args.allow_tools:
        keys = [agents.server_key(Path(name).stem) for name in agents.build_agent_files(instances, server, blender, blender_server, args.runner)]
        allow_plan = settings.plan_tool_allow(constants.REPO_ROOT, keys, prune=args.prune)
        for path in allow_plan:
            print(T("{prefix}도구 자동 허용: {path} ({rules})", prefix=prefix, path=path, rules=', '.join(f'mcp__{k}__*' for k in keys)))
        if not args.dry_run:
            settings.write_json_files(allow_plan)
    print(T("{prefix}Unity MCP 설정: {transport} ({origin})", prefix=prefix, transport=server.transport, origin=server.origin))
    if blender_server:
        print(T("{prefix}Blender MCP 설정: {command} {args} ({origin})",
                prefix=prefix, command=blender_server.command, args=' '.join(blender_server.args), origin=blender_server.origin))
        if blender_server.warning:
            print(T("{prefix}주의: {warning}", prefix=prefix, warning=blender_server.warning))
    for p in result.written:
        print(T("{prefix}작성: {p}", prefix=prefix, p=p))
    for p in result.unchanged:
        print(T("{prefix}그대로: {p}", prefix=prefix, p=p))
    for p in result.removed:
        print(T("{prefix}삭제: {p}", prefix=prefix, p=p))
    if not args.no_skill:
        print(T("{prefix}스킬: {path}", prefix=prefix, path=agents.install_skill(Path(args.skills_dir).expanduser(), args.dry_run)))
    print(T("Claude Code에서 /agents 로 unity-* / blender-* 하위 세션이 보이는지 확인하세요. agents 폴더를 처음 만든 경우에만 Claude Code를 다시 시작하면 됩니다."))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude_hand", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"claude-hand {constants.VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="show Unity editors MCP for Unity can see")
    p_list.add_argument("--instance", action="append", default=[], metavar="NAME@HASH", help="add an editor by hand")
    p_list.add_argument("--blender-ports", default=constants.DEFAULT_BLENDER_PORTS, help="ports to probe for MCP for Blender (default %(default)s)")
    p_list.add_argument("--no-blender", action="store_true", help="skip Blender")
    p_list.set_defaults(func=cmd_list)

    p_sync = sub.add_parser("sync", help="write one subagent per Unity editor (+ the supervisor skill)")
    p_sync.add_argument("--agents-dir", default=str(Path.home() / ".claude" / "agents"))
    p_sync.add_argument("--skills-dir", default=str(Path.home() / ".claude" / "skills"))
    p_sync.add_argument("--instance", action="append", default=[], metavar="NAME@HASH", help="add an editor by hand")
    p_sync.add_argument("--include-dead", action="store_true", help="also write agents for editors that do not answer")
    p_sync.add_argument("--prune", action="store_true", help="delete generated unity-*.md for editors that are gone")
    p_sync.add_argument("--runner", default="uv run -q --no-project", help="how the guard hook starts Python")
    p_sync.add_argument("--no-allowlist", action="store_true", help="use disallowedTools instead of a tools allowlist")
    p_sync.add_argument("--keep-focus-nudge", action="store_true", help="let MCP for Unity pull Unity to the front during tests")
    p_sync.add_argument("--no-skill", action="store_true", help="do not install the supervisor skill")
    p_sync.add_argument("--blender-ports", default=constants.DEFAULT_BLENDER_PORTS, help="ports to probe for MCP for Blender (default %(default)s)")
    p_sync.add_argument("--no-blender", action="store_true", help="skip Blender")
    p_sync.add_argument("--allow-tools", action="store_true",
                        help="let the subagents use their Unity/Blender tools without asking (this folder's settings.local.json)")
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    p_doctor = sub.add_parser("doctor", help="check what is installed and configured")
    p_doctor.add_argument("--unity-project", action="append", default=[], metavar="PATH", help="also check this Unity project")
    p_doctor.add_argument("--blender-ports", default=constants.DEFAULT_BLENDER_PORTS)
    p_doctor.add_argument("--json", action="store_true", help="machine-readable output")
    p_doctor.set_defaults(func=cmd_doctor)

    p_st = sub.add_parser("selftest", help="run the tests and check that the open Unity/Blender windows and the chat answer "
                                           "with this version (changes nothing)")
    p_st.add_argument("--no-tests", action="store_true", help="skip the Python and in-Blender test suites")
    p_st.add_argument("--unity-project", action="append", default=[], metavar="PATH",
                      help="also open this (closed) Unity project without a window and run Claude Hand Tools' self-test in it: "
                           "a model comes in, is replaced in place, and goes back out. Takes a minute or two; the test "
                           "deletes what it made")
    p_st.add_argument("--blender-ports", default=constants.DEFAULT_BLENDER_PORTS)
    p_st.add_argument("--json", action="store_true", help="machine-readable output")
    p_st.set_defaults(func=cmd_selftest)

    p_ui = sub.add_parser("unity-install", help="add MCP for Unity + Claude Hand Tools to a project's manifest")
    p_ui.add_argument("project")
    p_ui.add_argument("--no-mcp-for-unity", action="store_true", help="only add Claude Hand Tools")
    p_ui.add_argument("--no-claude-hand", action="store_true", help="only add MCP for Unity")
    p_ui.add_argument("--pin-mcp-for-unity", action="store_true",
                      help=f"move an MCP for Unity that follows a branch (#main) to {constants.MCP_FOR_UNITY_TESTED}, the tested release")
    how = p_ui.add_mutually_exclusive_group()
    how.add_argument("--release", action="store_true",
                     help=f"take Claude Hand Tools from GitHub at v{constants.VERSION} (default when this checkout is that release)")
    how.add_argument("--folder", action="store_true",
                     help="take Claude Hand Tools from this folder (default otherwise; the project then opens on this PC only)")
    p_ui.add_argument("--dry-run", action="store_true")
    p_ui.set_defaults(func=cmd_unity_install)

    p_uo = sub.add_parser("unity-open", help="open a project with the Unity version it was made with")
    p_uo.add_argument("project")
    p_uo.add_argument("--unity-exe", help="path to Unity.exe when it is not in Unity Hub's default folder")
    p_uo.add_argument("--dry-run", action="store_true")
    p_uo.set_defaults(func=cmd_unity_open)

    p_bo = sub.add_parser("blender-open", help="start Blender with the MCP for Blender add-on on a chosen port")
    p_bo.add_argument("file", nargs="?", help=".blend file to open (default: a new file)")
    p_bo.add_argument("--port", required=True, type=int)
    p_bo.add_argument("--blender-exe", help="path to blender.exe")
    p_bo.add_argument("--wait", type=int, default=60, help="seconds to wait for the add-on to answer")
    p_bo.add_argument("--dry-run", action="store_true")
    p_bo.set_defaults(func=cmd_blender_open)

    p_cs = sub.add_parser("chat-setup", help="register the chat channel for this folder and pre-start it once")
    p_cs.add_argument("--runner", default="uv run -q --no-project", help="how Claude Code starts Python")
    p_cs.add_argument("--no-check", action="store_true", help="skip the pre-start check")
    p_cs.add_argument("--register-local", action="store_true",
                      help="fallback: register with `claude mcp add --scope local` instead of .mcp.json")
    p_cs.add_argument("--dry-run", action="store_true")
    p_cs.set_defaults(func=cmd_chat_setup)

    p_bic = sub.add_parser("blender-install-chat", help="install and enable the Blender chat panel add-on")
    p_bic.add_argument("--addons-dir", help="Blender scripts/addons folder to install into")
    p_bic.add_argument("--blender-exe", help="path to blender.exe used to enable the add-on")
    p_bic.add_argument("--dry-run", action="store_true")
    p_bic.set_defaults(func=cmd_blender_install_chat)

    p_pre = sub.add_parser("preflight", help="run by start-supervisor before Claude Code starts")
    p_pre.add_argument("--runner", default="uv run -q --no-project")
    p_pre.add_argument("--blender-ports", default=constants.DEFAULT_BLENDER_PORTS)
    p_pre.set_defaults(func=cmd_preflight)

    p_ce = sub.add_parser("clean-exchange", help="delete old transfer files from the exchange folder (every start does this too)")
    p_ce.add_argument("--days", type=int, help="delete files older than this many days now (default: the kept setting)")
    p_ce.add_argument("--keep-days", type=int, help=f"how long every start keeps them from now on (default {launch.EXCHANGE_KEEP_DAYS}; 0: never clean)")
    p_ce.add_argument("--dry-run", action="store_true")
    p_ce.set_defaults(func=cmd_clean_exchange)

    p_tr = sub.add_parser("trace", help="show what happened to a chat panel request, step by step (from the event log)")
    p_tr.add_argument("chat", nargs="?", help="a chat (subagent) name such as unity-hand-test: its last request. Leave out to list the requests")
    p_tr.add_argument("--request", help="a request id from the list")
    p_tr.add_argument("--days", type=int, default=2, help="how many days of the log to read (default %(default)s)")
    p_tr.add_argument("--last", type=int, default=20, help="how many requests the list shows (default %(default)s)")
    p_tr.add_argument("--json", action="store_true", help="machine-readable output")
    p_tr.set_defaults(func=cmd_trace)

    p_us = sub.add_parser("usage", help="show what the chat panel requests took: time, actions, tokens (from the event log)")
    p_us.add_argument("--days", type=int, default=2, help="how many days of the log to read (default %(default)s)")
    p_us.add_argument("--last", type=int, default=20, help="how many requests the list shows (default %(default)s); the sums are of all")
    p_us.add_argument("--json", action="store_true", help="machine-readable output")
    p_us.set_defaults(func=cmd_usage)

    p_mode = sub.add_parser("mode", help="(earlier versions) there is one mode now; clears an old setting")
    p_mode.add_argument("mode", nargs="?")
    p_mode.set_defaults(func=cmd_mode)

    p_start = sub.add_parser("start", help="open the Claude Hand window (the supervisor session)")
    p_start.add_argument("--dry-run", action="store_true")
    p_start.set_defaults(func=cmd_start)

    p_ns = sub.add_parser("new-session", help="start the next Claude Hand as a new conversation (it resumes the same one otherwise)")
    p_ns.set_defaults(func=cmd_new_session)

    p_min = sub.add_parser("minimize", help="minimize the Claude Hand window once it is in use (Windows)")
    p_min.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    p_min.set_defaults(func=cmd_minimize)

    p_rc = sub.add_parser("remote-control", help="show the supervisor in the Claude desktop app (Remote Control)")
    p_rc.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    p_rc.set_defaults(func=cmd_remote_control)

    p_set = sub.add_parser("settings", help="show or change the settings and the lab (what the gear button of a chat panel shows): "
                                            "`settings`, `settings language en`, `settings show_cost off`, `settings lab english_prompts on`")
    p_set.add_argument("name", nargs="?", help="a setting's key from the list, or `lab` for the lab's features")
    p_set.add_argument("value", nargs="?", help="on | off, a language (ko | en), or a number of days; after `lab`: a feature's key")
    p_set.add_argument("more", nargs="?", help="after `lab <feature>`: on | off")
    p_set.add_argument("--json", action="store_true", help="machine-readable output (of the list)")
    p_set.add_argument("--agents-dir", default=str(Path.home() / ".claude" / "agents"))
    p_set.add_argument("--skills-dir", default=str(Path.home() / ".claude" / "skills"))
    p_set.set_defaults(func=cmd_settings)

    p_sc = sub.add_parser("shortcut", help='put a "Claude Hand" icon on the Desktop and in the Start menu')
    p_sc.add_argument("--startup", action="store_true", help="also start Claude Hand when Windows starts")
    p_sc.add_argument("--remove", action="store_true")
    p_sc.add_argument("--dry-run", action="store_true")
    p_sc.set_defaults(func=cmd_shortcut)

    p_uc = sub.add_parser("unity-call", help="run one ch_* tool in an open Unity project, e.g. ch_apply_settings action=apply")
    p_uc.add_argument("project", help="project name, hash or folder")
    p_uc.add_argument("tool")
    p_uc.add_argument("param", nargs="*", help="key=value (values are JSON when they parse, else text)")
    p_uc.set_defaults(func=cmd_unity_call)

    p_status = sub.add_parser("status", help="show every open window and what goes on in it (working, waiting for an answer, "
                                             "stopped by a dialog, idle)")
    p_status.add_argument("--json", action="store_true", help="machine-readable output")
    p_status.set_defaults(func=cmd_status)

    p_cst = sub.add_parser("chat-status", help="explain why a chat panel does or does not reach the supervisor")
    p_cst.set_defaults(func=cmd_chat_status)

    p_up = sub.add_parser("update", help="after git pull: refresh subagents, skill, chat setup and the Blender add-on")
    p_up.add_argument("--agents-dir", default=str(Path.home() / ".claude" / "agents"))
    p_up.add_argument("--skills-dir", default=str(Path.home() / ".claude" / "skills"))
    p_up.add_argument("--blender-ports", default=constants.DEFAULT_BLENDER_PORTS)
    p_up.add_argument("--runner", default="uv run -q --no-project")
    p_up.add_argument("--dry-run", action="store_true")
    p_up.set_defaults(func=cmd_update)

    p_guard = sub.add_parser("guard", help="PreToolUse hook as subagent files made before tools/guard.py call it")
    guard.add_arguments(p_guard)
    p_guard.set_defaults(func=guard.run_guard)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Korean output in Windows consoles
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
