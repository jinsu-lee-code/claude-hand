"""The settings and the lab: config.json as every part reads it (handlib/prefs.py), the one list of what can be set
(settings/catalog.json), the `settings` command, and what the chat channel does with a setting while it runs.
Run: python -m unittest discover -s tools/tests -v"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CLAUDE_HAND_LOG"] = "off"  # the tests leave the real event log alone
os.environ["CLAUDE_HAND_LANG"] = "ko"  # the words here are the Korean ones, whatever this PC is set to

import chat_channel as cc  # noqa: E402
import claude_hand  # noqa: E402
from handlib import agents, constants, discovery, prefs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
UNITY_SERVER = discovery.ServerConfig(transport="stdio", command="uvx", args=["mcp-for-unity"], key="unityMCP")
BLENDER_SERVER = discovery.ServerConfig(transport="stdio", command="uvx", args=list(constants.DEFAULT_BLENDER_ARGS), key="blender")


def write_agents(folder: Path) -> None:
    """Two windows' subagent files, as sync writes them."""
    unity = [discovery.Instance(name="MyGame", hash="aaaa1111", path="C:/Games/MyGame")]
    blender = [discovery.BlenderInstance(port=9877, filepath="C:/Art/props.blend")]
    agents.write_agent_files(agents.build_agent_files(unity, UNITY_SERVER, blender, BLENDER_SERVER, "uv run -q --no-project"), folder)


class Home(unittest.TestCase):
    """A claude-hand home folder of the test's own, and no language forced: these go by its config.json."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {"CLAUDE_HAND_HOME": str(self.home)})
        self.env.start()
        os.environ.pop("CLAUDE_HAND_LANG", None)
        # How this PC has MCP for Unity and for Blender registered is not what these tests are about.
        self.servers = [mock.patch.object(discovery, "detect_server_config", lambda: UNITY_SERVER),
                        mock.patch.object(discovery, "detect_blender_config", lambda: BLENDER_SERVER)]
        for patch in self.servers:
            patch.start()

    def tearDown(self):
        for patch in self.servers:
            patch.stop()
        self.env.stop()
        os.environ["CLAUDE_HAND_LANG"] = "ko"
        self.tmp.cleanup()

    def config(self) -> dict:
        return json.loads((self.home / "config.json").read_text(encoding="utf-8"))


class CatalogTests(unittest.TestCase):
    def test_every_entry_says_what_the_windows_need(self):
        catalog = prefs.catalog()
        keys = [e["key"] for e in catalog["settings"]]
        self.assertEqual(len(keys), len(set(keys)))
        for wanted in ("language", "show_cost", "show_previews", "flash_taskbar", "remote_control", "minimize_after_start",
                       "history_keep_days", "exchange_keep_days"):
            self.assertIn(wanted, keys)
        for entry in catalog["settings"]:
            self.assertIn(entry["kind"], ("switch", "choice", "days"), entry["key"])
            self.assertIn(entry["applies"], ("now", "start"), entry["key"])
            self.assertEqual(prefs.clean(entry, entry["default"]), entry["default"], entry["key"])  # a default it can hold
            if entry["kind"] == "choice":
                self.assertTrue(all(c.get("value") and c.get("label") for c in entry["choices"]), entry["key"])

    def test_the_defaults_are_what_claude_hand_did_before_there_were_settings(self):
        was = {"language": "ko", "show_cost": True, "show_previews": True, "flash_taskbar": True, "remote_control": True,
               "minimize_after_start": True, "history_keep_days": 0, "exchange_keep_days": 30}
        self.assertEqual({key: prefs.get(key, config={}) for key in was}, was)

    def test_a_lab_feature_says_since_when_and_starts_turned_off(self):
        version = tuple(int(part) for part in constants.VERSION.split("."))
        for entry in prefs.catalog()["lab"]:
            self.assertIn(entry.get("stage"), ("lab", "stable"), entry["key"])
            self.assertLessEqual(tuple(int(part) for part in entry["since"].split(".")), version, entry["key"])
            self.assertIs(entry["default"], False, entry["key"])  # a feature still in the lab is the user's to turn on
            self.assertIn(entry["applies"], ("now", "start"), entry["key"])
        self.assertIn("english_prompts", [e["key"] for e in prefs.in_lab()])

    def test_what_guards_the_windows_is_no_setting(self):
        text = (ROOT / "settings" / "catalog.json").read_text(encoding="utf-8")
        keys = [e["key"] for part in ("settings", "lab") for e in json.loads(text)[part]]
        for word in ("guard", "protect", "undo", "checkpoint", "hook"):
            self.assertFalse([key for key in keys if word in key], word)


class PrefsTests(Home):
    def test_a_setting_is_read_anew_when_the_file_changes(self):
        self.assertEqual(prefs.live(self.home), {})  # no file: nothing set
        self.assertIs(prefs.get("show_cost"), True)
        (self.home / "config.json").write_text(json.dumps({"show_cost": False, "repo": "C:/claude-hand"}), encoding="utf-8")
        self.assertIs(prefs.get("show_cost"), False)
        prefs.put("show_cost", True, self.home)  # as a settings window writes it
        self.assertIs(prefs.get("show_cost"), True)
        self.assertEqual(self.config(), {"show_cost": True, "repo": "C:/claude-hand"})  # what else was there stays

    def test_a_command_asked_to_speak_the_other_language_still_knows_the_configured_one(self):
        with mock.patch.dict(os.environ, {"CLAUDE_HAND_LANG": "en"}):
            self.assertEqual(prefs.language(), "en")
            with prefs.as_configured():
                self.assertEqual(prefs.language(), "ko")  # what the files on disk are written in
            self.assertEqual(prefs.language(), "en")
        os.environ.pop("CLAUDE_HAND_LANG", None)

    def test_a_file_caught_half_written_leaves_the_settings_as_they_were(self):
        prefs.put("language", "en", self.home)
        self.assertEqual(prefs.language(), "en")
        time.sleep(0.02)
        (self.home / "config.json").write_text('{"language": "k', encoding="utf-8")
        self.assertEqual(prefs.language(), "en")
        (self.home / "config.json").write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(prefs.language(), "en")
        (self.home / "config.json").unlink()
        self.assertEqual(prefs.language(), "ko")  # gone: as nothing was ever set

    def test_what_a_setting_cannot_be_is_its_default(self):
        config = {"show_cost": "no", "language": "fr", "history_keep_days": -3, "exchange_keep_days": True, "remote_control": 0}
        self.assertEqual([prefs.get(key, config) for key in config], [True, "ko", 0, 30, True])
        self.assertEqual(prefs.get("history_keep_days", {"history_keep_days": 14}), 14)

    def test_words_a_command_takes(self):
        switch, days, choice = prefs.entry("show_cost"), prefs.entry("history_keep_days"), prefs.entry("language")
        self.assertEqual([prefs.parse(switch, word) for word in ("on", "OFF", "true", "0")], [True, False, True, False])
        self.assertEqual((prefs.parse(days, "30"), prefs.parse(days, "0"), prefs.parse(choice, "EN")), (30, 0, "en"))
        for found, word in ((switch, "maybe"), (days, "-1"), (days, "9999"), (days, "soon"), (choice, "fr")):
            with self.assertRaises(ValueError, msg=word):
                prefs.parse(found, word)

    def test_the_lab(self):
        self.assertFalse(prefs.lab_on("english_prompts"))  # off until turned on
        prefs.put_lab("english_prompts", True, self.home)
        prefs.put("language", "en", self.home)
        self.assertTrue(prefs.lab_on("english_prompts"))
        self.assertEqual(self.config(), {"lab": {"english_prompts": True}, "language": "en"})
        self.assertFalse(prefs.lab_on("no_such_feature", {"lab": {"no_such_feature": True}}))  # not in the catalog: not a feature

    def test_a_feature_that_left_the_lab_is_on_for_everybody(self):
        graduated = {"settings": [], "lab": [{"key": "proven", "stage": "stable", "since": "1.1.0", "default": False},
                                              {"key": "trying", "stage": "lab", "since": "1.1.0", "default": False}]}
        with mock.patch.object(prefs, "catalog", lambda: graduated):
            self.assertTrue(prefs.lab_on("proven", {}))
            self.assertTrue(prefs.lab_on("proven", {"lab": {"proven": False}}))  # no longer the user's to turn off
            self.assertFalse(prefs.lab_on("trying", {}))
            self.assertEqual([e["key"] for e in prefs.in_lab()], ["trying"])  # and it is not in the lab tab


class CommandTests(Home):
    def run_settings(self, *words):
        out = io.StringIO()
        args = claude_hand.build_parser().parse_args(["settings", *words, "--agents-dir", str(self.root / "agents"),
                                                      "--skills-dir", str(self.root / "skills")])
        with redirect_stdout(out):
            code = claude_hand.cmd_settings(args)
        return code, out.getvalue()

    def test_the_list_says_each_setting_in_words(self):
        code, out = self.run_settings()
        self.assertEqual(code, 0)
        self.assertIn("language               한국어", out)
        self.assertRegex(out, r"show_cost +켜짐 +요청마다 든 시간 · 토큰 줄  \[바로 적용\]")
        self.assertRegex(out, r"history_keep_days +지우지 않음 +대화 내용 보관 일수  \[다음에 Claude Hand를 켤 때부터\]")
        self.assertRegex(out, r"english_prompts +꺼짐 +Claude 지침 영어판  \[1\.1\.0부터 · 다음에 Claude Hand를 켤 때부터\]")
        code, out = self.run_settings("--json")
        self.assertEqual(json.loads(out)["settings"]["exchange_keep_days"], 30)

    def test_a_setting_is_changed_and_said_back(self):
        code, out = self.run_settings("history_keep_days", "30")
        self.assertEqual((code, self.config()), (0, {"history_keep_days": 30}))
        self.assertIn("30일", out)
        self.assertIn("다음에 Claude Hand를 켤 때부터 적용됩니다.", out)
        code, out = self.run_settings("show_cost", "off")
        self.assertEqual((code, self.config()["show_cost"]), (0, False))
        self.assertNotIn("다음에 Claude Hand를 켤 때부터 적용됩니다.", out)  # the channel reads it where it uses it
        code, out = self.run_settings("show_cost", "maybe")
        self.assertEqual(code, 1)
        self.assertIn("on | off", out)
        self.assertEqual(self.run_settings("no_such")[0], 1)
        code, out = self.run_settings("lab", "english_prompts", "on")
        self.assertEqual((code, self.config()["lab"]), (0, {"english_prompts": True}))

    def test_the_language_is_changed_with_what_was_written_out_in_the_other(self):
        write_agents(self.root / "agents")
        agents.install_skill(self.root / "skills")
        before = (self.root / "agents" / "unity-mygame.md").read_text(encoding="utf-8")
        code, out = self.run_settings("language", "en")
        self.assertEqual(code, 0)
        self.assertIn("The subagent definitions and the supervisor skill were written again in this language (3).", out)
        after = (self.root / "agents" / "unity-mygame.md").read_text(encoding="utf-8")
        self.assertIn('description: "Dedicated worker for the Unity project MyGame', after)
        self.assertIn("## 화면 언어: English", after)
        self.assertIn("description: Supervisor rules", (self.root / "skills" / "claude-hand" / "SKILL.md").read_text(encoding="utf-8"))
        code, out = self.run_settings()
        self.assertIn("Settings  (", out)
        self.assertRegex(out, r"show_cost +On +Time and token line for each request  \[applies at once\]")
        self.run_settings("language", "ko")
        self.assertEqual((self.root / "agents" / "unity-mygame.md").read_text(encoding="utf-8"), before)  # as it was, to the letter


class ChannelTests(Home):
    """What the chat channel does with the settings while it runs: nothing is started again."""

    def setUp(self):
        super().setUp()
        write_agents(self.root / "agents")
        self.sent = []
        self.hub = cc.ChatHub(self.sent.append, self.root / "agents")
        self.hub.initialized = True
        self.hub.home = self.home
        self.hub.setting = lambda key: prefs.get(key, home=self.home)
        self.shots = []

        def capture(app, pin, path):
            self.shots.append(pin)
            Path(path).write_bytes(b"png")
            return path
        self.hub.capture, self.hub.images_dir = capture, self.root / "images"

    def notices(self, chat_id="unity-mygame"):
        return [i["text"] for i in self.hub.logs.get(chat_id, []) if i["kind"] == "notice"]

    def ask(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 하나 만들어줘", pin="MyGame@aaaa1111")
        return self.hub.logs["unity-mygame"][-1]["request"]

    def test_the_line_of_time_and_tokens_follows_the_setting_at_once(self):
        first = self.ask()
        self.hub.reply("unity-mygame", "만들었어요")
        self.assertEqual(self.hub.check_costs(), [first])
        self.assertEqual(len(self.notices()), 1)
        prefs.put("show_cost", False, self.home)  # turned off in a settings window, Claude Hand still on
        second = self.ask()
        self.hub.reply("unity-mygame", "또 만들었어요")
        self.assertEqual(self.hub.check_costs(), [second])
        self.assertEqual(len(self.notices()), 1)  # no second line
        prefs.put("show_cost", True, self.home)
        third = self.ask()
        self.hub.reply("unity-mygame", "하나 더요")
        self.assertEqual(self.hub.check_costs(), [third])
        self.assertEqual(len(self.notices()), 2)

    def test_the_lines_the_channel_writes_follow_the_language_at_once(self):
        self.hub.request_stop("unity", "MyGame", "MyGame@aaaa1111")  # nothing going on: no line
        self.ask()
        self.hub.request_stop("unity", "MyGame", "MyGame@aaaa1111")
        prefs.put("language", "en", self.home)
        self.ask()
        self.hub.request_stop("unity", "MyGame", "MyGame@aaaa1111")
        self.assertEqual(self.notices(), ["중지를 요청했어요. 지금 하던 동작 하나는 끝까지 갈 수 있어요.",
                                          "Stop requested. The one action under way may still run to its end."])
        self.assertIn("[claude-hand] The user pressed [Stop] in this chat panel", self.sent[-1]["params"]["content"])

    def test_pictures_of_other_windows_can_be_turned_off(self):
        def changed_elsewhere():
            self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
            self.hub.reply("blender-props", "보냈어요", working=False, changed=["unity-mygame"])
            deadline = time.time() + 2
            while time.time() < deadline and not self.shots:
                time.sleep(0.05)
            return list(self.shots)

        prefs.put("show_previews", False, self.home)
        self.assertEqual(changed_elsewhere(), [])
        prefs.put("show_previews", True, self.home)
        self.assertEqual(changed_elsewhere(), ["MyGame@aaaa1111"])

    def test_a_change_of_language_is_told_to_the_supervisor_and_written_out(self):
        rewritten = []
        self.hub.on_language = lambda: rewritten.append(prefs.language(home=self.home))
        self.assertFalse(self.hub.check_language())  # the language it started with: nothing to tell
        self.assertFalse(self.hub.check_language())
        prefs.put("language", "en", self.home)
        others = self.hub.overview_count
        self.assertTrue(self.hub.check_language())
        self.assertEqual(rewritten, ["en"])
        self.assertGreater(self.hub.overview_count, others)  # the panels fetch the other windows' lines again, in the language now
        told = self.sent[-1]["params"]
        self.assertEqual(told["meta"]["notice"], "language")
        self.assertTrue(told["content"].startswith("[claude-hand] The user changed the interface language to English."))
        self.assertIn("- [되돌리기] → [Undo]", told["content"])  # its instructions are Korean: what the buttons are called now
        self.assertFalse(self.hub.check_language())  # told once
        prefs.put("language", "ko", self.home)
        self.assertTrue(self.hub.check_language())
        self.assertTrue(self.sent[-1]["params"]["content"].startswith("[claude-hand] 사용자가 화면 언어를 한국어로 바꿨습니다."))
        self.assertNotIn("→", self.sent[-1]["params"]["content"])  # Korean instructions, Korean interface: nothing to add

    def test_the_subagents_files_follow_the_language(self):
        skills = self.root / "skills"
        agents.install_skill(skills)
        before = (self.root / "agents" / "blender-props.md").read_text(encoding="utf-8")
        prefs.put("language", "en", self.home)
        written = agents.rewrite_installed(self.root / "agents", skills).written
        self.assertEqual(sorted(p.name for p in written), ["SKILL.md", "blender-props.md", "unity-mygame.md"])
        self.assertIn("Dedicated worker for a Blender window: props", (self.root / "agents" / "blender-props.md").read_text(encoding="utf-8"))
        self.assertEqual(agents.rewrite_installed(self.root / "agents", skills).written, [])  # as they would be written: left alone
        prefs.put("language", "ko", self.home)
        agents.rewrite_installed(self.root / "agents", skills)
        self.assertEqual((self.root / "agents" / "blender-props.md").read_text(encoding="utf-8"), before)

    def test_the_skill_is_the_one_beside_the_agents_folder(self):
        """A channel started on another agents folder (a test, a trial run) must not write the user's own skill again."""
        agents.install_skill(self.root / "skills")
        prefs.put("language", "en", self.home)
        written = agents.rewrite_installed(self.root / "agents").written
        self.assertIn(self.root / "skills" / "claude-hand" / "SKILL.md", written)
        self.assertTrue(all(self.root in path.parents for path in written))


class HistoryDaysTests(Home):
    """config.json "history_keep_days": how long the conversation a panel shows again is kept."""

    def setUp(self):
        super().setUp()
        write_agents(self.root / "agents")
        self.history, self.images = self.root / "history", self.root / "images"

    def hub(self, keep_days=0, now=None):
        hub = cc.ChatHub(lambda message: None, self.root / "agents")
        hub.images_dir = self.images
        gone = hub.use_history(self.history, keep_days, now)
        return hub, gone

    def test_messages_older_than_the_days_kept_are_not_shown_again_and_leave_the_file(self):
        first, _ = self.hub()
        first.post_user_message("unity", "MyGame", "지난달 요청", pin="MyGame@aaaa1111")
        first.reply("unity-mygame", "지난달 답")
        first.agent_ids["unity-mygame"] = "agent-1"
        for item in first.logs["unity-mygame"]:
            item["ts"] = time.time() - 40 * 86400
        first.reply("unity-mygame", "어제 답")
        first.logs["unity-mygame"][-1]["ts"] = time.time() - 86400
        first._save_history("unity-mygame")
        self.images.mkdir()
        for name, age in (("old.png", 40), ("new.png", 1)):
            (self.images / name).write_bytes(b"png")
            os.utime(self.images / name, (time.time() - age * 86400,) * 2)

        kept_all, gone = self.hub(keep_days=0)  # 0: never by their age (what claude-hand did before the setting)
        self.assertEqual((gone, len(kept_all.logs["unity-mygame"])), (0, 3))
        self.assertEqual(sorted(p.name for p in self.images.glob("*.png")), ["new.png", "old.png"])

        thinned, gone = self.hub(keep_days=30)
        self.assertEqual(gone, 2)
        self.assertEqual([i["text"] for i in thinned.logs["unity-mygame"]], ["어제 답"])
        saved = json.loads((self.history / "unity-mygame.json").read_text(encoding="utf-8"))
        self.assertEqual(([i["text"] for i in saved["items"]], saved["agent_id"]), (["어제 답"], "agent-1"))  # whose window it was stays
        self.assertEqual(sorted(p.name for p in self.images.glob("*.png")), ["new.png"])

        emptied, gone = self.hub(keep_days=30, now=time.time() + 60 * 86400)
        self.assertEqual((gone, emptied.logs["unity-mygame"]), (1, []))


if __name__ == "__main__":
    unittest.main()
