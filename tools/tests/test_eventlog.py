"""Tests for tools/handlib/eventlog.py and `claude_hand.py trace`. Run: python -m unittest discover -s tools/tests -v"""

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CLAUDE_HAND_LOG"] = "off"  # the tests leave the real event log alone
os.environ["CLAUDE_HAND_LANG"] = "ko"  # the words here are the Korean ones, whatever this PC is set to
import chat_channel as cc  # noqa: E402
import claude_hand as ch  # noqa: E402
import guard  # noqa: E402
import handlib.discovery  # noqa: E402
import unity_bridge as ub  # noqa: E402
from handlib import agents, eventlog  # noqa: E402

NOON = time.mktime((2026, 11, 10, 12, 0, 0, 0, 0, -1))


class FileTests(unittest.TestCase):
    def test_one_line_per_event_in_a_file_per_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            eventlog.write("message", home, NOON, request="1110-120000-1", chat_id="unity-mygame", text="큐브 만들어줘", agent_id="")
            eventlog.write("reply", home, NOON + 5, request="1110-120000-1", chat_id="unity-mygame", text="만들었어요", working=None)
            eventlog.write("message", home, NOON + 86400, request="1111-120000-9", chat_id="unity-mygame", text="하나 더")
            self.assertEqual(sorted(p.name for p in (home / "logs").iterdir()), ["2026-11-10.jsonl", "2026-11-11.jsonl"])
            first = json.loads((home / "logs" / "2026-11-10.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first, {"ts": NOON, "event": "message", "request": "1110-120000-1", "chat_id": "unity-mygame",
                                     "text": "큐브 만들어줘"})  # empty fields are left out; Korean is kept readable
            with open(home / "logs" / "2026-11-11.jsonl", "a", encoding="utf-8") as f:
                f.write("half a li\n[1, 2]\n")  # a line cut off by a crash, something else's line
            self.assertEqual([e["event"] for e in eventlog.read(home)], ["message", "reply", "message"])
            self.assertEqual(len(eventlog.read(home, days=1)), 1)
            self.assertEqual(eventlog.clean(home, keep_days=14, now=NOON + 15 * 86400), 1)  # the 10th is too old by then
            self.assertEqual([p.name for p in (home / "logs").iterdir()], ["2026-11-11.jsonl"])
            self.assertEqual(eventlog.read(Path(tmp) / "nothing"), [])

    def test_writing_never_raises_and_can_be_turned_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "file"
            blocked.write_text("not a folder", encoding="utf-8")
            eventlog.write("message", blocked, NOON, text="x")  # logs/ cannot be made under a file: nothing happens
            with unittest.mock.patch.dict(os.environ, {"CLAUDE_HAND_HOME": tmp, "CLAUDE_HAND_LOG": "off"}):
                eventlog.write("message", text="x")
                self.assertFalse((Path(tmp) / "logs").exists())
            with unittest.mock.patch.dict(os.environ, {"CLAUDE_HAND_HOME": tmp, "CLAUDE_HAND_LOG": ""}):
                eventlog.write("message", text="x")
                self.assertTrue((Path(tmp) / "logs").is_dir())
        self.assertEqual(eventlog.short("  여러\n줄   글  "), "여러 줄 글")
        self.assertEqual(len(eventlog.short("x" * 500)), eventlog.TEXT_LIMIT)


class TraceTests(unittest.TestCase):
    """A request that goes through the real channel hub, guard and bridge, each writing to one log."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        files = agents.build_agent_files([handlib.discovery.Instance(name="MyGame", hash="aaaa1111"),
                                          handlib.discovery.Instance(name="Props", hash="bbbb2222")],
                                         handlib.discovery.ServerConfig(transport="stdio"), [], None, "uv run")
        agents.write_agent_files(files, Path(self.tmp.name) / "agents")
        self.hub = cc.ChatHub(lambda message: None, Path(self.tmp.name) / "agents")
        self.hub.initialized = True
        self.write = lambda event, **fields: eventlog.write(event, self.home, **fields)
        self.hub.events = self.write

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_request_from_message_to_reply(self):
        hub = self.hub
        hub.post_user_message("unity", "MyGame", "Props에 있는 의자 가져와")
        hub.report_action("unity", "Props@bbbb2222", "ch_export_objects", "chair", "aaa")       # asked in MyGame's panel, done in Props
        bridge = ub.BridgeServer(io.StringIO(), io.StringIO(), "bbbb2222", "Props", directory=Path(self.tmp.name) / "none")
        bridge.log_event = self.write
        bridge.call("ch_export_objects", {})                                                     # Props is not open: an error, logged
        guard.note_refusal({"tool_name": "mcp__unity_other__manage_scene", "agent_id": "aaa"}, "Props@bbbb2222", "unity",
                           "이 하위 세션은 Props@bbbb2222 전용입니다.", write=self.write)
        hub.agent_stopped("unity", "Props@bbbb2222", "aaa")
        hub.report_action("unity", "MyGame@aaaa1111", "ch_import_package", "chair", "bbb")
        self.assertTrue(hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "save", "ccc")["busy"])
        hub.reply("unity-mygame", "가져오는 중이에요", working=True)
        hub.agent_stopped("unity", "MyGame@aaaa1111", "bbb")
        hub.reply("unity-mygame", "의자를 가져왔어요")
        hub.report_action("unity", "Props@bbbb2222", "manage_scene", "save", "ddd")              # afterwards, asked in the Claude app
        hub.reply("unity-props", "저장했어요", origin="desktop", request="Props 씬 저장")

        events = eventlog.read(self.home)
        listed = eventlog.requests(events)
        self.assertEqual(len(listed), 1)
        request = listed[0]
        self.assertRegex(request["request"], r"^\d{4}-\d{6}-\d+$")
        self.assertEqual((request["chat_id"], request["text"], request["actions"], request["refused"]),
                         ("unity-mygame", "Props에 있는 의자 가져와", 2, 2))  # refused: once by the guard itself, once by the channel
        self.assertIsNotNone(request["answered"])
        lines = eventlog.trace(events, request["request"])
        self.assertEqual([e["event"] for e in lines],
                         ["message", "action", "call", "refused", "stopped", "action", "refused", "reply", "stopped", "reply"])
        self.assertEqual([e.get("chat_id") or e.get("pin") for e in lines if e["event"] in ("action", "call")],
                         ["unity-props", "Props@bbbb2222", "unity-mygame"])
        call = next(e for e in lines if e["event"] == "call")
        self.assertEqual((call["tool"], call["ok"], call["code"]), ("ch_export_objects", False, "NOT_FOUND"))
        exported, imported = (e for e in lines if e["event"] == "action")
        self.assertNotIn("first", exported)  # exporting leaves Props as it was: no undo point there
        self.assertTrue(imported["first"])   # the first action that may change something: where the undo point is set
        self.assertNotIn("저장했어요", json.dumps(lines, ensure_ascii=False))  # work asked elsewhere afterwards is not this request
        after = [e for e in events if e["ts"] >= lines[-1]["ts"] and e not in lines]
        self.assertEqual([(e["event"], e.get("request")) for e in after], [("action", None), ("reply", None)])
        text = "\n".join(eventlog.describe(e, since=request["ts"]) for e in lines)
        for expected in ("요청", "동작", "ch 호출", "막음", "멈춤", "답", "unity-props", "실패 NOT_FOUND", "되돌리기 지점", "하는 중이라는 답", "초 만에"):
            self.assertIn(expected, text)

    def test_the_guard_logs_only_the_calls_it_refuses_itself(self):
        noted = []
        args = guard.build_parser().parse_args(["--pin", "MyGame@aaaa1111", "--server", "unity_mygame"])
        wrong = {"tool_name": "mcp__unity_mygame__manage_scene", "tool_input": {"unity_instance": "Props@bbbb2222"}, "agent_id": "aaa"}
        fine = {"tool_name": "mcp__unity_mygame__manage_scene", "tool_input": {}}
        with unittest.mock.patch.object(guard, "note_refusal", lambda *a, **kw: noted.append(a)), \
                unittest.mock.patch.object(guard, "dialog_block", lambda *a, **kw: None), \
                unittest.mock.patch.object(guard, "report_progress", lambda *a, **kw: "다른 하위 세션이 작업 중"):
            guard.run_guard(args, io.StringIO(json.dumps(wrong)), io.StringIO())
            guard.run_guard(args, io.StringIO(json.dumps(fine)), io.StringIO())  # refused by the channel, which logs it itself
        self.assertEqual(len(noted), 1)
        self.assertEqual(noted[0][1:3], ("MyGame@aaaa1111", "unity"))
        guard.note_refusal(wrong, "MyGame@aaaa1111", "unity", "x", write=lambda *a, **kw: 1 / 0)  # never raises

    def test_trace_command(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_gameobject", "create Cube", "aaa")
        self.hub.reply("unity-mygame", "만들었어요")
        self.hub.post_user_message("unity", "MyGame", "하나 더")

        run = lambda *argv: run_cli(self.home, "trace", *argv)
        code, listing = run()
        self.assertEqual(code, 0)
        self.assertIn("큐브 만들어줘", listing)
        self.assertIn("답 없음", listing)  # the second request
        first = json.loads(run("--json")[1])[0]["request"]
        code, detail = run("--request", first)
        self.assertEqual(code, 0)
        self.assertIn("manage_gameobject create Cube", detail)
        self.assertNotIn("하나 더", detail)
        code, last = run("unity-mygame")
        self.assertIn("하나 더", last)
        self.assertIn("아직 답이 없습니다", last)
        self.assertEqual(run("unity-nobody")[0], 1)
        self.assertEqual([e["event"] for e in json.loads(run("--request", first, "--json")[1])], ["message", "action", "reply"])

    def test_what_a_request_took_in_trace_and_usage(self):
        """The channel writes it down when a request is answered (handlib/usage.py reads the tokens from Claude Code's
        transcripts): `trace` shows it as the request's last line, `usage` adds the requests up."""
        hub = self.hub
        self.assertIn("시간·토큰 기록이 없습니다", run_cli(self.home, "usage")[1])
        cost = {"감독": {"calls": 4, "output": 200, "input": 77, "cached": 4900, "seconds": 6.0},
                "unity-mygame": {"calls": 2, "output": 250, "input": 4310, "cached": 4300, "seconds": 16.5}}
        ledger = type("Ledger", (), {"update": lambda self: None, "settled": lambda self, request, after: True,
                                     "cost": lambda self, request: cost})()
        hub._ledger = lambda: ledger
        hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        first = hub.logs["unity-mygame"][-1]["request"]
        hub.report_action("unity", "MyGame@aaaa1111", "manage_gameobject", "create Cube", "aaa")
        hub.reply("unity-mygame", "만들었어요")
        hub.costs[first]["asked"] = hub.costs[first]["answered"] - 32.4
        self.assertEqual(hub.check_costs(), [first])
        hub._ledger = lambda: None  # a request with no transcript to read: the time alone
        hub.post_user_message("unity", "MyGame", "지금 씬 이름이 뭐야?")
        second = hub.logs["unity-mygame"][-1]["request"]
        hub.reply("unity-mygame", "Main이에요")
        hub.costs[second]["asked"] = hub.costs[second]["answered"] - 4
        self.assertEqual(hub.check_costs(), [second])
        yesterday = time.time() - 86400  # and one from the day before, as the log keeps it
        eventlog.write("message", self.home, yesterday, request="1109-090000-1", chat_id="blender-props", text="의자 만들어줘")
        eventlog.write("cost", self.home, yesterday + 70, request="1109-090000-1", chat_id="blender-props", seconds=70.2, actions=3,
                       tokens={"감독": {"calls": 3, "output": 1000, "input": 2000, "cached": 300000, "seconds": 9.0},
                               "blender-props": {"calls": 5, "output": 3000, "input": 9000, "cached": 360000, "seconds": 55.0}}, text="x")

        events = eventlog.read(self.home)
        self.assertEqual([e["event"] for e in eventlog.trace(events, first)], ["message", "action", "reply", "cost"])
        code, detail = run_cli(self.home, "trace", "--request", first)
        took = detail.splitlines()[-1]
        self.assertIn("시간·토큰", took)
        self.assertIn("unity-mygame  32.4초 · 동작 1번  감독 6초 API 4번 출력 200 · 새 입력 77 · 다시 읽기 4.9천 / "
                      "unity-mygame 16초 API 2번 출력 250 · 새 입력 4.3천 · 다시 읽기 4.3천", took)
        self.assertIn("4.0초  (토큰 기록 없음)", run_cli(self.home, "trace", "--request", second)[1].splitlines()[-1])
        listed = {r["request"]: r for r in eventlog.requests(events)}
        self.assertEqual((listed[first]["actions"], listed[first]["refused"]), (1, 0))  # the line counts as neither

        code, text = run_cli(self.home, "usage")
        self.assertEqual(code, 0)
        rows, sums = text.split("\n\n합계: ")
        rows = rows.splitlines()
        self.assertEqual(len(rows), 3)
        self.assertIn('blender-props            1분 10초 · 동작 3번 · 출력 4.0천 · 새 입력 1.1만 · 다시 읽기 66만  "의자 만들어줘"', rows[0])
        self.assertIn('unity-mygame             32초 · 동작 1번 · 출력 450 · 새 입력 4.4천 · 다시 읽기 9.2천  "큐브 만들어줘"', rows[1])
        self.assertIn('4초 · 동작 0번 · 토큰 기록 없음  "지금 씬 이름이 뭐야?"', rows[2])
        sums = sums.splitlines()
        self.assertEqual(sums[0], "요청 3개 · 기다린 시간 1분 47초 · 출력 4.5천 · 새 입력 1.5만 · 다시 읽기 67만")
        self.assertEqual([line.split()[0] for line in sums[1:4]], ["감독", "blender-props", "unity-mygame"])  # the supervisor first
        self.assertIn("API 7번 · 출력 1.2천 · 새 입력 2.1천 · 다시 읽기 30만", sums[1])
        self.assertEqual(sums[4], "날짜별:")
        self.assertRegex(sums[5], r"^  \d\d-\d\d  요청 1개 · 1분 10초 · 출력 4.0천 · 새 입력 1.1만 · 다시 읽기 66만$")
        self.assertRegex(sums[6], r"^  \d\d-\d\d  요청 2개 · 36초 · 출력 450 · 새 입력 4.4천 · 다시 읽기 9.2천$")
        self.assertIn("다시 읽기: 캐시에서 다시 읽은 토큰", text)
        short = run_cli(self.home, "usage", "--last", "1")[1]
        self.assertNotIn("큐브 만들어줘", short)       # the list is cut
        self.assertIn("합계: 요청 3개", short)         # the sums are of all
        self.assertNotIn("날짜별", run_cli(self.home, "usage", "--days", "1")[1])  # one day: nothing to compare
        data = json.loads(run_cli(self.home, "usage", "--json")[1])
        self.assertEqual([(row["request"], row["asked"]) for row in data],
                         [("1109-090000-1", "의자 만들어줘"), (first, "큐브 만들어줘"), (second, "지금 씬 이름이 뭐야?")])
        self.assertEqual(data[1]["tokens"]["unity-mygame"]["output"], 250)
        self.assertNotIn("tokens", data[2])


def run_cli(home: Path, *argv):
    """A claude_hand.py command against the log in `home`: (exit code, what it printed)."""
    out = io.StringIO()
    with unittest.mock.patch.dict(os.environ, {"CLAUDE_HAND_HOME": str(home)}), contextlib.redirect_stdout(out):
        args = ch.build_parser().parse_args(list(argv))
        code = args.func(args)
    return code, out.getvalue()


if __name__ == "__main__":
    unittest.main()
