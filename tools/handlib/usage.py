"""What a chat panel request cost in tokens, and how long each side worked on it, from the transcripts Claude Code keeps.

Claude Code writes every answer of the supervisor session and of its subagents to a file, with the tokens the answer
took:
  <config>/projects/<folder>/<session>.jsonl                          the supervisor
  <config>/projects/<folder>/<session>/subagents/agent-<id>.jsonl     each subagent (agent-<id>.meta.json says which)
The files also say which request an answer belongs to, without anybody having to guess:
  - each turn of the supervisor starts with a line that says what started it: a panel message (its <channel> tag
    carries the request id), a subagent's report (the request is the one on the work that subagent was last handed,
    `[요청 <id>]`), or something typed in the terminal or the Claude app (no request);
  - each turn of a subagent starts with what it was handed, and the supervisor's hook makes sure that begins with
    `[요청 <id>]` (tools/supervisor_hook.py).
So a Ledger reads the files, as far as they have grown, and adds every answer up under its request and under who
gave it.

Three numbers, not one. "output" is what was written. "input" is what was read anew (plain input and what was put
into the prompt cache). "cached" is what was read again from the cache: nearly all of the tokens of any request, and
cheap. Put together they would show nothing; apart, a request that came after a pause stands out, because the cache
had gone cold and the whole conversation was read anew.

This is not a documented format (seen with Claude Code 2.1.322). Everything here reads leniently: a line it does not
understand is passed over, and a file that says nothing gives no tokens, never an error. Standard library only.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

SUPERVISOR = "감독"
NAME_WAIT = 5.0  # seconds a new subagent's transcript may be without the file that names the subagent
TAG = re.compile(r"\[\s*요청\s*[:：]?\s*(없음|none|\d{4}-\d{6}-\d+)\s*\]", re.IGNORECASE)  # as tools/supervisor_hook.py reads it
CHANNEL_REQUEST = re.compile(r'<channel\b[^>]*\brequest="(\d{4}-\d{6}-\d+)"')
TASK_ID = re.compile(r"<task-id>([A-Za-z0-9_-]+)</task-id>")
SEEK_ABOVE = 1024 * 1024  # a smaller file is simply read from its start


def stamp(entry: dict) -> float:
    """When a transcript line was written (Unix seconds), 0.0 when it does not say."""
    try:
        return datetime.fromisoformat(str(entry.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def said(entry: dict) -> str:
    """The text of a user line: what was typed, sent or handed over (tool results have none)."""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return ""


def starts_a_turn(entry: dict) -> bool:
    """A user line that somebody sent, as opposed to the result of a tool call or a note Claude Code adds by itself."""
    if entry.get("type") != "user":
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return False
    return bool(entry.get("origin")) or not (entry.get("isMeta") or said(entry).lstrip().startswith("<system-reminder>"))


def tagged(text: str) -> str | None:
    """The request id of the first `[요청 …]` tag, "" for `[요청 없음]`, None for no tag."""
    found = TAG.search(text or "")
    if not found:
        return None
    return "" if found.group(1).lower() in ("없음", "none") else found.group(1)


class Tail:
    """A transcript read as far as it has grown: each call returns the lines that are new and whole."""

    def __init__(self, path: Path, since: float = 0.0):
        self.path, self.offset, self.since = Path(path), 0, since

    def new_lines(self) -> list[dict]:
        try:
            size = self.path.stat().st_size
            if size < self.offset:  # replaced by a shorter file: start over
                self.offset = 0
            with open(self.path, "rb") as f:
                if self.since and self.offset == 0 and size > SEEK_ABOVE:
                    self.offset = self._seek(f, size)
                self.since = 0.0
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return []
        end = data.rfind(b"\n") + 1  # a last line still being written waits for the next call
        self.offset += end
        entries = []
        for raw in data[:end].splitlines():
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def _seek(self, f, size: int) -> int:
        """Where to start reading a long file so as to get everything from `since` on: lines are in the order they
        were written, so halve the file until the lines after the cut are too new, and start a little before."""
        low, high = 0, size
        while high - low > 64 * 1024:
            middle = (low + high) // 2
            f.seek(middle)
            f.readline()  # the rest of the line the cut fell into
            when = 0.0
            for _ in range(50):  # not every line says when
                line = f.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                when = stamp(entry) if isinstance(entry, dict) else 0.0
                if when:
                    break
            if when and when < self.since:
                low = middle
            else:
                high = middle
        if low == 0:
            return 0
        f.seek(low)
        f.readline()
        return f.tell()


def blank() -> dict:
    return {"calls": 0, "output": 0, "input": 0, "cached": 0, "seconds": 0.0}


class Reader:
    """One transcript put down request by request. `who` gives the answers (SUPERVISOR or a subagent's name);
    `request_of(entry)` says which request a turn-starting line is for ("" for none, None to leave it as it is)."""

    def __init__(self, who: str, requests: dict):
        self.who, self.requests = who, requests
        self.current = ""    # the request the turn going on is for
        self.cursor = 0.0    # the time up to which the turn's time is counted
        self.seen: set[str] = set()
        self.last: dict[str, tuple[float, str]] = {}  # request -> when its newest answer here came, and how it ended

    def request_of(self, entry: dict) -> str | None:
        raise NotImplementedError

    def calls(self, block: dict) -> None:
        """A tool call in an answer (the supervisor reads its hand-outs)."""

    def feed(self, entry: dict) -> None:
        kind = entry.get("type")
        if kind == "user" and starts_a_turn(entry):
            request = self.request_of(entry)
            if request is not None:
                self.current, self.cursor = request, stamp(entry)
            return
        if kind != "assistant":
            return
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        for block in message.get("content") if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                self.calls(block)
        identity = str(message.get("id") or entry.get("uuid") or "")
        used = message.get("usage") if isinstance(message.get("usage"), dict) else None
        if used is None or identity in self.seen:  # one answer is written as a line per block, each with the same tokens
            return
        self.seen.add(identity)
        number = lambda key: int(used.get(key) or 0) if isinstance(used.get(key), (int, float)) else 0
        when = stamp(entry)
        total = self.requests.setdefault(self.current, {}).setdefault(self.who, blank())
        total["calls"] += 1
        total["output"] += number("output_tokens")
        total["input"] += number("input_tokens") + number("cache_creation_input_tokens")
        total["cached"] += number("cache_read_input_tokens")
        if when and self.cursor and when >= self.cursor:
            total["seconds"] = round(total["seconds"] + when - self.cursor, 3)
        self.cursor = when or self.cursor
        self.last[self.current] = (when, str(message.get("stop_reason") or ""))


class SupervisorReader(Reader):
    def __init__(self, requests: dict, agent_of_call):
        super().__init__(SUPERVISOR, requests)
        self.agent_of_call = agent_of_call  # callable(agent id) -> the id of the Agent call that started it, or None
        self.handed: dict[str, str] = {}    # subagent id -> the request of the work it was last handed
        self.started: dict[str, str] = {}   # id of an Agent call -> the request on its prompt

    def calls(self, block: dict) -> None:
        name, given = block.get("name"), block.get("input") if isinstance(block.get("input"), dict) else {}
        if name in ("Agent", "Task"):
            request = tagged(str(given.get("prompt") or ""))
            if request is not None:
                self.started[str(block.get("id") or "")] = request
        elif name == "SendMessage":
            request = tagged(str(given.get("message") or given.get("content") or ""))
            if request is not None:
                self.handed[str(given.get("to") or given.get("recipient") or "")] = request

    def request_of(self, entry: dict) -> str | None:
        origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
        kind, text = origin.get("kind"), said(entry) or str((entry.get("message") or {}).get("content") or "")
        if kind == "channel":
            found = CHANNEL_REQUEST.search(text)
            return found.group(1) if found else ""
        if kind in ("peer", "task-notification"):  # a subagent reports back: the request it was working for
            found = TASK_ID.search(text)
            agent = str(origin.get("from") or origin.get("senderTaskId") or (found.group(1) if found else ""))
            if agent not in self.handed:  # started with Agent: its id was not known when the call was made
                self.handed[agent] = self.started.get(str(self.agent_of_call(agent) or ""), "")
            return self.handed[agent]
        return ""  # typed in the terminal or the Claude app


class AgentReader(Reader):
    def request_of(self, entry: dict) -> str | None:
        request = tagged(said(entry))
        return "" if request is None else request  # handed over with no tag: from before requests had numbers


class Ledger:
    """The tokens and the working time of every request, by who spent them, read from a supervisor's transcript and
    its subagents' as far as they have grown. since: leave out what was written before then (a channel that has just
    started has no use for last week); 0 reads everything."""

    def __init__(self, transcript: Path, since: float = 0.0):
        self.transcript, self.since = Path(transcript), since
        self.subagents = self.transcript.with_suffix("") / "subagents"
        self.requests: dict[str, dict[str, dict]] = {}  # request id ("" for none) -> who -> blank()
        self._supervisor = (Tail(self.transcript, since), SupervisorReader(self.requests, self._agent_call))
        self._agents: dict[str, tuple[Tail, AgentReader]] = {}
        self.turn_over, self.turn_at = True, 0.0  # see resting(): nothing read yet means nothing is going on

    def _meta(self, agent: str) -> dict:
        try:
            data = json.loads((self.subagents / f"agent-{agent}.meta.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _agent_call(self, agent: str) -> str | None:
        return self._meta(agent).get("toolUseId") if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", agent or "") else None

    def update(self) -> None:
        """Read what is new in the files. Subagents first: a supervisor line is then never ahead of the work it tells of."""
        try:
            paths = sorted(self.subagents.glob("agent-*.jsonl"))
        except OSError:
            paths = []
        for path in paths:
            agent = path.stem[len("agent-"):]
            if agent not in self._agents:
                who = str(self._meta(agent).get("agentType") or "")
                if not who:
                    try:  # the file that says which subagent this is may be a moment behind: look again next time
                        if time.time() - path.stat().st_mtime < NAME_WAIT:
                            continue
                    except OSError:
                        continue
                self._agents[agent] = (Tail(path, self.since), AgentReader(who or f"agent-{agent}", self.requests))
            tail, reader = self._agents[agent]
            for entry in tail.new_lines():
                reader.feed(entry)
        tail, reader = self._supervisor
        for entry in tail.new_lines():
            reader.feed(entry)
            self._note_turn(entry)

    def _note_turn(self, entry: dict) -> None:
        """Whether the supervisor is in the middle of a turn, as far as its transcript tells: an answer that ends the
        turn puts it at rest; a message, a tool result, something queued for it or attached to its turn means it is
        (or is about to be) at work again. Other lines (hook notes, snapshots) say nothing either way."""
        kind = entry.get("type")
        if kind == "assistant":
            message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            self.turn_over = message.get("stop_reason") == "end_turn"
        elif (kind == "user" and starts_a_turn(entry)) or (kind == "queue-operation" and entry.get("operation") == "enqueue"):
            self.turn_over = False
        else:
            return
        self.turn_at = stamp(entry) or self.turn_at

    def resting(self) -> tuple[bool, float]:
        """(the supervisor's last turn is over, when the line that says so was written). A message handed to it while
        a turn is going on does not start a turn of its own: it is slipped into the one going on, where the model
        does not take it for the user's word (found 2026-11-24). True and 0.0 before anything was read."""
        return self.turn_over, self.turn_at

    def cost(self, request: str) -> dict[str, dict]:
        """who -> {"calls", "output", "input", "cached", "seconds"} for one request; {} when the files say nothing."""
        return {who: dict(total) for who, total in self.requests.get(request, {}).items()} if request else {}

    def settled(self, request: str, after: float) -> bool:
        """The supervisor's turn that answered the request is over: its newest answer for it came after `after` and
        ended the turn. Until then the tokens of that last answer are not in the file."""
        when, how = self._supervisor[1].last.get(request, (0.0, ""))
        return when >= after and how == "end_turn"


# --------------------------------------------------------------------------- in words


def count(tokens: int) -> str:
    """A number of tokens the way it is said: 950, 3.9천, 1.2만, 66만, 1.3억."""
    tokens = int(tokens)
    if tokens < 1000:
        return str(tokens)
    if tokens < 9950:
        return f"{tokens / 1000:.1f}천"
    if tokens < 99500:
        return f"{tokens / 10000:.1f}만"
    if tokens < 99_500_000:
        return f"{tokens / 10000:.0f}만"
    return f"{tokens / 100_000_000:.1f}억"


def duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds}초"
    if seconds < 3600:
        return f"{seconds // 60}분 {seconds % 60}초" if seconds % 60 else f"{seconds // 60}분"
    return f"{seconds // 3600}시간 {seconds % 3600 // 60}분"


def together(cost: dict[str, dict]) -> dict:
    total = blank()
    for one in cost.values():
        for key in total:
            total[key] += one.get(key) or 0
    return total


def tokens_text(total: dict) -> str:
    return f"출력 {count(total['output'])} · 새 입력 {count(total['input'])} · 다시 읽기 {count(total['cached'])}"


def line(seconds: float, actions: int, cost: dict[str, dict]) -> str:
    """A request in one line for its chat panel: how long the user waited, how many actions, and the tokens of
    everybody together. Without tokens (no transcript to read) it says the time alone."""
    parts = [duration(seconds)] + ([f"동작 {actions}번"] if actions else [])
    if any(one.get("calls") for one in cost.values()):
        parts.append("토큰 " + tokens_text(together(cost)))
    return " · ".join(parts)


def detail(cost: dict[str, dict]) -> str:
    """Who spent what, the supervisor first: "감독 12초 API 4번 출력 1.3천 · 새 입력 2.8천 · 다시 읽기 46만 / hand_test …"."""
    order = sorted(cost, key=lambda who: (who != SUPERVISOR, who))
    return " / ".join(f"{who} {duration(cost[who].get('seconds') or 0)} API {cost[who].get('calls') or 0}번 {tokens_text({**blank(), **cost[who]})}"
                      for who in order if cost[who].get("calls"))
