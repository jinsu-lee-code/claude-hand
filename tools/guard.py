#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""claude-hand guard: the PreToolUse hook of every unity-* / blender-* subagent.

  guard.py --pin "MyGame@aaaa1111" --server unity_mygame --transport stdio --app unity     (hook input on stdin)

It refuses any call that would reach a project or Blender other than the subagent's own, any Unity call while a
dialog stops that editor, and any call while another subagent is still at work in the same window. For a call it
lets through it also tells the chat channel what the subagent is about to do (the panel's work line) and, on the
first action of a request, sets an undo point in that window. In a Unity project it writes down which request is at
work and copies the files the call names, so that undoing the request puts them back (handlib/keeps.py). A call it
refuses goes into the event log.

  guard.py --pin ... --server ... --app ... --stop       the same subagents' Stop hook: tells the chat channel the
                                                         subagent finished its turn. Never refuses anything.
  guard.py --pin ... --server ... --app ... --after      their PostToolUse hook: asks the window what its protection
                                                         rules found once the call has run, and tells the subagent.

Before a call it also holds the call against the window's protection rules (handlib/protect.py): what the user said
must not be changed there. A call that shows in its own words that it would is refused; the rest is the window's to
catch, which is what --after asks about.

Whatever goes wrong, the call is refused. Claude Code reads a hook's result like this:
  exit 0 with a "deny" decision on stdout   the call is refused, with the reason
  exit 0 with nothing on stdout             the call goes ahead
  exit 2                                    the call is refused
  any other exit code                       a hook error that does NOT stop the call
So as the PreToolUse hook this process only ever ends with 0 or 2: bad arguments and any error while deciding
refuse the call. (As the Stop hook it always ends with 0: there, 2 would keep the subagent from stopping.) That is
also why the guard lives in a small file of its own with nothing but the standard library at import time: a
mistake elsewhere in claude-hand cannot make it end with another code. What comes after the decision (work line,
undo point, kept files) is best effort and loads its helpers only then.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------- what the guard says, and in which language

# The Korean text stays where it is said; with the language set to English (the settings window writes "language" into
# config.json) T looks it up here. The guard carries its own few texts and its own reader of that file, standard
# library only like everything before the decision: nothing elsewhere in claude-hand can keep a refusal from being said.
EN = {
    "이 하위 세션은 {pin} 전용입니다. {what} 도구는 {prefix}* 만 쓸 수 있습니다.":
        "This subagent works for {pin} only. Of the {what} tools it may use {prefix}* alone.",
    "대상 프로젝트는 {pin}로 고정돼 있어 바꿀 수 없습니다.": "The target project is fixed to {pin} and cannot be changed.",
    "unity_instance={value!r}는 이 하위 세션의 프로젝트({pin})가 아닙니다. unity_instance 없이 호출하세요.":
        "unity_instance={value!r} is not this subagent's project ({pin}). Call without unity_instance.",
    "Unity가 대화상자 때문에 멈춰 있어 이 호출을 막았습니다: {dialogs}. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요. 어느 버튼을 누를지는 사용자가 정합니다.":
        "Unity is stopped at a dialog, so this call was refused: {dialogs}. Make no more calls and report this as it is to the "
        "supervisor. Which button to press is the user's decision.",
    "Blender가 보호 규칙을 알려 주지 않았습니다: ": "Blender did not tell its protection rules: ",
    "Blender가 보호 규칙을 알 수 없는 모양으로 알려 주었습니다": "Blender told its protection rules in a form that cannot be read",
    "ch_protect의 before · after는 Claude Hand의 가드가 부릅니다. 직접 부르지 마세요(state는 됩니다).":
        "ch_protect's before and after are called by Claude Hand's guard. Do not call them yourself (state is fine).",
    "보호 규칙 파일을 읽지 못해 이 호출을 막았습니다: {problem}. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요. 그 파일은 사용자가 고쳐야 합니다.":
        "The protection rules file could not be read, so this call was refused: {problem}. Make no more calls and report this as "
        "it is to the supervisor. The user has to fix that file.",
    "사용자가 채팅창에서 [중지]를 눌러 이 호출을 막았습니다. 더 부르지 말고, 지금까지 한 것과 남은 것을 그대로 "
    "감독에게 보고하고 끝내세요. 한 것을 되돌리지는 마세요(되돌릴지는 사용자가 정합니다).":
        "The user pressed [Stop] in the chat panel, so this call was refused. Make no more calls: report to the supervisor what "
        "was done so far and what is left, and finish. Do not undo what was done (whether to undo is the user's decision).",
    "이 창({pin})은 지금 다른 하위 세션이 작업 중이라 이 호출을 막았습니다. 같은 대상에 둘이 동시에 일하면 서로 덮어씁니다. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요.":
        "Another subagent is at work in this window ({pin}) right now, so this call was refused. Two working on the same target at "
        "once overwrite each other. Make no more calls and report this as it is to the supervisor.",
    "이 요청의 되돌리기 지점은 Claude Hand가 이미 찍어 두었습니다(채팅창의 [되돌리기]). 따로 찍지 않아도 되니 "
    "이 호출 없이 하던 일을 이어서 하세요. 문제가 아니므로 보고에 적지 않아도 됩니다.":
        "Claude Hand has already set this request's undo point (the chat panel's [Undo]). There is no need to set another: carry "
        "on with your work without this call. This is not a problem and need not be in your report.",
    "claude-hand guard 오류로 호출을 막았습니다: {exc}": "The call was refused because of an error in the claude-hand guard: {exc}",
    "보호 규칙을 확인하지 못해 이 호출을 막았습니다: {exc}. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요.":
        "The protection rules could not be checked, so this call was refused: {exc}. Make no more calls and report this as it is "
        "to the supervisor.",
}


def language() -> str:
    """"ko" or "en": CLAUDE_HAND_LANG when it is one of the two, else config.json's "language". Korean whatever goes
    wrong (no file, a file that is being written, anything)."""
    try:
        import os
        forced = os.environ.get("CLAUDE_HAND_LANG", "").strip().lower()
        if forced in ("ko", "en"):
            return forced
        home = Path(os.environ.get("CLAUDE_HAND_HOME") or Path.home() / ".claude-hand")
        value = json.loads((home / "config.json").read_text(encoding="utf-8-sig")).get("language")
        return value if value in ("ko", "en") else "ko"
    except Exception:
        return "ko"


def T(text: str, /, **values) -> str:
    """`text` as the user's language has it (Korean as written, or English from EN), its {names} filled in."""
    if language() == "en":
        text = EN.get(text, text)
    return text.format(**values) if values else text


# --------------------------------------------------------------------------- the decision


def instance_matches(value: str, pin: str) -> bool:
    """Accept only identifiers that MCP for Unity would resolve to the pinned editor."""
    value = str(value).strip()
    name, _, hash_ = pin.partition("@")
    if value in (pin, name, hash_):
        return True
    if "@" in value:
        v_name, _, v_hint = value.partition("@")
        return v_name == name and len(v_hint) >= 4 and hash_.startswith(v_hint)
    return len(value) >= 4 and hash_.startswith(value) and not value.isdigit()


def find_instance_values(obj) -> list[str]:
    """Every unity_instance value anywhere in the tool input (batch_execute nests commands)."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "unity_instance" and v not in (None, ""):
                found.append(str(v))
            else:
                found += find_instance_values(v)
    elif isinstance(obj, list):
        for item in obj:
            found += find_instance_values(item)
    return found


def guard_decision(event: dict, pin: str, server: str, transport: str, app: str = "unity") -> str | None:
    """Return a denial reason, or None to let the call through."""
    tool = str(event.get("tool_name", ""))
    if not tool.startswith("mcp__"):
        return None
    prefix = f"mcp__{server}__"
    if app == "unity" and tool.startswith(f"mcp__{server}_ch__"):
        return None  # claude-hand's own ch_* server: pinned to this project by its arguments, takes no unity_instance
    if not tool.startswith(prefix):
        what = "Blender" if app == "blender" else "Unity"
        return T("이 하위 세션은 {pin} 전용입니다. {what} 도구는 {prefix}* 만 쓸 수 있습니다.", pin=pin, what=what, prefix=prefix)
    if app == "blender":
        return None  # one MCP for Blender server talks to exactly one port, so its own tools cannot reach another Blender
    short = tool[len(prefix):]
    tool_input = event.get("tool_input") or {}
    if short == "set_active_instance":
        target = str(tool_input.get("instance", ""))
        if transport == "http" and instance_matches(target, pin):
            return None
        return T("대상 프로젝트는 {pin}로 고정돼 있어 바꿀 수 없습니다.", pin=pin)
    for value in find_instance_values(tool_input):
        if not instance_matches(value, pin):
            return T("unity_instance={value!r}는 이 하위 세션의 프로젝트({pin})가 아닙니다. unity_instance 없이 호출하세요.", value=value, pin=pin)
    return None


def dialog_block(event: dict, pin: str, server: str, waiting=None) -> str | None:
    """A Unity call while a dialog stops that editor would only wait for its timeout: refuse it with what the dialog
    says, so the subagent reports it at once. Detection is best effort: if it fails, the call goes ahead."""
    tool = str(event.get("tool_name", ""))
    if not tool.startswith((f"mcp__{server}__", f"mcp__{server}_ch__")):
        return None
    try:
        import unity_bridge
        dialogs = (waiting or (lambda: unity_bridge.waiting_dialogs(unity_bridge.find_project(hash_=pin.rpartition("@")[2]))))()
        if not dialogs:
            return None
        return T("Unity가 대화상자 때문에 멈춰 있어 이 호출을 막았습니다: {dialogs}. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요. 어느 버튼을 누를지는 사용자가 정합니다.",
                 dialogs=unity_bridge.describe_dialogs(dialogs))
    except Exception:
        return None


# --------------------------------------------------------------------------- after the decision (best effort)


def action_summary(tool_input: dict) -> str:
    """A few words on what a Unity/Blender tool call does, for the panel's work line ("create Cube")."""
    parts = [str(tool_input[key]).strip() for key in ("action", "name", "target", "search_term", "tool_name", "path")
             if isinstance(tool_input.get(key), (str, int, float)) and str(tool_input[key]).strip()]
    code = tool_input.get("code")
    if not parts and isinstance(code, str):  # execute_code: its first statement
        parts = [next((line.strip() for line in code.splitlines()
                       if line.strip() and not line.strip().startswith(("#", "//", "import ", "from ", "using "))), "")]
    return " ".join(parts)[:120]


def only_looks(app: str, tool_input: dict) -> bool:
    """Blender code that can only look (handlib.looks reads it): it needs no undo point and leaves the window as it
    was. Exporting for Unity with the chat add-on counts as looking there. False for anything else, and for Unity,
    whose own package tells afterwards whether a point has anything to undo (ChCheckpoint)."""
    code = tool_input.get("code")
    if app != "blender" or not isinstance(code, str):
        return False
    try:
        from handlib import looks
        return looks.python_only_looks(code)
    except Exception:
        return False


def post_to_channel(path: str, body: dict, home: Path | None = None, post=None) -> dict:
    """POST to the chat channel of the running supervisor; {} when there is none. Waits half a second at most."""
    from handlib import link
    info, _ = link.read_chat_files((home or link.claude_hand_home()) / "chat")
    if not info:
        return {}
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"X-Claude-Hand-Token": info.get("token", ""), "Content-Type": "application/json"})
    return (post or (lambda r: json.loads(urllib.request.urlopen(r, timeout=0.5).read() or b"{}")))(req) or {}


def project_folder(pin: str) -> Path | None:
    """Where the subagent's Unity project lies, as its editor registered it. None while that editor is not open."""
    import unity_bridge
    path = (unity_bridge.find_project(hash_=pin.rpartition("@")[2]) or {}).get("project_path")
    return Path(path) if path else None


def work_begins(app: str, pin: str, reply: dict, fresh: bool, folder=None) -> tuple[Path | None, str]:
    """Write down, in the Unity project itself, which request this subagent is about to work for (handlib/keeps.py):
    Claude Hand Tools counts what happens to files meanwhile as that request's doing, and an undo takes it back.
    The chat channel names the request (point); without Claude Hand the work that goes on is one request until it
    pauses or the subagent sets an undo point of its own (fresh). Returns (the project folder, the request's name);
    the folder is None for Blender, a project that is not open, or when anything goes wrong."""
    key = str(reply.get("point") or "")
    if app != "unity":
        return None, key
    try:
        from handlib import keeps
        project = (folder or project_folder)(pin)
        if project is None:
            return None, key
        key = key or keeps.local_key(keeps.read_work(project), time.time(), fresh)
        keeps.begin_work(project, key)
        return project, key
    except Exception:
        return None, key


def work_ends(app: str, pin: str, folder=None) -> None:
    """The subagent finished its turn: what happens to the project's files from now on is the user's own. Best effort."""
    if app != "unity":
        return
    try:
        from handlib import keeps
        project = (folder or project_folder)(pin)
        if project is not None:
            keeps.end_work(project)
    except Exception:
        pass


def keep_named_files(project: Path | None, key: str, tool: str, tool_input: dict) -> None:
    """Just before a call that may write files: copy the ones its input names, so that undoing the request can put
    them back (MCP for Unity writes a script or a shader directly, and Unity learns of it when the old one is gone).
    Not for claude-hand's own ch_* tools, which keep what they replace themselves, nor for tools that only look.
    Best effort: the call goes ahead whatever happens here."""
    if project is None or not key or tool.startswith("ch_"):
        return
    try:
        from handlib import keeps, looks
        if not looks.tool_only_looks(tool):
            keeps.keep(project, key, keeps.named_files(project, tool_input))
    except Exception:
        pass


def short_name(event: dict, server: str) -> str:
    """A tool of this subagent's own servers without its prefix ("manage_asset", "ch_checkpoint"); "" for any other."""
    tool = str(event.get("tool_name", ""))
    return next((tool[len(prefix):] for prefix in (f"mcp__{server}_ch__", f"mcp__{server}__") if tool.startswith(prefix)), "")


def call_looks(app: str, short: str, tool_input: dict) -> bool:
    """The call leaves its window as it was: by the tool's name, or (Blender code) by what the code is made of."""
    if only_looks(app, tool_input):
        return True
    try:
        from handlib import looks
        return looks.tool_only_looks(short, str(tool_input.get("action") or ""))
    except Exception:
        return False


# --------------------------------------------------------------------------- protection rules (handlib/protect.py)

# What the guard asks a Blender window: its rules as its add-on reads them (it knows its own file, who is in a
# collection and who is whose child), and with before=True the protected objects as they are now, to compare after
# the call. An add-on from before there were rules only says which file is open; the guard then reads the file's rules.
BLENDER_ASKS = ("import bpy, json\n"
                "try:\n"
                "    import claude_hand_chat\n"
                "    state = claude_hand_chat.{call}\n"
                "except Exception as error:\n"
                "    state = {{'file': bpy.data.filepath, 'error': repr(error)}}\n"
                "print('CH_PROTECT ' + json.dumps(state))")


def ask_blender(pin: str, call: str, request=None, timeout: float = 5.0) -> dict | None:
    """What the chat add-on of the subagent's Blender answers to `call` (BLENDER_ASKS); None when that Blender is not
    there to ask (the call the subagent is about to make will not get there either)."""
    from handlib import link
    try:
        reply = (request or link.blender_request)(int(pin.rpartition("@")[2]),
                                                  {"type": "execute_code", "params": {"code": BLENDER_ASKS.format(call=call)}}, timeout)
    except ConnectionError:
        return None
    printed = reply.get("result") if isinstance(reply, dict) else None  # {"executed": True, "result": what the code printed}
    printed = printed.get("result") if isinstance(printed, dict) else printed
    start = str(printed or "").find("CH_PROTECT ")
    if start < 0:
        raise ValueError(T("Blender가 보호 규칙을 알려 주지 않았습니다: ") + str(reply)[:200])
    state = json.loads(str(printed)[start + len("CH_PROTECT "):].splitlines()[0])
    if not isinstance(state, dict):
        raise ValueError(T("Blender가 보호 규칙을 알 수 없는 모양으로 알려 주었습니다"))
    return state


def ask_unity(pin: str, action: str, request=None, timeout: float = 8.0) -> dict | None:
    """What Claude Hand Tools in the subagent's project answers to ch_protect `action`; None when that editor cannot
    be asked just now (closed, or compiling: what it refuses itself, it refuses all the same)."""
    import unity_bridge
    entry = unity_bridge.find_project(hash_=pin.rpartition("@")[2])
    if not entry:
        return None
    try:
        result = (request or unity_bridge.request)(entry, "ch_protect", {"action": action}, timeout=timeout) or {}
    except Exception as exc:
        if getattr(exc, "code", "") in ("UNREACHABLE", "NOT_RUNNING", "EDITOR_BUSY", "DIALOG", "NOT_FOUND"):  # NOT_FOUND: a package from before
            return None
        raise
    data = result.get("data")
    return data if result.get("success") is True and isinstance(data, dict) else None


def protection_block(event: dict, pin: str, server: str, app: str, home: Path | None = None, folder=None, ask=None) -> tuple[str | None, bool]:
    """(why the window's protection rules do not let this call through, or None; whether the window has to be asked
    again after the call). The rules are the user's (handlib/protect.py): this window may only be looked at, these
    assets and objects are not to be changed, these actions are not to be done. What shows in the call's words is
    refused here; what does not, the window catches itself (see protection_after). Anything that goes wrong while
    finding out is the caller's to turn into a refusal: a rule that cannot be read is not a rule that is not there."""
    short = short_name(event, server)
    if not short:
        return None, False
    from handlib import link, protect
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if short == "ch_protect" and str(tool_input.get("action") or "state").strip().lower() != "state":
        return T("ch_protect의 before · after는 Claude Hand의 가드가 부릅니다. 직접 부르지 마세요(state는 됩니다)."), False
    looking = call_looks(app, short, tool_input)
    home = home or link.claude_hand_home()
    key = protect.session_key(app, pin)
    files, covered, pending = [], {}, None
    if app == "blender":
        state = (ask or ask_blender)(pin, f"protection_state({not looking})")
        if state is None:
            return None, False
        rules = protect.from_dict(state["rules"]) if isinstance(state.get("rules"), dict) else \
            protect.window_rules("blender", state.get("file") or None, home, key)
        covered = state.get("covered") if isinstance(state.get("covered"), dict) else {}
        pending = state.get("pending")
    else:
        project = (folder or project_folder)(pin)
        if project is None:
            return None, False
        rules = protect.window_rules("unity", project, home, key)
        if rules.watched() and not looking:
            from handlib import keeps
            files = keeps.named_files(project, tool_input)
            state = (ask or ask_unity)(pin, "before") or {}
            covered = state.get("covered") if isinstance(state.get("covered"), dict) else {}
            pending = state.get("pending")
    if rules.problems and not looking:
        return T("보호 규칙 파일을 읽지 못해 이 호출을 막았습니다: {problem}. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요. 그 파일은 사용자가 고쳐야 합니다.",
                 problem=rules.problems[0][:300]), False
    told = protect.found_after(pending)[1]
    if told and not looking:  # found by the window since the last call, and nobody was told yet
        return told, False
    reason = protect.refusal(app, rules, short, tool_input, looking, files, covered)
    return reason, bool(reason is None and rules.watched() and not looking)


def tell_protected(event: dict, pin: str, app: str, text: str, home: Path | None = None, post=None, write=None) -> None:
    """A call the protection rules stopped, or took back: a line in the window's chat panel (the channel knows whose
    request it was) and in the event log. Best effort."""
    try:
        reply = post_to_channel("/v1/protected", {"app": app, "pin": pin, "agent_id": str(event.get("agent_id") or ""),
                                                  "tool": str(event.get("tool_name", ""))[:120], "text": text[:600]}, home, post)
    except Exception:
        reply = {}
    if reply.get("ok"):
        return
    try:
        from handlib import eventlog
        (write or eventlog.write)("protected", pin=pin, app=app, tool=str(event.get("tool_name", ""))[:120],
                                  agent_id=str(event.get("agent_id") or ""), reason=eventlog.short(text, 300))
    except Exception:
        pass


def protection_after(event: dict, pin: str, server: str, app: str, home: Path | None = None, folder=None, ask=None,
                     stopping: bool = False) -> str | None:
    """After a call that might have changed the window: ask the window what it found. It refused what it could
    itself while the call ran (Unity: saving, deleting or moving a protected asset, a forbidden action), and where
    something protected is no longer as it was before the call, it has taken back what the request did there
    (handlib/protect.py, found_after). Returns what the subagent is told, or None. Best effort: never an error.
    stopping: asked as the subagent ends its turn, whatever its last call was: should the hook after a call not
    have run, what its last call did is still looked at before it reports."""
    try:
        short = short_name(event, server)
        tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        if not stopping and (not short or short == "ch_protect" or call_looks(app, short, tool_input)):
            return None
        from handlib import link, protect
        wait = 10.0 if stopping else 60.0
        if app == "blender":
            found = (ask or ask_blender)(pin, "protection_after()", timeout=wait)
        else:
            project = (folder or project_folder)(pin)
            home = home or link.claude_hand_home()
            if project is None or not protect.window_rules("unity", project, home, protect.session_key(app, pin)).watched():
                return None
            found = (ask or ask_unity)(pin, "after", timeout=wait)
        line, told = protect.found_after(found)
        if line:
            tell_protected(event, pin, app, line, home)
        return told
    except Exception:
        return None


def report_progress(event: dict, pin: str, server: str, app: str, home: Path | None = None, post=None, mark=None,
                    folder=None, watch: bool = False) -> str | None:
    """Tell the chat channel what this subagent is about to do, so its window's panel can show it, and on the first
    action of a request set an undo point in that window (the chat panel's "되돌리기"). In a Unity project it also
    says which request is at work and keeps the files the call names (see work_begins, keep_named_files). Best
    effort: no Claude Hand, a slow server or any error just skips it; the tool call itself never waits more than a
    moment (but for an editor that is reloading scripts, whose undo point is worth some seconds: mark_checkpoint).
    Returns a denial reason when the channel says another subagent is still at work in this window.
    watch: the window has protection rules it looks after once the call is over (protection_after). What it then
    takes back is what was done since the newest undo point, so without Claude Hand, which sets one per request,
    one is set here before every such call."""
    short = short_name(event, server)
    if not short:
        return None
    tool_input = event.get("tool_input") or {}
    reply = {}
    try:
        body = {"app": app, "pin": pin, "tool": short, "agent_id": str(event.get("agent_id") or ""), "detail": action_summary(tool_input)}
        if only_looks(app, tool_input):
            body["looks"] = True  # the channel goes by the tool's name otherwise, and this one runs any code
        reply = post_to_channel("/v1/progress", body, home, post)
    except Exception:  # no channel to tell: the call goes ahead, and its files are still kept
        pass
    try:
        if reply.get("stop"):
            return (T("사용자가 채팅창에서 [중지]를 눌러 이 호출을 막았습니다. 더 부르지 말고, 지금까지 한 것과 남은 것을 그대로 "
                    "감독에게 보고하고 끝내세요. 한 것을 되돌리지는 마세요(되돌릴지는 사용자가 정합니다)."))
        if reply.get("busy"):
            return (T("이 창({pin})은 지금 다른 하위 세션이 작업 중이라 이 호출을 막았습니다. 같은 대상에 둘이 동시에 일하면 서로 덮어씁니다. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요.", pin=pin))
        own_mark = short == "ch_checkpoint" and str(tool_input.get("action") or "mark").strip().lower() == "mark"
        # A "cancel that" request starts with the undo itself: a fresh point there would leave nothing to undo.
        undoing = (short == "ch_checkpoint" and not own_mark) or "undo_last_request" in str(tool_input.get("code", ""))
        project, key = work_begins(app, pin, reply, own_mark, folder)
        marked = True
        if (reply.get("new_request") or (watch and not reply.get("chat_id") and not own_mark)) and not undoing:
            try:
                (mark or mark_checkpoint)(app, pin, str(reply.get("label") or ""), key=key)
            except Exception:
                marked = False  # the window does not answer: no undo point, but the call goes ahead and its files are kept
        if own_mark and reply.get("chat_id") and marked:
            # With Claude Hand running, every request has its point already (set here, named after what the user
            # asked). A second one from the subagent would take over the chat window's 되돌리기 button under a name
            # the user never typed, and leave the first behind with nothing to undo.
            return (T("이 요청의 되돌리기 지점은 Claude Hand가 이미 찍어 두었습니다(채팅창의 [되돌리기]). 따로 찍지 않아도 되니 "
                    "이 호출 없이 하던 일을 이어서 하세요. 문제가 아니므로 보고에 적지 않아도 됩니다."))
        keep_named_files(project, key, short, tool_input)
    except Exception:  # progress is a nicety; the guard's answer must not depend on it
        pass
    return None


def note_refusal(event: dict, pin: str, app: str, reason: str, write=None) -> None:
    """A call this guard refused by itself goes into the event log (`claude_hand.py trace`): the chat channel never
    hears of it. Calls the channel refuses (another subagent at work) it logs itself. Best effort."""
    try:
        from handlib import eventlog
        (write or eventlog.write)("refused", pin=pin, app=app, tool=str(event.get("tool_name", ""))[:120],
                                  agent_id=str(event.get("agent_id") or ""), reason=eventlog.short(reason, 300))
    except Exception:
        pass


def report_stop(event: dict, pin: str, app: str, home: Path | None = None, post=None, folder=None) -> None:
    """The subagent finished its turn: its window is free for another subagent, and the chat channel can tell when
    the supervisor owes the panel an answer. Best effort."""
    try:
        post_to_channel("/v1/stopped", {"app": app, "pin": pin, "agent_id": str(event.get("agent_id") or "")}, home, post)
    except Exception:
        pass
    work_ends(app, pin, folder)


MARK_TRIES = 6      # an editor that reloads scripts has its bridge away for some seconds: a request that follows one
MARK_WAIT = 2.0     # which changed a script starts just then, and must not go without its undo point
MARK_SLOW = 3.0     # an attempt that took this long did not find the bridge away: the editor is stuck, and asking again will not help


def mark_checkpoint(app: str, pin: str, label: str, request=None, key: str = "", sleep=time.sleep, clock=time.time) -> None:
    """Set an undo point in the subagent's window: in Unity through Claude Hand Tools' own bridge (ch_checkpoint), in
    Blender through MCP for Blender's execute_code into the Claude Hand Chat add-on (nothing happens without it).
    key: the name of the request the point is for (see work_begins). The point carries it: in Unity the files kept
    under that name go back with it, and in both a request that changed several windows is undone in each by that
    name."""
    if app == "blender":
        from handlib import link
        code = f"import claude_hand_chat\nprint(claude_hand_chat.mark_undo_point({label!r}))"
        if key:  # an add-on from before points had names takes the label alone
            code = ("import claude_hand_chat\n"
                    "named = 'request' in claude_hand_chat.mark_undo_point.__code__.co_varnames\n"
                    f"print(claude_hand_chat.mark_undo_point({label!r}, **({{'request': {key!r}}} if named else {{}})))")
        (request or link.blender_request)(int(pin.rpartition("@")[2]), {"type": "execute_code", "params": {"code": code}}, 5.0)
        return
    import unity_bridge
    params = {"action": "mark", "label": label, **({"request": key} if key else {})}
    for attempt in range(MARK_TRIES):
        entry = unity_bridge.find_project(hash_=pin.rpartition("@")[2])  # anew each time: the port may change over a reload
        if not entry:
            return
        started = clock()
        try:
            (request or unity_bridge.request)(entry, "ch_checkpoint", params, timeout=8.0)
            return
        except Exception as exc:
            away = getattr(exc, "code", "") in ("UNREACHABLE", "EDITOR_BUSY") and clock() - started < MARK_SLOW
            if not away or attempt == MARK_TRIES - 1:
                raise
            sleep(MARK_WAIT)


# --------------------------------------------------------------------------- the hook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PreToolUse hook of the claude-hand subagents (not meant to be run by hand).")
    add_arguments(parser)
    return parser


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pin", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--app", choices=["unity", "blender"], default="unity")
    parser.add_argument("--stop", action="store_true", help="run as the subagent's Stop hook")
    parser.add_argument("--after", action="store_true", help="run as the subagent's PostToolUse hook")


def read_event(stdin=None) -> dict:
    """The hook input. Claude Code writes UTF-8 whatever the console code page is, so read the bytes ourselves."""
    if stdin is not None:
        text = stdin.read()
    else:
        text = sys.stdin.buffer.read().decode("utf-8-sig")
    event = json.loads(text or "{}")
    if not isinstance(event, dict):
        raise ValueError("the hook input is not a JSON object")
    return event


def run_guard(args: argparse.Namespace, stdin=None, stdout=None) -> int:
    stdout = stdout or sys.stdout
    try:
        event = read_event(stdin)
        reason = guard_decision(event, args.pin, args.server, args.transport, args.app)
    except Exception as exc:  # fail closed: a broken guard must not let calls through
        event, reason = {}, T("claude-hand guard 오류로 호출을 막았습니다: {exc}", exc=exc)
    if not reason and args.app == "unity":
        reason = dialog_block(event, args.pin, args.server)
    watch = False
    if reason:
        note_refusal(event, args.pin, args.app, reason)
    else:
        try:
            reason, watch = protection_block(event, args.pin, args.server, args.app)
        except Exception as exc:  # fail closed here too: rules that cannot be looked up are not rules that are not there
            reason = (T("보호 규칙을 확인하지 못해 이 호출을 막았습니다: {exc}. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요.", exc=exc))
        if reason:
            try:
                from handlib import protect
                tell_protected(event, args.pin, args.app, protect.panel_line(reason))
            except Exception:  # telling of it is a nicety; the refusal stands
                pass
        else:
            reason = report_progress(event, args.pin, args.server, args.app, watch=watch)
    if reason:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, stdout)  # ASCII-escaped: safe whatever code page the hook shell uses
    return 0


def run_after(args: argparse.Namespace, stdin=None, stdout=None) -> int:
    """The PostToolUse hook: the call has run. When the window found something protected changed, or refused part of
    what the call tried, the subagent is told with the call's result (Claude Code hands a "block" with its reason to
    the model; the call itself is not undone by that, the window has seen to it)."""
    told = protection_after(read_event(stdin), args.pin, args.server, args.app)
    if told:
        json.dump({"decision": "block", "reason": told}, stdout or sys.stdout)
    return 0


def run_stop(args: argparse.Namespace, stdin=None, stdout=None) -> int:
    """The Stop hook: the subagent is about to end its turn. The window is asked once more what its protection
    rules found (the hook after a call asks this as a rule; should it not have run, this is the last moment). When
    the window took the request's work back just now, the subagent is kept from stopping once, with what happened,
    so that its report says so; else the chat channel is told that it finished."""
    event = read_event(stdin)
    told = None if event.get("stop_hook_active") else protection_after(event, args.pin, args.server, args.app, stopping=True)
    if told:
        json.dump({"decision": "block", "reason": told}, stdout or sys.stdout)
        return 0
    report_stop(event, args.pin, args.app)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)  # bad arguments: argparse exits with 2, which refuses the call
    if args.stop:
        return run_stop(args)
    if args.after:
        return run_after(args)
    return run_guard(args)


def exit_code(run=main, stop_hook: bool = False) -> int:
    """What this process ends with: `run`'s 0, or 2 for anything else at all (see the top of this file). As the
    Stop hook, always 0: nothing that goes wrong here may keep a subagent from stopping."""
    try:
        code = run()
    except SystemExit as exc:
        code = exc.code
    except BaseException as exc:
        print(f"claude-hand guard failed{'' if stop_hook else ', so the call is refused'}: {exc!r}", file=sys.stderr)
        code = 2
    return 0 if stop_hook or code in (0, None) else 2


if __name__ == "__main__":
    raise SystemExit(exit_code(stop_hook="--stop" in sys.argv[1:] or "--after" in sys.argv[1:]))  # after the call: nothing left to refuse
