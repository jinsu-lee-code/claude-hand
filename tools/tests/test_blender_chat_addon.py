"""Runs the Claude Hand Chat add-on inside real Blender against a real chat channel hub.

Needs Blender's Python module (`pip install bpy`); skipped when it is not importable.
"""

import base64
import importlib.util
import json
import os
import secrets
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_channel as cc  # noqa: E402
import claude_hand as ch  # noqa: E402

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
        ch.write_agent_files(ch.build_agent_files([], None, [ch.BlenderInstance(port=9876, filepath=str(blend))], None, "uv run"),
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
        os.environ.pop("CLAUDE_HAND_HOME", None)
        self.tmp.cleanup()

    def test_chat_round_trip_and_permission(self):
        addon = self.addon
        self.assertTrue(wait_for(lambda: addon._status[0] == "담당: blender-props", addon), addon._status[0])

        bpy.context.window_manager.claude_hand_chat_input = "큐브 하나 추가해줘"  # Enter in the field sends it
        self.assertTrue(wait_for(lambda: self.emitted, addon))
        note = self.emitted[0]["params"]
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
        pin = next(a.pin for a in cc.load_agents(Path(self.tmp.name) / "agents") if a.app == "blender")
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
        ch.write_agent_files(ch.build_agent_files([ch.Instance(name="MyGame", hash="aaaa1111")], ch.ServerConfig(transport="stdio"),
                                                  [ch.BlenderInstance(port=9876, filepath=str(root / "props.blend"))], None, "uv run"),
                             root / "agents")
        self.hub.acted["unity-mygame"] = time.time() + 1  # its subagent worked after the request
        self.hub.reply("blender-props", "Unity에 놓았어요")
        self.assertTrue(wait_for(lambda: any(e.get("kind") == "image" for e in addon._entries), addon))
        entry = next(e for e in addon._entries if e.get("kind") == "image")
        self.assertEqual(addon.entry_rows(entry, None)[0], [("MyGame (Unity) 지금 모습", "IMAGE_DATA")])
        self.assertIs(addon.visible_entries(None)[0][-1][0], entry)
        self.assertIsNotNone(addon.image_preview(entry["path"]))
        self.assertIsNone(addon.image_preview(str(Path(self.tmp.name) / "gone.png")))

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
        addon._entries[:] = [{"kind": "reply", "seq": i, "text": "긴 답 " * 200} for i in range(30)]
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
            code = "import bpy\nexec(" + repr(ch.ENABLE_CHAT_ADDON) + ")\nprint('PANEL', hasattr(bpy.types, 'CLAUDEHAND_PT_chat'))"
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
