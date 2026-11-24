import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlib import protect  # noqa: E402
from handlib.protect import Rules  # noqa: E402


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_where_a_windows_rules_are(self):
        self.assertEqual(protect.unity_rules_path("C:/Games/MyGame"), Path("C:/Games/MyGame/ClaudeHand/protect.json"))
        self.assertEqual(protect.blender_rules_path("C:/Art/robot.blend"), Path("C:/Art/robot.claude-hand.protect.json"))
        self.assertEqual(protect.session_rules_path(self.root, "unity-my game").name, "unity-my-game.json")

    def test_no_file_is_no_rules_and_a_broken_one_says_so(self):
        self.assertTrue(protect.read(self.root / "none.json").empty())
        self.assertTrue(protect.read(None).empty())
        broken = self.root / "protect.json"
        broken.write_text("{not json", encoding="utf-8")
        rules = protect.read(broken)
        self.assertTrue(rules.empty())
        self.assertEqual(len(rules.problems), 1)
        broken.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(len(protect.read(broken).problems), 1)

    def test_a_file_is_read_leniently(self):
        path = self.root / "protect.json"
        path.write_text(json.dumps({"version": 1, "window": "LOOK", "paths": ["Assets/Art/**", "", 3], "objects": "Player",
                                    "actions": ["save_scene", "fly"]}), encoding="utf-8")
        rules = protect.read(path)
        self.assertEqual((rules.window, rules.paths, rules.objects, rules.actions), ("look", ["Assets/Art/**"], [], ["save_scene"]))
        path.write_text(json.dumps({"window": "shut"}), encoding="utf-8")
        self.assertEqual(protect.read(path).window, "open")  # a word it does not know is not a rule

    def test_a_windows_rules_are_its_lasting_ones_and_this_runs(self):
        project = self.root / "MyGame"
        (project / "ClaudeHand").mkdir(parents=True)
        protect.unity_rules_path(project).write_text(json.dumps({"window": "look", "paths": ["Assets/Art/**"]}), encoding="utf-8")
        home = self.root / "home"
        session = protect.session_rules_path(home, "unity-mygame")
        session.parent.mkdir(parents=True)
        session.write_text(json.dumps({"window": "open", "paths": ["Assets/Art/**", "Assets/Audio"], "actions": ["play_mode"]}), encoding="utf-8")
        rules = protect.window_rules("unity", project, home, "unity-mygame")
        self.assertEqual((rules.window, rules.paths, rules.actions), ("look", ["Assets/Art/**", "Assets/Audio"], ["play_mode"]))
        self.assertEqual(protect.merged(Rules(window="look"), Rules(window="locked"), Rules()).window, "locked")
        blend = self.root / "robot.blend"
        protect.blender_rules_path(blend).write_text(json.dumps({"objects": ["Hero"]}), encoding="utf-8")
        self.assertEqual(protect.window_rules("blender", blend).objects, ["Hero"])
        self.assertTrue(protect.window_rules("unity", None).empty())


CASES = json.loads((Path(__file__).with_name("protect_cases.json")).read_text(encoding="utf-8"))  # Unity and Blender answer the same


class CoverTests(unittest.TestCase):
    def test_paths(self):
        rules = Rules(paths=CASES["paths"]["rules"])
        for case in CASES["paths"]["cases"]:
            self.assertEqual(protect.path_rule(rules, case["path"]), case["rule"], case["path"])
        self.assertIsNone(protect.path_rule(Rules(paths=["", "  "]), "Assets/x"))

    def test_objects(self):
        rules = Rules(objects=CASES["objects"]["rules"])
        for case in CASES["objects"]["cases"]:
            self.assertEqual(protect.object_rule(rules, case["name"]), case["rule"], case["name"])

    def test_in_words(self):
        self.assertEqual(protect.in_words(Rules()), "")
        self.assertEqual(protect.in_words(Rules(window="look", paths=["a", "b"], objects=["x"], actions=["save_scene", "play_mode"])),
                         "보기만 · 경로 2 · 오브젝트 1 · 금지 동작: 씬 저장, 플레이 모드")


class RefusalTests(unittest.TestCase):
    def refused(self, rules, tool, tool_input=None, only_looks=False, files=None, app="unity", covered=None):
        return protect.refusal(app, rules, tool, tool_input or {}, only_looks, files, covered)

    def test_no_rules_refuse_nothing(self):
        self.assertIsNone(self.refused(Rules(), "manage_asset", {"action": "delete", "path": "Assets/Art/Hero.fbx"}))

    def test_a_locked_window_takes_no_call_and_a_looking_one_only_those_that_look(self):
        locked, look = Rules(window="locked"), Rules(window="look")
        self.assertIn("잠금", self.refused(locked, "find_gameobjects", {"search_term": "x"}, only_looks=True))
        self.assertIsNone(self.refused(look, "find_gameobjects", {"search_term": "x"}, only_looks=True))
        said = self.refused(look, "manage_gameobject", {"action": "create", "name": "Cube"})
        self.assertIn("보기만", said)
        self.assertTrue(said.startswith("보호 규칙이 이 호출을 막았습니다: ") and said.endswith("규칙을 푸는 것은 사용자만 할 수 있습니다."))
        self.assertIn("보기만", self.refused(look, "execute_blender_code", {"code": "bpy.data.objects['Cube'].location.x = 1"}, app="blender"))
        # A tool that does many things, called for one that only looks, is a look.
        for tool, action in (("manage_scene", "get_hierarchy"), ("manage_asset", "search"), ("manage_gameobject", "find"),
                             ("manage_editor", "get_state"), ("manage_prefabs", "get_info")):
            self.assertIsNone(self.refused(look, tool, {"action": action}), (tool, action))
        self.assertIn("보기만", self.refused(look, "manage_scene", {"action": "save"}))
        self.assertIn("보기만", self.refused(look, "execute_code", {"action": "get", "code": "Debug.Log(1);"}))  # not by a word of its own

    def test_forbidden_actions(self):
        rules = Rules(actions=["save_scene", "delete_asset", "play_mode", "save_file"])
        self.assertIn("씬 저장", self.refused(rules, "manage_scene", {"action": "save"}))
        self.assertIsNone(self.refused(rules, "manage_scene", {"action": "load", "name": "Main"}))
        self.assertIn("에셋 삭제", self.refused(rules, "manage_asset", {"action": "delete", "path": "Assets/x.mat"}))
        self.assertIn("에셋 삭제", self.refused(rules, "delete_script", {"uri": "Assets/Scripts/a.cs"}))
        self.assertIn("플레이 모드", self.refused(rules, "manage_editor", {"action": "play"}))
        self.assertIn(".blend 저장", self.refused(rules, "execute_blender_code", {"code": "import bpy\nbpy.ops.wm.save_mainfile()"}, app="blender"))
        self.assertIsNone(self.refused(rules, "execute_blender_code", {"code": "print(bpy.data.filepath)"}, app="blender"))
        self.assertIsNone(self.refused(Rules(actions=["play_mode"]), "manage_scene", {"action": "save"}))
        # ... and what they look like in code, or as a menu item (the window refuses the action itself all the same)
        self.assertIn("씬 저장", self.refused(rules, "execute_code", {"code": "EditorSceneManager.SaveOpenScenes();"}))
        self.assertIn("에셋 삭제", self.refused(rules, "execute_code", {"code": 'AssetDatabase.DeleteAsset(path);'}))
        self.assertIn("플레이 모드", self.refused(rules, "execute_code", {"code": "EditorApplication.isPlaying = true;"}))
        self.assertIn("플레이 모드", self.refused(rules, "execute_menu_item", {"menu_path": "Edit/Play"}))
        self.assertIn("씬 저장", self.refused(rules, "execute_menu_item", {"menu_path": "File/Save"}))
        self.assertIsNone(self.refused(rules, "execute_code", {"code": "Debug.Log(EditorApplication.isPlaying);"}))

    def test_protected_paths(self):
        rules = Rules(paths=["Assets/Art/**"])
        self.assertIn("Assets/Art/Hero.mat", self.refused(rules, "manage_material", {"action": "set_color", "path": "Assets/Art/Hero.mat"}))
        self.assertIn("보호 경로 'Assets/Art/**'", self.refused(rules, "create_script", {"path": "Assets/Art/New.cs", "contents": "x"}))  # not there yet
        self.assertIn("Assets/Art/b.fbx", self.refused(rules, "manage_asset", {"action": "move", "uri": "b"}, files=["Assets/Art/b.fbx"]))  # what keeps found
        self.assertIsNone(self.refused(rules, "manage_material", {"action": "set_color", "path": "Assets/Props/Hero.mat"}))
        self.assertIsNone(self.refused(rules, "read_resource", {"uri": "Assets/Art/Hero.mat"}, only_looks=True))  # looking is allowed
        self.assertIsNone(self.refused(rules, "manage_asset", {"action": "get_info", "path": "Assets/Art/Hero.mat"}))
        # Putting a protected asset to use changes nothing of it: a prefab into the scene, a scene opened.
        self.assertIsNone(self.refused(rules, "manage_gameobject", {"action": "create", "name": "Hero", "prefab_path": "Assets/Art/Hero.prefab"}))
        self.assertIsNone(self.refused(rules, "manage_material", {"action": "assign_material_to_renderer", "target": "Cube",
                                                                  "material_path": "Assets/Art/Hero.mat"}))
        self.assertIsNone(self.refused(Rules(paths=["Assets/Scenes/Main.unity"]), "manage_scene", {"action": "load", "path": "Assets/Scenes/Main.unity"}))
        self.assertIn("Main.unity", self.refused(Rules(paths=["Assets/Scenes/Main.unity"]), "manage_scene", {"action": "save", "path": "Assets/Scenes/Main.unity"}))
        # Code that names one may be about to write the file past Unity, where nothing else stops it: refused, and
        # told how to go on if it only meant to read or use it.
        said = self.refused(rules, "execute_code", {"code": 'File.Delete("Assets/Art/a.png");'})
        self.assertIn("Assets/Art/a.png", said)
        self.assertIn("읽는 호출과 바꾸는 호출을 나눠", said)
        self.assertTrue(said.startswith("보호 규칙이 이 호출을 막았습니다: ") and said.endswith("규칙을 푸는 것은 사용자만 할 수 있습니다."))

    def test_protected_objects(self):
        rules = Rules(objects=["Player", "Environment/**"])
        said = self.refused(rules, "manage_gameobject", {"action": "modify", "target": "Player", "position": [1, 2, 3]})
        self.assertIn("보호 오브젝트 'Player'", said)
        self.assertIn("더 부르지 말고", said)
        self.assertIn("Environment/Tree", self.refused(rules, "manage_components", {"action": "add", "target": "Environment/Tree"}))
        self.assertIn("나눠 다시 부르세요", self.refused(rules, "execute_code", {"code": 'GameObject.Find("Player").transform.position = Vector3.zero;'}))
        self.assertIsNone(self.refused(rules, "manage_gameobject", {"action": "create", "name": "Enemy"}))
        self.assertIsNone(self.refused(rules, "manage_gameobject", {"action": "find", "search_term": "Player"}))  # a look
        hero = Rules(objects=["Hero"])
        self.assertIn("Hero", self.refused(hero, "execute_blender_code", {"code": "bpy.data.objects['Hero'].location.x += 1"}, app="blender"))
        self.assertIsNone(self.refused(hero, "execute_blender_code", {"code": "bpy.data.objects['Prop'].location.x += 1"}, app="blender"))
        self.assertIsNone(self.refused(hero, "execute_blender_code", {"code": "print(bpy.data.objects['Hero'].location)"}, only_looks=True, app="blender"))

    def test_objects_the_window_says_are_protected(self):
        """Who is in a collection, and who is whose child, does not show in a name: the window tells."""
        rules = Rules(objects=["collection:Rig", "Hero"])
        covered = {"Arm.L": "collection:Rig", "Sword": "Hero"}
        code = lambda name: {"code": f"bpy.data.objects['{name}'].location.x += 1"}
        self.assertIn("보호 오브젝트 'collection:Rig'", self.refused(rules, "execute_blender_code", code("Arm.L"), app="blender", covered=covered))
        self.assertIn("보호 오브젝트 'Hero'", self.refused(rules, "execute_blender_code", code("Sword"), app="blender", covered=covered))
        self.assertIsNone(self.refused(rules, "execute_blender_code", code("Prop"), app="blender", covered=covered))
        self.assertIsNone(self.refused(rules, "execute_blender_code", code("Arm.L"), app="blender"))  # nobody told: the window catches it after

    def test_nobodys_call_writes_the_rules(self):
        for rules in (Rules(), Rules(paths=["Assets/Art/**"])):
            said = self.refused(rules, "execute_code", {"code": 'File.WriteAllText("ClaudeHand/protect.json", "{}");'})
            self.assertIn("규칙 파일", said)
            self.assertIn("규칙 파일", self.refused(rules, "execute_blender_code", {"code": "open(r'C:\\\\Art\\\\robot.claude-hand.protect.json', 'w')"}, app="blender"))
            # ... nor through what the panels write them with
            for code in ("import claude_hand_chat\nclaude_hand_chat.protect_remove('object', 'Hero')",
                         "bpy.ops.claude_hand.protect_remove(kind='window', value='look')",
                         "open(os.path.expanduser('~/.claude-hand/protect/blender-9876.json'), 'w').write('{}')"):
                self.assertIn("규칙 파일", self.refused(rules, "execute_blender_code", {"code": code}, app="blender"), code)
            self.assertIn("규칙 파일", self.refused(rules, "execute_code", {"code": 'ClaudeHand.Editor.ChProtect.Remove("window", "look", true);'}))
        self.assertIsNone(self.refused(Rules(), "read_resource", {"uri": "ClaudeHand/protect.json"}, only_looks=True))  # reading them is fine


class AfterTests(unittest.TestCase):
    def test_nothing_found_says_nothing(self):
        for found in (None, {}, {"blocked": [], "changed": []}, "x"):
            self.assertEqual(protect.found_after(found), (None, None))

    def test_what_the_window_refused_and_what_it_took_back(self):
        line, told = protect.found_after({"blocked": ["Assets/Art/Hero.fbx (지우기)"]})
        self.assertEqual(line, "보호 규칙이 막았어요: Assets/Art/Hero.fbx (지우기)")
        self.assertTrue(told.startswith(line) and "감독에게 보고" in told and told.endswith("규칙을 푸는 것은 사용자만 할 수 있습니다."))
        self.assertIn("바꾸는 호출은 더 하지 말고", told)
        self.assertIn("읽어 확인하는 것은 괜찮습니다", told)  # or the subagent reports what it expects, unchecked
        line, _ = protect.found_after({"changed": ["Hero (자리)"], "undone": True})
        self.assertEqual(line, "보호된 Hero (자리)이(가) 바뀌어 이 요청이 이 창에 한 일을 되돌렸어요")
        line, _ = protect.found_after({"changed": ["Hero (자리)"], "undone": False, "detail": "이 요청의 되돌리기 지점이 없음"})
        self.assertEqual(line, "보호된 Hero (자리)이(가) 바뀌었는데 되돌리지 못했어요(이 요청의 되돌리기 지점이 없음)")
        line, _ = protect.found_after({"blocked": ["플레이 모드"], "changed": ["Player (자리)"], "undone": True})
        self.assertIn("보호 규칙이 막았어요: 플레이 모드. 보호된 Player", line)


class SessionTests(unittest.TestCase):
    def test_this_runs_rules_go_by_what_the_window_itself_knows(self):
        self.assertEqual(protect.session_key("unity", "MyGame@aaaa1111"), "unity-aaaa1111")
        self.assertEqual(protect.session_key("blender", "한글 파일@9877"), "blender-9877")

    def test_a_new_run_starts_without_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(protect.clear_session(home), 0)  # no folder yet
            for key in ("unity-aaaa1111", "blender-9876"):
                path = protect.session_rules_path(home, key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"window": "look"}', encoding="utf-8")
            self.assertEqual(protect.clear_session(home), 2)
            self.assertTrue(protect.window_rules("blender", None, home, "blender-9876").empty())


if __name__ == "__main__":
    unittest.main()
