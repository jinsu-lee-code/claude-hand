"""Tests for tools/guard.py, the subagents' PreToolUse hook. Run: python -m unittest discover -s tools/tests -v"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
os.environ["CLAUDE_HAND_LOG"] = "off"  # the tests leave the real event log alone
import guard  # noqa: E402


class DecisionTests(unittest.TestCase):
    PIN = "MyGame@aaaa1111"
    SERVER = "unity_mygame"

    def decide(self, tool, tool_input=None, transport="stdio"):
        return guard.guard_decision({"tool_name": tool, "tool_input": tool_input or {}}, self.PIN, self.SERVER, transport)

    def test_own_tools_pass(self):
        self.assertIsNone(self.decide("mcp__unity_mygame__ch_scene_snapshot", {"depth": 2}))
        self.assertIsNone(self.decide("mcp__unity_mygame__manage_scene", {"action": "get_hierarchy", "unity_instance": "MyGame@aaaa1111"}))
        self.assertIsNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "MyGame"}))
        self.assertIsNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "aaaa"}))

    def test_non_mcp_tools_pass(self):
        self.assertIsNone(self.decide("Read", {"file_path": "x"}))

    def test_other_project_is_blocked(self):
        self.assertIsNotNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "Props@bbbb2222"}))
        self.assertIsNotNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "6402"}))
        self.assertIsNotNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "aa"}))
        self.assertIsNotNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "MyGame@bbbb"}))

    def test_nested_batch_commands_are_checked(self):
        batch = {"commands": [{"tool": "manage_scene", "params": {"unity_instance": "Props@bbbb2222"}}]}
        self.assertIsNotNone(self.decide("mcp__unity_mygame__batch_execute", batch))

    def test_other_servers_are_blocked(self):
        self.assertIsNotNone(self.decide("mcp__unityMCP__manage_scene", {}))
        self.assertIsNotNone(self.decide("mcp__unity_props__ch_scene_snapshot", {}))

    def test_the_bridge_server_passes_and_other_bridges_do_not(self):
        self.assertIsNone(self.decide("mcp__unity_mygame_ch__ch_scene_snapshot"))
        self.assertIsNotNone(self.decide("mcp__unity_other_ch__ch_scene_snapshot"))
        self.assertIsNotNone(self.decide("mcp__unity-mcp__Unity_ManageGameObject"))

    def test_set_active_instance(self):
        self.assertIsNotNone(self.decide("mcp__unity_mygame__set_active_instance", {"instance": self.PIN}, "stdio"))
        self.assertIsNone(self.decide("mcp__unity_mygame__set_active_instance", {"instance": self.PIN}, "http"))
        self.assertIsNotNone(self.decide("mcp__unity_mygame__set_active_instance", {"instance": "Props@bbbb2222"}, "http"))

    def test_blender_mode(self):
        own = {"tool_name": "mcp__blender_character__execute_code", "tool_input": {"code": "print(1)"}}
        other = {"tool_name": "mcp__blender_props__execute_code", "tool_input": {}}
        self.assertIsNone(guard.guard_decision(own, "character@9876", "blender_character", "stdio", "blender"))
        reason = guard.guard_decision(other, "character@9876", "blender_character", "stdio", "blender")
        self.assertIn("Blender", reason)

    def test_unity_calls_are_refused_while_a_dialog_is_up(self):
        save = {"title": "Scene(s) Have Been Modified", "text": "Do you want to save the changes?",
                "buttons": ["Save", "Don't Save", "Cancel"], "progress": False}
        event = {"tool_name": "mcp__unity_hand_test__manage_scene", "tool_input": {"action": "save"}}
        reason = guard.dialog_block(event, "hand_test@49ac508c", "unity_hand_test", waiting=lambda: [save])
        self.assertIn("대화상자 때문에 멈춰 있어 이 호출을 막았습니다", reason)
        self.assertIn("Don't Save", reason)
        self.assertIsNone(guard.dialog_block(event, "hand_test@49ac508c", "unity_hand_test", waiting=lambda: []))
        self.assertIsNone(guard.dialog_block({"tool_name": "Read"}, "hand_test@49ac508c", "unity_hand_test",
                                             waiting=lambda: [save]))  # not a Unity call
        self.assertIsNone(guard.dialog_block(event, "hand_test@49ac508c", "unity_hand_test", waiting=lambda: 1 / 0))


class HookTests(unittest.TestCase):
    PIN = "MyGame@aaaa1111"
    SERVER = "unity_mygame"

    def args(self, *more):
        return guard.build_parser().parse_args(["--pin", self.PIN, "--server", self.SERVER, *more])

    def test_output_format(self):
        out = io.StringIO()
        event = json.dumps({"tool_name": "mcp__unityMCP__manage_scene", "tool_input": {}})
        self.assertEqual(guard.run_guard(self.args(), io.StringIO(event), out), 0)
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual((decision["hookEventName"], decision["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertTrue(decision["permissionDecisionReason"])

    def test_allowed_call_prints_nothing(self):
        out, reported = io.StringIO(), []
        event = json.dumps({"tool_name": "mcp__unity_mygame_ch__ch_editor_status", "tool_input": {}})
        with unittest.mock.patch.object(guard, "report_progress", lambda *a, **kw: reported.append(a)), \
                unittest.mock.patch.object(guard, "dialog_block", lambda *a, **kw: None):
            guard.run_guard(self.args(), io.StringIO(event), out)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(len(reported), 1)  # an allowed call is shown in the window's panel

    def test_broken_input_fails_closed(self):
        for broken in ("{not json", "[1, 2]"):
            out = io.StringIO()
            guard.run_guard(self.args(), io.StringIO(broken), out)
            self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_refused_call_is_neither_reported_nor_marked(self):
        out, reported = io.StringIO(), []
        event = json.dumps({"tool_name": "mcp__unity_mygame__manage_scene", "tool_input": {"unity_instance": "Props@bbbb2222"}})
        with unittest.mock.patch.object(guard, "report_progress", lambda *a, **kw: reported.append(a)):
            guard.run_guard(self.args(), io.StringIO(event), out)
        self.assertEqual(reported, [])

    def test_the_process_only_ends_with_0_or_2(self):
        """Claude Code lets the call through on any other exit code, so nothing may end the hook with one."""
        self.assertEqual(guard.exit_code(lambda: 0), 0)
        self.assertEqual(guard.exit_code(lambda: None), 0)
        self.assertEqual(guard.exit_code(lambda: 1), 2)
        self.assertEqual(guard.exit_code(lambda: sys.exit(1)), 2)
        self.assertEqual(guard.exit_code(lambda: sys.exit(2)), 2)  # argparse: bad arguments
        self.assertEqual(guard.exit_code(lambda: sys.exit(2), stop_hook=True), 0)  # a Stop hook never keeps a subagent going
        with unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(guard.exit_code(lambda: 1 / 0), 2)
            self.assertEqual(guard.exit_code(lambda: (_ for _ in ()).throw(KeyboardInterrupt())), 2)


class ProcessTests(unittest.TestCase):
    """The hook as Claude Code starts it: its own process, the event as UTF-8 on stdin."""

    def run_hook(self, event, *args):
        data = event if isinstance(event, bytes) else json.dumps(event, ensure_ascii=False).encode("utf-8")
        return subprocess.run([sys.executable, str(TOOLS / "guard.py"), *args], input=data, capture_output=True, timeout=60)

    def test_refuses_another_project_and_keeps_korean_intact(self):
        event = {"tool_name": "mcp__unity_mygame__manage_scene", "tool_input": {"unity_instance": "큐브@bbbb2222"}}
        proc = self.run_hook(event, "--pin", "MyGame@aaaa1111", "--server", "unity_mygame")
        self.assertEqual(proc.returncode, 0)
        decision = json.loads(proc.stdout.decode("ascii"))["hookSpecificOutput"]  # ASCII: safe in any code page
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("큐브@bbbb2222", decision["permissionDecisionReason"])

    def test_lets_other_tools_through_silently(self):
        proc = self.run_hook({"tool_name": "Read", "tool_input": {}}, "--pin", "MyGame@aaaa1111", "--server", "unity_mygame")
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))

    def test_bad_arguments_and_bad_input_refuse_the_call(self):
        self.assertEqual(self.run_hook({}, "--pin", "MyGame@aaaa1111").returncode, 2)  # --server missing
        self.assertEqual(self.run_hook({}, "--pin", "x@y", "--server", "s", "--app", "nope").returncode, 2)
        proc = self.run_hook(b"\xff\xfe not utf-8", "--pin", "MyGame@aaaa1111", "--server", "unity_mygame")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_as_the_stop_hook_it_never_refuses_anything(self):
        for args in (["--pin", "MyGame@aaaa1111", "--server", "unity_mygame", "--stop"], ["--stop"], ["--stop", "--app", "nope"]):
            proc = self.run_hook(b"\xff not json", *args)
            self.assertEqual((proc.returncode, proc.stdout), (0, b""), args)

    def test_deciding_loads_nothing_but_the_standard_library(self):
        """A mistake in claude_hand.py, the bridge or handlib must not be able to stop the guard from deciding."""
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import guard\n"
                "guard.guard_decision({'tool_name': 'mcp__unity_x__manage_scene', 'tool_input': {'unity_instance': 'a@b'}}, "
                "'x@00000000', 'unity_x', 'stdio')\n"
                "print(sorted(m for m in ('claude_hand', 'handlib', 'unity_bridge', 'chat_channel') if m in sys.modules))")
        proc = subprocess.run([sys.executable, "-c", code, str(TOOLS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.stdout.strip(), "[]", proc.stderr)


class ProgressTests(unittest.TestCase):
    PIN = "MyGame@aaaa1111"
    SERVER = "unity_mygame"

    def home(self, tmp):
        home = Path(tmp)
        (home / "chat").mkdir()
        (home / "chat" / "server.json").write_text(json.dumps({"port": 8765, "token": "t"}), encoding="utf-8")
        return home

    def test_allowed_calls_are_reported_to_the_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.home(tmp)
            sent = []
            post = lambda req: sent.append((req.full_url, req.headers.get("X-claude-hand-token"), json.loads(req.data)))
            event = {"tool_name": "mcp__unity_mygame__manage_gameobject", "tool_input": {"action": "create", "name": "Cube"}}
            guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=post)
            guard.report_progress({"tool_name": "mcp__unity_mygame_ch__ch_scene_snapshot", "tool_input": {"depth": 2}},
                                  self.PIN, self.SERVER, "unity", home=home, post=post)
            guard.report_progress({"tool_name": "Read", "tool_input": {}}, self.PIN, self.SERVER, "unity", home=home, post=post)
            guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=lambda req: 1 / 0)  # never raises
        self.assertEqual(sent[0], ("http://127.0.0.1:8765/v1/progress", "t",
                                   {"app": "unity", "pin": self.PIN, "tool": "manage_gameobject", "agent_id": "",
                                    "detail": "create Cube"}))
        self.assertEqual(sent[1][2]["tool"], "ch_scene_snapshot")
        self.assertEqual(len(sent), 2)  # not for tools outside the subagent's own servers

    def test_first_action_of_a_request_sets_an_undo_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.home(tmp)
            marks = []
            event = {"tool_name": "mcp__unity_mygame__manage_gameobject", "tool_input": {"action": "create", "name": "Cube"}}
            note = lambda app, pin, label, key="": marks.append((app, pin, label, key))
            for reply in ({"new_request": True, "label": "큐브 만들어줘", "point": "1116-101500-7"}, {"new_request": False}, None):
                guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=lambda req, r=reply: r, mark=note,
                                      folder=lambda pin: None)
            guard.report_progress({"tool_name": "mcp__blender_props__execute_code", "tool_input": {"code": "x = 1"}},
                                  "props@9876", "blender_props", "blender", home=home,
                                  post=lambda req: {"new_request": True, "label": "의자"}, mark=note)
            # "cancel that" starts with the undo: no fresh point in front of it
            new = lambda req: {"new_request": True, "label": "방금 거 취소해줘"}
            guard.report_progress({"tool_name": "mcp__unity_mygame_ch__ch_checkpoint", "tool_input": {"action": "undo"}},
                                  self.PIN, self.SERVER, "unity", home=home, post=new, mark=note, folder=lambda pin: None)
            guard.report_progress({"tool_name": "mcp__blender_props__execute_code",
                                   "tool_input": {"code": "import claude_hand_chat; print(claude_hand_chat.undo_last_request())"}},
                                  "props@9876", "blender_props", "blender", home=home, post=new, mark=note)
        # once per request, under the name the channel gives the request
        self.assertEqual(marks, [("unity", self.PIN, "큐브 만들어줘", "1116-101500-7"), ("blender", "props@9876", "의자", "")])

    def test_blender_code_that_only_looks_is_reported_as_looking(self):
        """execute_code runs any Python, so its name does not say whether the request has changed anything yet: a
        question answered with it left an undo point with nothing to undo."""
        with tempfile.TemporaryDirectory() as tmp:
            home, sent = self.home(tmp), []
            post = lambda req: sent.append(json.loads(req.data)) or {"new_request": False}
            blender = lambda tool, **tool_input: guard.report_progress(
                {"tool_name": f"mcp__blender_props__{tool}", "tool_input": tool_input}, "props@9876", "blender_props", "blender", home=home, post=post)
            blender("execute_blender_code", code='import bpy\nprint(bpy.data.filepath, bpy.app.version_string, "dirty", bpy.data.is_dirty)')
            blender("execute_blender_code", code='import bpy\nbpy.data.objects["Cube"].location.x += 2')
            blender("execute_blender_code", code="import bpy\nprint(")  # cannot be read: may change
            blender("get_scene_info")  # its name says it
            # Unity's execute_code is C#, not read here: ChCheckpoint tells afterwards whether the point has anything to undo.
            guard.report_progress({"tool_name": "mcp__unity_mygame__execute_code", "tool_input": {"code": "print(1)"}},
                                  self.PIN, self.SERVER, "unity", home=home, post=post)
        self.assertEqual([body.get("looks") for body in sent], [True, None, None, None, None])
        self.assertEqual(sent[0]["detail"], 'print(bpy.data.filepath, bpy.app.version_string, "dirty", bpy.data.is_dirty)')

    def test_a_subagents_own_undo_point_is_refused_while_claude_hand_sets_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, marks = self.home(tmp), []
            mark = lambda app, pin, label, key="": marks.append(label)
            own = {"tool_name": "mcp__unity_mygame_ch__ch_checkpoint", "tool_input": {"action": "mark", "label": "Import CH_Capsule"}}
            going = lambda req: {"ok": True, "chat_id": "unity-mygame", "new_request": False, "label": ""}
            reason = guard.report_progress(own, self.PIN, self.SERVER, "unity", home=home, post=going, mark=mark)
            self.assertIn("이미 찍어 두었습니다", reason)
            self.assertIn("문제가 아니므로", reason)
            self.assertEqual(marks, [])
            # As the first action of a request: the request's point is set, under the user's words; the subagent's is not.
            first = lambda req: {"ok": True, "chat_id": "unity-mygame", "new_request": True, "label": "캡슐 보내줘"}
            default = {"tool_name": "mcp__unity_mygame_ch__ch_checkpoint", "tool_input": {"label": "x"}}  # no action: mark
            self.assertIn("이미 찍어 두었습니다", guard.report_progress(default, self.PIN, self.SERVER, "unity", home=home, post=first, mark=mark))
            self.assertEqual(marks, ["캡슐 보내줘"])
            # Without Claude Hand nobody else sets a point: the subagent's own goes through.
            self.assertIsNone(guard.report_progress(own, self.PIN, self.SERVER, "unity", home=home, post=lambda req: {}, mark=mark))
            self.assertIsNone(guard.report_progress(own, self.PIN, self.SERVER, "unity", home=home, post=lambda req: 1 / 0, mark=mark))
            for action in ("list", "undo"):
                event = {"tool_name": "mcp__unity_mygame_ch__ch_checkpoint", "tool_input": {"action": action}}
                self.assertIsNone(guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=first, mark=mark))
            self.assertEqual(marks, ["캡슐 보내줘"])

    def test_the_report_says_which_subagent_it_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, sent = self.home(tmp), []
            post = lambda req: sent.append((req.full_url, json.loads(req.data)))
            event = {"tool_name": "mcp__unity_mygame__manage_gameobject", "tool_input": {"action": "create"}, "agent_id": "a6183685"}
            guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=post)
            guard.report_stop({"hook_event_name": "SubagentStop", "agent_id": "a6183685"}, self.PIN, "unity", home=home, post=post)
            guard.report_stop({}, self.PIN, "unity", home=home, post=lambda req: 1 / 0)  # never raises
        self.assertEqual(sent[0][1]["agent_id"], "a6183685")
        self.assertEqual(sent[1], ("http://127.0.0.1:8765/v1/stopped", {"app": "unity", "pin": self.PIN, "agent_id": "a6183685"}))

    def test_a_window_where_another_subagent_works_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, marks = self.home(tmp), []
            event = {"tool_name": "mcp__unity_mygame__manage_gameobject", "tool_input": {"action": "create"}, "agent_id": "bbb"}
            busy = lambda req: {"ok": True, "busy": True, "other": "aaa", "new_request": True}
            reason = guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=busy, mark=lambda *a: marks.append(a))
            self.assertIn("다른 하위 세션이 작업 중", reason)
            self.assertEqual(marks, [])  # no undo point for a call that does not happen
            self.assertIsNone(guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=lambda req: {"ok": True}))
            out = io.StringIO()
            args = guard.build_parser().parse_args(["--pin", self.PIN, "--server", self.SERVER])
            with unittest.mock.patch.object(guard, "report_progress", lambda *a, **kw: reason), \
                    unittest.mock.patch.object(guard, "dialog_block", lambda *a, **kw: None):
                guard.run_guard(args, io.StringIO(json.dumps(event)), out)
            self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_call_after_the_panels_stop_button_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, marks = self.home(tmp), []
            event = {"tool_name": "mcp__unity_mygame__manage_gameobject", "tool_input": {"action": "create"}, "agent_id": "aaa"}
            stopped = lambda req: {"ok": True, "stop": True, "by": "unity-mygame", "new_request": True}
            reason = guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=stopped, mark=lambda *a: marks.append(a))
            self.assertIn("[중지]를 눌러 이 호출을 막았습니다", reason)
            self.assertIn("되돌리지는 마세요", reason)  # stopping is not undoing: that stays the user's call
            self.assertEqual(marks, [])

    def test_undo_point_goes_through_the_bridge(self):
        calls = []
        with unittest.mock.patch.dict(sys.modules, {"unity_bridge": unittest.mock.Mock(
                find_project=lambda hash_: {"hash": hash_, "port": 1} if hash_ == "aaaa1111" else None)}):
            sys.modules["unity_bridge"].request = lambda entry, tool, params, timeout: calls.append((entry["hash"], tool, params))
            guard.mark_checkpoint("unity", self.PIN, "큐브 만들어줘")
            guard.mark_checkpoint("unity", "Other@ffff0000", "x")  # not open: nothing to mark
            guard.mark_checkpoint("unity", self.PIN, "큐브 지워줘", key="1116-101500-7")
        self.assertEqual(calls, [("aaaa1111", "ch_checkpoint", {"action": "mark", "label": "큐브 만들어줘"}),
                                 ("aaaa1111", "ch_checkpoint", {"action": "mark", "label": "큐브 지워줘", "request": "1116-101500-7"})])

    def test_undo_point_waits_for_an_editor_that_is_reloading_scripts(self):
        """A request that follows one which changed a script starts while Unity reloads, with its bridge away for some
        seconds. Without its own undo point, its changes could only be undone together with the earlier request's."""
        class Away(Exception):
            def __init__(self, code):
                super().__init__(code)
                self.code = code

        def bridge(failures, clock=None):
            calls, waits = [], []

            def request(entry, tool, params, timeout):
                calls.append(entry["port"])
                if clock is not None:
                    clock.append(clock[-1] + 8.0)  # the attempt ran into its timeout
                if failures:
                    raise Away(failures.pop(0))
            module = unittest.mock.Mock(find_project=lambda hash_: {"hash": hash_, "port": 100 + len(calls)})
            with unittest.mock.patch.dict(sys.modules, {"unity_bridge": module}):
                try:
                    guard.mark_checkpoint("unity", self.PIN, "x", request=request, sleep=waits.append,
                                          **({"clock": lambda: clock[-1]} if clock is not None else {}))
                    return calls, waits, None
                except Away as exc:
                    return calls, waits, exc.code
        self.assertEqual(bridge(["UNREACHABLE", "EDITOR_BUSY"]), ([100, 101, 102], [guard.MARK_WAIT] * 2, None))  # found again each time
        calls, waits, code = bridge(["UNREACHABLE"] * 20)
        self.assertEqual((len(calls), len(waits), code), (guard.MARK_TRIES, guard.MARK_TRIES - 1, "UNREACHABLE"))  # not for ever
        self.assertEqual(bridge(["INVALID_ARGS"]), ([100], [], "INVALID_ARGS"))  # not a matter of waiting
        self.assertEqual(bridge(["UNREACHABLE"], clock=[0.0]), ([100], [], "UNREACHABLE"))  # stuck, not away: the call is not held up longer

    def test_blender_undo_point_goes_to_the_chat_addon(self):
        sent = []
        guard.mark_checkpoint("blender", "props@9876", "의자를 '빨갛게'", request=lambda port, command, timeout: sent.append((port, command)))
        port, command = sent[0]
        self.assertEqual((port, command["type"]), (9876, "execute_code"))
        namespace = {}
        exec(command["params"]["code"].replace("import claude_hand_chat\n", ""),
             {"claude_hand_chat": type("Addon", (), {"mark_undo_point": staticmethod(lambda label: namespace.update(label=label))}),
              "print": lambda *_: None})
        self.assertEqual(namespace["label"], "의자를 '빨갛게'")  # the request text arrives as it was, quotes and all
        # With the request's name (the channel gives it): the point carries it, so the request can be undone by name
        # in every window it changed. An add-on from before points had names still gets its point.
        guard.mark_checkpoint("blender", "props@9876", "의자", request=lambda port, command, timeout: sent.append((port, command)),
                              key="1123-101500-7")
        code = sent[1][1]["params"]["code"].replace("import claude_hand_chat\n", "")
        named, plain = {}, {}
        exec(code, {"claude_hand_chat": type("Addon", (), {"mark_undo_point": staticmethod(
            lambda label="", request="": named.update(label=label, request=request))}), "print": lambda *_: None})
        exec(code, {"claude_hand_chat": type("Addon", (), {"mark_undo_point": staticmethod(lambda label="": plain.update(label=label))}),
                    "print": lambda *_: None})
        self.assertEqual((named, plain), ({"label": "의자", "request": "1123-101500-7"}, {"label": "의자"}))

    def test_as_the_stop_hook_it_says_in_the_project_that_the_work_is_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project = self.home(tmp), Path(tmp) / "MyGame"
            event = {"tool_name": "mcp__unity_mygame__manage_gameobject", "tool_input": {"action": "create"}}
            guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=lambda req: {"point": "1116-101500-7"},
                                  folder=lambda pin: project)
            work = json.loads((project / "Library/ClaudeHand/at-work.json").read_text(encoding="utf-8"))
            self.assertEqual((work["request"], work["stopped"]), ("1116-101500-7", 0))
            guard.report_stop({}, self.PIN, "unity", home=home, post=lambda req: {}, folder=lambda pin: project)
            self.assertGreater(json.loads((project / "Library/ClaudeHand/at-work.json").read_text(encoding="utf-8"))["stopped"], 0)
            guard.report_stop({}, self.PIN, "unity", home=home, post=lambda req: {}, folder=lambda pin: 1 / 0)  # never raises
            guard.report_stop({}, "props@9876", "blender", home=home, post=lambda req: {}, folder=lambda pin: 1 / 0)  # not asked for Blender

    def test_action_summary(self):
        self.assertEqual(guard.action_summary({"action": "create", "name": "Cube", "position": [0, 0, 0]}), "create Cube")
        self.assertEqual(guard.action_summary({"code": "import bpy\n\nbpy.ops.mesh.primitive_uv_sphere_add()"}),
                         "bpy.ops.mesh.primitive_uv_sphere_add()")
        self.assertEqual(guard.action_summary({}), "")


class FileTests(unittest.TestCase):
    """Before a call that may write files, the files it names are copied into the project's Library, under the name
    of the request: undoing the request puts them back (handlib/keeps.py, ChCheckpoint.cs)."""
    PIN = "MyGame@aaaa1111"
    SERVER = "unity_mygame"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        (self.home / "chat").mkdir(parents=True)
        (self.home / "chat" / "server.json").write_text(json.dumps({"port": 8765, "token": "t"}), encoding="utf-8")
        self.project = Path(os.path.realpath(self.tmp.name)) / "MyGame"
        self.script = self.project / "Assets" / "Scripts" / "Player.cs"
        self.script.parent.mkdir(parents=True)
        self.script.write_text("class Player {}", encoding="utf-8")
        self.marks = []

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, tool, tool_input, reply, app="unity", pin=None, **more):
        post = reply if callable(reply) else (lambda req: reply)
        more.setdefault("folder", lambda pin: self.project)
        server = self.SERVER if app == "unity" else "blender_props"
        return guard.report_progress({"tool_name": f"mcp__{server}{'_ch' if tool.startswith('ch_') else ''}__{tool}", "tool_input": tool_input},
                                     pin or self.PIN, server, app, home=self.home, post=post,
                                     mark=lambda app, pin, label, key="": self.marks.append((label, key)), **more)

    def work(self):
        return json.loads((self.project / "Library/ClaudeHand/at-work.json").read_text(encoding="utf-8"))

    def kept(self, key):
        folder = self.project / "Library" / "ClaudeHand" / "replaced" / f"req_{key}"
        lines = (folder / "kept.jsonl").read_text(encoding="utf-8").splitlines() if (folder / "kept.jsonl").exists() else []
        return [json.loads(line)["path"] for line in lines]

    def test_the_file_a_call_names_is_kept_under_the_requests_name(self):
        edit = {"action": "update", "name": "Player", "path": "Assets/Scripts", "contents": "class Player { int hp; }"}
        going = {"chat_id": "unity-mygame", "new_request": True, "label": "체력 넣어줘", "point": "1116-101500-7"}
        self.assertIsNone(self.call("manage_script", edit, going))
        self.assertEqual(self.marks, [("체력 넣어줘", "1116-101500-7")])  # the undo point carries the same name
        self.assertEqual((self.work()["request"], self.work()["stopped"]), ("1116-101500-7", 0))
        self.assertEqual(self.kept("1116-101500-7"), ["Assets/Scripts/Player.cs"])
        self.script.write_text("class Player { int hp; }", encoding="utf-8")
        self.call("apply_text_edits", {"uri": "unity://path/Assets/Scripts/Player.cs", "edits": []}, {**going, "new_request": False})
        self.assertEqual(self.kept("1116-101500-7"), ["Assets/Scripts/Player.cs"])  # once: the copy is the file before the request
        self.assertEqual(len(self.marks), 1)

    def test_not_for_tools_that_only_look_nor_for_claude_hands_own(self):
        going = {"chat_id": "unity-mygame", "new_request": False, "label": "", "point": "1116-101500-7"}
        self.call("read_console", {"filter": "Assets/Scripts/Player.cs"}, going)
        self.call("find_gameobjects", {"search_term": "Player"}, going)
        self.call("ch_scene_snapshot", {"root": "Assets/Scripts/Player.cs"}, going)
        self.call("ch_import_model", {"manifest": "Assets/Scripts/Player.cs"}, going)  # keeps what it replaces itself
        self.assertEqual(self.kept("1116-101500-7"), [])
        self.assertEqual(self.work()["request"], "1116-101500-7")  # at work all the same: Unity notes what happens meanwhile

    def test_without_claude_hand_the_work_that_goes_on_is_one_request(self):
        edit = {"name": "Player", "path": "Assets/Scripts", "contents": "x"}
        self.assertIsNone(self.call("manage_script", edit, lambda req: 1 / 0))  # no channel answers
        first = self.work()["request"]
        self.assertTrue(first.startswith("local-"))
        self.assertEqual(self.kept(first), ["Assets/Scripts/Player.cs"])
        self.call("manage_gameobject", {"action": "create"}, {})  # a channel with nothing to say about this window
        self.assertEqual(self.work()["request"], first)
        time.sleep(0.005)
        # The subagent sets an undo point of its own (nobody else does): what follows is a new request, and the point takes its name.
        self.assertIsNone(self.call("ch_checkpoint", {"action": "mark", "label": "새 일"}, {}))
        second = self.work()["request"]
        self.assertNotEqual(second, first)
        self.call("manage_script", edit, {})
        self.assertEqual((self.kept(second), self.marks), (["Assets/Scripts/Player.cs"], []))

    def test_a_refused_call_keeps_nothing_and_a_failure_never_holds_the_call_up(self):
        edit = {"name": "Player", "path": "Assets/Scripts", "contents": "x"}
        self.assertIn("중지", self.call("manage_script", edit, {"stop": True, "point": "k"}))
        self.assertIn("다른 하위 세션", self.call("manage_script", edit, {"busy": True, "point": "k"}))
        self.assertFalse((self.project / "Library").exists())
        self.assertIsNone(self.call("manage_script", edit, {"point": "k"}, folder=lambda pin: 1 / 0))
        self.assertIsNone(self.call("manage_script", edit, {"point": "k"}, folder=lambda pin: None))  # the editor is not registered
        self.assertFalse((self.project / "Library").exists())
        with unittest.mock.patch("handlib.keeps.named_files", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.call("manage_script", edit, {"point": "k", "new_request": True, "label": "x"}))
        self.assertEqual(self.marks, [("x", "k")])  # the undo point is set whatever becomes of the copies

    def test_blender_is_left_alone(self):
        self.assertIsNone(self.call("execute_blender_code", {"code": "open('Assets/Scripts/Player.cs')"},
                                    {"new_request": True, "label": "의자", "point": "1116-101500-7"}, app="blender", pin="props@9876",
                                    folder=lambda pin: 1 / 0))
        self.assertEqual(self.marks, [("의자", "1116-101500-7")])
        self.assertFalse((self.project / "Library").exists())


if __name__ == "__main__":
    unittest.main()
