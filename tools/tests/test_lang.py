"""The interface in two languages: every Korean text of the code has its English, and English comes out when asked.

The Korean text stands in the code and is what T() is given (handlib/lang.py); the English is in a table per
implementation: handlib/english.py for the Python tools, tools/guard.py's own for the guard hook, ChText.cs for the
Unity package and the Blender add-on's own (at its end). These tests read the sources: a Korean text that is not
marked for translation, or is marked and has no English, fails here, and so does one that two tables say differently.
The instructions Claude is given (prompts/) are checked the same way: their English edition line for line against
the Korean one, and the table of names Korean instructions get under an English interface against the tables.
"""

import ast
import io
import json
import os
import re
import string
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CLAUDE_HAND_LOG"] = "off"
os.environ["CLAUDE_HAND_LANG"] = "ko"  # each test that wants English says so

import guard  # noqa: E402
import supervisor_hook  # noqa: E402
from handlib import agents, chatsetup, checks, discovery, english, eventlog, lang, overview, prefs, protect, usage  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "blender" / "claude_hand_chat.py"
UNITY = ROOT / "unity" / "com.claudehand.tools" / "Editor"
HANGUL = re.compile("[\uac00-\ud7a3]")
MARKS = ("T", "N_", "P")  # T: said here. N_: a constant, said through T elsewhere. P: a piece of a prompt (handlib/agents.py)

# Korean in the code that is not said to anybody: what code reads back (a tag Claude writes, a file an earlier
# version wrote), words a command takes, a language's own name.
NOT_SAID = {
    "tools/supervisor_hook.py": {r"\[\s*(?:요청|request)\s*[:：]?\s*(없음|none|\d{4}-\d{6}-\d+)\s*\]", "없음"},
    "tools/handlib/usage.py": {r"\[\s*(?:요청|request)\s*[:：]?\s*(없음|none|\d{4}-\d{6}-\d+)\s*\]", "없음",
                               "천", "만", "억"},  # count(): Korean counts by ten thousands, so English has its own sums there
    "tools/handlib/agents.py": {r"파일 `([^`]*)`, MCP for Blender 포트", "저장 안 된 파일", r"\(경로: `([^`]*)`\)"},
    "tools/handlib/prefs.py": {"켜기", "켬", "끄기", "끔", "한국어"},
    "tools/handlib/checks.py": {"반영"},  # selftest reads the Blender panel's line of a run it starts in Korean
    "blender/claude_hand_chat.py": {"가a", "언어 · Language", "한국어"},  # a text to measure the font with; the language's row
}
# ... and in the Unity package: the self-test's expectations (it runs the package in Korean), the words of what
# changed of an object (two lists there, see ChProtect.PrintWords), a tool's description for Claude (an attribute).
NOT_SAID_UNITY = {
    "ChSelfTest.cs": None,  # all of it
    "ChProtect.cs": {"이름", "켜짐", "자리", "자식", "컴포넌트", "값"},
    "ChSettings.cs": {"언어 · Language", "한국어"},
    "ChImportModel.cs": {"request (ch_checkpoint undo, the chat window's 되돌리기) takes the files back too: a model that came "},
    "ChImportPackage.cs": {"Undoing the request (ch_checkpoint undo, the chat window's 되돌리기) takes the files back too: what the "},
    "ChModelFollow.cs": {"Blender's panel only, for update: what made it send, in the user's words (저장, 되돌리기, ...)"},
}
# English that keeps a Korean word on purpose: the tag the Korean instructions have the supervisor write.
KOREAN_IN_ENGLISH = ("[요청 <id>]", "[요청 없음]", "[요청 {tag}]")
# Entries no T("...") in the code names: looked up with a text read from a file.
READ_FROM_FILES = {"skills/orchestrator/SKILL.md": r"^description: (.+)$"}


def names(text: str) -> list[str]:
    """The {names} of a text, with their conversions and formats: both languages must fill in the same."""
    return sorted(f"{field}!{conversion or ''}:{spec or ''}" for _, field, spec, conversion in string.Formatter().parse(text) if field)


ONES: dict[str, dict] = {}  # file -> its table of English wordings for a count of one: {korean: (the count's name, the text)}


def read_python(path: Path) -> tuple[set[str], list[tuple[int, str]], dict]:
    """(the texts marked for translation, the Korean texts that are not, the table when the file holds one):
    docstrings and the table itself aside."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip, marked, table = set(), set(), {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            skip.add(id(node.body[0].value))
        if isinstance(node, (ast.Assign, ast.AnnAssign)):  # EN = {...}: the table
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in ("EN", "_EN") for t in targets) and node.value is not None:
                skip.update(id(inner) for inner in ast.walk(node.value))
                table = ast.literal_eval(node.value)
            if any(isinstance(t, ast.Name) and t.id in ("EN_ONE", "_EN_ONE") for t in targets) and node.value is not None:
                skip.update(id(inner) for inner in ast.walk(node.value))  # the wordings for one of a thing: part of the table
                ONES[path.name] = ast.literal_eval(node.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in MARKS and node.args and \
                isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            marked.add(node.args[0].value)
            skip.add(id(node.args[0]))
    allowed = NOT_SAID.get(path.relative_to(ROOT).as_posix(), set())
    loose = [(node.lineno, node.value) for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and HANGUL.search(node.value)
             and id(node) not in skip and node.value not in allowed]
    return marked, loose, table


CS_LITERAL = r'"(?:[^"\\]|\\.)*"'
CS_JOINED = CS_LITERAL + r"(?:\s*\+\s*" + CS_LITERAL + r")*"
CS_MARKED = re.compile(r"(?<![A-Za-z0-9_])(?:ChText\.)?[TN]\(\s*(" + CS_JOINED + r")")
CS_ENTRY = re.compile(r"\{\s*(" + CS_JOINED + r")\s*,\s*(" + CS_JOINED + r")\s*\}")
CS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "0": "\0"}


def cs_value(literals: str) -> str:
    """What one or several C# string literals joined by + say."""
    out = ""
    for found in re.finditer(CS_LITERAL, literals):
        body, i = found.group(0)[1:-1], 0
        while i < len(body):
            if body[i] == "\\" and body[i + 1] == "u":
                out, i = out + chr(int(body[i + 2:i + 6], 16)), i + 6
            elif body[i] == "\\":
                out, i = out + CS_ESCAPES[body[i + 1]], i + 2
            else:
                out, i = out + body[i], i + 1
    return out


def cs_code(path: Path) -> str:
    """A C# file without its comments (a line of its own, or after the code of a line)."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("//"):
            continue
        cut = re.match(r"^((?:[^\"/]|" + CS_LITERAL + r"|/(?!/))*)//.*$", line)
        lines.append(cut.group(1) if cut else line)
    return "\n".join(lines)


def read_unity() -> tuple[set[str], list[tuple[str, str]], dict]:
    """(the texts marked for translation, the Korean texts that are not, ChText.cs's table)."""
    marked, loose = set(), []
    for path in sorted(UNITY.glob("*.cs")):
        if path.name == "ChText.cs":
            continue
        code = cs_code(path)
        allowed = NOT_SAID_UNITY.get(path.name, set())
        if allowed is None:
            continue
        marked |= {cs_value(found.group(1)) for found in CS_MARKED.finditer(code)}
        rest = CS_MARKED.sub("", code)  # what is left has no marked text in it
        loose += [(path.name, cs_value(found.group(0))) for found in re.finditer(r"\$?" + CS_LITERAL, rest)
                  if HANGUL.search(found.group(0)) and cs_value(found.group(0)) not in allowed]
    table_code = cs_code(UNITY / "ChText.cs")
    table = {cs_value(found.group(1)): cs_value(found.group(2)) for found in CS_ENTRY.finditer(table_code[table_code.index("English = new"):])}
    one = re.compile(r"\{\s*(" + CS_JOINED + r")\s*,\s*new KeyValuePair<int, string>\((\d+),\s*(" + CS_JOINED + r")\)\s*\}")
    ONES["ChText.cs"] = {cs_value(found.group(1)): (found.group(2), cs_value(found.group(3))) for found in one.finditer(table_code)}
    return marked, loose, table


def python_sources(*paths: Path) -> tuple[set[str], list, dict]:
    marked, loose, table = set(), [], {}
    for path in paths:
        here, left, found = read_python(path)
        marked |= here
        loose += [(path.name, *row) for row in left]
        table = found or table
    return marked, loose, table


TOOLS = sorted(p for p in [*(ROOT / "tools").glob("*.py"), *(ROOT / "tools" / "handlib").glob("*.py")] if p.name not in ("guard.py", "english.py"))
# table -> (the texts its code marks, the Korean it leaves unmarked, its entries)
TABLES = {
    "tools": (*python_sources(*TOOLS)[:2], english.EN),
    "guard": (*python_sources(ROOT / "tools" / "guard.py")[:2], guard.EN),
    "blender": python_sources(ADDON),
    "unity": read_unity(),
}
ONES["english.py"] = english.EN_ONE  # the others were found while their files were read


class EveryTextHasItsEnglish(unittest.TestCase):
    def test_the_tables_are_read(self):
        self.assertGreater(len(TABLES["tools"][2]), 400)
        self.assertGreater(len(TABLES["blender"][2]), 200)
        self.assertGreater(len(TABLES["unity"][2]), 120)
        self.assertGreater(len(TABLES["guard"][2]), 10)

    def test_no_korean_text_is_left_unmarked(self):
        for table, (_, loose, _) in TABLES.items():
            self.assertEqual(loose, [], f"{table}: wrap these in T(...) (N_ / N for a constant) and give them English in its table")

    def test_every_marked_text_is_in_its_table_and_no_entry_is_left_over(self):
        for table, (marked, _, entries) in TABLES.items():
            missing = sorted(text for text in marked if HANGUL.search(text) and text not in entries)
            self.assertEqual(missing, [], f"no English in the {table} table")
            read = {re.search(pattern, (ROOT / name).read_text(encoding="utf-8"), re.M).group(1) for name, pattern in READ_FROM_FILES.items()} \
                if table == "tools" else set()
            self.assertEqual(sorted(set(entries) - marked - read), [], f"the {table} table has entries nothing says any more")

    def test_both_languages_fill_in_the_same_names(self):
        for table, (_, _, entries) in TABLES.items():
            for korean, said in entries.items():
                self.assertEqual(names(korean), names(said), f"{table}: {korean[:60]}")
                self.assertTrue(said.strip() or not korean.strip(), korean)

    def test_the_english_has_no_korean_left_in_it(self):
        for table, (_, _, entries) in TABLES.items():
            for korean, said in entries.items():
                rest = said
                for kept in KOREAN_IN_ENGLISH:
                    rest = rest.replace(kept, "")
                self.assertIsNone(HANGUL.search(rest), f"{table}: {said[:80]}")

    def test_a_text_two_tables_say_is_the_same_in_both(self):
        for one in TABLES:
            for other in TABLES:
                for korean in set(TABLES[one][2]) & set(TABLES[other][2]):
                    self.assertEqual(TABLES[one][2][korean], TABLES[other][2][korean], f"{korean[:60]} ({one} / {other})")

    def test_one_of_a_thing_has_a_wording_of_its_own(self):
        """Korean has no plural; English has a second wording for the texts that count, for when the count is one."""
        tables = {"english.py": TABLES["tools"][2], "claude_hand_chat.py": TABLES["blender"][2], "ChText.cs": TABLES["unity"][2]}
        self.assertEqual(sorted(ONES), sorted(tables))
        for file, ones in ONES.items():
            self.assertTrue(ones, file)
            for korean, (count, said) in ones.items():
                self.assertIn(korean, tables[file], f"{file}: a wording for one of a text the table does not have")
                self.assertEqual(names(said), names(tables[file][korean]), f"{file}: {korean[:60]}")
                self.assertIn(str(count), [name.split("!")[0] for name in names(korean)], f"{file}: {korean[:60]} has no {{{count}}}")
                self.assertNotEqual(said, tables[file][korean])
                self.assertIsNone(HANGUL.search(said), said)
        for korean in set(ONES["english.py"]) & set(ONES["claude_hand_chat.py"]):  # a text two tables say: the same in both
            self.assertEqual(ONES["english.py"][korean], ONES["claude_hand_chat.py"][korean])
        self.assertEqual(ONES["ChText.cs"]["{0}일"][1].replace("{0}", "{days}"), ONES["english.py"]["{days}일"][1])

    def test_the_settings_and_the_lab_are_named_in_both_languages(self):
        catalog = prefs.catalog()
        self.assertTrue(catalog["settings"] and catalog["lab"])
        for entry in catalog["settings"] + catalog["lab"]:
            for part in ("name", "hint"):
                both = entry.get(part) or {}
                self.assertTrue(both.get("ko") and both.get("en"), (entry["key"], part))
                self.assertIsNone(HANGUL.search(both["en"].replace("언어 · Language", "")), (entry["key"], part))


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"CLAUDE_HAND_HOME": str(self.home)})
        self.env.start()
        os.environ.pop("CLAUDE_HAND_LANG", None)  # these go by config.json

    def tearDown(self):
        self.env.stop()
        os.environ["CLAUDE_HAND_LANG"] = "ko"
        self.tmp.cleanup()

    def say(self, language):
        prefs.put("language", language, self.home)

    def test_korean_is_the_text_as_written_and_what_is_used_when_nothing_says_otherwise(self):
        self.assertEqual(prefs.language(), "ko")  # no config.json at all
        self.assertEqual(lang.T("되돌렸어요"), "되돌렸어요")
        self.assertEqual(lang.T("경로 {count}", count=2), "경로 2")
        (self.home / "config.json").write_text(json.dumps({"language": "fr"}), encoding="utf-8")
        self.assertEqual(prefs.language(), "ko")  # a word it does not know

    def test_english_is_said_once_the_setting_says_so_without_anything_starting_again(self):
        self.assertEqual(protect.in_words(protect.Rules(window="look", paths=["a", "b"])), "보기만 · 경로 2")
        self.say("en")
        self.assertEqual(protect.in_words(protect.Rules(window="look", paths=["a", "b"])), "Look only · paths: 2")
        self.assertEqual(overview.duration(75), "1m 15s")
        self.assertEqual(usage.line(32, 2, {usage.SUPERVISOR: {"calls": 3, "output": 2900, "input": 4500, "cached": 660000}}),
                         "32s · 2 actions · tokens output 2.9k · new input 4.5k · read again 660k")
        self.assertEqual(usage.detail({usage.SUPERVISOR: {"calls": 3, "output": 2900, "input": 4500, "cached": 1_500_000, "seconds": 12}}),
                         "supervisor 12s API 3 calls output 2.9k · new input 4.5k · read again 1.5M")
        self.say("ko")
        self.assertEqual(overview.duration(75), "1분 15초")

    def test_the_variable_wins_over_the_setting(self):
        self.say("en")
        with mock.patch.dict(os.environ, {"CLAUDE_HAND_LANG": "ko"}):
            self.assertEqual(lang.T("되돌렸어요"), "되돌렸어요")
            self.assertEqual(guard.language(), "ko")
        self.assertEqual(lang.T("되돌렸어요"), "undone")
        self.assertEqual(guard.language(), "en")

    def test_one_of_a_thing_is_said_in_the_singular(self):
        self.assertEqual(lang.T(" · 동작 {actions}번", actions=1), " · 동작 1번")  # Korean: one text for any count
        prefs.put("language", "en", self.home)
        self.assertEqual(lang.T(" · 동작 {actions}번", actions=1), " · 1 action")
        self.assertEqual(lang.T(" · 동작 {actions}번", actions=2), " · 2 actions")
        self.assertEqual(lang.T("{days}일", days=1), "1 day")
        self.assertEqual(lang.T("{days}일", days=30), "30 days")
        self.assertEqual(lang.T("{days}일", days=True), "True days")  # a yes is not a count of one
        self.assertEqual(usage.line(3.2, 1, {}), "3s · 1 action")

    def test_a_text_without_english_comes_out_in_korean(self):
        self.say("en")
        self.assertEqual(lang.T("아직 번역이 없는 글 {n}", n=1), "아직 번역이 없는 글 1")
        self.assertEqual(guard.T("아직 번역이 없는 글 {n}", n=1), "아직 번역이 없는 글 1")

    def test_refusals_come_out_in_english(self):
        self.say("en")
        rules = protect.Rules(paths=["Assets/Art/**"], actions=["save_scene"])
        said = protect.refusal("unity", rules, "manage_material", {"action": "set_color", "path": "Assets/Art/Hero.mat"}, False)
        self.assertTrue(said.startswith("A protection rule refused this call: protected path 'Assets/Art/**' (manage_material: Assets/Art/Hero.mat). "))
        self.assertTrue(said.endswith("Only the user can release a rule."))
        self.assertEqual(protect.panel_line(said), "A protection rule stopped it: protected path 'Assets/Art/**' (manage_material: Assets/Art/Hero.mat)")
        self.assertIn("forbidden action 'Save scene'", protect.refusal("unity", rules, "manage_scene", {"action": "save"}, False))
        line, told = protect.found_after({"blocked": ["Play mode"], "changed": ["Hero (position)"], "undone": True})
        self.assertEqual(line, "A protection rule stopped it: Play mode. Protected Hero (position) was changed, so what this request did in this window was undone")
        self.assertIsNone(HANGUL.search(told))
        self.assertEqual(protect.proposal_words("window", "look", False), "Look only in this window (read, change nothing) — this run only (until Claude Hand is closed)")
        # ... the guard's own, which it reads config.json for by itself
        event = {"tool_name": "mcp__unity_other__manage_scene", "tool_input": {}}
        self.assertEqual(guard.guard_decision(event, "MyGame@aaaa1111", "unity_mygame", "stdio"),
                         "This subagent works for MyGame@aaaa1111 only. Of the Unity tools it may use mcp__unity_mygame__* alone.")
        event = {"tool_name": "mcp__unity_mygame__manage_scene", "tool_input": {"unity_instance": "Other@bbbb2222"}}
        self.assertEqual(guard.guard_decision(event, "MyGame@aaaa1111", "unity_mygame", "stdio"),
                         "unity_instance='Other@bbbb2222' is not this subagent's project (MyGame@aaaa1111). Call without unity_instance.")
        # ... and the supervisor's hook
        hand = {"tool_name": "Agent", "tool_input": {"subagent_type": "unity-mygame", "prompt": "no tag"}}
        said = supervisor_hook.decision(hand, lambda path, body: {"ours": True, "chat_id": "unity-mygame", "missing": True})
        self.assertTrue(said.startswith("The work handed to unity-mygame does not say which chat panel request it is for"))
        self.assertIsNone(HANGUL.search(said.replace("[요청 <id>]", "").replace("[요청 없음]", "")))

    def test_a_refusal_is_read_back_in_the_language_it_was_said_in(self):
        korean = protect.refusal("unity", protect.Rules(window="look"), "manage_scene", {"action": "save"}, False)
        self.say("en")  # the setting changed between the refusal and the panel's line
        self.assertEqual(protect.panel_line(korean), "보호 규칙이 막았어요: 이 창은 보기만 합니다(보기만) (manage_scene: 바꾸는 호출)")

    def test_the_request_tag_is_read_in_both_languages(self):
        for text, want in (("[요청 1111-052233-12] 큐브", "1111-052233-12"), ("[request 1111-052233-12] a cube", "1111-052233-12"),
                           ("[요청 없음] x", ""), ("[request none] x", ""), ("[Request: None]", ""), ("no tag", None)):
            self.assertEqual(supervisor_hook.tag_of(text), want, text)
            self.assertEqual(usage.tagged(text), want, text)

    def test_commands_print_english(self):
        import claude_hand
        self.say("en")
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(claude_hand.link, "channel_get", lambda path: None):
            claude_hand.cmd_status(claude_hand.build_parser().parse_args(["status"]))
            checks.print_checks([checks.Check(lang.T("채팅"), lang.T("감독 스킬"), False, lang.T("없음"), "uv run tools/claude_hand.py sync")])
        self.assertIn("Claude Hand is off.", out.getvalue())
        self.assertIn("supervisor skill", out.getvalue())
        self.assertIsNone(HANGUL.search(out.getvalue()), out.getvalue())
        for kind in eventlog.LABELS:
            self.assertIsNone(HANGUL.search(eventlog.describe({"ts": 1.0, "event": kind, "text": "x"})), kind)
        self.assertIsNone(HANGUL.search("\n".join(chatsetup.chat_status_report())))


def quoted(text: str) -> list[str]:
    """The names and pieces of texts a row of the glossary quotes: what is in quotes, brackets or backticks (the
    whole of it when nothing is), cut where the row leaves something out."""
    found = re.findall(r'"([^"]+)"|\[([^\]]+)\]|`([^`]+)`', text)
    whole = [next(part for part in group if part) for group in found] or [text]
    pieces = [piece.strip(" []`") for one in whole for piece in re.split(r"…|\.\.\.| · |, ", one)]
    return [piece for piece in pieces if len(piece) >= 3]


class InstructionTests(unittest.TestCase):
    """What Claude is given: Korean instructions as they were, the names of an English interface added to them, and
    the English edition for the lab's "english_prompts"."""

    PAIRS = [("prompts/" + name, "prompts/en/" + name) for name in ("blender-subagent.md", "unity-subagent.md", "chat-rules.md", "notes-section.md")] \
        + [("skills/orchestrator/SKILL.md", "skills/orchestrator/SKILL.en.md")]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"CLAUDE_HAND_HOME": str(self.home)})
        self.env.start()
        os.environ.pop("CLAUDE_HAND_LANG", None)
        self.unity = discovery.Instance(name="MyGame", hash="aaaa1111", path="C:/Games/MyGame")
        self.blender = discovery.BlenderInstance(port=9876, filepath="C:/Art/props.blend")

    def tearDown(self):
        self.env.stop()
        os.environ["CLAUDE_HAND_LANG"] = "ko"
        self.tmp.cleanup()

    def files(self):
        server = discovery.ServerConfig(transport="stdio", command="uvx", args=["mcp-for-unity"], key="unityMCP")
        return agents.build_agent_files([self.unity], server, [self.blender], None, "uv run -q --no-project")

    def test_the_english_edition_follows_the_korean_one_line_for_line(self):
        marker = lambda line: re.match(r"\s*(#+ |- |\d+\. |```)?", line).group(0)
        for korean, said in self.PAIRS:
            ko, en = [(ROOT / name).read_text(encoding="utf-8").splitlines() for name in (korean, said)]
            self.assertEqual(len(ko), len(en), said)
            for number, (one, other) in enumerate(zip(ko, en), 1):
                where = f"{said}:{number}"
                self.assertEqual(re.findall(r"\{\{\??\w+\}\}", one), re.findall(r"\{\{\??\w+\}\}", other), where)
                self.assertEqual(marker(one), marker(other), where)
                self.assertEqual(bool(one.strip()), bool(other.strip()), where)
                self.assertIsNone(HANGUL.search(other), where)
                for code in re.findall(r"`[^`]+`", one):
                    if not HANGUL.search(code):
                        self.assertIn(code, other, where)

    def test_korean_instructions_are_as_they_were_whatever_the_lab_says(self):
        before = self.files()
        prefs.put_lab("english_prompts", True, self.home)  # the lab's edition is for an English interface only
        self.assertEqual(self.files(), before)
        self.assertEqual(agents.glossary(), "")
        self.assertEqual(agents.prompt_language(), "ko")
        self.assertIn("너는 Unity 프로젝트", before["unity-mygame.md"])
        self.assertIn("description: \"Unity 프로젝트 MyGame 전담 작업자", before["unity-mygame.md"])

    def test_an_english_interface_adds_its_names_to_the_korean_instructions(self):
        prefs.put("language", "en", self.home)
        files = self.files()
        unity, blender = files["unity-mygame.md"], files["blender-props.md"]
        self.assertIn('description: "Dedicated worker for the Unity project MyGame (MyGame@aaaa1111).', unity)  # what /agents lists
        self.assertIn("너는 Unity 프로젝트 **MyGame** (경로: `C:/Games/MyGame`) 하나만 맡는 작업자다.", unity)  # the instructions themselves
        self.assertIn("## 이 프로젝트 메모", unity)
        for text in (unity, blender, agents.rendered_skill()):
            self.assertIn("## 화면 언어: English", text)
            self.assertIn("- [되돌리기] → [Undo]", text)
            self.assertTrue(text.endswith("\n"))
        self.assertIn("description: Supervisor rules for working with several Unity projects", agents.rendered_skill())
        self.assertEqual(agents.read_agent(unity).pin, "MyGame@aaaa1111")  # still read back as that window's
        self.assertEqual(agents.read_agent(blender).path, "C:/Art/props.blend")

    def test_the_lab_gives_english_instructions_to_an_english_interface(self):
        prefs.put("language", "en", self.home)
        prefs.put_lab("english_prompts", True, self.home)
        self.assertEqual(agents.prompt_language(), "en")
        files = self.files()
        skill = agents.rendered_skill(Path("/opt/claude-hand"))
        for text in (*files.values(), skill):
            body = text.split("-->", 1)[-1]
            self.assertIsNone(HANGUL.search(body), body[:300])
            self.assertNotIn("{{", text)
        self.assertIn("(path: `C:/Games/MyGame`)", files["unity-mygame.md"])
        self.assertIn("[request <id>]", skill)
        self.assertIn('uv run "/opt/claude-hand/tools/claude_hand.py" sync', skill)
        self.assertEqual(agents.glossary(), "")

    def test_the_glossary_names_what_the_instructions_and_the_tables_say(self):
        korean = "\n".join((ROOT / name).read_text(encoding="utf-8") for name, _ in self.PAIRS)
        said = "\n".join(value for _, _, entries in TABLES.values() for value in entries.values()).lower()
        said += said.replace("{app}", "unity") + said.replace("{app}", "blender")  # "[{app} window info: ...]" as it is sent
        for ko, en in english.GLOSSARY:
            for piece in quoted(ko):
                self.assertIn(piece, korean, f"the Korean instructions do not say {piece!r} (glossary row {ko!r})")
            for piece in quoted(en):
                self.assertIn(piece.lower(), said, f"no table says {piece!r} (glossary row {en!r})")
            self.assertIsNone(HANGUL.search(en), en)


if __name__ == "__main__":
    unittest.main()
