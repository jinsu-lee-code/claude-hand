#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""claude-hand supervisor hook: the PreToolUse hook of the supervisor session on Agent and SendMessage.

  supervisor_hook.py          (hook input on stdin; start-supervisor passes it to Claude Code with --settings)

The chat channel has to know whose request the work in a window is for: which panel's 중지 stops it, which request
it goes under in the event log, whose answer may show the window. It used to guess that from who was waiting, and
with several projects worked on side by side the guess went wrong. The supervisor knows: it is the one handing the
work out. So it starts the prompt (Agent) or the message (SendMessage) for a window's subagent with the request's id
from the <channel> tag:

  [요청 1111-052233-12] ...        work for that panel message
  [요청 없음] ...                  asked in the terminal or the Claude app

This hook reads the tag and tells the channel, which from then on puts that window's work down for that request
(until the subagent stops). A hand-out to a window's subagent without a tag, with an id the channel does not know,
or for a request whose panel pressed 중지, is refused with what to do instead. Being a hook, this is not left to the
supervisor remembering it.

Unlike the subagents' guard (tools/guard.py), this hook never stands in the way when it cannot do its job: no
Claude Hand running, a slow channel, any error at all, and the hand-out goes ahead (the channel then guesses as
before). Claude Code reads exit 0 with nothing on stdout as "go ahead", and that is how every failure here ends.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

TAG_RE = re.compile(r"\[\s*요청\s*[:：]?\s*(없음|none|\d{4}-\d{6}-\d+)\s*\]", re.IGNORECASE)
HOW = ("프롬프트(SendMessage면 메시지) 첫 줄에 `[요청 <번호>]`를 적고 다시 부르세요. 번호는 그 채팅창 메시지의 <channel> 태그에 있는 "
       "request 값입니다. 채널 태그 없이 터미널이나 Claude 앱에서 온 요청이면 `[요청 없음]`이라고 적습니다.")


def tag_of(text: str) -> str | None:
    """The request id of the first `[요청 …]` tag in a prompt, "" for `[요청 없음]`, None when there is no tag."""
    found = TAG_RE.search(str(text or ""))
    if not found:
        return None
    return "" if found.group(1).lower() in ("없음", "none") else found.group(1)


def handed_out(event: dict) -> tuple[str, str] | None:
    """(the subagent, the words it is given) of a call that hands work to a subagent; None for any other call."""
    tool, tool_input = str(event.get("tool_name", "")), event.get("tool_input") or {}
    if tool in ("Agent", "Task"):
        return str(tool_input.get("subagent_type") or ""), str(tool_input.get("prompt") or "")
    if tool == "SendMessage":
        return str(tool_input.get("to") or tool_input.get("recipient") or ""), str(tool_input.get("message") or tool_input.get("content") or "")
    return None


def agent_type(event: dict, agent_id: str) -> str:
    """The subagent type of an agent this session started earlier. SendMessage names such an agent by its id, and a
    supervisor that is resumed (start-supervisor does that) goes on with agents from days ago, whose ids a chat
    channel that has just started has never seen. Claude Code keeps each agent's type beside the session's
    transcript: <transcript without .jsonl>/subagents/agent-<id>.meta.json. "" when that cannot be read (the channel
    may still know the id from the agent's own work)."""
    transcript = str(event.get("transcript_path") or "")
    if not transcript or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", agent_id):
        return ""
    try:
        meta = json.loads((Path(transcript).with_suffix("") / "subagents" / f"agent-{agent_id}.meta.json").read_text(encoding="utf-8"))
        return str(meta.get("agentType") or "") if isinstance(meta, dict) else ""
    except (OSError, ValueError):
        return ""


def decision(event: dict, post) -> str | None:
    """A denial reason, or None to let the call through. post(path, body) -> the channel's answer ({} when none)."""
    handed = handed_out(event)
    if handed is None or not handed[0]:
        return None
    target, text = handed
    answer = post("/v1/assign", {"target": target, "agent": agent_type(event, target), "request": tag_of(text)}) or {}
    if not answer.get("ours"):
        return None  # not a window's subagent (or no Claude Hand to ask)
    window = answer.get("chat_id") or target
    if answer.get("missing"):
        return f"{window}에게 맡기는 일이 어느 채팅창 요청 때문인지 적혀 있지 않아 막았습니다. " + HOW
    if answer.get("unknown"):
        return f"`[요청 {tag_of(text)}]`은 채널이 모르는 요청 번호라 {window}에게 맡기는 호출을 막았습니다. " + HOW
    if answer.get("stop"):
        return ("사용자가 그 채팅창에서 [중지]를 누른 요청이라 이 호출을 막았습니다. 이 요청의 일은 더 맡기지 말고, 어디까지 했고 "
                "무엇이 남았는지 reply로 알리세요.")
    return None


def channel_post(path: str, body: dict) -> dict:
    """POST to the running supervisor's chat channel; {} when there is none. A hand-out happens a few times per
    request, so this may wait a little longer than the guard does on every tool call."""
    import guard
    return guard.post_to_channel(path, body, post=lambda req: json.loads(urllib.request.urlopen(req, timeout=3).read() or b"{}"))


def main(stdin=None, stdout=None, post=channel_post) -> int:
    try:
        text = stdin.read() if stdin is not None else sys.stdin.buffer.read().decode("utf-8-sig")
        event = json.loads(text or "{}")
        reason = decision(event, post) if isinstance(event, dict) else None
    except Exception as exc:  # never in the way: see the top of this file
        print(f"claude-hand supervisor hook skipped: {exc!r}", file=sys.stderr)
        return 0
    if reason:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, stdout or sys.stdout)  # ASCII-escaped: safe whatever code page the hook shell uses
    return 0


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # including SystemExit from a broken import: still "go ahead"
        print(f"claude-hand supervisor hook skipped: {exc!r}", file=sys.stderr)
    raise SystemExit(0)
