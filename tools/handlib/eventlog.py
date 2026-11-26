"""What happened to each request, one line per event, in ~/.claude-hand/logs/<date>.jsonl.

A panel message gets a request id from the chat channel. From then on the channel writes what it sees for that
request (each tool call a subagent's guard reports, a call it refuses, the subagent stopping, the reply, a reminder,
and at the end what the request took in time and tokens),
the guard writes the calls it refuses by itself, and the Unity bridge writes how each ch_* call ended and how long it
took. They are three kinds of process, so every writer appends one whole line and none keeps the file open.
`claude_hand.py trace` puts the lines of one request back together.

  {"ts": 1762756325.12, "event": "message", "request": "1110-143205-12", "chat_id": "unity-hand-test", "text": "..."}

Standard library only, and small: the guard loads this after its decision.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from handlib import link
from handlib.lang import N_, T

KEEP_DAYS = 14
TEXT_LIMIT = 200  # of a message or reply kept in the log: enough to recognize it, not a second chat history


def log_dir(home: Path | None = None) -> Path:
    return (home or link.claude_hand_home()) / "logs"


def write(event: str, home: Path | None = None, now: float | None = None, **fields) -> None:
    """Append one event. Best effort: a full disk or a locked file must never stop the work being logged.
    CLAUDE_HAND_LOG=off turns the log off (the tests and selftest's hook probe set it); an explicit home still writes."""
    if home is None and os.environ.get("CLAUDE_HAND_LOG", "").lower() == "off":
        return
    try:
        now = time.time() if now is None else now
        directory = log_dir(home)
        directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": round(now, 3), "event": event, **{k: v for k, v in fields.items() if v not in (None, "")}},
                          ensure_ascii=False)
        with open(directory / (time.strftime("%Y-%m-%d", time.localtime(now)) + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def short(text, limit: int = TEXT_LIMIT) -> str:
    """Text on one line, cut to `limit`."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def read(home: Path | None = None, days: int = 2) -> list[dict]:
    """The events of the newest `days` log files, oldest first. Lines that are not events are skipped."""
    events = []
    files = sorted(log_dir(home).glob("*.jsonl")) if log_dir(home).is_dir() else []
    for path in files[-max(1, days):]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("ts"), (int, float)) and event.get("event"):
                events.append(event)
    return sorted(events, key=lambda e: e["ts"])


def clean(home: Path | None = None, keep_days: int = KEEP_DAYS, now: float | None = None) -> int:
    """Delete log files older than keep_days (by the date in their name). Returns how many."""
    oldest = time.strftime("%Y-%m-%d", time.localtime((time.time() if now is None else now) - keep_days * 86400))
    removed = 0
    for path in sorted(log_dir(home).glob("*.jsonl")) if log_dir(home).is_dir() else []:
        if path.stem < oldest:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def requests(events: list[dict]) -> list[dict]:
    """One entry per panel message, oldest first: {"request", "chat_id", "ts", "text", "actions", "refused",
    "answered": when its answer came, or None}."""
    found = []
    for event in events:
        if event["event"] != "message" or not event.get("request"):
            continue
        lines = trace(events, event["request"])  # counted as `trace` shows them, so the list and the detail agree
        found.append({"request": event["request"], "chat_id": event.get("chat_id", ""), "ts": event["ts"], "text": event.get("text", ""),
                      "actions": sum(e["event"] == "action" for e in lines), "refused": sum(e["event"] == "refused" for e in lines),
                      "answered": next((e["ts"] for e in lines if e["event"] == "reply" and not e.get("working")), None)})
    return sorted(found, key=lambda r: r["ts"])


def trace(events: list[dict], request: str) -> list[dict]:
    """The events of one request, in order: those written with its id, and between its message and its answer also
    those written without one by the windows that worked for it (a call the guard refused by itself, a ch_* call:
    neither the guard nor the bridge knows which request it serves)."""
    own = [i for i, e in enumerate(events) if e.get("request") == request]
    if not own:
        return []
    # "Between" goes by the order of the lines, not by their times: on Windows before Python 3.13 the clock moves in
    # steps of about 15 ms, and a reply and the next thing can carry the same time.
    start, chat, end = own[0], events[own[0]].get("chat_id"), len(events)
    for i in range(start + 1, len(events)):
        e = events[i]
        answered = e.get("request") == request and e["event"] == "reply" and not e.get("working")
        if answered or (e["event"] == "message" and e.get("chat_id") == chat):  # its answer, or that panel's next request
            end = i
            break
    pins = {events[i]["pin"] for i in own if events[i].get("pin")}
    return [e for i, e in enumerate(events)
            if e.get("request") == request or (start < i < end and not e.get("request") and e.get("pin") in pins)]


LABELS = {"message": N_("요청"), "assigned": N_("맡김"), "action": N_("동작"), "refused": N_("막음"), "call": N_("ch 호출"), "stopped": N_("멈춤"), "reply": N_("답"),
          "reminder": N_("다시 알림"), "dialog": N_("대화상자"), "stop": N_("중지 누름"), "permission": N_("허락 요청"), "verdict": N_("허락 답"),
          "undo": N_("되돌림"), "cost": N_("시간·토큰"), "held": N_("쥐고 있음"), "released": N_("넘김"),
          "protected": N_("막음 (보호 규칙)"), "protect": N_("보호 규칙")}


def describe(event: dict, since: float | None = None) -> str:
    """One event as a line of text: "14:32:09.480  동작      unity-hand-test  manage_gameobject create Cube"."""
    stamp = time.strftime("%H:%M:%S", time.localtime(event["ts"])) + f".{int(event['ts'] * 1000) % 1000:03d}"
    kind, parts = event["event"], []
    who = event.get("chat_id") or event.get("pin") or ""
    if kind in ("message", "reply"):
        parts = [who, f'"{event.get("text", "")}"']
        if kind == "reply":
            if event.get("working"):
                parts.append(T("(하는 중이라는 답)"))
            elif since is not None:
                parts.append(T("({seconds:.1f}초 만에)", seconds=event['ts'] - since))
            if event.get("origin") == "desktop":
                parts.append(T("(데스크톱 앱에서 시킨 일)"))
    elif kind == "action":
        parts = [who, " ".join(filter(None, [event.get("tool", ""), event.get("detail", "")]))]
        if event.get("first"):
            parts.append(T("(이 요청의 되돌리기 지점)"))
    elif kind in ("refused", "protected"):
        parts = [who, event.get("tool", ""), event.get("reason", "")]
    elif kind == "call":
        result = T("성공") if event.get("ok") else T("실패 {code}", code=event.get('code', '')).strip()
        parts = [who, event.get("tool", ""), f"{result} {event.get('ms', 0)} ms"]
    elif kind in ("permission", "verdict"):
        parts = [event.get("tool", ""), event.get("behavior", "")]
    elif kind == "cost":  # what the request took: the wait in the panel, then who spent what (handlib/usage.py wrote the words)
        waited = T("{seconds:.1f}초", seconds=event.get('seconds', 0)) + (T(" · 동작 {actions}번", actions=event['actions']) if event.get("actions") else "")
        parts = [who, waited, event.get("text") or T("(토큰 기록 없음)")]
    else:
        parts = [who, event.get("text", "")]
    return f"{stamp}  {T(LABELS[kind]) if kind in LABELS else kind:<8}  " + "  ".join(p for p in parts if p)
