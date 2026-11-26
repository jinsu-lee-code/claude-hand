"""Tests for tools/handlib/usage.py: what a request took, read from Claude Code's transcripts.
Run: python -m unittest discover -s tools/tests -v

The transcripts here are written the way Claude Code 2.1 writes them (checked against a real supervisor session):
an answer is one line per block, each with the whole answer's tokens and the way it ended."""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CLAUDE_HAND_LANG"] = "ko"  # the words here are the Korean ones, whatever this PC is set to
from handlib import usage  # noqa: E402

BASE = datetime(2026, 11, 23, 3, 0, 0, tzinfo=timezone.utc).timestamp()
R1, R2 = "1123-120000-1", "1123-120010-2"


def at(seconds: float) -> str:
    return datetime.fromtimestamp(BASE + seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def user(kind: str, text: str, when: float, **origin) -> dict:
    return {"type": "user", "origin": {"kind": kind, **origin}, "message": {"role": "user", "content": text}, "timestamp": at(when)}


def channel(request: str, text: str, when: float, chat: str = "unity-mygame") -> dict:
    return user("channel", f'<channel source="claude-hand" chat_id="{chat}" request="{request}">\n{text}\n</channel>', when)


def done(agent: str, when: float) -> dict:
    return user("task-notification", f"<task-notification>\n<task-id>{agent}</task-id>\n<status>completed</status>\n</task-notification>", when)


def answer(identity: str, when: float, output=0, fresh=0, written=0, cached=0, stop="tool_use", blocks=None) -> list[dict]:
    used = {"input_tokens": fresh, "cache_creation_input_tokens": written, "cache_read_input_tokens": cached, "output_tokens": output}
    return [{"type": "assistant", "timestamp": at(when),
             "message": {"id": identity, "role": "assistant", "content": [block], "stop_reason": stop, "usage": used}}
            for block in blocks or [{"type": "text", "text": "..."}]]


def call(identity: str, name: str, **given) -> dict:
    return {"type": "tool_use", "id": identity, "name": name, "input": given}


def result(identity: str, when: float) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": identity, "content": "ok"}]},
            "timestamp": at(when)}


def flat(*parts) -> list[dict]:
    return [entry for part in parts for entry in (part if isinstance(part, list) else [part])]


def write(path: Path, entries, mode: str = "w") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8", newline="\n") as f:
        for entry in entries:
            f.write((entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)) + "\n")


class Session:
    """A supervisor's transcript and its subagents' in a folder of their own."""

    def __init__(self, tmp: str):
        self.transcript = Path(tmp) / "2bfcab59-32d4-4e90-8df7-37b79974a4e9.jsonl"
        self.subagents = self.transcript.with_suffix("") / "subagents"

    def supervisor(self, *parts, mode="w") -> None:
        write(self.transcript, flat(*parts), mode)

    def agent(self, identity: str, who: str | None, *parts, started_by: str = "", mode="w") -> Path:
        path = self.subagents / f"agent-{identity}.jsonl"
        write(path, flat(*parts), mode)
        if who is not None:
            (self.subagents / f"agent-{identity}.meta.json").write_text(json.dumps(
                {"agentType": who, "toolUseId": started_by, "description": "x"}), encoding="utf-8")
        return path


def one_request(session: Session) -> None:
    """A panel asks for a cube: the supervisor hands it to the window's subagent, which works and reports back."""
    session.supervisor(
        channel(R1, "큐브 만들어줘", 0),
        answer("s1", 2, output=100, fresh=10, written=20, cached=1000, blocks=[
            {"type": "thinking", "thinking": "..."}, {"type": "text", "text": "맡길게요"},
            call("toolu_1", "Agent", subagent_type="unity-mygame", prompt=f"[요청 {R1}] 큐브를 만들어라")]),
        result("toolu_1", 2.2),
        answer("s2", 3, output=30, fresh=5, cached=1200, stop="end_turn"),
        done("aaa111", 20),
        answer("s3", 22, output=60, fresh=40, cached=1300, blocks=[call("toolu_2", "mcp__claude-hand-chat__reply", text="만들었어요")]),
        result("toolu_2", 22.1),
        answer("s4", 23, output=10, fresh=2, cached=1400, stop="end_turn"))
    session.agent("aaa111", "unity-mygame",
                  user("coordinator", f"[요청 {R1}] 큐브를 만들어라", 2.5),
                  answer("a1", 5, output=200, fresh=300, written=4000, cached=0, blocks=[call("toolu_9", "mcp__unity_mygame__manage_gameobject")]),
                  result("toolu_9", 9),
                  answer("a2", 19, output=50, fresh=10, cached=4300, stop="end_turn"),
                  started_by="toolu_1")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Session(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, since: float = 0.0) -> usage.Ledger:
        ledger = usage.Ledger(self.session.transcript, since)
        ledger.update()
        return ledger

    def test_tokens_and_working_time_go_under_the_request_and_who_spent_them(self):
        one_request(self.session)
        cost = self.ledger().cost(R1)
        self.assertEqual(cost, {
            # Each answer once, however many lines it was written as. New input = plain input + what went into the cache.
            usage.SUPERVISOR: {"calls": 4, "output": 200, "input": 77, "cached": 4900, "seconds": 6.0},   # 0→2→3, then 20→22→23
            "unity-mygame": {"calls": 2, "output": 250, "input": 4310, "cached": 4300, "seconds": 16.5}})  # 2.5→5→19
        self.assertEqual(usage.together(cost), {"calls": 6, "output": 450, "input": 4387, "cached": 9200, "seconds": 22.5})
        cost[usage.SUPERVISOR]["calls"] = 99  # a copy: the ledger's own numbers are not the caller's to change
        self.assertEqual(self.ledger().cost(R1)[usage.SUPERVISOR]["calls"], 4)
        self.assertEqual(self.ledger().cost("1123-999999-9"), {})
        self.assertEqual(self.ledger().cost(""), {})

    def test_two_requests_side_by_side_and_work_asked_in_the_terminal(self):
        self.session.supervisor(
            channel(R1, "의자 만들어줘", 0, chat="blender-props"),
            answer("s1", 1, output=10, cached=100, blocks=[call("t1", "Agent", subagent_type="blender-props", prompt=f"[요청 {R1}] 의자")]),
            result("t1", 1.1),
            answer("s2", 2, output=1, cached=100, stop="end_turn"),
            channel(R2, "큐브 만들어줘", 10),
            answer("s3", 11, output=20, cached=200, blocks=[call("t2", "Agent", subagent_type="unity-mygame", prompt=f"[ 요청: {R2} ]\n큐브")]),
            result("t2", 11.1),
            answer("s4", 12, output=2, cached=200, stop="end_turn"),
            done("uuu", 30),                                                         # the cube's subagent is back first
            answer("s5", 31, output=40, cached=400, stop="end_turn"),
            user("peer", "의자를 만들었습니다", 40, **{"from": "bbb"}),               # then the chair's, with a message
            answer("s6", 41, output=80, cached=800, blocks=[call("t3", "SendMessage", to="bbb", message=f"[요청 {R2}] 그 의자를 내보내라")]),
            result("t3", 41.1),
            answer("s7", 42, output=3, cached=800, stop="end_turn"),
            done("bbb", 50),                                                         # ... which now works for the other request
            answer("s8", 51, output=160, cached=1600, stop="end_turn"),
            user("human", "지금 몇 시야?", 60),                                       # typed in the terminal: nobody's request
            answer("s9", 61, output=500, cached=5000, stop="end_turn"),
            {"type": "user", "isMeta": True, "message": {"content": "<system-reminder>note</system-reminder>"}, "timestamp": at(62)},
            answer("s10", 63, output=7, cached=70, stop="end_turn"))                  # a note Claude Code adds starts no turn
        self.session.agent("bbb", "blender-props",
                           user("coordinator", f"[요청 {R1}] 의자", 1.5), answer("b1", 39, output=1000, cached=10, stop="end_turn"),
                           user("coordinator", f"[요청 {R2}] 그 의자를 내보내라", 41.5), answer("b2", 49, output=2000, cached=20, stop="end_turn"),
                           started_by="t1")
        self.session.agent("uuu", "unity-mygame",
                           user("coordinator", f"[요청: {R2}]\n큐브", 11.5), answer("u1", 29, output=3000, cached=30, stop="end_turn"),
                           started_by="t2")
        ledger = self.ledger()
        spent = lambda request: {who: (one["calls"], one["output"]) for who, one in ledger.requests[request].items()}
        self.assertEqual(spent(R1), {usage.SUPERVISOR: (4, 10 + 1 + 80 + 3), "blender-props": (1, 1000)})
        self.assertEqual(spent(R2), {usage.SUPERVISOR: (4, 20 + 2 + 40 + 160), "blender-props": (1, 2000), "unity-mygame": (1, 3000)})
        self.assertEqual(spent(""), {usage.SUPERVISOR: (2, 507)})
        self.assertEqual(sum(one["output"] for who in ledger.requests.values() for one in who.values()), 6823)  # nothing twice, nothing lost

    def test_an_untagged_hand_out_and_a_subagent_nobody_handed_anything(self):
        self.session.supervisor(
            channel(R1, "큐브", 0),
            answer("s1", 1, output=10, blocks=[call("t1", "Agent", subagent_type="Explore", prompt="코드에서 찾아봐")]),  # not a window's subagent
            result("t1", 1.1),
            answer("s2", 2, output=1, stop="end_turn"),
            done("eee", 10),          # started without a tag: its report is nobody's request
            answer("s3", 11, output=100, stop="end_turn"),
            done("zzz", 20),          # an id with no files at all
            answer("s4", 21, output=1000, stop="end_turn"),
            user("peer", "x", 30, **{"from": "../../x"}),  # never used as a path
            answer("s5", 31, output=10000, stop="end_turn"))
        self.session.agent("eee", "Explore", user("coordinator", "코드에서 찾아봐", 1.5), answer("e1", 9, output=5, stop="end_turn"), started_by="t1")
        ledger = self.ledger()
        self.assertEqual(ledger.cost(R1), {usage.SUPERVISOR: {"calls": 2, "output": 11, "input": 0, "cached": 0, "seconds": 2.0}})
        self.assertEqual({who: one["output"] for who, one in ledger.requests[""].items()}, {usage.SUPERVISOR: 11100, "Explore": 5})

    def test_the_files_are_read_as_far_as_they_have_grown(self):
        one_request(self.session)
        whole = self.ledger().cost(R1)
        lines = self.session.transcript.read_text(encoding="utf-8").splitlines()
        agent = self.session.subagents / "agent-aaa111.jsonl"
        agent_lines = agent.read_text(encoding="utf-8").splitlines()
        self.session.transcript.write_text("\n".join(lines[:3]) + "\n" + lines[3][:40], encoding="utf-8", newline="\n")  # a line half written
        agent.write_text(agent_lines[0] + "\n", encoding="utf-8", newline="\n")
        ledger = self.ledger()
        self.assertEqual(ledger.cost(R1), {usage.SUPERVISOR: {"calls": 1, "output": 100, "input": 30, "cached": 1000, "seconds": 2.0}})
        self.assertFalse(ledger.settled(R1, BASE))  # its newest answer went on to call a tool
        with open(self.session.transcript, "a", encoding="utf-8", newline="\n") as f:
            f.write(lines[3][40:] + "\n" + "\n".join(lines[4:6]) + "\n")
        write(agent, agent_lines[1:], "a")
        ledger.update()
        self.assertEqual(ledger.cost(R1)[usage.SUPERVISOR]["calls"], 2)
        self.assertEqual(ledger.cost(R1)["unity-mygame"], whole["unity-mygame"])
        self.assertTrue(ledger.settled(R1, BASE + 2.5))    # the turn that handed the work out is over
        self.assertFalse(ledger.settled(R1, BASE + 22.5))  # but not the one that answers at :22.5, which is yet to be written
        write(self.session.transcript, lines[6:], "a")
        ledger.update()
        ledger.update()  # nothing new: nothing counted again
        self.assertEqual(ledger.cost(R1), whole)
        self.assertTrue(ledger.settled(R1, BASE + 22.5))
        self.assertFalse(ledger.settled(R1, BASE + 24))  # an answer sent after everything in the file
        self.assertFalse(ledger.settled(R2, 0))

    def test_lines_it_does_not_understand_are_passed_over(self):
        self.session.supervisor(
            "not json", "[1, 2]", '"text"', {"type": "summary", "summary": "x"},
            channel(R1, "큐브", 0),
            {"type": "assistant", "timestamp": at(1), "message": {"id": "s0", "content": "plain", "usage": "none"}},          # no tokens to read
            {"type": "assistant", "timestamp": at(1), "message": "gone"},
            {"type": "assistant", "timestamp": "yesterday",                                                                  # no time: tokens still count
             "message": {"id": "s1", "content": [{"type": "text"}, "odd"], "stop_reason": "end_turn",
                         "usage": {"output_tokens": 5, "input_tokens": "7", "cache_read_input_tokens": None, "server_tool_use": {"x": 1}}}},
            {"type": "assistant", "timestamp": at(3), "uuid": "u-1", "message": {"content": [], "usage": {"output_tokens": 2}}},  # no id: by its uuid
            {"type": "assistant", "timestamp": at(3), "uuid": "u-1", "message": {"content": [], "usage": {"output_tokens": 2}}})
        ledger = self.ledger()
        self.assertEqual(ledger.cost(R1), {usage.SUPERVISOR: {"calls": 2, "output": 7, "input": 0, "cached": 0, "seconds": 3.0}})
        self.assertEqual(usage.Ledger(Path(self.tmp.name) / "nothing.jsonl").cost(R1), {})
        missing = usage.Ledger(Path(self.tmp.name) / "nothing.jsonl")
        missing.update()  # no file, no folder of subagents: no tokens, no error
        self.assertEqual((missing.requests, missing.settled(R1, 0)), ({}, False))
        self.session.transcript.write_text("", encoding="utf-8")  # replaced by a shorter file: read from its start again
        self.session.supervisor(channel(R2, "x", 0), answer("n1", 1, output=9, stop="end_turn"))
        ledger.update()
        self.assertEqual(ledger.cost(R2)[usage.SUPERVISOR]["output"], 9)

    def test_a_subagent_is_named_by_the_file_beside_its_transcript(self):
        self.session.supervisor(channel(R1, "큐브", 0), answer("s1", 1, output=1, stop="end_turn"))
        path = self.session.agent("new1", None, user("coordinator", f"[요청 {R1}] 큐브", 2), answer("a1", 3, output=10, stop="end_turn"))
        ledger = self.ledger()
        self.assertEqual(sorted(ledger.cost(R1)), [usage.SUPERVISOR])  # a moment old and not named yet: looked at again next time
        (self.session.subagents / "agent-new1.meta.json").write_text(json.dumps({"agentType": "unity-mygame"}), encoding="utf-8")
        ledger.update()
        self.assertEqual(ledger.cost(R1)["unity-mygame"]["output"], 10)
        old = self.session.agent("old1", None, user("coordinator", f"[요청 {R1}] 큐브", 4), answer("o1", 5, output=20, stop="end_turn"))
        os.utime(old, (time.time() - 60, time.time() - 60))
        (self.session.subagents / "agent-bad1.meta.json").write_text("[1]", encoding="utf-8")
        bad = self.session.subagents / "agent-bad1.jsonl"
        write(bad, flat(user("coordinator", f"[요청 {R1}] 큐브", 6), answer("x1", 7, output=40, stop="end_turn")))
        os.utime(bad, (time.time() - 60, time.time() - 60))
        ledger.update()
        self.assertEqual({who: one["output"] for who, one in ledger.cost(R1).items()},
                         {usage.SUPERVISOR: 1, "unity-mygame": 10, "agent-old1": 20, "agent-bad1": 40})  # never named: by its id
        self.assertTrue(path.exists())

    def test_a_long_transcript_is_read_from_where_this_run_began(self):
        old = []
        for i in range(1500):  # days of earlier requests, well over a megabyte
            request = f"1120-{i:06d}-{i}"
            old += flat(channel(request, "옛 요청 " + "가" * 300, -90000 + i * 10), answer(f"old{i}", -90000 + i * 10 + 5, output=1, cached=10, stop="end_turn"))
        new = flat(channel(R1, "큐브 만들어줘", 0), answer("s1", 2, output=100, fresh=10, cached=1000, stop="end_turn"))
        self.session.supervisor(old, new)
        self.assertGreater(self.session.transcript.stat().st_size, usage.SEEK_ABOVE)
        whole, recent = self.ledger(), self.ledger(since=BASE - 60)
        self.assertEqual(len(whole.requests), 1501)
        self.assertEqual(recent.cost(R1), whole.cost(R1))
        self.assertEqual(recent.cost(R1)[usage.SUPERVISOR]["output"], 100)
        self.assertLess(len(recent.requests), 400)  # most of the old ones were never read
        self.assertNotIn("1120-000000-0", recent.requests)
        self.session.supervisor(flat(channel(R2, "하나 더", 10), answer("s2", 12, output=7, stop="end_turn")), mode="a")
        recent.update()  # from then on it reads what is added, as any other
        self.assertEqual(recent.cost(R2)[usage.SUPERVISOR]["output"], 7)
        early = self.ledger(since=BASE - 10 ** 6)  # older than the whole file: all of it
        self.assertEqual(len(early.requests), 1502)


class ReadingTests(unittest.TestCase):
    def test_what_starts_a_turn(self):
        self.assertTrue(usage.starts_a_turn(channel(R1, "x", 0)))
        self.assertTrue(usage.starts_a_turn({"type": "user", "message": {"content": "터미널에서 친 것"}}))  # an older Claude Code: no origin
        self.assertTrue(usage.starts_a_turn({"type": "user", "message": {"content": [{"type": "text", "text": "붙여 넣은 것"}]}}))
        self.assertFalse(usage.starts_a_turn(result("t1", 0)))
        self.assertFalse(usage.starts_a_turn({"type": "user", "isMeta": True, "message": {"content": "note"}}))
        self.assertFalse(usage.starts_a_turn({"type": "user", "message": {"content": " <system-reminder>x</system-reminder>"}}))
        self.assertFalse(usage.starts_a_turn({"type": "user", "isCompactSummary": True,  # Claude Code compacted the conversation
                                              "message": {"content": "This session is being continued from a previous conversation"}}))
        self.assertFalse(usage.starts_a_turn(answer("s1", 0)[0]))
        self.assertEqual(usage.said({"message": {"content": [{"type": "text", "text": "a"}, {"type": "image"}, "b", {"type": "text", "text": "c"}]}}), "a c")
        self.assertEqual(usage.said({"message": None}), "")
        self.assertEqual(usage.stamp({"timestamp": "2026-11-23T03:00:01.250Z"}), BASE + 1.25)
        self.assertEqual((usage.stamp({}), usage.stamp({"timestamp": 5}), usage.stamp({"timestamp": "soon"})), (0.0, 0.0, 0.0))

    def test_the_request_tag_is_read_as_the_supervisors_hook_reads_it(self):
        import supervisor_hook
        for text in (f"[요청 {R1}] 큐브", f"할 일:\n[ 요청: {R1} ]", "[요청 없음] 저장", "[요청 none]", "큐브", "[요청 큐브]", "", None):
            self.assertEqual(usage.tagged(text), supervisor_hook.tag_of(text), text)

    def test_a_channel_message_without_a_request_id_is_nobodys_request(self):
        reader = usage.SupervisorReader({}, lambda agent: None)
        self.assertEqual(reader.request_of(user("channel", '<channel source="claude-hand" chat_id="unity-mygame">x</channel>', 0)), "")
        self.assertEqual(reader.request_of(channel(R1, "x", 0)), R1)
        self.assertEqual(reader.request_of(user("human", f'<channel request="{R1}"> 를 흉내 낸 글', 0)), "")  # only a channel line counts


class WordsTests(unittest.TestCase):
    def test_numbers_the_way_they_are_said(self):
        self.assertEqual([usage.count(n) for n in (0, 950, 1000, 3940, 9949, 9950, 12345, 99499, 99500, 660_000, 4_550_000, 99_499_999, 130_000_000)],
                         ["0", "950", "1.0천", "3.9천", "9.9천", "1.0만", "1.2만", "9.9만", "10만", "66만", "455만", "9950만", "1.3억"])
        self.assertEqual([usage.duration(s) for s in (-3, 0, 0.4, 32.4, 59.6, 60, 125, 3599, 3600, 7384)],
                         ["0초", "0초", "0초", "32초", "1분", "1분", "2분 5초", "59분 59초", "1시간 0분", "2시간 3분"])

    def test_the_line_for_the_panel_and_the_detail_for_the_log(self):
        cost = {"unity-mygame": {"calls": 2, "output": 250, "input": 4310, "cached": 4300, "seconds": 16.5},
                usage.SUPERVISOR: {"calls": 4, "output": 200, "input": 77, "cached": 4900, "seconds": 6.0},
                "blender-props": usage.blank()}  # handed nothing in the end
        self.assertEqual(usage.line(32.4, 2, cost), "32초 · 동작 2번 · 토큰 출력 450 · 새 입력 4.4천 · 다시 읽기 9.2천")
        self.assertEqual(usage.line(125, 0, {}), "2분 5초")                      # no transcript to read: the time alone
        self.assertEqual(usage.line(3, 1, {"x": usage.blank()}), "3초 · 동작 1번")
        self.assertEqual(usage.detail(cost), "감독 6초 API 4번 출력 200 · 새 입력 77 · 다시 읽기 4.9천 / "
                                             "unity-mygame 16초 API 2번 출력 250 · 새 입력 4.3천 · 다시 읽기 4.3천")
        self.assertEqual(usage.detail({}), "")
        self.assertEqual(usage.detail({"unity-mygame": {"calls": 1, "output": 5}}),  # as read back from an older log line
                         "unity-mygame 0초 API 1번 출력 5 · 새 입력 0 · 다시 읽기 0")


if __name__ == "__main__":
    unittest.main()
