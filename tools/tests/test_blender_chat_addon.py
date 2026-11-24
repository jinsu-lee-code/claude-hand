"""Runs the Claude Hand Chat add-on inside real Blender against a real chat channel hub.

Needs Blender's Python module (`pip install bpy`); skipped when it is not importable.
"""

import base64
import hashlib
import importlib.util
import json
import os
import secrets
import socketserver
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CLAUDE_HAND_LOG"] = "off"  # the tests leave the real event log alone
import chat_channel as cc  # noqa: E402
import handlib.agents  # noqa: E402
import handlib.discovery  # noqa: E402
import handlib.install  # noqa: E402

try:
    import bpy
except ImportError:  # pragma: no cover - depends on the machine
    bpy = None

ADDON = Path(__file__).resolve().parents[2] / "blender" / "claude_hand_chat.py"


def wait_for(condition, addon, seconds=8.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        addon.tick()  # what bpy.app.timers does in a GUI Blender
        if condition():
            return True
        time.sleep(0.05)
    return False


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class BlenderChatAddonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["CLAUDE_HAND_HOME"] = str(root / "home")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        blend = root / "props.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        handlib.agents.write_agent_files(handlib.agents.build_agent_files([], None, [handlib.discovery.BlenderInstance(port=9876, filepath=str(blend))], None, "uv run"),
                             root / "agents")
        self.emitted = []
        self.hub = cc.ChatHub(self.emitted.append, root / "agents")
        self.hub.initialized = True
        token = secrets.token_urlsafe(16)
        self.httpd = cc.start_http(self.hub, token, port=18790)
        cc.write_server_file(root / "home", self.httpd.server_address[1], token)
        spec = importlib.util.spec_from_file_location("claude_hand_chat", ADDON)
        self.addon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.addon)
        self.addon.register()

    def tearDown(self):
        self.addon.unregister()
        self.httpd.shutdown()
        self.httpd.server_close()  # or its port stays taken until the server object is collected, and the next tests run out of ports
        os.environ.pop("CLAUDE_HAND_HOME", None)
        self.tmp.cleanup()

    def test_chat_round_trip_and_permission(self):
        addon = self.addon
        self.assertTrue(wait_for(lambda: addon._status[0] == "담당: blender-props", addon), addon._status[0])

        bpy.context.window_manager.claude_hand_chat_input = "큐브 하나 추가해줘"  # Enter in the field sends it
        self.assertTrue(wait_for(lambda: self.emitted, addon))
        note = self.emitted[0]["params"]
        self.assertRegex(note["meta"].pop("request"), r"^\d{4}-\d{6}-\d+$")  # the id the supervisor puts on the work it hands out
        self.assertEqual(note["meta"], {"chat_id": "blender-props", "app": "blender", "window": "props", "agent": "blender-props"})
        self.assertIn("큐브 하나 추가해줘", note["content"])
        self.assertIn("파일:", note["content"])
        wait_for(lambda: bpy.context.window_manager.claude_hand_chat_input == "", addon, 2)

        self.hub.reply("blender-props", "큐브를 추가했어요.")
        self.assertTrue(wait_for(lambda: any(e.get("kind") == "reply" for e in addon._entries), addon))
        self.assertEqual([e["kind"] for e in addon._entries], ["user", "reply"])

        self.hub.permission_request({"request_id": "abcde", "tool_name": "Bash", "description": "run", "input_preview": "ls"})
        self.assertTrue(wait_for(lambda: any(e.get("kind") == "permission" for e in addon._entries), addon))
        bpy.ops.claude_hand.chat_permission(request_id="abcde", behavior="allow")
        self.assertTrue(wait_for(lambda: self.emitted[-1]["method"] == "notifications/claude/channel/permission", addon))
        self.assertEqual(self.emitted[-1]["params"], {"request_id": "abcde", "behavior": "allow"})
        self.assertTrue(wait_for(lambda: any(e.get("behavior") == "allow" for e in addon._entries), addon))

    def test_start_button_opens_the_supervisor(self):
        addon = self.addon
        script = Path(os.environ["CLAUDE_HAND_HOME"]) / "start-supervisor.cmd"
        script.write_text("", encoding="utf-8")
        (Path(os.environ["CLAUDE_HAND_HOME"]) / "config.json").write_text(json.dumps({"start_script": str(script)}), encoding="utf-8")
        launched = []
        import unittest.mock
        with unittest.mock.patch.object(addon.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd)):
            bpy.ops.claude_hand.start_supervisor()
        self.assertEqual(launched[0][-1], str(script))
        self.assertIn("여는 중", addon._status[0])

    def test_missing_supervisor_shows_the_button(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        (Path(os.environ["CLAUDE_HAND_HOME"]) / "chat" / "server.json").unlink()
        self.assertTrue(wait_for(lambda: self.addon._server_missing[0], self.addon))
        self.httpd = cc.start_http(self.hub, "x", port=18791)  # tearDown shuts this one down

    def test_message_while_off_starts_claude_hand_and_is_sent_later(self):
        addon, home = self.addon, Path(os.environ["CLAUDE_HAND_HOME"])
        self.httpd.shutdown()
        self.httpd.server_close()
        (home / "chat" / "server.json").unlink()
        self.assertTrue(wait_for(lambda: addon._server_missing[0], addon))
        script = home / "start-supervisor.cmd"
        script.write_text("", encoding="utf-8")
        (home / "config.json").write_text(json.dumps({"start_script": str(script)}), encoding="utf-8")
        launched = []
        import unittest.mock
        with unittest.mock.patch.object(addon.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd)):
            self.assertTrue(addon.send_text("의자 만들어줘"))
            self.assertFalse(addon.send_text("두 번째"))  # one held message at a time
        self.assertEqual(len(launched), 1)
        self.assertEqual(addon._pending[0][0], "의자 만들어줘")
        self.assertIn("확인", addon._status[0])
        self.hub.initialized = False  # Claude Code is still connecting when the server appears
        self.httpd = cc.start_http(self.hub, "fresh-token", port=18796)  # tearDown shuts this one down
        cc.write_server_file(home, self.httpd.server_address[1], "fresh-token")
        self.assertTrue(wait_for(lambda: addon._pending[0] is None, addon))
        self.assertTrue(wait_for(lambda: self.hub.backlog, addon))
        self.assertEqual(self.emitted, [])
        self.hub.mark_initialized()
        self.assertIn("의자 만들어줘", self.emitted[0]["params"]["content"])

    def test_selection_is_reported_without_the_panel_sending_anything(self):
        addon = self.addon
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.view_layer.objects.active.name = "Chair"
        self.assertTrue(wait_for(lambda: self.hub.contexts.get("blender-props", {}).get("active") == "Chair", addon))
        context = self.hub.current_context()["windows"][0]
        self.assertEqual((context["chat_id"], context["selection"]), ("blender-props", ["Chair"]))
        self.assertTrue(context["file"].endswith("props.blend"))

    def test_desktop_work_shows_in_the_panel(self):
        addon = self.addon
        self.assertTrue(wait_for(lambda: addon._status[0] == "담당: blender-props", addon))
        self.hub.reply("blender-props", "의자를 Unity로 보냈어요.", origin="desktop", request="선택한 의자를 Unity로")
        self.assertTrue(wait_for(lambda: addon._entries, addon))
        self.assertEqual((addon._entries[0]["origin"], addon._entries[0]["request"]), ("desktop", "선택한 의자를 Unity로"))

    def test_panel_draw_does_not_fail(self):
        self.assertTrue(hasattr(bpy.types, "CLAUDEHAND_PT_chat"))
        self.assertEqual(self.addon.wrap("가" * 50)[0], "가" * self.addon.WRAP_WIDTH)

    def test_progress_line_while_the_subagent_works(self):
        addon = self.addon
        pin = next(a.pin for a in handlib.agents.installed_agents(Path(self.tmp.name) / "agents") if a.app == "blender")
        self.assertTrue(wait_for(lambda: addon._status[0].startswith("담당"), addon))
        chat_id = self.hub.report_action("blender", pin, "execute_code", "bpy.ops.mesh.primitive_uv_sphere_add()")["chat_id"]
        self.assertTrue(wait_for(lambda: addon._progress[0] is not None, addon))
        line = addon.progress_view(*addon._progress[0], time.time())
        self.assertTrue(line.startswith("작업 중 · ") and "초 · execute_code bpy.ops.mesh.primitive_uv_sphere_add()" in line, line)
        self.hub.report_action("blender", pin, "execute_code", "bpy.ops.export_scene.fbx()")
        self.assertTrue(wait_for(lambda: (addon._progress[0] or ({}, 0))[0].get("actions") == 2, addon))
        self.assertTrue(addon.progress_view(*addon._progress[0], time.time()).endswith("2번째 동작"))
        self.hub.reply(chat_id, "다 했어요")
        self.assertTrue(wait_for(lambda: addon._progress[0] is None, addon))

    def test_stop_button_reaches_the_channel(self):
        addon = self.addon
        pin = next(a.pin for a in handlib.agents.installed_agents(Path(self.tmp.name) / "agents") if a.app == "blender")
        self.assertTrue(wait_for(lambda: addon._status[0].startswith("담당"), addon))
        addon.send_text("의자 만들어줘", include_context=False)
        self.assertTrue(wait_for(lambda: len(self.emitted) == 1, addon))
        self.hub.report_action("blender", pin, "execute_code", "bpy.ops.mesh.primitive_cube_add()", "aaa")
        self.assertTrue(wait_for(lambda: addon._progress[0] is not None, addon))
        self.assertEqual(bpy.ops.claude_hand.chat_stop(), {"FINISHED"})
        self.assertTrue(wait_for(lambda: len(self.emitted) == 2, addon))
        self.assertEqual(self.emitted[1]["params"]["meta"]["stop"], "requested")
        self.assertTrue(self.hub.report_action("blender", pin, "execute_code", "more", "aaa")["stop"])  # the next call is refused
        self.assertTrue(wait_for(lambda: (addon._progress[0] or ({}, 0))[0].get("stopping"), addon))
        self.assertTrue(addon.progress_view(*addon._progress[0], time.time()).startswith("중지하는 중 · "))
        self.assertTrue(wait_for(lambda: any("중지를 요청했어요" in str(e.get("text")) for e in addon._entries), addon))

    def test_walking_back_to_an_undo_point(self):
        """Blender's undo history as a list of the newest undo point each state comes after, including the failures."""
        history, at = [0, 1, 1, 2, 2], [4]

        def step(by):
            if not 0 <= at[0] + by < len(history):
                return {"CANCELLED"}
            at[0] += by
            return {"FINISHED"}
        walk = lambda seq, limit=50: self.addon.walk_back(seq, lambda: history[at[0]], lambda: step(-1), lambda: step(1), limit)
        self.assertIsNone(walk(2, limit=1))  # too many steps: back where it started
        self.assertEqual(at[0], 4)
        self.assertEqual(walk(2), 2)
        self.assertEqual(at[0], 3)  # the state right when point 2 was set
        self.assertEqual(walk(1), 3)
        self.assertEqual(at[0], 1)
        history[:] = [1, 1, 2]  # point 1 dropped out of the history
        at[0] = 2
        self.assertIsNone(walk(1))
        self.assertEqual(at[0], 2)

    def test_undo_points_in_blenders_own_history(self):
        addon = self.addon
        made = lambda: sorted(o.name for o in bpy.data.objects if o.name.startswith("CH_"))
        self.assertEqual(addon.undo_last_request(), "되돌릴 Claude 요청이 없습니다.")
        rows, _ = addon.entry_rows(addon._entries[-1], None)
        self.assertEqual(rows[0], ("되돌릴 Claude 요청이 없습니다.", "LOOP_BACK"))  # shown in the panel
        self.assertIs(addon.visible_entries(None)[0][-1][0], addon._entries[-1])

        addon._undo_op(bpy.ops.ed.undo_push, message="Original")  # what a file opened in the UI starts with
        self.assertEqual(addon.mark_undo_point("의자   만들어줘"), "undo point 1 set")
        bpy.context.scene.collection.objects.link(bpy.data.objects.new("CH_Chair", None))  # data API: no undo step
        addon.mark_undo_point("")  # a request from the Claude app: no panel text
        bpy.ops.mesh.primitive_cube_add()  # an operator: an undo step of its own
        bpy.context.active_object.name = "CH_Cube"
        self.assertEqual([m["label"] for m in addon._marks], ["의자 만들어줘", ""])
        self.assertTrue(addon.undo_last_request().startswith("되돌렸어요:"))
        self.assertEqual(made(), ["CH_Chair"])  # only the newest request
        self.assertTrue(addon.undo_last_request().startswith("되돌렸어요: '의자 만들어줘 ("))
        self.assertEqual(made(), [])

        addon.mark_undo_point("램프")
        bpy.ops.mesh.primitive_cube_add()
        addon._undo_op(bpy.ops.ed.undo)  # Ctrl+Z in Blender went back past the point already
        self.assertIn("이미 되돌려져", addon.undo_last_request())
        self.assertEqual(addon._marks, [])

        addon.mark_undo_point("x")
        bpy.ops.wm.read_factory_settings(use_empty=True)  # another file: a new undo history
        self.assertEqual(addon._marks, [])

    def test_a_request_is_undone_by_its_name_only_when_its_point_is_the_newest(self):
        """What the chat channel asks of every window a request changed (undo_in_window)."""
        addon = self.addon
        made = lambda: sorted(o.name for o in bpy.data.objects if o.name.startswith("CH_"))

        def add(name):
            bpy.ops.mesh.primitive_cube_add()
            bpy.context.active_object.name = name
        addon._undo_op(bpy.ops.ed.undo_push, message="Original")
        self.assertEqual(addon.undo_request("r1"), {"state": "nothing"})
        addon.mark_undo_point("의자", request="r1")
        add("CH_Chair")
        addon.mark_undo_point("램프", request="r2")
        add("CH_Lamp")
        self.assertEqual([(m["request"], m["stamp"] > 0) for m in addon._marks], [("r1", True), ("r2", True)])
        blocked = addon.undo_request("r1")  # a later request changed this window since: undoing r1 would take it along
        self.assertEqual((blocked["state"], blocked["top"].startswith("램프 (")), ("blocked", True))
        self.assertEqual(made(), ["CH_Chair", "CH_Lamp"])
        self.assertEqual(addon.undo_request("r2")["state"], "undone")
        self.assertEqual(made(), ["CH_Chair"])
        self.assertEqual(addon.undo_request("r2"), {"state": "nothing"})  # asked again: nothing of it is left
        self.assertEqual(addon.undo_request("r1")["state"], "undone")
        self.assertEqual((made(), addon._marks), ([], []))
        self.assertEqual(addon.undo_request(""), {"state": "nothing"})
        addon.mark_undo_point("x", request="r3")
        bpy.ops.mesh.primitive_cube_add()
        addon._undo_op(bpy.ops.ed.undo)  # Ctrl+Z in Blender went back past the point already
        self.assertEqual((addon.undo_request("r3"), addon._marks), ({"state": "nothing"}, []))
        addon.mark_undo_point("이름 없는 지점")  # from a guard of before points had names: only the panel's own button undoes it
        self.assertEqual((addon._marks[-1]["request"], addon.undo_request("r3")), ("", {"state": "nothing"}))

    def test_the_button_undoes_a_request_in_the_other_windows_it_changed(self):
        addon, root = self.addon, Path(self.tmp.name)
        made = lambda: sorted(o.name for o in bpy.data.objects if o.name.startswith("CH_"))
        handlib.agents.write_agent_files(handlib.agents.build_agent_files(
            [handlib.discovery.Instance(name="MyGame", hash="aaaa1111")], handlib.discovery.ServerConfig(transport="stdio"),
            [handlib.discovery.BlenderInstance(port=9876, filepath=bpy.data.filepath)], None, "uv run"), root / "agents")
        asked = []
        self.hub.undo_window = lambda app, pin, key: asked.append((app, pin, key)) or {"state": "undone"}
        self.assertTrue(wait_for(lambda: addon._status[0].startswith("담당"), addon))
        addon._undo_op(bpy.ops.ed.undo_push, message="Original")

        def ask(text, here):
            """A request from this panel: (here) this Blender changes, then the Unity project. As the guards and the
            supervisor's hook report it to the channel."""
            before = len(self.emitted)
            addon.send_text(text, include_context=False)
            self.assertTrue(wait_for(lambda: len(self.emitted) > before, addon))
            request = self.emitted[-1]["params"]["meta"]["request"]
            if here:
                self.hub.assign("blender-props", request)
                self.assertTrue(self.hub.report_action("blender", "props@9876", "execute_blender_code", "", "bbb")["new_request"])
                addon.mark_undo_point(text, request=request)  # what the guard does on new_request
                bpy.ops.mesh.primitive_cube_add()
                bpy.context.active_object.name = "CH_" + here
                self.hub.agent_stopped("blender", "props@9876", "bbb")
            self.hub.assign("unity-mygame", request)
            self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "", "aaa")
            self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
            self.hub.reply("blender-props", "MyGame에 놓았어요", changed=["unity-mygame"])
            self.assertTrue(wait_for(lambda: (addon._offer[0] or {}).get("request") == request and addon._progress[0] is None, addon))
            return request

        first = ask("의자를 고쳐서 MyGame으로 보내줘", "Chair")
        mark, offer = addon.undo_plan()
        self.assertEqual((mark["request"], offer["windows"]), (first, ["MyGame (Unity)"]))
        self.assertEqual(addon.describe_plan(mark, offer), "의자를 고쳐서 MyGame으로 보내줘 (이 창 + MyGame (Unity))")
        addon._undo_from_button()  # what the button runs once the confirmation is given
        self.assertTrue(wait_for(lambda: any("MyGame (Unity): 되돌렸어요" in str(e.get("text")) for e in addon._entries), addon))
        self.assertEqual(asked, [("unity", "MyGame@aaaa1111", first)])  # not this window: the panel undid it itself
        self.assertEqual(made(), [])
        self.assertTrue(wait_for(lambda: addon._offer[0] is None, addon))
        self.assertEqual(addon.undo_plan(), (None, None))

        # A request that only changed the other window (this Blender just exported): this window's older point is not it.
        addon.mark_undo_point("옛 요청", request="older")
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.active_object.name = "CH_Old"
        second = ask("램프도 MyGame으로 보내줘", "")
        mark, offer = addon.undo_plan()
        self.assertEqual((mark, offer["request"]), (None, second))
        self.assertEqual(addon.describe_plan(mark, offer), "램프도 MyGame으로 보내줘 (MyGame (Unity))")
        addon._undo_from_button()
        self.assertTrue(wait_for(lambda: len(asked) == 2, addon))
        self.assertEqual((asked[1], made()), (("unity", "MyGame@aaaa1111", second), ["CH_Old"]))
        self.assertTrue(wait_for(lambda: addon._offer[0] is None, addon))
        mark, offer = addon.undo_plan()  # and now the plain button is back, for this window's own older request
        self.assertEqual((mark["label"], offer, addon.describe_plan(mark, offer).startswith("옛 요청 (")), ("옛 요청", None, True))

    def test_the_message_after_an_undo_says_so(self):
        addon = self.addon
        self.assertTrue(wait_for(lambda: addon._status[0].startswith("담당"), addon))
        addon._undo_note[0] = "[사용자가 채팅창에서 '의자' 요청 이후의 Blender 변경을 실행 취소로 되돌렸음]"
        addon.send_text("다시 파랗게 해줘")
        self.assertTrue(wait_for(lambda: self.emitted, addon))
        self.assertIn("실행 취소로 되돌렸음", self.emitted[0]["params"]["content"])
        self.assertEqual(addon._undo_note[0], "")

    def test_windows_input_box_messages_go_to_this_chat(self):
        """The input box itself runs in PowerShell (checked by hand); here: what it prints reaches this window's chat."""
        addon = self.addon
        self.assertTrue(wait_for(lambda: addon._status[0].startswith("담당"), addon))
        line = lambda text: base64.b64encode(text.encode("utf-8")) + b"\n"
        addon.read_native_input([line("안녕 하세요?! @#"), b"not base64 !!\n", line("   ")])
        self.assertTrue(wait_for(lambda: self.emitted, addon))
        self.assertIn("안녕 하세요?! @#", self.emitted[0]["params"]["content"])
        self.assertEqual(len(self.emitted), 1)  # garbage and blank lines are dropped

        command = addon.native_input_command("Claude Hand · props'; Remove-Item x", 4321)
        self.assertEqual(command[:2], ["powershell.exe", "-NoProfile"])
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertIn("$parentId = 4321", script)
        self.assertNotIn("Remove-Item", script)  # the title goes in as base64, not as script text

    def test_result_preview_of_another_window_shows_here(self):
        addon = self.addon
        self.assertTrue(wait_for(lambda: addon._status[0].startswith("담당"), addon))
        png = Path(self.tmp.name) / "unity.png"
        image = bpy.data.images.new("shot", 32, 24)
        image.filepath_raw, image.file_format = str(png), "PNG"
        image.save()
        bpy.data.images.remove(image)
        self.hub.capture, self.hub.images_dir = lambda app, pin, path: str(png), Path(self.tmp.name) / "images"
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin=addon.window_identity()[1])
        root = Path(self.tmp.name)
        handlib.agents.write_agent_files(handlib.agents.build_agent_files([handlib.discovery.Instance(name="MyGame", hash="aaaa1111")], handlib.discovery.ServerConfig(transport="stdio"),
                                                  [handlib.discovery.BlenderInstance(port=9876, filepath=str(root / "props.blend"))], None, "uv run"),
                             root / "agents")
        self.hub.reply("blender-props", "Unity에 놓았어요", changed=["unity-mygame"])  # the supervisor says what it changed
        self.assertTrue(wait_for(lambda: any(e.get("kind") == "image" for e in addon._entries), addon))
        entry = next(e for e in addon._entries if e.get("kind") == "image")
        self.assertEqual(addon.entry_rows(entry, None)[0], [("MyGame (Unity) 지금 모습", "IMAGE_DATA")])
        self.assertIs(addon.visible_entries(None)[0][-1][0], entry)
        self.assertIsNotNone(addon.image_preview(entry["path"]))
        self.assertIsNone(addon.image_preview(str(Path(self.tmp.name) / "gone.png")))

    def test_the_panel_lists_what_the_other_windows_are_busy_with(self):
        addon = self.addon
        self.assertTrue(wait_for(lambda: addon._status[0].startswith("담당"), addon))
        self.assertEqual(addon._others, [])
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")  # another window's panel waits for an answer
        self.assertTrue(wait_for(lambda: addon._others, addon))
        self.assertEqual(addon._others, ["MyGame (Unity) · 답 기다림 · “큐브 만들어줘”"])
        self.assertEqual(addon.others_rows(addon._others, None), [(addon._others[0], "DOT")])
        self.hub.reply("unity-mygame", "만들었어요")  # answered: this panel shows the change without being asked
        self.assertTrue(wait_for(lambda: addon._others != ["MyGame (Unity) · 답 기다림 · “큐브 만들어줘”"], addon))
        rows = addon.others_rows(["아주 긴 이름의 창 (Unity) · 작업 중 · " + "긴 요청 " * 60], None)
        self.assertEqual(len(rows), addon.OTHER_LINES)  # a window never takes more of the panel than that
        self.assertEqual((rows[0][1], rows[1][1]), ("DOT", "BLANK1"))
        self.assertTrue(rows[-1][0].endswith("…"))

    def test_only_fresh_answers_to_this_window_call_the_user_back(self):
        want, now = self.addon.wants_attention, 1000.0
        self.assertTrue(want({"kind": "reply", "ts": now - 2}, now))
        self.assertTrue(want({"kind": "permission", "ts": now}, now))  # work is waiting on the user
        self.assertFalse(want({"kind": "reply", "ts": now - 120}, now))  # history shown again after a restart
        self.assertFalse(want({"kind": "reply", "origin": "desktop", "ts": now}, now))  # asked in the Claude app
        self.assertFalse(want({"kind": "reply", "working": True, "ts": now}, now))  # "작업 중이에요": not done yet
        self.assertFalse(want({"kind": "user", "ts": now}, now))
        self.assertFalse(want({"kind": "image", "ts": now}, now))  # comes with a reply that already called
        self.assertTrue(want({"kind": "notice", "alert": True, "ts": now}, now))  # a dialog stopped the other window
        self.assertFalse(want({"kind": "notice", "ts": now}, now))  # this panel's own 되돌리기 note
        self.assertEqual(self.addon.entry_rows({"kind": "notice", "alert": True, "text": "멈춤"}, None)[0][0][1], "ERROR")
        took = {"kind": "notice", "cost": True, "text": "이 요청: 32초 · 동작 2번", "ts": now}
        self.assertFalse(want(took, now))  # what the request took, under its result: nothing to call anybody for
        self.assertEqual(self.addon.entry_rows(took, None)[0][0], (took["text"], "TIME"))  # a clock, not the undo arrow
        self.assertIn("TIME", bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items)  # this Blender has it

    def test_lines_fit_the_sidebar(self):
        """Korean is about twice as wide as Latin text; a fixed character count overflowed into "..."."""
        addon = self.addon
        metrics = (160.0, 11.0)  # a narrow sidebar: 160 px for text at an 11 px label font
        measure = addon.text_measure(11.0)
        text = "SampleScene에 큐브를 하나 만들었어요. 원점에 두었고 씬은 아직 저장하지 않았어요."
        lines = addon.wrap(text, metrics)
        self.assertGreater(len(lines), 2)
        self.assertTrue(all(measure(line) <= 160 for line in lines), lines)
        self.assertEqual(" ".join(lines), text)
        self.assertEqual(len(addon.wrap(text, (2000.0, 11.0))), 1)  # a wide sidebar keeps it on one line
        path = "C:/Users/me/Desktop/Unity/hand_test/Assets/Scenes/SampleScene.unity"
        self.assertEqual("".join(addon.wrap(path, (100.0, 11.0))), path)  # no spaces: broken between characters
        self.assertTrue(all(measure(line) <= 100 for line in addon.wrap(path, (100.0, 11.0))))

    def test_long_conversation_keeps_the_input_in_reach(self):
        addon = self.addon
        # Past the poller's first "reset" (a new server): it would empty the entries put here by hand if it came later.
        self.assertTrue(wait_for(lambda: addon._status[0] == "담당: blender-props", addon), addon._status[0])
        addon._entries[:] =[{"kind": "reply", "seq": i, "text": "긴 답 " * 200} for i in range(30)]
        shown, folded = addon.visible_entries((200.0, 11.0))
        self.assertEqual(shown[-1][0]["seq"], 29)  # the newest is always there
        self.assertEqual(folded, 30 - len(shown))
        self.assertEqual(len(shown[-1][1]), 1 + addon.ENTRY_LINES)  # header + capped text
        self.assertGreater(shown[-1][2], 0)  # the rest is behind 전체 복사
        self.assertEqual([e["seq"] for e, _, _ in shown], sorted(e["seq"] for e, _, _ in shown))
        addon._inbox.put(("items", [{"kind": "user", "seq": 100 + i, "text": "x"} for i in range(addon.KEPT_ENTRIES)]))
        addon.tick()
        self.assertEqual(len(addon._entries), addon.KEPT_ENTRIES)
        self.assertEqual(addon._entries[-1]["seq"], 100 + addon.KEPT_ENTRIES - 1)



@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class TransferTests(unittest.TestCase):
    """What the Blender subagent calls to move objects to another Blender or to Unity."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exchange = Path(self.tmp.name) / "exchange"
        os.environ["CLAUDE_HAND_EXCHANGE_DIR"] = str(self.exchange)
        spec = importlib.util.spec_from_file_location("claude_hand_chat", ADDON)
        self.addon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.addon)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mesh = bpy.data.meshes.new("RockMesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.materials.append(bpy.data.materials.new("RockMat"))
        rock = bpy.data.objects.new("Rock", mesh)
        pebble = bpy.data.objects.new("Pebble", mesh)
        pebble.parent = rock
        bystander = bpy.data.objects.new("Bystander", mesh)
        for obj in (rock, pebble, bystander):
            bpy.context.scene.collection.objects.link(obj)
        bystander.select_set(True)

    def tearDown(self):
        os.environ.pop("CLAUDE_HAND_EXCHANGE_DIR", None)
        self.tmp.cleanup()

    def test_blend_round_trip(self):
        sent = self.addon.export_blend(["Rock"], "rock")
        self.assertEqual(sent["objects"], ["Pebble", "Rock"])  # children go along
        self.assertNotIn("\\", sent["manifest"])  # prints the same as it is passed on
        manifest = json.loads(Path(sent["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual((manifest["format"], manifest["roots"], manifest["objects"]), ("claude-hand/blend@1", ["Rock"], ["Pebble", "Rock"]))
        self.assertTrue((self.exchange / manifest["blend_file"]).exists())
        self.assertEqual({o.name for o in bpy.context.scene.objects}, {"Rock", "Pebble", "Bystander"})  # nothing changed here

        bpy.ops.wm.read_factory_settings(use_empty=True)  # "another Blender": an empty scene
        got = self.addon.import_blend(sent["manifest"])
        self.assertEqual((sorted(got["added"]), got["renamed"]), (["Pebble", "Rock"], {}))
        self.assertEqual({o.name for o in bpy.context.scene.objects}, {"Pebble", "Rock"})
        self.assertEqual(bpy.data.objects["Pebble"].parent.name, "Rock")
        self.assertEqual(bpy.data.objects["Rock"].data.materials[0].name, "RockMat")

    def test_receiving_says_which_names_were_taken(self):
        sent = self.addon.export_blend("Rock", "rock")  # one name works too
        got = self.addon.import_blend(f'"{sent["manifest"]}"')  # a path pasted with its quotes
        self.assertEqual(got["renamed"], {"Pebble": "Pebble.001", "Rock": "Rock.001"})
        self.assertEqual(sorted(got["added"]), ["Pebble.001", "Rock.001"])

    def test_fbx_for_unity_puts_the_selection_back(self):
        sent = self.addon.export_fbx(["Rock"], "rock set!")
        manifest = json.loads(Path(sent["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual((manifest["format"], manifest["roots"]), ("claude-hand/model@1", ["Rock"]))
        self.assertRegex(manifest["model_file"], r"^rock_set_\d{8}_\d{6}\.fbx$")  # a name safe for files and asset paths
        self.assertEqual(manifest["name"], "rock_set")  # what Unity names the asset: the same when it comes again
        fbx = self.exchange / manifest["model_file"]
        self.assertTrue(fbx.exists() and fbx.stat().st_size > 0)
        self.assertEqual({o.name for o in bpy.context.view_layer.objects if o.select_get()}, {"Bystander"})

    def test_a_resend_is_known_by_the_saved_file_and_the_objects(self):
        """Unity replaces its model in place when `source` and `roots` come again; a file never saved has no source."""
        unsaved = self.addon.export_fbx(["Rock"], "rock")
        self.assertEqual(json.loads(Path(unsaved["manifest"]).read_text(encoding="utf-8"))["source"], "")
        self.assertIn("저장", unsaved["note"])
        blend = Path(self.tmp.name) / "rocks.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        sent = [self.addon.export_fbx(["Rock"], name) for name in ("rock", "rock_fixed")]
        self.assertNotIn("note", sent[0])
        first, second = (json.loads(Path(s["manifest"]).read_text(encoding="utf-8")) for s in sent)
        self.assertEqual(Path(first["source"]), blend)
        self.assertEqual((first["source"], first["roots"]), (second["source"], second["roots"]))
        self.assertNotEqual(first["transfer_id"], second["transfer_id"])

    def test_model_from_unity_comes_in(self):
        """import_model reads a claude-hand/model@1 manifest, whoever wrote it: here, this add-on's own export_fbx."""
        sent = self.addon.export_fbx(["Rock"], "rock")
        bpy.ops.wm.read_factory_settings(use_empty=True)  # "another Blender": an empty scene
        got = self.addon.import_model(sent["manifest"])
        self.assertEqual(got["added"], ["Pebble", "Rock"])
        self.assertEqual(bpy.data.objects["Pebble"].parent.name, "Rock")
        self.assertTrue(got["file"].endswith(".fbx") and "\\" not in got["file"])
        again = self.addon.import_model(f'"{got["file"]}"')  # the model file itself works too; names taken get .001
        self.assertEqual(again["added"], ["Pebble.001", "Rock.001"])

    def test_import_model_refuses_what_is_not_a_model(self):
        sent = self.addon.export_blend(["Rock"], "rock")
        with self.assertRaises(ValueError) as wrong:
            self.addon.import_model(sent["manifest"])
        self.assertIn("import_blend", str(wrong.exception))
        with self.assertRaises(ValueError):
            self.addon.import_model(str(self.exchange / "nothing_here.fbx"))

    def test_a_wrong_name_or_manifest_says_so_and_writes_nothing(self):
        with self.assertRaises(ValueError) as wrong:
            self.addon.export_fbx(["Rok"], "rock")
        self.assertIn("'Rok'", str(wrong.exception))
        self.assertFalse(self.exchange.exists() and any(self.exchange.iterdir()))
        sent = self.addon.export_fbx(["Rock"], "rock")
        with self.assertRaises(ValueError):
            self.addon.import_blend(sent["manifest"])  # a model for Unity, not a .blend for Blender

    def test_port_expression_without_addon_is_harmless(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(compile(handlib.install.blender_port_expr(9877), "<port>", "exec"), {"__name__": "__port__"})
        self.assertIn("not enabled", out.getvalue())

    def test_the_same_objects_give_the_same_file_and_the_scene_is_left_alone(self):
        """Whether Unity has a model already is told by the file. So an export must not depend on when it is made
        (the FBX holds its time of writing) nor on the Blender run (the exporter numbers what it writes with Python's
        hash(); that part is checked with separate Blender runs in docs/DESIGN.md, here that it is put back)."""
        from io_scene_fbx import fbx_utils
        addon, layer = self.addon, bpy.context.view_layer
        fbx_utils._keys_to_uuids["theirs"] = 7  # what an export the user started left behind
        layer.objects.active = bpy.data.objects["Bystander"]
        bpy.ops.object.mode_set(mode="EDIT")  # the user is in the middle of editing another mesh
        files = []
        for _ in range(2):
            path = self.exchange / f"same_{len(files)}.fbx"
            self.exchange.mkdir(exist_ok=True)
            addon._write_fbx([bpy.data.objects["Rock"], bpy.data.objects["Pebble"]], str(path))
            files.append(path.read_bytes())
            time.sleep(1.1)  # the clock moves on to the next second
        self.assertEqual(files[0], files[1])
        self.assertEqual(({o.name for o in layer.objects if o.select_get()}, layer.objects.active.name, bpy.context.mode),
                         ({"Bystander"}, "Bystander", "EDIT_MESH"))
        self.assertEqual((fbx_utils._keys_to_uuids.get("theirs"), "hash" in vars(fbx_utils)), (7, False))  # the exporter is as it was
        del fbx_utils._keys_to_uuids["theirs"]
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.data.objects["Bystander"].location.x += 1  # not sent: the file is the same
        addon._write_fbx([bpy.data.objects["Rock"], bpy.data.objects["Pebble"]], str(self.exchange / "same_2.fbx"))
        self.assertEqual((self.exchange / "same_2.fbx").read_bytes(), files[0])
        bpy.data.objects["Pebble"].location.x += 0.001  # a millimetre: another file
        addon._write_fbx([bpy.data.objects["Rock"], bpy.data.objects["Pebble"]], str(self.exchange / "same_3.fbx"))
        self.assertNotEqual((self.exchange / "same_3.fbx").read_bytes(), files[0])
        self.assertEqual(self.addon.import_model(str(self.exchange / "same_3.fbx"))["added"], ["Pebble.001", "Rock.001"])  # and it reads back

    def test_an_object_keeps_the_name_it_was_sent_under(self):
        """Unity knows a model by the names of the objects it was made of. An object that is renamed afterwards still
        goes to its model; a copy of it, or another object that takes its old name, is something else."""
        addon, rock = self.addon, bpy.data.objects["Rock"]
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(self.tmp.name) / "rocks.blend"))
        roots = lambda sent: json.loads(Path(sent["manifest"]).read_text(encoding="utf-8"))["roots"]
        self.assertEqual((addon.sent_as(rock), addon.key_of(rock)), ("", "Rock"))
        self.assertEqual(roots(addon.export_fbx(["Rock"], "rock")), ["Rock"])
        self.assertEqual((addon.sent_as(rock), addon.sent_as(bpy.data.objects["Pebble"])), ("Rock", ""))  # the ones named, not their children
        rock.name = "Boulder"
        self.assertEqual((addon.object_for("Rock"), addon.key_of(rock)), (rock, "Rock"))
        self.assertEqual(roots(addon.export_fbx(["Boulder"], "boulder")), ["Rock"])  # the same model to Unity
        copy = rock.copy()  # Shift+D: the mark comes along
        bpy.context.scene.collection.objects.link(copy)
        bpy.context.view_layer.update()
        self.assertIsNone(addon.object_for("Rock"))  # two that were "sent as Rock", neither of that name: not guessed
        self.assertEqual(roots(addon.export_fbx([copy.name], "copy")), [copy.name])  # sent by name, each is its own model again
        self.assertEqual((addon.sent_as(copy), addon.object_for("Rock")), (copy.name, rock))
        stranger = bpy.data.objects.new("Rock", rock.data)  # something new takes the old name
        bpy.context.scene.collection.objects.link(stranger)
        bpy.context.view_layer.update()
        self.assertEqual((addon.object_for("Rock"), addon.key_of(stranger)), (rock, "Rock#2"))
        self.assertEqual(roots(addon.export_fbx(["Rock"], "rock2")), ["Rock#2"])
        self.assertEqual(addon.object_for("Rock#2"), stranger)
        twin = stranger.copy()
        twin.name = "Rock#2"  # a copy that happens to carry the name of the mark: the named one is it
        self.assertEqual(addon.object_for("Rock#2"), twin)


class FakeUnity:
    """A Unity editor as Blender's side meets it: the registry file its bridge keeps and the socket behind it
    (ChBridge.cs: a line of JSON in, a line out), with ch_model_follow doing what ChModelFollow.cs does as far as the
    add-on can tell: models known by their source and roots, whether each follows, and the file each holds."""

    class Refused(Exception):
        pass

    def __init__(self, home, name="MyGame", hash_="aaaa1111", tools=("ch_import_model", "ch_model_follow"), pid=4242):
        self.name, self.hash, self.token = name, hash_, "secret"
        self.models = {}       # asset path -> {"source", "roots", "follow", "data": the bytes of its file}
        self.calls = []        # (action, params) of every call that got in, in order
        self.busy = 0          # compiling: this many calls that would change something are told EDITOR_BUSY
        self.at_work = False   # a request is at work in the project
        unity = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                request = json.loads(self.rfile.readline().decode("utf-8"))
                self.wfile.write((json.dumps(unity.answer(request)) + "\n").encode("utf-8"))
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.registry = Path(home) / "unity" / f"{hash_}.json"
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(json.dumps({"format": "claude-hand/unity-bridge@1", "name": name, "hash": hash_, "pid": pid,
                                             "port": self.server.server_address[1], "token": self.token,
                                             "tools": [{"name": tool} for tool in tools]}), encoding="utf-8")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.registry.unlink(missing_ok=True)

    def did(self, action):
        return [params for done, params in self.calls if done == action]

    def came(self, manifest, follow=False):
        """A model as ch_import_model takes it from Claude's "send it to Unity"."""
        data = json.loads(Path(manifest).read_text(encoding="utf-8"))
        path = f"Assets/ClaudeHand/Imports/{data['name']}/{data['name']}.fbx"
        self.models[path] = {"source": data["source"].replace("\\", "/"), "roots": sorted(data["roots"]), "follow": follow,
                             "data": (Path(manifest).parent / data["model_file"]).read_bytes()}
        return path

    def answer(self, request):
        if request.get("token") != self.token:
            return {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "Wrong token"}}
        if request.get("tool") != "ch_model_follow":
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "No such tool"}}
        params = request.get("params") or {}
        try:
            data = self.follow(params)
        except FakeUnity.Refused as no:
            return {"ok": True, "result": {"success": False, "code": str(no), "error": str(no), "data": {"code": str(no), "message": ""}}}
        self.calls.append((params.get("action", "list"), params))
        return {"ok": True, "result": {"success": True, "message": "ok", "data": data}}

    def follow(self, p):
        action = p.get("action", "list")
        posix = lambda path: str(path or "").replace("\\", "/")
        same = lambda one, other: posix(one).lower() == posix(other).lower()
        if action == "list":
            moved = [path for path, model in self.models.items() if p.get("moved_from") and same(model["source"], p["moved_from"])]
            for path in moved:
                self.models[path]["source"] = posix(p["source"])
            return {"project": self.name, "moved": moved, "models": [
                {"asset_path": path, "name": Path(path).stem, "source": model["source"], "roots": model["roots"], "follow": model["follow"],
                 "here": same(model["source"], p.get("source")), "instances": 1,
                 "file_hash": hashlib.sha256(model["data"]).hexdigest() if model["follow"] and same(model["source"], p.get("source")) else None}
                for path, model in self.models.items()]}
        if self.busy:
            self.busy -= 1
            raise FakeUnity.Refused("EDITOR_BUSY")
        if action == "set":
            model = self.models[p["asset_path"]]
            model["follow"] = bool(p["follow"]) if "follow" in p else model["follow"]
            model["source"] = posix(p["source"]) if p.get("source") else model["source"]
            return {"asset_path": p["asset_path"], "follow": model["follow"], "source": model["source"]}
        manifest = json.loads(Path(p["manifest"]).read_text(encoding="utf-8"))
        data = (Path(p["manifest"]).parent / manifest["model_file"]).read_bytes()
        found = next((path for path, model in self.models.items()
                      if same(model["source"], manifest["source"]) and model["roots"] == sorted(manifest["roots"])), None)
        if action == "update":
            if found is None:
                return {"state": "missing"}
            if not self.models[found]["follow"]:
                return {"state": "off", "asset_path": found}
            if self.models[found]["data"] == data:
                return {"state": "unchanged", "asset_path": found}
            self.models[found]["data"] = data
            return {"state": "updated", "asset_path": found, "instance_count": 1}
        if self.at_work:
            raise FakeUnity.Refused("CONFLICT")
        path = found or self.came(p["manifest"])
        self.models[path].update(follow=True, data=data)
        return {"state": "received", "asset_path": path, "updated": found is not None, "follow": True}


class Layout:
    """Enough of a UILayout to run a draw function without a window: what it would show, in order."""

    def __init__(self, shown=None):
        self.shown = [] if shown is None else shown

    def label(self, text="", icon="NONE"):
        self.shown.append(text)

    def operator(self, idname, text="", icon="NONE", emboss=True):
        self.shown.append(f"[{text}]")
        return types.SimpleNamespace()

    def column(self, align=False):
        return Layout(self.shown)


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class FollowTests(unittest.TestCase):
    """A model that follows this file: sent again to Unity, straight to its bridge, when the file is saved (and after
    a 되돌리기, when the file is opened, when a Unity opens). Claude Hand is off throughout: none of it needs Claude."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home, self.exchange, self.blend = root / "home", root / "exchange", root / "rocks.blend"
        os.environ["CLAUDE_HAND_HOME"] = str(self.home)
        os.environ["CLAUDE_HAND_EXCHANGE_DIR"] = str(self.exchange)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mesh = bpy.data.meshes.new("RockMesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.materials.append(bpy.data.materials.new("RockMat"))
        other = mesh.copy()
        for name, data, parent in (("Rock", mesh, None), ("Pebble", mesh, "Rock"), ("Bystander", other, None)):
            obj = bpy.data.objects.new(name, data)
            obj.parent = bpy.data.objects.get(parent) if parent else None
            bpy.context.scene.collection.objects.link(obj)
        bpy.ops.wm.save_as_mainfile(filepath=str(self.blend))
        self.unity = FakeUnity(self.home)
        self.others = []
        spec = importlib.util.spec_from_file_location("claude_hand_chat", ADDON)
        self.addon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.addon)
        self.addon.UNITY_LOOK_SECONDS, self.addon.BUSY_WAIT = 0.0, 0.01
        self.addon.register()
        self.settle()

    def tearDown(self):
        self.addon.unregister()
        for unity in (self.unity, *self.others):
            unity.close()
        os.environ.pop("CLAUDE_HAND_HOME", None)
        os.environ.pop("CLAUDE_HAND_EXCHANGE_DIR", None)
        self.tmp.cleanup()

    def settle(self):
        """Until every look that is due has been made and answered."""
        addon = self.addon
        idle = lambda: not addon._follow["busy"] and addon._follow["due"] is None and addon._inbox.empty()
        self.assertTrue(wait_for(lambda: idle() and (addon.tick() or True) and idle(), addon, 20), addon._follow)

    def sent_by_claude(self, roots=("Rock",), name="rock", follow=True):
        """As after "send it to Unity" through Claude: exported here, taken in there."""
        return self.unity.came(self.addon.export_fbx(list(roots), name)["manifest"], follow)

    def save(self):
        bpy.context.view_layer.update()  # what a tick of the UI does: Blender tells what was changed
        bpy.ops.wm.save_mainfile()
        self.settle()

    def notices(self):
        return [e["text"] for e in self.addon._entries if e.get("kind") == "notice"]

    def test_a_model_that_follows_is_sent_when_the_file_is_saved(self):
        addon, unity, rock = self.addon, self.unity, bpy.data.objects["Rock"]
        path = self.sent_by_claude(follow=False)
        rock.location.x += 1
        self.save()
        self.assertEqual((unity.did("update"), [(r["name"], r["project"], r["follow"], r["here"]) for r in addon._follow["rows"]]),
                         ([], [("rock", "MyGame", False, True)]))  # listed, and left alone: it was not told to follow
        rock.location.x -= 1
        self.assertEqual(bpy.ops.claude_hand.follow_toggle(index=0), {"FINISHED"})  # the panel's check box
        self.settle()
        self.assertEqual((unity.did("set")[0]["follow"], unity.models[path]["follow"], addon._follow["rows"][0]["follow"]), (True, True, True))
        self.assertEqual(unity.did("update"), [])  # what Claude sent is what a look finds: nothing to send
        before = unity.models[path]["data"]
        rock.location.x += 1
        self.save()
        self.assertEqual(len(unity.did("update")), 1)
        self.assertNotEqual(unity.models[path]["data"], before)
        self.assertRegex(self.notices()[-1], r"^저장 → MyGame: Rock 반영 \(씬 오브젝트 1개\) \(\d\d:\d\d\)$")  # by its name here
        self.assertEqual(unity.did("update")[0]["why"], "저장")  # Unity's chat window says the same
        self.assertEqual(addon.entry_rows(addon._entries[-1], None)[0][0][1], "FILE_REFRESH")
        self.assertEqual([p.name for p in self.exchange.iterdir() if p.name.startswith("follow_")], [])  # nothing piles up
        self.save()                                     # saved again with nothing done
        bpy.data.objects["Bystander"].location.x += 1   # ... and with something done that does not go to Unity
        self.save()
        self.assertEqual(len(unity.did("update")), 1)
        bpy.data.objects["Pebble"].location.z += 1      # a child goes with its parent
        self.save()
        self.assertEqual((len(unity.did("update")), len(self.notices())), (2, 1))  # the line it had says so, no second line
        self.assertRegex(self.notices()[-1], r"반영 \(씬 오브젝트 1개\) · 2번째 \(\d\d:\d\d\)$")
        bpy.ops.claude_hand.follow_toggle(index=0)      # off again
        self.settle()
        rock.location.x += 1
        self.save()
        self.assertEqual((unity.models[path]["follow"], len(unity.did("update"))), (False, 2))

    def test_two_models_keep_a_line_each_and_a_look_that_fails_costs_only_itself(self):
        addon, unity = self.addon, self.unity
        self.sent_by_claude()
        self.sent_by_claude(("Bystander",), "bystander")
        self.save()
        for step in range(3):
            bpy.data.objects["Rock"].location.x += 1
            bpy.data.objects["Bystander"].location.x += 1 if step != 1 else 0  # the second save changes one of them only
            self.save()
        lines = self.notices()
        self.assertEqual((len(unity.did("update")), len(lines)), (5, 2))  # five sends, a line for each model
        self.assertRegex(lines[0], r"^저장 → MyGame: Rock 반영 \(씬 오브젝트 1개\) · 3번째 \(\d\d:\d\d\)$")
        self.assertRegex(lines[1], r"^저장 → MyGame: Bystander 반영 \(씬 오브젝트 1개\) · 2번째 \(\d\d:\d\d\)$")
        addon._notice("다른 줄")  # something else in between: after it, a new line
        bpy.data.objects["Rock"].location.x += 1
        self.save()
        self.assertEqual(len(self.notices()), 4)
        # Blender drops a timer whose function raises, and the panel's timer is the chat's too.
        real, addon._row = addon._row, lambda entry, model: 1 / 0
        bpy.data.objects["Rock"].location.x += 1
        self.save()
        self.assertEqual((addon._follow["busy"], len(unity.did("update"))), (0, 6))
        self.assertIn("살펴보다 오류가 났어요: division by zero", addon._follow["status"])
        addon._row = real
        self.save()
        self.assertEqual((len(unity.did("update")), addon._follow["status"]), (7, ""))  # the next look is as good as ever

    def test_a_copy_saved_elsewhere_is_not_this_file_and_another_name_takes_the_models_along(self):
        addon, unity, rock = self.addon, self.unity, bpy.data.objects["Rock"]
        path = self.sent_by_claude()
        self.save()
        rock.location.x += 1
        bpy.context.view_layer.update()
        bpy.ops.wm.save_as_mainfile(filepath=str(self.blend.with_name("backup.blend")), copy=True)  # Save Copy
        self.settle()
        self.assertEqual(unity.did("update"), [])
        renamed = self.blend.with_name("rocks_v2.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(renamed))                                         # Save As
        self.settle()
        told = [p for p in unity.did("list") if p.get("moved_from")]
        self.assertEqual((Path(told[0]["moved_from"]), Path(told[0]["source"])), (self.blend, renamed))
        self.assertEqual((Path(unity.models[path]["source"]), len(unity.did("update")), addon._follow["moved_from"]), (renamed, 1, ""))
        self.assertEqual(Path(unity.did("update")[0]["manifest"]).name, "follow_aaaa1111_rock.claudehand.json")
        rock.location.x += 1
        self.save()
        self.assertEqual(len(unity.did("update")), 2)  # and it goes on following under the new name
        self.assertEqual([p for p in unity.did("list") if p.get("moved_from")], told)  # told once

    def test_renamed_and_copied_objects_still_find_their_model(self):
        addon, unity, rock = self.addon, self.unity, bpy.data.objects["Rock"]
        self.sent_by_claude()
        self.save()
        rock.name = "Boulder"
        rock.location.x += 1
        self.save()
        self.assertEqual((len(unity.did("update")), addon._follow["rows"][0]["objects"]), (1, ["Boulder"]))
        self.assertRegex(self.notices()[-1], r"^저장 → MyGame: Boulder 반영")
        copy = rock.copy()
        bpy.context.scene.collection.objects.link(copy)
        copy.location.y += 3
        self.save()
        row = addon._follow["rows"][0]
        self.assertEqual((len(unity.did("update")), row["objects"], row["missing"]), (1, None, "Rock"))  # which of the two? not guessed
        self.assertIn("'Rock'(으)로 보낸 오브젝트가 둘이에요(Boulder, Boulder.001)", row["problem"])
        shown = Layout()
        addon.draw_follow(shown, None)
        self.assertIn("[선택한 오브젝트로 잇기]", shown.shown)
        bpy.context.view_layer.objects.active = copy
        self.assertEqual(bpy.ops.claude_hand.follow_adopt(index=0), {"FINISHED"})
        self.settle()
        self.assertEqual((addon.sent_as(copy), addon.sent_as(rock), addon._follow["rows"][0]["objects"]), ("Rock", "", ["Boulder.001"]))
        self.assertEqual(len(unity.did("update")), 2)  # the one that was picked is what Unity has now

    def test_a_mesh_that_never_comes_out_the_same_is_sent_only_when_something_was_done_to_it(self):
        """A Bevel modifier gives other UVs every time the mesh is worked out, so the file alone cannot tell."""
        addon, unity = self.addon, self.unity
        bpy.ops.mesh.primitive_monkey_add()
        monkey = bpy.context.active_object
        monkey.name = "Monkey"
        monkey.modifiers.new("Bevel", "BEVEL")
        self.assertNotEqual(*(addon._write_fbx([monkey], str(self.home / "probe.fbx")) or (self.home / "probe.fbx").read_bytes() for _ in range(2)))
        path = self.sent_by_claude(("Monkey",), "monkey")
        self.save()
        self.assertEqual(len(unity.did("update")), 1)  # the first look of a file cannot know: sent once
        self.save()
        bpy.data.objects["Bystander"].location.x += 1
        self.save()
        self.assertEqual(len(unity.did("update")), 1)  # differs from what Unity has, as it always will: nothing was done to it
        monkey.location.x += 1
        self.save()
        self.assertEqual(len(unity.did("update")), 2)
        monkey.modifiers["Bevel"].width *= 2
        self.save()
        self.assertEqual((len(unity.did("update")), unity.models[path]["follow"]), (3, True))
        monkey.location.z += 1  # changed and saved in one go, as a script does it: Blender has not told of the change yet
        bpy.ops.wm.save_mainfile()
        self.settle()
        self.assertEqual(len(unity.did("update")), 4)

    def test_a_model_that_takes_long_to_export_is_exported_only_when_something_was_done_to_it(self):
        addon, unity, rock = self.addon, self.unity, bpy.data.objects["Rock"]
        self.sent_by_claude()
        self.save()
        written, real = [], addon._write_fbx
        addon._write_fbx = lambda objects, path: written.append(path) or real(objects, path)
        self.save()
        self.assertEqual(len(written), 1)  # a quick one is exported at every save: the file is what tells for sure
        next(iter(addon._follow["models"].values()))["took"] = 5.0
        self.save()
        bpy.data.objects["Bystander"].location.x += 1
        self.save()
        self.assertEqual(len(written), 1)
        rock.location.x += 1
        self.save()
        self.assertEqual((len(written), len(unity.did("update"))), (2, 1))
        rock.name = "Stone"  # Blender does not tell of a new name, but the names are looked at
        self.save()
        self.assertEqual(len(written), 3)

    def test_after_an_undo_the_model_goes_back_in_unity_too(self):
        addon, unity = self.addon, self.unity
        path = self.sent_by_claude()
        self.save()
        original = unity.models[path]["data"]
        addon._undo_op(bpy.ops.ed.undo_push, message="Original")
        addon.mark_undo_point("바위를 옮겨줘")
        bpy.ops.transform.translate(value=(2, 0, 0))  # the request moves what is selected ...
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj.name == "Rock")
        bpy.ops.transform.translate(value=(2, 0, 0))
        self.save()                                    # ... and the user saves: Unity has the moved rock
        self.assertNotEqual(unity.models[path]["data"], original)
        self.assertTrue(addon.undo_last_request().startswith("되돌렸어요"))
        self.settle()
        self.assertEqual(unity.models[path]["data"], original)  # though nothing was saved since
        self.assertRegex(self.notices()[-1], r"^되돌리기 → MyGame: Rock 반영")
        self.assertEqual(unity.did("update")[-1]["why"], "되돌리기")

    def test_a_unity_that_opens_later_and_a_file_that_is_opened_catch_up(self):
        addon, unity, rock = self.addon, self.unity, bpy.data.objects["Rock"]
        path = self.sent_by_claude()
        self.save()
        stale = unity.models[path]["data"]
        unity.registry.rename(unity.registry.with_suffix(".closed"))  # Unity is closed
        rock.location.x += 1
        self.save()
        self.assertEqual((unity.did("update"), addon._follow["rows"]), ([], []))
        unity.registry.with_suffix(".closed").rename(unity.registry)  # ... and opened again
        self.settle()
        self.assertEqual(len(unity.did("update")), 1)
        self.assertRegex(self.notices()[-1], r"^Unity 열림 → MyGame: Rock 반영")
        unity.models[path]["data"] = stale                            # as if the save had come while Unity was closed
        bpy.ops.wm.open_mainfile(filepath=str(self.blend))            # the file is opened (in another Blender run, say)
        self.settle()
        self.assertEqual(len(unity.did("update")), 2)
        self.assertRegex(self.notices()[-1], r"^파일 열기 → MyGame: Rock 반영")
        bpy.ops.wm.open_mainfile(filepath=str(self.blend))
        self.settle()
        self.assertEqual(len(unity.did("update")), 2)  # in step: opening sends nothing

    def test_a_unity_that_is_busy_gone_or_too_old(self):
        addon, unity, rock = self.addon, self.unity, bpy.data.objects["Rock"]
        self.sent_by_claude()
        self.save()
        unity.busy = 2  # compiling for a moment
        rock.location.x += 1
        self.save()
        self.assertEqual((len(unity.did("update")), unity.busy), (1, 0))  # only the calls that got through are counted there
        unity.busy = 99
        rock.location.x += 1
        self.save()
        self.assertIn("보내지 못했어요(Unity가 컴파일 중이에요). 다음 저장 때 다시 보냅니다.", self.notices()[-1])
        unity.busy = 0
        self.save()  # nothing done since, and still it is sent: Unity never got it
        self.assertEqual(len(unity.did("update")), 2)
        gone = FakeUnity(self.home, "Gone", "bbbb2222")
        gone.server.shutdown()
        gone.server.server_close()  # its registry file is left behind (a crash)
        self.others.append(gone)
        old = FakeUnity(self.home, "Old", "cccc3333", tools=("ch_import_model",))  # Claude Hand Tools from before models could follow
        self.others.append(old)
        rock.location.x += 1
        self.save()
        self.assertEqual((len(unity.did("update")), old.calls, addon._follow["editors"]), (3, [], [("MyGame", "aaaa1111"), ("Gone", "bbbb2222")]))
        self.assertEqual(addon._follow["status"], "Gone: Unity가 응답하지 않아요")

    def test_sending_from_the_panel_without_claude(self):
        addon, unity = self.addon, self.unity
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj.name in ("Rock", "Pebble"))
        bpy.context.view_layer.objects.active = bpy.data.objects["Pebble"]
        self.assertEqual(addon.selected_roots(), ["Rock"])  # the child goes with its parent anyway
        unity.at_work = True
        self.assertEqual(bpy.ops.claude_hand.follow_send(project="aaaa1111"), {"FINISHED"})
        self.settle()
        self.assertEqual((unity.models, self.notices()[-1]), ({}, "MyGame: 그 프로젝트에서 Claude가 일하는 중이에요. 끝난 뒤 다시 해 주세요"))
        unity.at_work = False
        bpy.ops.claude_hand.follow_send(project="aaaa1111")
        self.settle()
        (path, model), = unity.models.items()
        self.assertEqual((path, model["roots"], model["follow"], Path(model["source"])),
                         ("Assets/ClaudeHand/Imports/Rock/Rock.fbx", ["Rock"], True, self.blend))
        self.assertIn("MyGame에 Rock을(를) 보냈어요(씬에 놓음). 저장할 때마다 다시 갑니다.", self.notices()[-1])
        self.assertIn("[사용자가 Blender 패널에서 'Rock'을(를) Unity MyGame에 직접 보냈음: Assets/ClaudeHand/Imports/Rock/Rock.fbx", addon._undo_note[0])
        self.assertEqual([(r["name"], r["follow"]) for r in addon._follow["rows"]], [("Rock", True)])
        bpy.data.objects["Rock"].location.x += 1
        self.save()
        self.assertEqual(len(unity.did("update")), 1)  # and from then on it follows
        self.assertEqual(addon.send_to_unity(["Rock"], "ffff0000"), "그 Unity 프로젝트가 열려 있지 않아요.")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.assertIn("먼저 이 .blend를 저장하세요", addon.send_to_unity(["Rock"], "aaaa1111"))

    def test_the_panel_lists_the_models_and_ties_a_moved_file_to_them_again(self):
        addon, unity = self.addon, self.unity
        shown = Layout()
        addon.draw_follow(shown, None)
        self.assertEqual(shown.shown, ["이 파일에서 Unity로 간 모델이 없습니다.", "[선택한 오브젝트 보내기 → MyGame]", "[새로 고침]"])
        path = self.sent_by_claude()
        unity.models[path]["source"] = "D:/old place/rocks.blend"  # the file was moved outside Blender since
        unity.models["Assets/Other/Rock.fbx"] = {"source": "D:/another/file.blend", "roots": ["Bystander"], "follow": True, "data": b""}
        bpy.ops.claude_hand.follow_look()
        self.settle()
        # Listed: the model this file's Rock once went to. Not listed: one of another file that merely has an object of that name.
        self.assertEqual([(r["asset"], r["here"]) for r in addon._follow["rows"]], [(path, False)])
        shown = Layout()
        addon.draw_follow(shown, None)
        self.assertEqual(shown.shown[:2], ["Rock → MyGame · rocks.blend에서 온 것으로 적혀", "있음"])
        self.assertIn("[이 파일로 잇기]", shown.shown)
        self.assertEqual(unity.did("update"), [])
        bpy.ops.claude_hand.follow_relink(index=0)
        self.settle()
        self.assertEqual((Path(unity.models[path]["source"]), addon._follow["rows"][0]["here"]), (self.blend, True))
        shown = Layout()
        addon.draw_follow(shown, None)
        self.assertEqual(shown.shown[:2], ["[Rock → MyGame]", "체크한 모델은 이 파일을 저장할 때마다 Unity에서 바뀝니다."])
        unity.close()
        self.settle()
        shown = Layout()
        addon.draw_follow(shown, None)
        self.assertEqual(len(shown.shown), 2)  # "열려 있는 Unity가 없습니다 ..." on two lines, and nothing to press
        self.assertTrue(shown.shown[0].startswith("열려 있는 Unity가 없습니다"))


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class HeadlessEnableTests(unittest.TestCase):
    def test_enable_script_turns_the_addon_on_and_saves_preferences(self):
        """What `blender-install-chat` runs, in a fresh Blender with its own user folders."""
        import shutil
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            scripts, config = Path(tmp) / "scripts", Path(tmp) / "config"
            (scripts / "addons").mkdir(parents=True)
            config.mkdir()
            shutil.copy2(ADDON, scripts / "addons" / ADDON.name)
            env = {**os.environ, "BLENDER_USER_SCRIPTS": str(scripts), "BLENDER_USER_CONFIG": str(config),
                   "CLAUDE_HAND_HOME": str(Path(tmp) / "home")}
            code = "import bpy\nexec(" + repr(handlib.install.ENABLE_CHAT_ADDON) + ")\nprint('PANEL', hasattr(bpy.types, 'CLAUDEHAND_PT_chat'))"
            # Inside Blender itself (selftest), sys.executable is its bundled Python, which has no bpy: run Blender.
            blender = getattr(bpy.app, "binary_path", "") or ""
            cmd = ([blender, "--background", "--python-expr", code] if Path(blender).name.lower().startswith("blender")
                   else [sys.executable, "-c", code])
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            self.assertIn("CLAUDE_HAND_CHAT enabled", proc.stdout, proc.stderr)
            self.assertIn("PANEL True", proc.stdout)
            self.assertTrue((config / "userpref.blend").exists())


if __name__ == "__main__":
    unittest.main()
