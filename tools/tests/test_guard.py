"""Tests for tools/guard.py, the subagents' PreToolUse hook. Run: python -m unittest discover -s tools/tests -v"""

import io
import json
import os
import subprocess
import sys
import tempfile
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
            for reply in ({"new_request": True, "label": "큐브 만들어줘"}, {"new_request": False}, None):
                guard.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=lambda req, r=reply: r,
                                      mark=lambda app, pin, label: marks.append((app, pin, label)))
            guard.report_progress({"tool_name": "mcp__blender_props__execute_code", "tool_input": {"code": "x = 1"}},
                                  "props@9876", "blender_props", "blender", home=home,
                                  post=lambda req: {"new_request": True, "label": "의자"}, mark=lambda *a: marks.append(a))
            # "cancel that" starts with the undo: no fresh point in front of it
            new = lambda req: {"new_request": True, "label": "방금 거 취소해줘"}
            guard.report_progress({"tool_name": "mcp__unity_mygame_ch__ch_checkpoint", "tool_input": {"action": "undo"}},
                                  self.PIN, self.SERVER, "unity", home=home, post=new, mark=lambda *a: marks.append(a))
            guard.report_progress({"tool_name": "mcp__blender_props__execute_code",
                                   "tool_input": {"code": "import claude_hand_chat; print(claude_hand_chat.undo_last_request())"}},
                                  "props@9876", "blender_props", "blender", home=home, post=new, mark=lambda *a: marks.append(a))
        self.assertEqual(marks, [("unity", self.PIN, "큐브 만들어줘"), ("blender", "props@9876", "의자")])  # once per request

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
        self.assertEqual(calls, [("aaaa1111", "ch_checkpoint", {"action": "mark", "label": "큐브 만들어줘"})])

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

    def test_action_summary(self):
        self.assertEqual(guard.action_summary({"action": "create", "name": "Cube", "position": [0, 0, 0]}), "create Cube")
        self.assertEqual(guard.action_summary({"code": "import bpy\n\nbpy.ops.mesh.primitive_uv_sphere_add()"}),
                         "bpy.ops.mesh.primitive_uv_sphere_add()")
        self.assertEqual(guard.action_summary({}), "")


if __name__ == "__main__":
    unittest.main()
