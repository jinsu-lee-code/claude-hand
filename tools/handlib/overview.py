"""What goes on in each open window, in words: the lines a chat panel shows for the other windows, and what
`claude_hand.py status` prints. The chat channel works the states out from what it already knows (ChatHub._state);
here they become text. A state is told by its words, never by a colour.
"""

from __future__ import annotations

STATES = {"working": "작업 중", "stopping": "중지하는 중", "waiting": "답 기다림", "dialog": "대화상자로 멈춤", "idle": "한가함"}


def label(state: dict) -> str:
    return f"{state['window']} ({'Unity' if state['app'] == 'unity' else 'Blender'})"


def duration(seconds: int) -> str:
    return f"{seconds}초" if seconds < 60 else f"{seconds // 60}분 {seconds % 60}초"


def line(state: dict, names: dict[str, str], detail: bool = False) -> str:
    """One window in one line: "MyGame (Unity) · 작업 중 · props의 요청 · “의자를 Unity로 보내줘”".
    detail adds for how long and what it is doing right now (status). The panels leave those out: there they
    would stand still until the window's state changes."""
    parts = [label(state), STATES[state["state"]]]
    if detail and "seconds" in state:
        parts.append(duration(state["seconds"]))
    if state.get("asker"):
        parts.append(f"{names.get(state['asker'], state['asker'])}의 요청")
    if state["state"] == "dialog":
        parts.append(state.get("dialog", ""))
    else:
        if detail and state.get("action"):
            parts.append(state["action"])
        if state.get("request"):
            parts.append(f"“{state['request']}”")
    return " · ".join(part for part in parts if part)


def lines(states: list[dict], skip: str = "", detail: bool = False) -> list[str]:
    """The windows as lines: each busy one on its own, the idle ones together on the last.
    skip: the chat of the panel that shows them (it shows its own window above)."""
    names = {state["chat_id"]: state["window"] for state in states}
    shown = [state for state in states if state["chat_id"] != skip]
    out = [line(state, names, detail) for state in shown if state["state"] != "idle"]
    idle = [label(state) for state in shown if state["state"] == "idle"]
    if idle:
        out.append(f"{STATES['idle']}: " + ", ".join(idle))
    return out
