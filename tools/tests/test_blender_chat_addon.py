"""Runs the Claude Hand Chat add-on inside real Blender against a real chat channel hub.

Needs Blender's Python module (`pip install bpy`); skipped when it is not importable.
"""

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

    def test_progress_bar_while_the_subagent_works(self):
        addon = self.addon
        pin = next(a.pin for a in cc.load_agents(Path(self.tmp.name) / "agents") if a.app == "blender")
        self.assertTrue(wait_for(lambda: addon._status[0].startswith("담당"), addon))
        chat_id = self.hub.report_action("blender", pin, "execute_code", "bpy.ops.mesh.primitive_uv_sphere_add()")
        self.assertTrue(wait_for(lambda: addon._progress[0] is not None, addon))
        factor, bar, caption = addon.progress_view(*addon._progress[0], time.time())
        self.assertTrue(bar.startswith("작업 중") and 0.0 <= factor <= 1.0)
        self.assertIn("primitive_uv_sphere_add", caption)
        self.hub.set_progress(chat_id, 2, 4, "Unity로 가져오는 중")
        self.assertTrue(wait_for(lambda: (addon._progress[0] or ({}, 0))[0].get("total") == 4, addon))
        factor, bar, caption = addon.progress_view(*addon._progress[0], time.time())
        self.assertEqual((factor, bar.split(" ")[0]), (0.5, "2/4"))
        self.hub.reply(chat_id, "다 했어요")
        self.assertTrue(wait_for(lambda: addon._progress[0] is None, addon))

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
            proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
            self.assertIn("CLAUDE_HAND_CHAT enabled", proc.stdout, proc.stderr)
            self.assertIn("PANEL True", proc.stdout)
            self.assertTrue((config / "userpref.blend").exists())


if __name__ == "__main__":
    unittest.main()
