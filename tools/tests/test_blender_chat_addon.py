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
        self.models = {}       # asset path -> {"source", "roots", "follow", "data": the bytes of its file, "look": its materials_hash, "textures": {file name: bytes}}
        self.calls = []        # (action, params) of every call that got in, in order
        self.busy = 0          # compiling: this many calls that would change something are told EDITOR_BUSY
        self.at_work = False   # a request is at work in the project
        self.tells_materials = True  # Claude Hand Tools of 0.17 and later: the list says what the materials of a model were when it came
        self.shape_check = None      # what an update is answered with as "shape_check": Unity's measure of the model it took, against the manifest's "check"
        self.checked = []            # the "check" of every update, as it came
        self.kept = []               # the materials an update or a send is answered to have left as they were edited in Unity ("materials_made")
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
                             "data": (Path(manifest).parent / data["model_file"]).read_bytes(), "look": data.get("materials_hash"),
                             "textures": self.textures_of(manifest, data)}
        return path

    @staticmethod
    def textures_of(manifest, data):
        """The images that come with a model, as Unity copies them in when it takes the model: {file name: bytes}."""
        folder = Path(manifest).parent / (data.get("textures_dir") or "no such folder")
        return {image.name: image.read_bytes() for image in folder.iterdir()} if folder.is_dir() else {}

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
                 "file_hash": hashlib.sha256(model["data"]).hexdigest() if model["follow"] and same(model["source"], p.get("source")) else None,
                 **({"materials_hash": (model["look"] or "") if model["follow"] and same(model["source"], p.get("source")) else None} if self.tells_materials else {})}
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
            if self.models[found]["data"] == data and manifest.get("materials_hash") in (None, self.models[found]["look"]):
                return {"state": "unchanged", "asset_path": found}
            self.models[found].update(data=data, look=manifest.get("materials_hash", self.models[found]["look"]), textures=self.textures_of(p["manifest"], manifest))
            self.checked.append(manifest.get("check"))
            return {"state": "updated", "asset_path": found, "instance_count": 1, **({"shape_check": self.shape_check} if self.shape_check else {}),
                    "materials_made": {"made": [], "kept": list(self.kept)} if self.kept else None}
        if self.at_work:
            raise FakeUnity.Refused("CONFLICT")
        path = found or self.came(p["manifest"])
        self.models[path].update(follow=True, data=data, look=manifest.get("materials_hash"), textures=self.textures_of(p["manifest"], manifest))
        return {"state": "received", "asset_path": path, "updated": found is not None, "follow": True,
                "materials_made": {"made": [], "kept": list(self.kept)} if self.kept else None}


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
        bystander = bpy.data.objects["Bystander"]
        paint = bpy.data.materials.new("BystanderPaint")
        bystander.data.materials.append(paint)
        probes = []
        for _ in range(2):
            addon._write_fbx([monkey], str(self.home / "probe.fbx"))
            probes.append((self.home / "probe.fbx").read_bytes())
        self.assertNotEqual(*probes)
        path = self.sent_by_claude(("Monkey",), "monkey")
        self.save()
        self.assertEqual(len(unity.did("update")), 1)  # the first look of a file cannot know: sent once
        self.save()
        bpy.data.objects["Bystander"].location.x += 1
        self.save()
        self.assertEqual(len(unity.did("update")), 1)  # differs from what Unity has, as it always will: nothing was done to it
        paint.diffuse_color = (1.0, 0.0, 0.0, 1.0)  # another object's material and mesh are not this model's either
        bystander.data.vertices[0].co.x += 0.1      # (they counted for every model: found in the open windows, 0.17.0)
        bystander.data.update()
        self.save()
        self.assertEqual(len(unity.did("update")), 1)
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

    def paint(self, colour):
        """The image file the rock wears, as a new file in place of the one before: 2 x 2 pixels of this colour, and a later time on it
        than the last one had, whatever the clock of the file system."""
        path = self.blend.parent / "pictures" / "stone.png"
        path.parent.mkdir(exist_ok=True)
        before = path.stat().st_mtime_ns if path.exists() else 0
        made = bpy.data.images.new("claude_hand_test", 2, 2, alpha=True)
        made.pixels.foreach_set(list(colour) * 4)
        made.filepath_raw, made.file_format = str(path), "PNG"
        made.save()
        bpy.data.images.remove(made)
        later = max(path.stat().st_mtime_ns, before) + 2_000_000_000
        os.utime(path, ns=(later, later))
        return path

    def stone(self):
        """RockMat gets an image file as its base colour: (the file, the material)."""
        path = self.paint((0.2, 0.4, 0.6, 1.0))
        material = bpy.data.materials["RockMat"]
        shader = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(str(path), check_existing=False)
        material.node_tree.links.new(node.outputs["Color"], shader.inputs["Base Color"])
        return path, material

    def leftovers(self):
        return sorted(p.name for p in self.exchange.iterdir() if p.name.startswith("follow_"))

    def test_a_model_is_not_sent_again_when_neither_its_file_nor_its_materials_changed(self):
        addon, unity = self.addon, self.unity
        self.stone()
        path = self.sent_by_claude()
        self.assertEqual((unity.models[path]["look"], sorted(unity.models[path]["textures"])),
                         (addon._materials(addon._with_children(["Rock"])[1])[2], ["stone.png"]))  # Unity has the hash and the image that came
        self.save()
        bpy.data.objects["Bystander"].location.x += 1  # something that does not go
        self.save()
        self.assertEqual((unity.did("update"), self.leftovers()), ([], []))

    def test_a_model_is_sent_again_when_only_a_material_value_changed(self):
        addon, unity = self.addon, self.unity
        _, material = self.stone()
        path = self.sent_by_claude()
        self.save()
        objects, model = addon._with_children(["Rock"])[1], unity.models[path]["data"]
        look = unity.models[path]["look"]
        probe = self.home / "probe.fbx"
        addon._write_fbx(objects, str(probe))
        probed = probe.read_bytes()
        material.use_backface_culling = True
        addon._write_fbx(objects, str(probe))
        self.assertEqual(probe.read_bytes(), probed)  # a value that the manifest says and the FBX does not
        self.save()
        self.assertEqual(len(unity.did("update")), 1)
        self.assertEqual((unity.models[path]["data"], unity.models[path]["look"]), (model, addon._materials(objects)[2]))
        self.assertNotEqual(unity.models[path]["look"], look)
        self.assertRegex(self.notices()[-1], r"^저장 → MyGame: Rock 반영")
        self.save()
        self.assertEqual(len(unity.did("update")), 1)  # in step again
        next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Roughness"].default_value = 0.1  # this one is in the FBX too
        self.save()
        self.assertEqual((len(unity.did("update")), unity.models[path]["look"]), (2, addon._materials(objects)[2]))
        self.assertNotEqual(unity.models[path]["data"], model)
        self.assertEqual(self.leftovers(), [])

    def test_a_model_is_sent_again_with_its_images_when_only_an_image_file_changed(self):
        addon, unity = self.addon, self.unity
        picture, _ = self.stone()
        path = self.sent_by_claude()
        self.save()
        model, was = unity.models[path]["data"], unity.models[path]["textures"]["stone.png"]
        self.paint((0.9, 0.1, 0.1, 1.0))  # painted in another program and saved: Blender is not told
        self.save()
        self.assertEqual(len(unity.did("update")), 1)
        self.assertEqual(unity.models[path]["data"], model)  # the FBX only names the image
        self.assertEqual(unity.models[path]["textures"], {"stone.png": picture.read_bytes()})
        self.assertNotEqual(unity.models[path]["textures"]["stone.png"], was)
        self.assertEqual((unity.models[path]["look"], self.leftovers()), (addon._materials(addon._with_children(["Rock"])[1])[2], []))
        self.save()
        self.assertEqual(len(unity.did("update")), 1)

    def test_the_images_of_a_followed_model_are_written_only_when_it_is_sent(self):
        addon = self.addon
        self.stone()
        self.sent_by_claude()
        self.save()
        row = addon._follow["rows"][0]
        objects = addon._with_children(row["objects"])[1]
        _, files, quick = addon._export_followed(row, objects)  # what a look does to find out
        self.assertEqual((os.path.exists(files[0]), os.path.exists(files[2])), (True, False))
        addon._discard(files)
        manifest, files, full = addon._export_followed(row, objects, textures=True)  # what goes to Unity
        self.assertEqual(quick, full)
        self.assertEqual(sorted(p.name for p in Path(files[2]).iterdir()), ["stone.png"])
        self.assertEqual(json.loads(Path(manifest).read_text(encoding="utf-8"))["textures_dir"], Path(files[2]).name)
        addon._discard(files)
        self.assertEqual((self.leftovers(), [os.path.exists(f) for f in files]), ([], [False, False, False]))

    def test_a_unity_that_tells_nothing_of_materials_is_sent_only_what_its_file_shows(self):
        """Claude Hand Tools of before 0.17 have no materials_hash in their list: the look cannot be compared, only the model file."""
        unity, rock = self.unity, bpy.data.objects["Rock"]
        unity.tells_materials = False
        _, material = self.stone()
        self.sent_by_claude()
        self.save()
        material.use_backface_culling = True
        self.save()
        self.assertEqual(unity.did("update"), [])
        rock.location.x += 1
        self.save()
        self.assertEqual(len(unity.did("update")), 1)

    def noisy(self):
        """RockMat takes its base colour from a Noise Texture, which is baked (32 pixels a side, to be quick), and the rock's mesh
        gets the UV map that needs: the noise."""
        uv = bpy.data.meshes["RockMesh"].uv_layers.new(name="UVMap")
        for loop, place in zip(uv.data, ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))):
            loop.uv = place
        real = self.addon._materials
        self.addon._materials = lambda objects, folder=None, bake=False, size=32: real(objects, folder, bake, size)
        material = bpy.data.materials["RockMat"]
        shader = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        noise = material.node_tree.nodes.new("ShaderNodeTexNoise")
        material.node_tree.links.new(noise.outputs["Color"], shader.inputs["Base Color"])
        return noise

    def test_a_model_whose_material_is_fed_by_nodes_is_sent_again_when_a_node_changes(self):
        addon, unity = self.addon, self.unity
        noise = self.noisy()
        path = self.sent_by_claude()
        was = dict(unity.models[path]["textures"])
        self.assertEqual(sorted(was), ["RockMat_BaseColor.png"])  # baked when it was sent
        self.save()
        self.assertEqual(unity.did("update"), [])  # nothing changed: nothing is baked again, nothing is sent
        look = unity.models[path]["look"]
        noise.location.x += 100.0  # a node moved
        noise.label = "Rust"
        self.save()
        self.assertEqual(unity.did("update"), [])
        noise.inputs["Scale"].default_value = 9.0
        self.save()
        self.assertEqual(len(unity.did("update")), 1)
        self.assertEqual((unity.models[path]["look"], unity.models[path]["look"] != look), (addon._materials(addon._with_children(["Rock"])[1], None, True)[2], True))
        self.assertEqual(sorted(unity.models[path]["textures"]), ["RockMat_BaseColor.png"])  # the baked file is among the images Unity took ...
        self.assertNotEqual(unity.models[path]["textures"]["RockMat_BaseColor.png"], was["RockMat_BaseColor.png"])  # ... and it is the new one
        self.assertEqual(self.leftovers(), [])
        self.save()
        self.assertEqual(len(unity.did("update")), 1)

    def test_a_bake_that_waits_for_object_mode_is_told_in_the_panel(self):
        """Else nodes changed and saved in Edit Mode look as if they had not arrived (found in the open windows, 0.17.0)."""
        unity = self.unity
        noise = self.noisy()
        path = self.sent_by_claude()
        was = dict(unity.models[path]["textures"])
        lines = lambda: [t for t in self.notices() if "오브젝트 모드에서 저장하면 구워서 갑니다" in t]
        bpy.context.view_layer.objects.active = bpy.data.objects["Rock"]
        bpy.ops.object.mode_set(mode="EDIT")
        self.addCleanup(lambda: bpy.context.mode != "OBJECT" and bpy.ops.object.mode_set(mode="OBJECT"))
        noise.inputs["Scale"].default_value = 9.0
        self.save()
        self.assertEqual((len(unity.did("update")), len(lines())), (1, 1))
        self.assertRegex(lines()[0], r"^MyGame: Rock의 노드로 만든 재질은 오브젝트 모드에서 저장하면 구워서 갑니다\(지금은 Unity에 있던 이미지 그대로\)\. \(\d\d:\d\d\)$")
        self.assertEqual(unity.models[path]["textures"], {})  # nothing was baked: no image went (Unity keeps the one it has)
        bpy.ops.object.mode_set(mode="OBJECT")
        self.save()
        self.assertEqual((len(unity.did("update")), len(lines())), (2, 1))  # baked and sent now; nothing more is said
        self.assertNotEqual(unity.models[path]["textures"]["RockMat_BaseColor.png"], was["RockMat_BaseColor.png"])

    def test_a_model_that_went_in_differently_is_told_in_the_panel(self):
        unity, rock = self.unity, bpy.data.objects["Rock"]
        self.sent_by_claude()
        self.save()
        lines = lambda: [t for t in self.notices() if "다르게 들어갔어요" in t]
        unity.shape_check = {"same": True, "differing": [], "missing": []}
        rock.location.x += 1
        self.save()
        self.assertEqual((len(unity.did("update")), lines()), (1, []))
        self.assertEqual(sorted(unity.checked[-1]["parts"]), ["Pebble", "Rock"])  # what the update said the model should be
        unity.shape_check = {"same": False, "differing": [{"name": "Pebble", "off": 0.5}], "missing": []}
        rock.location.x += 1
        self.save()
        self.assertEqual(len(lines()), 1)
        self.assertRegex(lines()[0], r"^MyGame: Rock이\(가\) Unity에 보낸 것과 다르게 들어갔어요\(Pebble\)\. Claude에게 알려 주세요\. \(\d\d:\d\d\)$")
        unity.shape_check = {"same": False, "differing": [], "missing": ["Rock", "Pebble"]}
        rock.location.x += 1
        self.save()
        self.assertEqual(len(lines()), 1)  # the line it had, said again
        self.assertRegex(lines()[0], r"다르게 들어갔어요\(Rock, Pebble\)\. Claude에게 알려 주세요\. · 2번째 \(\d\d:\d\d\)$")

    def test_a_material_that_unity_left_as_edited_there_is_told_in_the_panel(self):
        """Else a material changed here and saved looks as if it had not arrived (found in the open windows, 0.17.0)."""
        unity, rock = self.unity, bpy.data.objects["Rock"]
        self.sent_by_claude()
        self.save()
        lines = lambda: [t for t in self.notices() if "Unity 것 그대로" in t]
        rock.location.x += 1
        self.save()
        self.assertEqual((len(unity.did("update")), lines()), (1, []))  # nothing was kept: nothing is said
        unity.kept = ["RockMat"]
        rock.location.x += 1
        self.save()
        self.assertEqual(len(lines()), 1)
        self.assertRegex(lines()[0], r"^MyGame에서 고친 재질은 Unity 것 그대로 뒀어요\(RockMat\)\. Blender 재질대로 다시 만들려면 Unity에서 그 \.mat 파일을 "
                                     r"지우고 다시 보내세요\. \(\d\d:\d\d\)$")
        rock.location.x += 1
        self.save()
        self.assertEqual(len(lines()), 1)  # the line it had, said again
        self.assertRegex(lines()[0], r"다시 보내세요\. · 2번째 \(\d\d:\d\d\)$")


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class LookCase(unittest.TestCase):
    """A prop that has a mesh to give materials to, and the helpers for making them: what LookTests and BakeTests start from."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.exchange, self.pictures, self.textures = root / "exchange", root / "pictures", root / "textures"
        self.pictures.mkdir()
        os.environ["CLAUDE_HAND_EXCHANGE_DIR"] = str(self.exchange)
        spec = importlib.util.spec_from_file_location("claude_hand_chat", ADDON)
        self.addon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.addon)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mesh = bpy.data.meshes.new("PropMesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.uv_layers.new(name="UVMap")
        self.prop = bpy.data.objects.new("Prop", mesh)
        bpy.context.scene.collection.objects.link(self.prop)
        bpy.context.view_layer.update()  # or the view layer does not have it yet

    def tearDown(self):
        os.environ.pop("CLAUDE_HAND_EXCHANGE_DIR", None)
        self.tmp.cleanup()

    def material(self, name="Mat"):
        """A new material on the prop: (it, its Principled BSDF)."""
        material = bpy.data.materials.new(name)
        if material.node_tree is None:  # before Blender 5 a new material had no nodes until asked for them
            material.use_nodes = True
        self.prop.data.materials.append(material)
        return material, next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    def picture(self, name, size=(2, 2), pixels=None, data=False):
        """An image file in the pictures folder, loaded here. `pixels`: one (r, g, b, a) per pixel, from the bottom row up.
        `data`: a picture of numbers (metallic, roughness, normals), not of colours."""
        width, height = size
        made = bpy.data.images.new("claude_hand_test", width, height, alpha=True)
        pixels = pixels or [(n / (width * height), 0.5, 1 - n / (width * height), 1.0) for n in range(width * height)]
        made.pixels.foreach_set([value for pixel in pixels for value in pixel])
        made.filepath_raw, made.file_format = str(self.pictures / f"{name}.png"), "PNG"
        made.save()
        bpy.data.images.remove(made)
        image = bpy.data.images.load(str(self.pictures / f"{name}.png"), check_existing=False)
        if data:
            image.colorspace_settings.name = "Non-Color"
        return image

    def feed(self, material, socket, image, output="Color"):
        """An Image Texture node of this image, with its `output` into `socket`."""
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        material.node_tree.links.new(node.outputs[output], socket)
        return node

    def look(self):
        """What the manifest would say of the prop's materials, the images written to the textures folder: (materials, notes, hash)."""
        return self.addon._materials([self.prop], str(self.textures))

    def read(self, path):
        """((width, height), the rows of RGBA numbers) of an image file, as Blender holds them."""
        import numpy
        image = bpy.data.images.load(str(path), check_existing=False)
        try:
            width, height = image.size
            return (width, height), numpy.array(image.pixels[:], dtype=numpy.float32).reshape(height, width, 4)
        finally:
            bpy.data.images.remove(image)


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class LookTests(LookCase):
    """What a model's materials go to Unity as: the values and image files their Principled BSDF reads, said in the manifest next to the FBX."""

    def test_values_alone_go_as_values(self):
        material, shader = self.material("Plain")
        shader.inputs["Base Color"].default_value = (0.2, 0.4, 0.6, 1.0)
        shader.inputs["Metallic"].default_value = 0.7
        shader.inputs["Roughness"].default_value = 0.3
        material.use_backface_culling = True
        (plain,), notes, _ = self.look()
        self.assertEqual((plain["name"], notes), ("Plain", []))
        self.assertEqual([round(c, 4) for c in plain["base_color"]], [0.2, 0.4, 0.6, 1.0])
        self.assertEqual((round(plain["metallic"], 4), round(plain["roughness"], 4), plain["two_sided"]), (0.7, 0.3, False))
        self.assertEqual([key for key in plain if key.endswith("_texture") or key.startswith(("emission", "alpha", "normal"))], [])
        self.assertEqual(list(self.textures.iterdir()), [])  # nothing to write

    def test_an_image_file_goes_as_it_is_and_the_colour_becomes_white(self):
        image = self.picture("bricks")
        material, shader = self.material("Wall")
        self.feed(material, shader.inputs["Base Color"], image)
        (wall,), notes, _ = self.look()
        self.assertEqual((wall["base_color"], wall["base_color_texture"]["file"], notes), ([1.0, 1.0, 1.0, 1.0], "bricks.png", []))
        self.assertNotIn("uv", wall["base_color_texture"])  # laid on the UVs as they are
        self.assertEqual((self.textures / "bricks.png").read_bytes(), (self.pictures / "bricks.png").read_bytes())  # not decoded and encoded again

    def test_an_image_packed_into_the_blend_is_written_from_the_pack(self):
        image = self.picture("packed")
        original = (self.pictures / "packed.png").read_bytes()
        image.pack()
        (self.pictures / "packed.png").unlink()  # only the .blend has it now
        material, shader = self.material()
        self.feed(material, shader.inputs["Base Color"], image)
        (found,), notes, _ = self.look()
        self.assertEqual((found["base_color_texture"]["file"], notes), ("packed.png", []))
        self.assertEqual((self.textures / "packed.png").read_bytes(), original)

    def test_an_image_made_here_and_never_saved_is_written_from_memory(self):
        made = bpy.data.images.new("Painted", 4, 2)
        sent = [(n / 8, 0.5, 1 - n / 8, 1.0) for n in range(8)]
        made.pixels.foreach_set([value for pixel in sent for value in pixel])
        material, shader = self.material()
        self.feed(material, shader.inputs["Base Color"], made)
        (found,), notes, _ = self.look()
        self.assertEqual((found["base_color_texture"]["file"], notes), ("Painted.png", []))
        size, rows = self.read(self.textures / "Painted.png")
        self.assertEqual(size, (4, 2))
        for got, want in zip(rows.reshape(-1, 4), sent):
            for channel, number in zip(got, want):
                self.assertAlmostEqual(float(channel), number, delta=0.01)

    def test_a_picture_painted_on_since_it_was_loaded_goes_as_it_is_now(self):
        image = self.picture("worn")
        image.pixels[0:4] = (1.0, 0.0, 0.0, 1.0)  # the first pixel, painted red and not saved
        self.assertTrue(image.is_dirty)
        material, shader = self.material()
        self.feed(material, shader.inputs["Base Color"], image)
        (found,), _, _ = self.look()
        self.assertNotEqual((self.textures / found["base_color_texture"]["file"]).read_bytes(), (self.pictures / "worn.png").read_bytes())
        rows = self.read(self.textures / found["base_color_texture"]["file"])[1]
        self.assertEqual([round(float(c), 2) for c in rows[0, 0]], [1.0, 0.0, 0.0, 1.0])

    def test_an_image_that_holds_more_than_8_bits_goes_as_exr(self):
        made = bpy.data.images.new("Bright", 2, 2, float_buffer=True)
        made.pixels.foreach_set([4.0, 0.5, 0.25, 1.0] * 4)  # a value above 1 that a .png would cut off
        material, shader = self.material()
        self.feed(material, shader.inputs["Base Color"], made)
        (found,), _, _ = self.look()
        self.assertEqual(found["base_color_texture"]["file"], "Bright.exr")
        rows = self.read(self.textures / "Bright.exr")[1]
        self.assertEqual([round(float(c), 2) for c in rows[0, 0]], [4.0, 0.5, 0.25, 1.0])

    def test_an_image_whose_file_is_gone_is_named_in_the_notes(self):
        image = self.picture("lost")
        image.filepath = str(self.pictures / "nowhere.png")
        image.reload()
        self.assertEqual(tuple(image.size), (0, 0))
        material, shader = self.material("Wall")
        self.feed(material, shader.inputs["Base Color"], image)
        (found,), notes, _ = self.look()
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Wall: 기본 색") and image.name in notes[0], notes)
        self.assertNotIn("base_color_texture", found)
        self.assertEqual(list(self.textures.iterdir()), [])

    def test_metallic_and_roughness_images_go_as_one_file(self):
        """Metallic in red, smoothness (one minus roughness) in alpha: what Unity's standard materials read."""
        grey = lambda *values: [(v, v, v, 1.0) for v in values]
        material, shader = self.material("Rusty")
        self.feed(material, shader.inputs["Metallic"], self.picture("metal", pixels=grey(0.0, 0.25, 0.5, 1.0), data=True))
        self.feed(material, shader.inputs["Roughness"], self.picture("rough", pixels=grey(0.1, 0.4, 0.8, 1.0), data=True))
        (rusty,), notes, _ = self.look()
        packed = rusty["metallic_smoothness_texture"]
        self.assertEqual((packed["file"], rusty["metallic"], rusty["roughness"], notes), ("Rusty_MetallicSmoothness.png", 1.0, 1.0, []))
        self.assertEqual([p.name for p in self.textures.iterdir()], ["Rusty_MetallicSmoothness.png"])  # the two are not written on their own
        size, rows = self.read(self.textures / packed["file"])
        self.assertEqual(size, (2, 2))
        for got, metal, rough in zip(rows.reshape(-1, 4), (0.0, 0.25, 0.5, 1.0), (0.1, 0.4, 0.8, 1.0)):
            self.assertAlmostEqual(float(got[0]), metal, delta=0.02)
            self.assertAlmostEqual(float(got[3]), 1 - rough, delta=0.02)

    def test_one_of_metallic_and_roughness_an_image_and_the_other_a_number(self):
        grey = lambda *values: [(v, v, v, 1.0) for v in values]
        material, shader = self.material("MetalMap")
        shader.inputs["Roughness"].default_value = 0.25
        self.feed(material, shader.inputs["Metallic"], self.picture("metal", pixels=grey(0.0, 0.25, 0.5, 1.0), data=True))
        other, shader = self.material("RoughMap")
        shader.inputs["Metallic"].default_value = 0.5
        self.feed(other, shader.inputs["Roughness"], self.picture("rough", pixels=grey(0.1, 0.4, 0.8, 0.9), data=True))
        (_, _), notes, _ = self.look()
        self.assertEqual((sorted(p.name for p in self.textures.iterdir()), notes), (["MetalMap_MetallicSmoothness.png", "RoughMap_MetallicSmoothness.png"], []))
        rows = self.read(self.textures / "MetalMap_MetallicSmoothness.png")[1].reshape(-1, 4)
        for got, metal in zip(rows, (0.0, 0.25, 0.5, 1.0)):
            self.assertAlmostEqual(float(got[0]), metal, delta=0.02)
            self.assertAlmostEqual(float(got[3]), 0.75, delta=0.02)  # the number, as smoothness
        rows = self.read(self.textures / "RoughMap_MetallicSmoothness.png")[1].reshape(-1, 4)
        for got, rough in zip(rows, (0.1, 0.4, 0.8, 0.9)):
            self.assertAlmostEqual(float(got[0]), 0.5, delta=0.02)
            self.assertAlmostEqual(float(got[3]), 1 - rough, delta=0.02)

    def test_metallic_and_roughness_can_be_two_channels_of_one_image(self):
        """The way many texture sets pack them: roughness in green, metallic in blue."""
        rough, metal = (0.2, 0.4, 0.6, 0.8), (1.0, 0.0, 0.5, 0.25)
        material, shader = self.material("Packed")
        tree = material.node_tree
        split = tree.nodes.new("ShaderNodeSeparateColor")
        self.feed(material, split.inputs["Color"], self.picture("orm", pixels=[(0.9, r, m, 1.0) for r, m in zip(rough, metal)], data=True))
        tree.links.new(split.outputs["Green"], shader.inputs["Roughness"])
        tree.links.new(split.outputs["Blue"], shader.inputs["Metallic"])
        (found,), notes, _ = self.look()
        self.assertEqual((found["metallic_smoothness_texture"]["file"], notes), ("Packed_MetallicSmoothness.png", []))
        rows = self.read(self.textures / "Packed_MetallicSmoothness.png")[1].reshape(-1, 4)
        for got, r, m in zip(rows, rough, metal):
            self.assertAlmostEqual(float(got[0]), m, delta=0.02)
            self.assertAlmostEqual(float(got[3]), 1 - r, delta=0.02)

    def test_metallic_and_roughness_images_of_other_sizes_make_a_file_of_the_larger(self):
        material, shader = self.material("Mixed")
        self.feed(material, shader.inputs["Metallic"], self.picture("metal", pixels=[(v, v, v, 1.0) for v in (0.0, 0.25, 0.5, 1.0)], data=True))
        self.feed(material, shader.inputs["Roughness"], self.picture("rough", size=(4, 4), pixels=[(0.5, 0.5, 0.5, 1.0)] * 16, data=True))
        self.look()
        size, rows = self.read(self.textures / "Mixed_MetallicSmoothness.png")
        self.assertEqual(size, (4, 4))
        for y in range(4):
            for x in range(4):  # each pixel of the small image covers a block of the large one
                self.assertAlmostEqual(float(rows[y, x, 0]), (0.0, 0.25, 0.5, 1.0)[y // 2 * 2 + x // 2], delta=0.02)
                self.assertAlmostEqual(float(rows[y, x, 3]), 0.5, delta=0.02)

    def test_a_normal_map_goes_with_its_strength_and_a_bump_does_not(self):
        material, shader = self.material("Bumpy")
        tree = material.node_tree
        normal = tree.nodes.new("ShaderNodeNormalMap")
        normal.inputs["Strength"].default_value = 0.8
        self.feed(material, normal.inputs["Color"], self.picture("relief", data=True))
        tree.links.new(normal.outputs["Normal"], shader.inputs["Normal"])
        (found,), notes, _ = self.look()
        self.assertEqual((found["normal_texture"]["file"], round(found["normal_strength"], 4), notes), ("relief.png", 0.8, []))
        tree.nodes.remove(normal)
        bump = tree.nodes.new("ShaderNodeBump")
        tree.links.new(bump.outputs["Normal"], shader.inputs["Normal"])
        (found,), notes, _ = self.look()
        self.assertEqual([key for key in found if key.startswith("normal")], [])
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Bumpy: 노멀") and bump.bl_label in notes[0], notes)

    def test_emission_goes_only_where_something_glows(self):
        material, shader = self.material("Lamp")
        shader.inputs["Emission Color"].default_value = (1.0, 0.5, 0.0, 1.0)
        shader.inputs["Emission Strength"].default_value = 0.0
        (lamp,), _, _ = self.look()
        self.assertEqual([key for key in lamp if key.startswith("emission")], [])
        shader.inputs["Emission Strength"].default_value = 2.0
        (lamp,), notes, _ = self.look()
        self.assertEqual((lamp["emission_strength"], [round(c, 3) for c in lamp["emission_color"]], notes), (2.0, [1.0, 0.5, 0.0], []))
        self.assertNotIn("emission_texture", lamp)
        shader.inputs["Emission Color"].default_value = (0.0, 0.0, 0.0, 1.0)  # strong, and black
        (lamp,), _, _ = self.look()
        self.assertEqual([key for key in lamp if key.startswith("emission")], [])
        self.feed(material, shader.inputs["Emission Color"], self.picture("glow"))
        (lamp,), notes, _ = self.look()
        self.assertEqual((lamp["emission_texture"]["file"], lamp["emission_color"], lamp["emission_strength"], notes), ("glow.png", [1.0, 1.0, 1.0], 2.0, []))

    def test_alpha_goes_as_a_number_or_from_the_base_colour_image(self):
        def see_through(material):
            if hasattr(material, "surface_render_method"):
                material.surface_render_method = "BLENDED"
            else:
                material.blend_method = "BLEND"

        material, shader = self.material("Glass")
        (glass,), _, _ = self.look()
        self.assertEqual([key for key in glass if key.startswith("alpha") or key == "blend"], [])  # opaque: nothing said
        shader.inputs["Alpha"].default_value = 0.4
        (glass,), notes, _ = self.look()
        self.assertEqual((round(glass["alpha"], 4), glass["blend"], notes), (0.4, "blend", []))
        see_through(material)
        self.assertEqual(self.look()[0][0]["blend"], "blend")
        image = self.picture("leaf")
        shader.inputs["Alpha"].default_value = 1.0
        base = self.feed(material, shader.inputs["Base Color"], image)
        material.node_tree.links.new(base.outputs["Alpha"], shader.inputs["Alpha"])
        (glass,), notes, _ = self.look()
        self.assertEqual((glass["alpha_from_base_color_texture"], glass["blend"], "alpha" in glass, notes), (True, "blend", False, []))
        if hasattr(material, "surface_render_method"):
            material.surface_render_method, material.alpha_threshold = "DITHERED", 0.3  # a cut-out: hashed in Blender
            (glass,), _, _ = self.look()
            self.assertEqual((glass["blend"], round(glass["alpha_clip"], 4)), ("clip", 0.3))

    def test_what_is_fed_through_other_nodes_is_named_in_the_notes(self):
        """Not carried is no secret: each input that comes out of a node other than an image says so, by the node's name."""
        via = (("Base Color", "기본 색", "ShaderNodeValToRGB", "Color"), ("Metallic", "금속", "ShaderNodeTexNoise", "Fac"),
               ("Roughness", "거칠기", "ShaderNodeTexNoise", "Fac"), ("Emission Color", "발광", "ShaderNodeTexNoise", "Color"),
               ("Alpha", "투명도", "ShaderNodeTexNoise", "Fac"), ("Normal", "노멀", "ShaderNodeTexNoise", "Color"),
               ("Emission Strength", "발광 세기", "ShaderNodeTexNoise", "Fac"))
        labels = {}
        for inlet, _, kind, outlet in via:
            material, shader = self.material("Via " + inlet)
            if inlet == "Emission Color":
                shader.inputs["Emission Strength"].default_value = 1.0  # a colour that comes out of nodes matters only where something glows
            node = material.node_tree.nodes.new(kind)
            material.node_tree.links.new(node.outputs[outlet], shader.inputs[inlet])
            labels[inlet] = node.bl_label
        found, notes, _ = self.look()
        for (inlet, what, _, _), material in zip(via, found):
            with self.subTest(inlet):
                told = [note for note in notes if note.startswith(f"Via {inlet}: ")]
                self.assertEqual(len(told), 1, notes)
                self.assertTrue(what in told[0] and f"'{labels[inlet]}'" in told[0], told)
                self.assertEqual([key for key in material if key.endswith("_texture") or key.startswith(("alpha", "normal", "emission"))], [])
        self.assertEqual((len(notes), list(self.textures.iterdir())), (len(via), []))
        material, shader = self.material("Dark")
        material.node_tree.links.new(material.node_tree.nodes.new("ShaderNodeTexNoise").outputs["Color"], shader.inputs["Emission Color"])
        self.assertEqual(len(self.look()[1]), len(via))  # nothing glows, so what its colour comes from is of no interest

    def test_a_material_that_is_not_a_principled_bsdf_goes_as_its_viewport_values(self):
        material, shader = self.material("Neon")
        tree = material.node_tree
        tree.nodes.remove(shader)
        glow = tree.nodes.new("ShaderNodeEmission")
        tree.links.new(glow.outputs["Emission"], next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL").inputs["Surface"])
        material.diffuse_color, material.metallic, material.roughness = (0.1, 0.2, 0.3, 1.0), 0.4, 0.9
        (neon,), notes, _ = self.look()
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Neon: ") and "Principled BSDF" in notes[0], notes)
        self.assertEqual(([round(c, 4) for c in neon["base_color"]], round(neon["metallic"], 4), round(neon["roughness"], 4)), ([0.1, 0.2, 0.3, 1.0], 0.4, 0.9))
        self.assertEqual([key for key in neon if key.endswith("_texture") or key.startswith(("alpha", "normal", "emission"))], [])

    def test_a_mapping_node_without_rotation_gives_the_images_scale_and_offset(self):
        material, shader = self.material("Floor")
        tree = material.node_tree
        tiles = self.feed(material, shader.inputs["Base Color"], self.picture("tiles"))
        coords, mapping = tree.nodes.new("ShaderNodeTexCoord"), tree.nodes.new("ShaderNodeMapping")
        tree.links.new(coords.outputs["UV"], tiles.inputs["Vector"])
        (floor,), notes, _ = self.look()
        self.assertEqual((floor["base_color_texture"]["file"], "uv" in floor["base_color_texture"], notes), ("tiles.png", False, []))  # the UVs as they are
        mapping.inputs["Scale"].default_value = (2.0, 3.0, 1.0)
        mapping.inputs["Location"].default_value = (0.5, 0.25, 0.0)
        tree.links.new(coords.outputs["UV"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], tiles.inputs["Vector"])
        (floor,), notes, _ = self.look()
        self.assertEqual((floor["base_color_texture"]["uv"], notes), ({"scale": [2.0, 3.0], "offset": [0.5, 0.25]}, []))
        tree.nodes.remove(coords)  # the node alone, with nothing in front of it, is the UVs too
        self.assertEqual(self.look()[0][0]["base_color_texture"]["uv"], {"scale": [2.0, 3.0], "offset": [0.5, 0.25]})
        mapping.inputs["Rotation"].default_value = (0.0, 0.0, 0.5)
        (floor,), notes, _ = self.look()
        self.assertEqual((floor["base_color_texture"]["file"], "uv" in floor["base_color_texture"]), ("tiles.png", False))  # the picture goes, laid as it is
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Floor: 기본 색") and mapping.bl_label in notes[0], notes)

    def test_a_texture_coordinate_that_is_not_the_uv_map_is_told(self):
        """An image laid on Object or Generated coordinates does not follow the UV map: more than a manifest can say, so the notes say it."""
        material, shader = self.material("Rock")
        tree = material.node_tree
        tiles = self.feed(material, shader.inputs["Base Color"], self.picture("tiles"))
        tree.links.new(tree.nodes.new("ShaderNodeTexCoord").outputs["Object"], tiles.inputs["Vector"])
        (found,), notes, _ = self.look()
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].startswith("Rock: 기본 색"), notes)
        self.assertNotIn("uv", found["base_color_texture"])

    def test_one_image_used_twice_is_one_file_and_images_of_one_name_are_told_apart(self):
        shared = self.picture("shared")
        for name in ("A", "B"):
            material, shader = self.material(name)
            self.feed(material, shader.inputs["Base Color"], shared)
        (one, two), _, _ = self.look()
        self.assertEqual((one["base_color_texture"]["file"], two["base_color_texture"]["file"], [p.name for p in self.textures.iterdir()]),
                         ("shared.png", "shared.png", ["shared.png"]))
        for name in ("a b", "a_b", "A_B"):  # one name for a file, and Windows tells upper from lower case no better than Unity
            material, shader = self.material("Made " + name)
            self.feed(material, shader.inputs["Base Color"], bpy.data.images.new(name, 2, 2))
        found, _, _ = self.look()
        self.assertEqual([m["base_color_texture"]["file"] for m in found[2:]], ["a_b.png", "a_b_2.png", "A_B_3.png"])
        self.assertEqual(sorted(p.name for p in self.textures.iterdir()), ["A_B_3.png", "a_b.png", "a_b_2.png", "shared.png"])
        taken = self.picture("tex")  # a file's picture and one made here that go by the same name
        material, shader = self.material("File")
        self.feed(material, shader.inputs["Base Color"], taken)
        material, shader = self.material("Made")
        self.feed(material, shader.inputs["Base Color"], bpy.data.images.new("tex", 2, 2))
        found, _, _ = self.look()
        self.assertEqual([m["base_color_texture"]["file"] for m in found[-2:]], ["tex.png", "tex_2.png"])
        self.assertNotEqual((self.textures / "tex.png").read_bytes(), (self.textures / "tex_2.png").read_bytes())

    def test_the_hash_changes_with_what_is_told_and_with_nothing_else(self):
        image = self.picture("worn")
        material, shader = self.material("Worn")
        self.feed(material, shader.inputs["Base Color"], image)
        first = self.addon._materials([self.prop])[2]  # the images only named: what a follow's look asks
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual((self.addon._materials([self.prop])[2], self.look()[2]), (first, first))  # nor does writing the images change it
        bpy.data.materials.new("Unused")  # a material that no object has
        self.assertEqual(self.addon._materials([self.prop])[2], first)
        shader.inputs["Roughness"].default_value = 0.1
        self.assertNotEqual(self.addon._materials([self.prop])[2], first)
        shader.inputs["Roughness"].default_value = 0.5  # as it was
        self.assertEqual(self.addon._materials([self.prop])[2], first)
        path = self.pictures / "worn.png"
        taken = path.stat().st_mtime_ns
        os.utime(path, ns=(taken, taken + 2_000_000_000))  # rewritten later, as far as the file can tell
        self.assertNotEqual(self.addon._materials([self.prop])[2], first)
        os.utime(path, ns=(taken, taken))
        self.assertEqual(self.addon._materials([self.prop])[2], first)
        path.write_bytes(path.read_bytes() + b"\0")  # another size
        self.assertNotEqual(self.addon._materials([self.prop])[2], first)
        path.write_bytes(path.read_bytes()[:-1])
        os.utime(path, ns=(taken, taken))
        self.assertEqual(self.addon._materials([self.prop])[2], first)
        self.material("Added")
        self.assertNotEqual(self.addon._materials([self.prop])[2], first)

    def test_the_manifest_says_how_the_model_looks(self):
        material, shader = self.material("Skin")
        self.feed(material, shader.inputs["Base Color"], self.picture("skin"))
        sent = self.addon.export_fbx(["Prop"], "prop")
        manifest = json.loads(Path(sent["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["axes"], "blender")  # the file's axes are Blender's own: Unity bakes the conversion in
        self.assertEqual([(m["name"], m["base_color_texture"]["file"]) for m in manifest["materials"]], [("Skin", "skin.png")])
        self.assertEqual(manifest["materials_hash"], self.addon._materials([self.prop])[2])
        self.assertEqual(manifest["textures_dir"], manifest["transfer_id"] + ".textures")
        folder = self.exchange / manifest["textures_dir"]  # next to the manifest and the model file
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["skin.png"])
        self.assertEqual((folder / "skin.png").read_bytes(), (self.pictures / "skin.png").read_bytes())
        self.assertEqual((manifest["not_carried"], sent["not_carried"]), ([], []))
        self.assertEqual(("rig" in manifest, "clips" in manifest), (False, False))  # a person's rig and clips only when there are any
        person = json.loads(Path(self.addon.export_fbx(["Prop"], "person", humanoid=True)["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(person["rig"], "humanoid")

    def test_what_could_not_go_is_told_to_the_one_who_sent_it(self):
        image = self.picture("lost")
        image.filepath = str(self.pictures / "nowhere.png")
        image.reload()
        material, shader = self.material("Wall")
        self.feed(material, shader.inputs["Base Color"], image)
        sent = self.addon.export_fbx(["Prop"], "prop")
        manifest = json.loads(Path(sent["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(len(sent["not_carried"]), 1)
        self.assertTrue(sent["not_carried"][0].startswith("Wall: 기본 색"), sent["not_carried"])
        self.assertEqual(manifest["not_carried"], sent["not_carried"])
        self.assertEqual([m["name"] for m in manifest["materials"]], ["Wall"])

    def test_a_model_without_materials_says_so(self):
        sent = self.addon.export_fbx(["Prop"], "bare")
        manifest = json.loads(Path(sent["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual((manifest["materials"], manifest["not_carried"], sent["not_carried"], manifest["axes"]), ([], [], [], "blender"))
        self.assertRegex(manifest["materials_hash"], r"^[0-9a-f]{64}$")


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class TakeTests(unittest.TestCase):
    """How a model's animation is cut into clips for Unity (_takes), and how what Geometry Nodes makes of instances is made real for the FBX (_realized)."""

    SCENE_TAKE = dict(bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False)  # the exporter's options for the scene's frames as one clip

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exchange = Path(self.tmp.name) / "exchange"
        os.environ["CLAUDE_HAND_EXCHANGE_DIR"] = str(self.exchange)
        spec = importlib.util.spec_from_file_location("claude_hand_chat", ADDON)
        self.addon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.addon)
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def tearDown(self):
        os.environ.pop("CLAUDE_HAND_EXCHANGE_DIR", None)
        self.tmp.cleanup()

    def thing(self, name):
        """A mesh object with a triangle, in the scene."""
        mesh = bpy.data.meshes.new(name + "Mesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        return obj

    def rig(self, name, bone="Bone", actions=("Walk",)):
        """An armature of one bone with an action for each name (the first one is what it plays), each moving that bone."""
        arm = bpy.data.armatures.new(name + "Data")
        rig = bpy.data.objects.new(name, arm)
        bpy.context.scene.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        edit = arm.edit_bones.new(bone)
        edit.head, edit.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode="OBJECT")
        for n, action in enumerate(actions):
            if rig.animation_data is not None:
                rig.animation_data.action = None
            rig.pose.bones[bone].location = (0, 0, n + 1)
            rig.pose.bones[bone].keyframe_insert("location", frame=1)
            rig.animation_data.action.name = action
        rig.animation_data.action = bpy.data.actions[actions[0]]
        bpy.context.view_layer.update()
        return rig

    def moving(self, name):
        """A mesh object that moves by an action of its own."""
        obj = self.thing(name)
        obj.keyframe_insert("location", frame=1)
        obj.animation_data.action.name = name + "Move"
        return obj

    def test_a_rig_alone_gives_a_clip_for_each_of_its_own_actions(self):
        rig = self.rig("Hero", actions=("Walk", "Run"))
        self.rig("Pet", bone="Tail", actions=("Wag",))  # someone else's action, with bones this rig has not
        still = self.moving("Crate")
        still.animation_data.action = None  # an object's action that nothing plays any more
        skin = self.thing("Skin")  # the rig's mesh: it does not move on its own
        self.assertEqual(self.addon._takes([rig]), ({}, [{"take": "Hero|Run", "name": "Run"}, {"take": "Hero|Walk", "name": "Walk"}]))
        self.assertEqual(self.addon._takes([rig, skin])[1], self.addon._takes([rig])[1])

    def test_several_things_that_move_are_the_scenes_frames_as_one_clip(self):
        rig, crate = self.rig("Hero"), self.moving("Crate")
        self.assertEqual(self.addon._takes([rig, crate]), (self.SCENE_TAKE, None))  # a rig and something else
        self.assertEqual(self.addon._takes([crate, self.moving("Door")]), (self.SCENE_TAKE, None))
        self.assertEqual(self.addon._takes([crate]), (self.SCENE_TAKE, None))  # one thing that is not a rig
        self.assertEqual(self.addon._takes([rig, self.rig("Pet", bone="Tail", actions=("Wag",))]), (self.SCENE_TAKE, None))

    def test_a_rigid_body_or_a_shape_key_that_moves_is_the_scenes_frames_too(self):
        ball = self.thing("Ball")
        bpy.context.view_layer.objects.active = ball
        bpy.ops.rigidbody.object_add()
        self.assertEqual((ball.rigid_body.type, self.addon._takes([ball])), ("ACTIVE", (self.SCENE_TAKE, None)))
        ball.rigid_body.type = "PASSIVE"  # a floor: it stays
        self.assertEqual(self.addon._takes([ball]), ({}, None))
        face = self.thing("Face")
        face.shape_key_add(name="Basis")
        smile = face.shape_key_add(name="Smile")
        smile.value = 1.0  # set, not keyframed: it does not move
        self.assertEqual(self.addon._takes([face]), ({}, None))
        smile.keyframe_insert("value", frame=1)
        self.assertEqual(self.addon._takes([face]), (self.SCENE_TAKE, None))

    def test_what_does_not_move_has_no_clip(self):
        plain, rig = self.thing("Plain"), self.rig("Hero")
        rig.animation_data.action = None  # a rig with no action to play
        self.assertEqual(self.addon._takes([plain, rig]), ({}, None))
        self.assertEqual(self.addon._takes([]), ({}, None))

    def test_the_clips_of_a_rig_are_in_its_manifest(self):
        rig = self.rig("Hero", actions=("Walk", "Run"))
        manifest = json.loads(Path(self.addon.export_fbx(["Hero"], "hero")["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["clips"], [{"take": "Hero|Run", "name": "Run"}, {"take": "Hero|Walk", "name": "Walk"}])
        self.assertTrue((self.exchange / manifest["model_file"]).stat().st_size > 0)
        self.moving("Crate")
        scene = json.loads(Path(self.addon.export_fbx(["Hero", "Crate"], "scene")["manifest"]).read_text(encoding="utf-8"))
        self.assertNotIn("clips", scene)  # all that comes, as one take: Unity keeps what it gets
        self.assertEqual(rig.animation_data.action.name, "Walk")

    def instancer(self, name, instances=True):
        """A mesh object whose Geometry Nodes make a row of four cubes: as instances, or as real geometry."""
        group = bpy.data.node_groups.new(name + "Nodes", "GeometryNodeTree")
        group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        line, cube, spread, out = (group.nodes.new(kind) for kind in ("GeometryNodeMeshLine", "GeometryNodeMeshCube", "GeometryNodeInstanceOnPoints", "NodeGroupOutput"))
        line.inputs["Count"].default_value = 4
        if instances:
            group.links.new(line.outputs["Mesh"], spread.inputs["Points"])
            group.links.new(cube.outputs["Mesh"], spread.inputs["Instance"])
            group.links.new(spread.outputs["Instances"], out.inputs["Geometry"])
        else:
            group.links.new(line.outputs["Mesh"], out.inputs["Geometry"])
        obj = self.thing(name)
        obj.modifiers.new("GeometryNodes", "NODES").node_group = group
        bpy.context.view_layer.update()
        return obj

    def vertices(self, obj):
        """How many vertices the object has as Blender works it out now."""
        there = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = there.to_mesh()
        try:
            return len(mesh.vertices)
        finally:
            there.to_mesh_clear()

    def test_instances_are_real_geometry_only_while_the_model_is_written(self):
        row = self.instancer("Row")
        groups = sorted(g.name for g in bpy.data.node_groups)
        self.assertEqual(self.vertices(row), 0)  # instances: no mesh as far as an FBX can tell
        with self.addon._realized([row]):
            self.assertEqual(self.vertices(row), 32)  # four cubes
        self.assertEqual(([m.name for m in row.modifiers], sorted(g.name for g in bpy.data.node_groups)), (["GeometryNodes"], groups))
        self.assertNotIn(self.addon.REALIZE, bpy.data.node_groups)
        self.assertEqual(self.vertices(row), 0)
        with self.assertRaises(RuntimeError):
            with self.addon._realized([row]):
                raise RuntimeError("the export fails")
        self.assertEqual(([m.name for m in row.modifiers], sorted(g.name for g in bpy.data.node_groups)), (["GeometryNodes"], groups))

    def test_geometry_nodes_that_make_a_real_mesh_are_left_alone(self):
        line = self.instancer("Line", instances=False)
        groups = sorted(g.name for g in bpy.data.node_groups)
        with self.addon._realized([line]):
            self.assertEqual(([m.name for m in line.modifiers], sorted(g.name for g in bpy.data.node_groups)), (["GeometryNodes"], groups))
            self.assertEqual(self.vertices(line), 4)  # the line's own points

    def test_an_fbx_of_instances_is_not_an_empty_mesh(self):
        import contextlib
        row = self.instancer("Row")
        self.exchange.mkdir()
        self.addon._write_fbx([row], str(self.exchange / "row.fbx"))
        self.addon._realized, real = lambda objects: contextlib.nullcontext(), self.addon._realized  # as it was before: the instances stay behind
        self.addon._write_fbx([row], str(self.exchange / "bare.fbx"))
        self.addon._realized = real
        self.assertGreater((self.exchange / "row.fbx").stat().st_size, (self.exchange / "bare.fbx").stat().st_size)
        bpy.data.objects.remove(row)
        got = self.addon.import_model(str(self.exchange / "row.fbx"))["added"]
        self.assertEqual(got, ["Row"])
        self.assertGreaterEqual(len(bpy.data.objects["Row"].data.vertices), 32)

    def test_sending_instances_leaves_the_selection_and_the_modifiers_as_they_were(self):
        row, bystander = self.instancer("Row"), self.thing("Bystander")
        bystander.select_set(True)
        bpy.context.view_layer.objects.active = bystander
        groups = sorted(g.name for g in bpy.data.node_groups)
        sent = self.addon.export_fbx(["Row"], "row")
        self.assertEqual(sent["objects"], ["Row"])
        self.assertEqual(({o.name for o in bpy.context.view_layer.objects if o.select_get()}, bpy.context.view_layer.objects.active.name), ({"Bystander"}, "Bystander"))
        self.assertEqual(([m.name for m in row.modifiers], sorted(g.name for g in bpy.data.node_groups)), (["GeometryNodes"], groups))
        self.assertGreater((self.exchange / json.loads(Path(sent["manifest"]).read_text(encoding="utf-8"))["model_file"]).stat().st_size, 1000)

    def test_instances_are_measured_while_they_are_real(self):
        row = self.instancer("Row")  # four cubes a unit across, a unit apart along z
        self.exchange.mkdir()
        part = self.addon._write_fbx([row], str(self.exchange / "row.fbx"))["parts"]["Row"]
        for got, want in ((part["low"], [-0.5, -0.5, -0.5]), (part["high"], [0.5, 3.5, 0.5])):  # Blender's z up is Unity's y
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, delta=1e-4)
        self.assertEqual(list(self.addon._shape([row])["parts"]["Row"]), ["at"])  # as it stands, the object has no mesh to measure


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class BakeTests(LookCase):
    """What is fed through other nodes goes as an image baked from them (with Cycles, in Object Mode), here at a size that is quick."""

    SIZE = 32

    def setUp(self):
        super().setUp()
        bpy.data.objects.remove(self.prop)
        bpy.ops.mesh.primitive_cube_add()  # a mesh that has a UV map
        self.prop = bpy.context.active_object
        self.prop.name = "Prop"
        bpy.context.view_layer.update()

    def bake(self):
        """What the manifest would say of the prop's materials with the baking on, the images written to the textures folder."""
        return self.addon._materials([self.prop], str(self.textures), True, self.SIZE)

    def through(self, material, inlet, kind, outlet=0):
        """A node of this kind in front of an input of the material's Principled BSDF: the node."""
        node = material.node_tree.nodes.new(kind)
        shader = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        material.node_tree.links.new(node.outputs[outlet], shader.inputs[inlet])
        return node

    def state(self):
        """Everything a bake is to put back: the nodes and links of every material, which node is active and which are selected,
        the render engine, the images, and what is selected and active among the objects."""
        layer = bpy.context.view_layer
        return {"trees": {m.name: (sorted(n.name for n in m.node_tree.nodes),
                                   sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in m.node_tree.links),
                                   m.node_tree.nodes.active.name if m.node_tree.nodes.active else None,
                                   sorted(n.name for n in m.node_tree.nodes if n.select)) for m in bpy.data.materials},
                "engine": bpy.context.scene.render.engine, "samples": bpy.context.scene.cycles.samples, "images": sorted(i.name for i in bpy.data.images),
                "objects": (sorted(o.name for o in layer.objects if o.select_get()), layer.objects.active.name if layer.objects.active else None)}

    def test_the_stamp_of_a_tree_follows_what_the_nodes_do_and_not_where_they_sit(self):
        material, shader = self.material("Stamped")
        tree = material.node_tree
        ramp = tree.nodes.new("ShaderNodeValToRGB")
        first = self.addon._tree_stamp(tree)
        self.assertEqual(self.addon._tree_stamp(tree), first)
        ramp.location, ramp.label, ramp.width = (500.0, 200.0), "Sky", 300.0  # moved, called something, made wider: it does the same
        self.assertEqual(self.addon._tree_stamp(tree), first)
        shader.inputs["Roughness"].default_value = 0.1
        self.assertNotEqual(self.addon._tree_stamp(tree), first)
        shader.inputs["Roughness"].default_value = 0.5  # as it was
        self.assertEqual(self.addon._tree_stamp(tree), first)
        link = tree.links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
        linked = self.addon._tree_stamp(tree)
        self.assertNotEqual(linked, first)
        ramp.color_ramp.elements[0].color = (1.0, 0.0, 0.0, 1.0)
        reddened = self.addon._tree_stamp(tree)
        self.assertNotIn(reddened, (first, linked))
        ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        self.assertEqual(self.addon._tree_stamp(tree), linked)
        tree.links.remove(link)
        self.assertEqual(self.addon._tree_stamp(tree), first)
        shader.inputs["Base Color"].default_value = (0.1, 0.2, 0.3, 1.0)  # a value on an input that nothing feeds
        self.assertNotEqual(self.addon._tree_stamp(tree), first)

    def test_a_colour_ramp_or_a_noise_in_front_of_the_base_colour_is_baked(self):
        material, shader = self.material("Fed")
        ramp = self.through(material, "Base Color", "ShaderNodeValToRGB", "Color")
        for stop in ramp.color_ramp.elements:
            stop.color = (1.0, 0.0, 0.0, 1.0)  # red all along, so what comes out is known
        (fed,), notes, _ = self.bake()
        self.assertEqual((notes, fed["base_color"], fed["base_color_texture"]["baked"], fed["base_color_texture"]["file"]), ([], [1.0, 1.0, 1.0, 1.0], True, "Fed_BaseColor.png"))
        size, rows = self.read(self.textures / "Fed_BaseColor.png")
        self.assertEqual(size, (self.SIZE, self.SIZE))
        self.assertGreater(float(((rows[:, :, 0] > 0.9) & (rows[:, :, 1] < 0.1) & (rows[:, :, 2] < 0.1)).mean()), 0.5)
        material.node_tree.nodes.remove(ramp)
        self.through(material, "Base Color", "ShaderNodeTexNoise", "Color")
        (fed,), notes, _ = self.bake()
        self.assertEqual((notes, fed["base_color_texture"]["baked"]), ([], True))
        self.assertGreater(float(self.read(self.textures / "Fed_BaseColor.png")[1][:, :, :3].std()), 0.02)  # a pattern, not a flat colour

    def test_roughness_and_metallic_from_nodes_are_baked_into_the_packed_file(self):
        material, shader = self.material("Fed")
        tree = material.node_tree
        value, half = self.through(material, "Roughness", "ShaderNodeMath"), tree.nodes.new("ShaderNodeValue")
        half.outputs[0].default_value = 0.3
        value.operation = "MULTIPLY"
        value.inputs[1].default_value = 2.0
        tree.links.new(half.outputs[0], value.inputs[0])  # 0.3 times 2: roughness 0.6
        shader.inputs["Metallic"].default_value = 0.25
        (fed,), notes, _ = self.bake()
        packed = fed["metallic_smoothness_texture"]
        self.assertEqual((notes, packed["baked"], packed["file"], fed["roughness"]), ([], True, "Fed_MetallicSmoothness.png", 1.0))
        rows = self.read(self.textures / packed["file"])[1]
        self.assertEqual(rows.shape[:2], (self.SIZE, self.SIZE))
        self.assertAlmostEqual(float(rows[:, :, 3].mean()), 0.4, delta=0.03)  # smoothness: one minus the roughness
        self.assertAlmostEqual(float(rows[:, :, 0].mean()), 0.25, delta=0.03)  # the metallic number
        self.assertLess(float(rows[:, :, 3].std()), 0.03)
        tree.nodes.remove(value)
        self.through(material, "Roughness", "ShaderNodeTexNoise", "Fac")  # a pattern, straight out of a noise
        self.through(material, "Metallic", "ShaderNodeTexNoise", "Fac")
        (fed,), notes, _ = self.bake()
        self.assertEqual((notes, fed["metallic_smoothness_texture"]["baked"], (fed["metallic"], fed["roughness"])), ([], True, (1.0, 1.0)))
        rows = self.read(self.textures / "Fed_MetallicSmoothness.png")[1]
        self.assertGreater(float(rows[:, :, 3].std()), 0.01)
        self.assertGreater(float(rows[:, :, 0].std()), 0.01)

    def test_a_bump_is_baked_into_a_normal_map_and_a_glow_that_comes_from_nodes_into_an_image(self):
        material, shader = self.material("Fed")
        self.through(material, "Normal", "ShaderNodeBump", "Normal")
        shader.inputs["Emission Strength"].default_value = 2.0
        ramp = self.through(material, "Emission Color", "ShaderNodeValToRGB", "Color")
        for stop in ramp.color_ramp.elements:
            stop.color = (0.0, 1.0, 0.0, 1.0)
        (fed,), notes, _ = self.bake()
        self.assertEqual((notes, fed["normal_texture"]["baked"], fed["normal_texture"]["file"], fed["normal_strength"]), ([], True, "Fed_Normal.png", 1.0))
        rows = self.read(self.textures / "Fed_Normal.png")[1]
        self.assertAlmostEqual(float(rows[:, :, 2].mean()), 1.0, delta=0.1)  # a flat bump: the normal stands straight up
        self.assertAlmostEqual(float(rows[:, :, 0].mean()), 0.5, delta=0.1)
        self.assertEqual((fed["emission_texture"]["baked"], fed["emission_texture"]["file"], fed["emission_strength"], fed["emission_color"]),
                         (True, "Fed_Emission.png", 2.0, [1.0, 1.0, 1.0]))
        rows = self.read(self.textures / "Fed_Emission.png")[1]
        self.assertGreater(float(((rows[:, :, 1] > 0.9) & (rows[:, :, 0] < 0.1)).mean()), 0.5)
        self.assertEqual(sorted(p.name for p in self.textures.iterdir()), ["Fed_Emission.png", "Fed_Normal.png"])

    def fed_through_everything(self, material):
        """Every kind of input that is baked, fed through nodes."""
        self.through(material, "Base Color", "ShaderNodeValToRGB", "Color")
        self.through(material, "Roughness", "ShaderNodeTexNoise", "Fac")
        self.through(material, "Normal", "ShaderNodeBump", "Normal")
        next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Emission Strength"].default_value = 1.0
        self.through(material, "Emission Color", "ShaderNodeTexNoise", "Color")

    def test_a_bake_puts_everything_back(self):
        material, shader = self.material("Fed")
        other, other_shader = self.material("Other")  # a second material on the same object, with nodes of its own
        self.prop.data.polygons[0].material_index = 1
        self.fed_through_everything(material)
        self.through(other, "Base Color", "ShaderNodeTexNoise", "Color")
        for place, node in ((material, shader), (other, other_shader)):
            for each in place.node_tree.nodes:
                each.select = each == node
            place.node_tree.nodes.active = node
        side = bpy.data.objects.new("Side", bpy.data.meshes.new("SideMesh"))
        bpy.context.scene.collection.objects.link(side)
        bpy.context.view_layer.update()
        self.prop.select_set(False)
        side.select_set(True)
        bpy.context.view_layer.objects.active = side
        bpy.context.scene.render.engine = "BLENDER_WORKBENCH"  # not the one the bake needs
        before = self.state()
        found, notes, _ = self.bake()
        self.assertEqual(notes, [])
        self.assertEqual(sorted(p.name for p in self.textures.iterdir()),
                         ["Fed_BaseColor.png", "Fed_Emission.png", "Fed_MetallicSmoothness.png", "Fed_Normal.png", "Other_BaseColor.png"])
        self.assertEqual(self.state(), before)
        self.assertEqual(before["engine"], "BLENDER_WORKBENCH")
        self.assertEqual([i.name for i in bpy.data.images if i.name.startswith(("claude_hand_bake", "claude_hand_texture"))], [])
        self.assertEqual(sum(1 for entry in found if entry["name"] == "Other"), 1)

    def test_baking_again_and_again_comes_through(self):
        """A node taken away and then written to once ended Blender some of the time: this is the guard, it is only to survive."""
        material, _ = self.material("Fed")
        other, _ = self.material("Other")
        self.prop.data.polygons[0].material_index = 1
        self.fed_through_everything(material)
        self.through(other, "Roughness", "ShaderNodeTexNoise", "Fac")
        hashes = {self.addon._materials([self.prop], str(self.textures), True, 16)[2] for _ in range(8)}
        self.assertEqual(len(hashes), 1)  # the same nodes: the same hash every time

    def test_what_cannot_be_baked_is_told_and_not_dropped(self):
        material, shader = self.material("Fed")
        noise = self.through(material, "Base Color", "ShaderNodeTexNoise", "Color")
        (fed,), notes, _ = self.addon._materials([self.prop], str(self.textures))  # baking is off unless asked for
        self.assertNotIn("base_color_texture", fed)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Fed: 기본 색") and f"'{noise.bl_label}'" in notes[0] and "굽기가 꺼져 있어서" in notes[0], notes)
        self.prop.data.uv_layers.remove(self.prop.data.uv_layers[0])
        (fed,), notes, _ = self.bake()
        self.assertNotIn("base_color_texture", fed)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Fed: 기본 색") and f"'{noise.bl_label}'" in notes[0] and "Prop에 UV 맵이 없어서" in notes[0], notes)
        self.assertEqual(list(self.textures.iterdir()), [])
        wire = bpy.data.objects.new("Wire", bpy.data.curves.new("WireCurve", "CURVE"))  # a material that no mesh has
        wire.data.materials.append(material)
        bpy.context.scene.collection.objects.link(wire)
        (fed,), notes, _ = self.addon._materials([wire], str(self.textures), True, self.SIZE)
        self.assertEqual((len(notes), "base_color_texture" in fed), (1, False))
        self.assertIn("이 재질을 쓰는 메시가 없어서", notes[0])

    def test_in_another_mode_a_baked_image_is_named_and_baked_later(self):
        material, _ = self.material("Fed")
        self.through(material, "Base Color", "ShaderNodeTexNoise", "Color")
        self.through(material, "Roughness", "ShaderNodeTexNoise", "Fac")
        bpy.context.view_layer.objects.active = self.prop
        bpy.ops.object.mode_set(mode="EDIT")
        self.addCleanup(lambda: bpy.context.mode != "OBJECT" and bpy.ops.object.mode_set(mode="OBJECT"))
        (fed,), notes, in_edit = self.bake()
        self.assertEqual(bpy.context.mode, "EDIT_MESH")  # the user is left editing
        self.assertEqual((fed["base_color_texture"]["baked"], fed["base_color_texture"]["stamp"][-1], fed["metallic_smoothness_texture"]["stamp"][-1]),
                         (True, "pending", "pending"))
        self.assertEqual(notes, [f"Fed: 기본 색: {self.addon.LATER}", f"Fed: 금속 · 거칠기: {self.addon.LATER}"])
        self.assertEqual(list(self.textures.iterdir()), [])  # named, not written
        waiting = self.addon._beside([self.prop], "waiting", True)  # what the manifest and the panel say of it
        self.assertEqual((waiting["baked"], len(waiting["not_carried"])), ([], 2))  # not said to be baked while it waits
        bpy.ops.object.mode_set(mode="OBJECT")
        self.assertEqual(self.addon._beside([self.prop], "named", False)["baked"], ["Fed: 기본 색", "Fed: 금속 · 거칠기"])
        (fed,), notes, in_object = self.bake()
        self.assertNotEqual(in_object, in_edit)  # so that it is sent once the mode allows
        self.assertEqual((notes, "pending" in fed["base_color_texture"]["stamp"], "pending" in fed["metallic_smoothness_texture"]["stamp"]), ([], False, False))
        self.assertEqual(sorted(p.name for p in self.textures.iterdir()), ["Fed_BaseColor.png", "Fed_MetallicSmoothness.png"])

    def test_an_image_laid_on_object_coordinates_is_baked_and_one_on_a_plain_mapping_is_not(self):
        material, shader = self.material("Laid")
        tree = material.node_tree
        tiles = self.feed(material, shader.inputs["Base Color"], self.picture("tiles"))
        coords = tree.nodes.new("ShaderNodeTexCoord")
        tree.links.new(coords.outputs["Object"], tiles.inputs["Vector"])
        (laid,), notes, _ = self.bake()
        texture = laid["base_color_texture"]
        self.assertEqual((texture["baked"], texture["file"], "uv" in texture, notes), (True, "Laid_BaseColor.png", False, []))
        self.assertEqual([p.name for p in self.textures.iterdir()], ["Laid_BaseColor.png"])  # the plain image is not what goes
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (2.0, 3.0, 1.0)
        mapping.inputs["Location"].default_value = (0.5, 0.25, 0.0)
        tree.links.new(coords.outputs["UV"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], tiles.inputs["Vector"])
        (laid,), notes, _ = self.bake()
        texture = laid["base_color_texture"]
        self.assertEqual((texture["file"], texture.get("baked"), texture["uv"], notes), ("tiles.png", None, {"scale": [2.0, 3.0], "offset": [0.5, 0.25]}, []))

    def test_a_roughness_image_on_object_coordinates_is_baked_too(self):
        material, shader = self.material("Laid")
        tiles = self.feed(material, shader.inputs["Roughness"], self.picture("rough", data=True))
        material.node_tree.links.new(material.node_tree.nodes.new("ShaderNodeTexCoord").outputs["Object"], tiles.inputs["Vector"])
        (laid,), notes, _ = self.bake()
        self.assertEqual((laid["metallic_smoothness_texture"]["baked"], laid["metallic_smoothness_texture"]["file"], notes), (True, "Laid_MetallicSmoothness.png", []))

    def test_an_image_and_what_comes_from_nodes_share_the_one_file(self):
        grey = lambda *values: [(v, v, v, 1.0) for v in values]
        material, shader = self.material("MetalMap")  # metallic from an image, roughness from a noise
        self.feed(material, shader.inputs["Metallic"], self.picture("metal", pixels=grey(0.5, 0.5, 0.5, 0.5), data=True))
        self.through(material, "Roughness", "ShaderNodeTexNoise", "Fac")
        (_,), notes, _ = self.bake()
        self.assertEqual(notes, [])
        size, rows = self.read(self.textures / "MetalMap_MetallicSmoothness.png")
        self.assertEqual(size, (self.SIZE, self.SIZE))  # the image's pixels are spread over the size of the bake
        self.assertAlmostEqual(float(rows[:, :, 0].mean()), 0.5, delta=0.03)
        self.assertLess(float(rows[:, :, 0].std()), 0.03)
        self.assertGreater(float(rows[:, :, 3].std()), 0.01)
        self.prop.data.materials.clear()
        other, shader = self.material("RoughMap")  # roughness from an image, metallic from nodes that come to a number
        self.feed(other, shader.inputs["Roughness"], self.picture("rough", pixels=grey(0.25, 0.25, 0.25, 0.25), data=True))
        value = self.through(other, "Metallic", "ShaderNodeMath")
        value.operation, value.inputs[0].default_value, value.inputs[1].default_value = "ADD", 0.25, 0.25
        (_,), notes, _ = self.bake()
        self.assertEqual(notes, [])
        rows = self.read(self.textures / "RoughMap_MetallicSmoothness.png")[1]
        self.assertAlmostEqual(float(rows[:, :, 0].mean()), 0.5, delta=0.03)
        self.assertAlmostEqual(float(rows[:, :, 3].mean()), 0.75, delta=0.03)

    def test_a_sent_model_says_what_was_baked_and_what_was_not_carried(self):
        real = self.addon._materials
        self.addon._materials = lambda objects, folder=None, bake=False, size=self.SIZE: real(objects, folder, bake, size)  # export_fbx has its own size: this is quicker
        material, _ = self.material("Fed")
        noise = self.through(material, "Base Color", "ShaderNodeTexNoise", "Color")
        sent = self.addon.export_fbx(["Prop"], "prop")
        manifest = json.loads(Path(sent["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual((sent["baked"], sent["not_carried"], manifest["baked"], manifest["not_carried"]), (["Fed: 기본 색"], [], ["Fed: 기본 색"], []))
        self.assertEqual((manifest["materials"][0]["base_color_texture"]["baked"], list(manifest["check"]["parts"])), (True, ["Prop"]))
        folder = self.exchange / manifest["textures_dir"]
        self.assertEqual([p.name for p in folder.iterdir()], ["Fed_BaseColor.png"])
        self.assertEqual(self.read(folder / "Fed_BaseColor.png")[0], (self.SIZE, self.SIZE))
        off = self.addon.export_fbx(["Prop"], "prop_off", bake=False)
        manifest = json.loads(Path(off["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual((off["baked"], manifest["baked"], list(self.exchange.joinpath(manifest["textures_dir"]).iterdir())), ([], [], []))
        self.assertEqual(len(off["not_carried"]), 1)
        self.assertTrue(off["not_carried"][0].startswith("Fed: 기본 색") and f"'{noise.bl_label}'" in off["not_carried"][0], off["not_carried"])
        self.assertEqual(manifest["not_carried"], off["not_carried"])


@unittest.skipIf(bpy is None, "Blender's bpy module is not installed")
class ShapeTests(unittest.TestCase):
    """What Unity checks a model it imported against: where each object is and how far each mesh reaches, in Unity's axes (Blender's x, y, z are Unity's -x, z, -y)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exchange = Path(self.tmp.name) / "exchange"
        os.environ["CLAUDE_HAND_EXCHANGE_DIR"] = str(self.exchange)
        spec = importlib.util.spec_from_file_location("claude_hand_chat", ADDON)
        self.addon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.addon)
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def tearDown(self):
        os.environ.pop("CLAUDE_HAND_EXCHANGE_DIR", None)
        self.tmp.cleanup()

    def cube(self, name, size=2.0, location=(0.0, 0.0, 0.0)):
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        obj = bpy.context.active_object
        obj.name = name
        return obj

    def empty(self, name, location=(0.0, 0.0, 0.0)):
        obj = bpy.data.objects.new(name, None)
        obj.location = location
        bpy.context.scene.collection.objects.link(obj)
        return obj

    def assertNumbers(self, got, want):
        self.assertEqual(len(got), len(want))
        for a, b in zip(got, want):
            self.assertAlmostEqual(a, b, delta=1e-4)

    def test_every_object_is_told_by_name_with_its_place_and_a_mesh_with_its_reach(self):
        box, marker = self.cube("Box", 2.0, (1.0, 2.0, 3.0)), self.empty("Marker", (-4.0, 5.0, 6.0))
        shape = self.addon._shape([box, marker])
        self.assertEqual(list(shape), ["parts"])
        self.assertEqual(sorted(shape["parts"]), ["Box", "Marker"])
        self.assertNumbers(shape["parts"]["Box"]["at"], [-1.0, 3.0, -2.0])  # Blender (1, 2, 3)
        self.assertNumbers(shape["parts"]["Box"]["low"], [-2.0, 2.0, -3.0])  # Blender x 0 to 2, y 1 to 3, z 2 to 4
        self.assertNumbers(shape["parts"]["Box"]["high"], [0.0, 4.0, -1.0])
        self.assertEqual(list(shape["parts"]["Marker"]), ["at"])  # an empty reaches nowhere
        self.assertNumbers(shape["parts"]["Marker"]["at"], [4.0, 6.0, -5.0])

    def test_a_parent_that_is_turned_and_scaled_is_in_the_numbers(self):
        parent = self.empty("Parent", (5.0, 0.0, 0.0))
        parent.rotation_euler.z, parent.scale = 1.5707963, (2.0, 2.0, 2.0)
        child = self.cube("Child")  # 2 across, so 4 across in the world
        child.parent, child.location = parent, (1.0, 0.0, 0.0)  # at (5, 2, 0) in the world: the parent's turn takes its x to y
        bpy.context.view_layer.update()
        part = self.addon._shape([parent, child])["parts"]["Child"]
        self.assertNumbers(part["at"], [-5.0, 0.0, -2.0])
        self.assertNumbers(part["low"], [-7.0, -2.0, -4.0])  # Blender x 3 to 7, y 0 to 4, z -2 to 2
        self.assertNumbers(part["high"], [-3.0, 2.0, 0.0])

    def test_a_turned_mesh_reaches_as_far_as_its_corners(self):
        box = self.cube("Box")
        box.rotation_euler.z = 0.7853982  # a quarter of a half turn: a corner points along x
        bpy.context.view_layer.update()
        part = self.addon._shape([box])["parts"]["Box"]
        self.assertNumbers(part["low"], [-1.41421, -1.0, -1.41421])
        self.assertNumbers(part["high"], [1.41421, 1.0, 1.41421])

    def test_a_mesh_with_modifiers_reaches_as_far_as_what_is_exported(self):
        """Not as far as Object.bound_box says: in an open window that is the box before subdivision (found there, 0.17.0)."""
        ball = self.cube("Ball")
        ball.modifiers.new("Smooth", "SUBSURF").levels = 2
        bpy.context.view_layer.update()
        part = self.addon._shape([ball])["parts"]["Ball"]
        evaluated = ball.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        far = max(abs(c) for v in mesh.vertices for c in v.co)
        evaluated.to_mesh_clear()
        self.assertLess(far, 0.9)  # a cube that is subdivided draws in from its corners
        self.assertNumbers(part["high"], [far, far, far])
        self.assertNumbers(part["low"], [-far, -far, -far])
        self.assertEqual(len(ball.modifiers), 1)  # nothing is applied to the object itself

    def test_a_mesh_that_a_rig_or_a_shape_key_bends_is_told_by_its_place_only(self):
        keyed, rigged, bare = self.cube("Keyed"), self.cube("Rigged"), self.cube("Bare")
        keyed.shape_key_add(name="Basis")
        rigged.modifiers.new("Rig", "ARMATURE")
        bare.data.clear_geometry()
        bare.location = (1.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        parts = self.addon._shape([keyed, rigged, bare])["parts"]
        self.assertEqual({name: list(part) for name, part in parts.items()}, {"Keyed": ["at"], "Rigged": ["at"], "Bare": ["at"]})
        self.assertNumbers(parts["Bare"]["at"], [-1.0, 0.0, 0.0])

    def test_the_manifest_carries_what_the_shape_says_of_the_same_objects(self):
        hull = self.cube("Hull", 2.0, (0.0, 1.0, 0.0))
        deck = self.cube("Deck", 1.0, (0.0, 0.0, 1.0))
        deck.parent = hull
        bpy.context.view_layer.update()
        manifest = json.loads(Path(self.addon.export_fbx(["Hull"], "ship")["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest["check"]["parts"]), ["Deck", "Hull"])
        self.assertEqual(manifest["check"], json.loads(json.dumps(self.addon._shape([hull, deck]))))


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
