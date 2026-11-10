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
first action of a request, sets an undo point in that window. A call it refuses goes into the event log.

  guard.py --pin ... --server ... --app ... --stop       the same subagents' Stop hook: tells the chat channel the
                                                         subagent finished its turn. Never refuses anything.

Whatever goes wrong, the call is refused. Claude Code reads a hook's result like this:
  exit 0 with a "deny" decision on stdout   the call is refused, with the reason
  exit 0 with nothing on stdout             the call goes ahead
  exit 2                                    the call is refused
  any other exit code                       a hook error that does NOT stop the call
So as the PreToolUse hook this process only ever ends with 0 or 2: bad arguments and any error while deciding
refuse the call. (As the Stop hook it always ends with 0: there, 2 would keep the subagent from stopping.) That is
also why the guard lives in a small file of its own with nothing but the standard library at import time: a
mistake elsewhere in claude-hand cannot make it end with another code. What comes after the decision (work line,
undo point) is best effort and loads its helpers only then.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

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
        return f"이 하위 세션은 {pin} 전용입니다. {what} 도구는 {prefix}* 만 쓸 수 있습니다."
    if app == "blender":
        return None  # one MCP for Blender server talks to exactly one port, so its own tools cannot reach another Blender
    short = tool[len(prefix):]
    tool_input = event.get("tool_input") or {}
    if short == "set_active_instance":
        target = str(tool_input.get("instance", ""))
        if transport == "http" and instance_matches(target, pin):
            return None
        return f"대상 프로젝트는 {pin}로 고정돼 있어 바꿀 수 없습니다."
    for value in find_instance_values(tool_input):
        if not instance_matches(value, pin):
            return f"unity_instance={value!r}는 이 하위 세션의 프로젝트({pin})가 아닙니다. unity_instance 없이 호출하세요."
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
        return (f"Unity가 대화상자 때문에 멈춰 있어 이 호출을 막았습니다: {unity_bridge.describe_dialogs(dialogs)}. 더 부르지 말고 "
                "이 내용을 그대로 감독에게 보고하세요. 어느 버튼을 누를지는 사용자가 정합니다.")
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


def post_to_channel(path: str, body: dict, home: Path | None = None, post=None) -> dict:
    """POST to the chat channel of the running supervisor; {} when there is none. Waits half a second at most."""
    from handlib import link
    info, _ = link.read_chat_files((home or link.claude_hand_home()) / "chat")
    if not info:
        return {}
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"X-Claude-Hand-Token": info.get("token", ""), "Content-Type": "application/json"})
    return (post or (lambda r: json.loads(urllib.request.urlopen(r, timeout=0.5).read() or b"{}")))(req) or {}


def report_progress(event: dict, pin: str, server: str, app: str, home: Path | None = None, post=None, mark=None) -> str | None:
    """Tell the chat channel what this subagent is about to do, so its window's panel can show it, and on the first
    action of a request set an undo point in that window (the chat panel's "되돌리기"). Best effort: no Claude Hand,
    a slow server or any error just skips it; the tool call itself never waits more than a moment.
    Returns a denial reason when the channel says another subagent is still at work in this window."""
    tool = str(event.get("tool_name", ""))
    short = next((tool[len(prefix):] for prefix in (f"mcp__{server}_ch__", f"mcp__{server}__") if tool.startswith(prefix)), "")
    if not short:
        return None
    try:
        reply = post_to_channel("/v1/progress", {"app": app, "pin": pin, "tool": short, "agent_id": str(event.get("agent_id") or ""),
                                                 "detail": action_summary(event.get("tool_input") or {})}, home, post)
        if reply.get("stop"):
            return ("사용자가 채팅창에서 [중지]를 눌러 이 호출을 막았습니다. 더 부르지 말고, 지금까지 한 것과 남은 것을 그대로 "
                    "감독에게 보고하고 끝내세요. 한 것을 되돌리지는 마세요(되돌릴지는 사용자가 정합니다).")
        if reply.get("busy"):
            return (f"이 창({pin})은 지금 다른 하위 세션이 작업 중이라 이 호출을 막았습니다. 같은 대상에 둘이 동시에 일하면 서로 "
                    "덮어씁니다. 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요.")
        # A "cancel that" request starts with the undo itself: a fresh point there would leave nothing to undo.
        undoing = short == "ch_checkpoint" or "undo_last_request" in str((event.get("tool_input") or {}).get("code", ""))
        if reply.get("new_request") and not undoing:
            (mark or mark_checkpoint)(app, pin, str(reply.get("label") or ""))
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


def report_stop(event: dict, pin: str, app: str, home: Path | None = None, post=None) -> None:
    """The subagent finished its turn: its window is free for another subagent, and the chat channel can tell when
    the supervisor owes the panel an answer. Best effort."""
    try:
        post_to_channel("/v1/stopped", {"app": app, "pin": pin, "agent_id": str(event.get("agent_id") or "")}, home, post)
    except Exception:
        pass


def mark_checkpoint(app: str, pin: str, label: str, request=None) -> None:
    """Set an undo point in the subagent's window: in Unity through Claude Hand Tools' own bridge (ch_checkpoint), in
    Blender through MCP for Blender's execute_code into the Claude Hand Chat add-on (nothing happens without it)."""
    if app == "blender":
        from handlib import link
        code = f"import claude_hand_chat\nprint(claude_hand_chat.mark_undo_point({label!r}))"
        (request or link.blender_request)(int(pin.rpartition("@")[2]), {"type": "execute_code", "params": {"code": code}}, 5.0)
        return
    import unity_bridge
    entry = unity_bridge.find_project(hash_=pin.rpartition("@")[2])
    if entry:
        (request or unity_bridge.request)(entry, "ch_checkpoint", {"action": "mark", "label": label}, timeout=8.0)


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
        event, reason = {}, f"claude-hand guard 오류로 호출을 막았습니다: {exc}"
    if not reason and args.app == "unity":
        reason = dialog_block(event, args.pin, args.server)
    if reason:
        note_refusal(event, args.pin, args.app, reason)
    else:
        reason = report_progress(event, args.pin, args.server, args.app)
    if reason:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, stdout)  # ASCII-escaped: safe whatever code page the hook shell uses
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)  # bad arguments: argparse exits with 2, which refuses the call
    if args.stop:
        report_stop(read_event(), args.pin, args.app)
        return 0
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
    raise SystemExit(exit_code(stop_hook="--stop" in sys.argv[1:]))
