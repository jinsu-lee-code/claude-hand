"""Tests for tools/handlib/keeps.py: the files a request is about to change, kept by the guard hook so that undoing
the request can put them back. Run: python -m unittest discover -s tools/tests -v"""

import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
os.environ["CLAUDE_HAND_LANG"] = "ko"  # the words here are the Korean ones, whatever this PC is set to
from handlib import keeps  # noqa: E402


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(os.path.realpath(self.tmp.name)) / "MyGame"
        for rel, text in {"Assets/Scripts/Player.cs": "class Player {}", "Assets/Scripts/Player.cs.meta": "guid: 11",
                          "Assets/Scripts/Enemy.cs": "class Enemy {}", "Assets/Shaders/Glow.shader": "Shader {}",
                          "Assets/Materials/Red.mat": "red", "Assets/Materials/Red.mat.meta": "guid: 22",
                          "Assets/Scenes/Main.unity": "scene", "Assets/Notes.txt": "notes",
                          "Assets/Other/Player.cs": "class Player2 {}", "ProjectSettings/TagManager.asset": "tags"}.items():
            path = self.project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def named(self, tool_input):
        return keeps.named_files(self.project, tool_input)


class NamedFilesTests(ProjectCase):
    def test_the_ways_mcp_for_unity_names_a_file(self):
        script = ["Assets/Scripts/Player.cs"]
        self.assertEqual(self.named({"path": "Assets/Scripts/Player.cs", "contents": "class Player { int hp; }"}), script)
        self.assertEqual(self.named({"uri": "unity://path/Assets/Scripts/Player.cs"}), script)
        self.assertEqual(self.named({"uri": "mcpforunity://path/Assets/Scripts/Player.cs"}), script)
        self.assertEqual(self.named({"uri": "file:///" + (self.project / "Assets/Scripts/Player.cs").as_posix().lstrip("/")}), script)
        self.assertEqual(self.named({"file": str(self.project / "Assets" / "Scripts" / "Player.cs")}), script)  # the system's own separators
        self.assertEqual(self.named({"path": "Assets\\Scripts\\Player.cs"}), script)
        self.assertEqual(self.named({"path": "Scripts/Player.cs"}), script)  # completed with Assets/, as the tools do
        self.assertEqual(self.named({"path": "/Assets/Assets/Scripts/Player.cs"}), script)
        self.assertEqual(self.named({"material_path": "Assets/Materials/Red.mat", "color": [1, 0, 0, 1]}), ["Assets/Materials/Red.mat"])

    def test_a_name_next_to_its_folder_and_a_name_alone(self):
        self.assertEqual(self.named({"action": "update", "name": "Player", "path": "Assets/Scripts", "contents": "x"}),
                         ["Assets/Scripts/Player.cs"])
        self.assertEqual(self.named({"name": "Player", "path": "Other"}), ["Assets/Other/Player.cs"])  # the folder given, not the usual one
        self.assertEqual(self.named({"name": "Enemy"}), ["Assets/Scripts/Enemy.cs"])  # where a script goes by default
        self.assertEqual(self.named({"name": "Glow", "contents": "Shader {}"}), ["Assets/Shaders/Glow.shader"])
        self.assertEqual(self.named({"action": "save", "name": "Main"}), ["Assets/Scenes/Main.unity"])
        self.assertEqual(self.named({"name": "Notes.txt"}), ["Assets/Notes.txt"])

    def test_nested_input_and_json_given_as_text(self):
        batch = {"commands": [{"tool": "manage_script", "params": {"name": "Enemy", "path": "Assets/Scripts"}},
                              {"tool": "manage_material", "params": {"material_path": "Assets/Materials/Red.mat"}}]}
        self.assertEqual(self.named(batch), ["Assets/Scripts/Enemy.cs", "Assets/Materials/Red.mat"])
        self.assertEqual(self.named({"properties": json.dumps({"mainTexture": "Assets/Notes.txt"})}), ["Assets/Notes.txt"])
        self.assertEqual(self.named({"paths": ["Assets/Notes.txt", "Assets/Scripts/Enemy.cs", "Assets/Notes.txt"]}),
                         ["Assets/Notes.txt", "Assets/Scripts/Enemy.cs"])

    def test_what_is_not_a_file_to_keep(self):
        for tool_input in ({"path": "Assets/Scripts"},                      # a folder
                           {"path": "Assets"}, {"path": ""}, {"count": 3, "flag": True, "position": [0, 1, 2]},
                           {"path": "Assets/Scripts/Player.cs.meta"},     # goes along with its asset
                           {"path": "Assets/Scripts/Missing.cs"},         # a new file: nothing was there
                           {"path": "ProjectSettings/TagManager.asset"},  # outside Assets
                           {"path": "Assets/../ProjectSettings/TagManager.asset"},
                           {"path": "C:/Windows/notepad.exe"}, {"path": "//server/share/Player.cs"},
                           {"contents": "using UnityEngine;\nclass Player {}"},  # contents, not a path
                           {"contents": "Assets/Scripts/Player.cs " + "x" * 400},
                           {"query": "t:Material Red*"}, {"name": "create"}, None, "Assets/Scripts/Player.cs"):
            self.assertEqual(self.named(tool_input), [], tool_input)

    def test_only_so_many_files_for_one_call(self):
        for n in range(keeps.MAX_FILES + 5):
            (self.project / "Assets" / f"f{n}.txt").write_text("x", encoding="utf-8")
        named = self.named({"paths": [f"Assets/f{n}.txt" for n in range(keeps.MAX_FILES + 5)]})
        self.assertEqual(len(named), keeps.MAX_FILES)

    @unittest.skipUnless(os.name == "nt", "file names tell upper from lower case here")
    def test_the_path_is_spelled_as_on_disk(self):
        self.assertEqual(self.named({"path": "assets/scripts/player.cs"}), ["Assets/Scripts/Player.cs"])  # what Unity calls it


class KeepTests(ProjectCase):
    KEY = "1116-101500-7"

    def folder(self, key=KEY):
        return self.project / "Library" / "ClaudeHand" / "replaced" / f"req_{key}"

    def test_the_file_and_its_meta_are_copied_and_written_down(self):
        written = keeps.keep(self.project, self.KEY, ["Assets/Scripts/Player.cs"], now=1763260000.5)
        line = json.loads((self.folder() / "kept.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(written, [line])
        self.assertEqual(set(line), {"path", "kept", "ts"})  # what ChCheckpoint.cs reads (KeptLine)
        self.assertEqual((line["path"], line["ts"]), ("Assets/Scripts/Player.cs", 1763260000.5))
        kept = Path(line["kept"])
        self.assertEqual(kept.parent, self.folder())
        self.assertNotIn("\\", line["kept"])
        self.assertEqual((kept.read_text(encoding="utf-8"), Path(str(kept) + ".meta").read_text(encoding="utf-8")),
                         ("class Player {}", "guid: 11"))
        self.assertEqual((kept.suffix, kept.name.startswith("Player_")), (".cs", True))

    def test_once_per_file_and_request_so_the_copy_is_the_file_before_the_request(self):
        keeps.keep(self.project, self.KEY, ["Assets/Scripts/Player.cs"])
        (self.project / "Assets/Scripts/Player.cs").write_text("class Player { int hp; }", encoding="utf-8")  # the call's doing
        self.assertEqual(keeps.keep(self.project, self.KEY, ["Assets/Scripts/Player.cs", "Assets/Scripts/Enemy.cs"])[0]["path"],
                         "Assets/Scripts/Enemy.cs")
        kept = keeps.read_kept(self.project, self.KEY)
        self.assertEqual([entry["path"] for entry in kept], ["Assets/Scripts/Player.cs", "Assets/Scripts/Enemy.cs"])
        self.assertEqual(Path(kept[0]["kept"]).read_text(encoding="utf-8"), "class Player {}")
        # The next request starts over: its copy is the file as that request found it.
        later = keeps.keep(self.project, "1116-102000-9", ["Assets/Scripts/Player.cs"])
        self.assertEqual(Path(later[0]["kept"]).read_text(encoding="utf-8"), "class Player { int hp; }")
        self.assertEqual(Path(later[0]["kept"]).parent, self.folder("1116-102000-9"))

    def test_two_files_of_one_name_and_files_that_cannot_be_kept(self):
        written = keeps.keep(self.project, self.KEY, ["Assets/Scripts/Player.cs", "Assets/Other/Player.cs", "Assets/Gone.cs"], now=1763260000.0)
        self.assertEqual([entry["path"] for entry in written], ["Assets/Scripts/Player.cs", "Assets/Other/Player.cs"])
        self.assertEqual(len({entry["kept"] for entry in written}), 2)
        self.assertEqual(Path(written[1]["kept"]).read_text(encoding="utf-8"), "class Player2 {}")
        with unittest.mock.patch.object(keeps, "MAX_BYTES", 3):
            self.assertEqual(keeps.keep(self.project, self.KEY, ["Assets/Notes.txt"]), [])  # too large to copy before every call
        with unittest.mock.patch.object(keeps.shutil, "copy2", side_effect=PermissionError("in use")):
            self.assertEqual(keeps.keep(self.project, self.KEY, ["Assets/Notes.txt"]), [])  # never raises: the call goes ahead

    def test_the_folder_name_is_the_one_the_unity_package_makes(self):
        """ChPaths.Sanitize in ChCommon.cs: the package keeps what Unity saves in the same folder and reads kept.jsonl there."""
        self.assertEqual(keeps.sanitize("req_1116-101500-7"), "req_1116-101500-7")
        self.assertEqual(keeps.sanitize("req_local-1763260000123"), "req_local-1763260000123")
        self.assertEqual(keeps.sanitize("req_큐브 a/b"), "req__a_b")
        self.assertEqual(keeps.sanitize("__x__"), "x")
        self.assertEqual(keeps.sanitize("a" * 60), "a" * 48)
        self.assertEqual(keeps.sanitize("큐브"), "object")
        self.assertEqual(keeps.request_folder(self.project, self.KEY), self.folder())

    def test_the_newest_requests_are_kept(self):
        for n in range(keeps.KEPT_REQUESTS + 2):
            keeps.keep(self.project, f"r{n}", ["Assets/Notes.txt"])
            time.sleep(0.02)  # folders are told apart by when they were made
        left = sorted(p.name for p in self.folder().parent.iterdir())
        self.assertEqual(len(left), keeps.KEPT_REQUESTS)
        self.assertNotIn("req_r0", left)
        self.assertNotIn("req_r1", left)
        self.assertIn(f"req_r{keeps.KEPT_REQUESTS + 1}", left)

    def test_a_journal_with_a_broken_line(self):
        keeps.keep(self.project, self.KEY, ["Assets/Notes.txt"])
        with open(self.folder() / "kept.jsonl", "a", encoding="utf-8") as journal:
            journal.write('{"path": "Assets/Scr\n[1, 2]\n{"path": "Assets/x.txt"}\n')
        self.assertEqual([entry["path"] for entry in keeps.read_kept(self.project, self.KEY)], ["Assets/Notes.txt"])
        self.assertEqual(keeps.read_kept(self.project, "never"), [])


class WorkTests(ProjectCase):
    def test_which_request_is_at_work(self):
        self.assertEqual(keeps.read_work(self.project), {})
        keeps.begin_work(self.project, "1116-101500-7", now=100.0)
        self.assertEqual(json.loads((self.project / "Library/ClaudeHand/at-work.json").read_text(encoding="utf-8")),
                         {"request": "1116-101500-7", "ts": 100.0, "stopped": 0})  # what ChCheckpoint.cs reads (Work)
        keeps.end_work(self.project, now=130.0)
        keeps.end_work(self.project, now=140.0)  # a second Stop: the first one counts
        self.assertEqual(keeps.read_work(self.project)["stopped"], 130.0)
        keeps.begin_work(self.project, "1116-101500-7", now=150.0)  # its next turn: at work again
        self.assertEqual(keeps.read_work(self.project), {"request": "1116-101500-7", "ts": 150.0, "stopped": 0})
        self.assertEqual([p.name for p in (self.project / "Library/ClaudeHand").iterdir()], ["at-work.json"])  # no temp file left

    def test_nothing_to_stop_and_an_unreadable_file(self):
        keeps.end_work(self.project, now=5.0)
        self.assertFalse((self.project / "Library/ClaudeHand/at-work.json").exists())
        (self.project / "Library/ClaudeHand").mkdir(parents=True)
        (self.project / "Library/ClaudeHand/at-work.json").write_text("{half", encoding="utf-8")
        self.assertEqual(keeps.read_work(self.project), {})
        (self.project / "Library/ClaudeHand/at-work.json").write_text("[1]", encoding="utf-8")
        self.assertEqual(keeps.read_work(self.project), {})

    def test_a_name_for_the_request_when_no_channel_gives_one(self):
        first = keeps.local_key({}, 1000.0)
        self.assertEqual(first, "local-1000000")
        going = {"request": first, "ts": 1000.0, "stopped": 1010.0}
        self.assertEqual(keeps.local_key(going, 1000.0 + keeps.WORK_SECONDS), first)  # the same work, also over its turns
        self.assertNotEqual(keeps.local_key(going, 1001.0 + keeps.WORK_SECONDS), first)  # a pause: the next request
        self.assertNotEqual(keeps.local_key(going, 1005.0, fresh=True), first)  # the subagent set an undo point: a new request
        self.assertTrue(keeps.local_key({"request": "1116-101500-7", "ts": 1000.0}, 1001.0).startswith("local-"))  # not the channel's


if __name__ == "__main__":
    unittest.main()
