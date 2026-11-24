"""Tests for tools/chat_channel.py. Run: python -m unittest discover -s tools/tests -v"""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CLAUDE_HAND_LOG"] = "off"  # the tests leave the real event log alone
import chat_channel as cc  # noqa: E402
import handlib.agents  # noqa: E402
import handlib.discovery  # noqa: E402

TOOL = Path(__file__).resolve().parents[1] / "chat_channel.py"


def write_agents(directory: Path, more_unity=()) -> None:
    """Agent files exactly as `claude_hand.py sync` writes them."""
    unity = [handlib.discovery.Instance(name="MyGame", hash="aaaa1111"), *more_unity]
    blender = handlib.discovery.BlenderInstance(port=9877, filepath="C:/Art/props.blend")
    files = handlib.agents.build_agent_files(unity, handlib.discovery.ServerConfig(transport="stdio"), [blender], None, "uv run")
    handlib.agents.write_agent_files(files, directory)


class Recorder:
    def __init__(self):
        self.messages = []

    def __call__(self, message):
        self.messages.append(message)


class RoutingTests(unittest.TestCase):
    def test_reads_generated_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_agents(Path(tmp))
            (Path(tmp) / "mine.md").write_text("---\nname: mine\n---\nhand written\n", encoding="utf-8")
            agents = handlib.agents.installed_agents(Path(tmp))
        self.assertEqual(sorted((a.name, a.app, a.pin) for a in agents),
                         [("blender-props", "blender", "props@9877"), ("unity-mygame", "unity", "MyGame@aaaa1111")])

    def test_resolve_window(self):
        agents = [handlib.agents.AgentInfo("unity-mygame", "unity", "MyGame@aaaa1111"), handlib.agents.AgentInfo("blender-props", "blender", "props@9877")]
        self.assertEqual(cc.resolve_window(agents, "unity", "MyGame"), ("unity-mygame", "unity-mygame"))
        self.assertEqual(cc.resolve_window(agents, "blender", "props", "props@9877"), ("blender-props", "blender-props"))
        self.assertEqual(cc.resolve_window(agents, "blender", "props", "props@9999"), ("blender-props", None))
        self.assertEqual(cc.resolve_window(agents, "unity", "Other Game"), ("unity-other-game", None))

    def test_same_project_name_twice_is_not_guessed(self):
        agents = [handlib.agents.AgentInfo("unity-mygame-aaaa", "unity", "MyGame@aaaa1111"), handlib.agents.AgentInfo("unity-mygame-cccc", "unity", "MyGame@cccc3333")]
        self.assertEqual(cc.resolve_window(agents, "unity", "MyGame")[1], None)
        self.assertEqual(cc.resolve_window(agents, "unity", "MyGame", "MyGame@cccc3333")[1], "unity-mygame-cccc")


class HubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_agents(Path(self.tmp.name))
        self.emitted = Recorder()
        self.hub = cc.ChatHub(self.emitted, Path(self.tmp.name))
        self.hub.initialized = True

    def tearDown(self):
        self.tmp.cleanup()

    def test_user_message_becomes_channel_notification(self):
        result = self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘", context="선택: Player")
        self.assertEqual(result, {"chat_id": "unity-mygame", "agent": "unity-mygame", "delivered": True})
        note = self.emitted.messages[-1]
        self.assertEqual(note["method"], "notifications/claude/channel")
        request = note["params"]["meta"].pop("request")  # the id the supervisor puts on the work it hands out for this message
        self.assertRegex(request, r"^\d{4}-\d{6}-\d+$")
        self.assertEqual(self.hub.logs["unity-mygame"][-1]["request"], request)
        self.assertEqual(note["params"]["meta"], {"chat_id": "unity-mygame", "app": "unity", "window": "MyGame", "agent": "unity-mygame"})
        self.assertTrue(all(k.isidentifier() for k in note["params"]["meta"]))  # hyphenated keys are dropped by Claude Code
        self.assertIn("큐브 만들어줘", note["params"]["content"])
        self.assertIn("선택: Player", note["params"]["content"])

    def test_reply_reaches_only_that_chat(self):
        self.hub.post_user_message("unity", "MyGame", "hi")
        self.hub.post_user_message("blender", "props", "hi", pin="props@9877")
        self.assertTrue(self.hub.reply("unity-mygame", "done"))
        self.assertFalse(self.hub.reply("unity-unknown", "x"))
        unity = [i["kind"] for i in self.hub.updates("unity-mygame", 0, 0)]
        blender = [i["kind"] for i in self.hub.updates("blender-props", 0, 0)]
        self.assertEqual((unity, blender), (["user", "reply"], ["user"]))

    def test_long_poll_wakes_on_reply(self):
        self.hub.post_user_message("unity", "MyGame", "hi")
        after = self.hub.seq
        threading.Timer(0.2, lambda: self.hub.reply("unity-mygame", "late")).start()
        started = time.time()
        items = self.hub.updates("unity-mygame", after, 5)
        self.assertLess(time.time() - started, 3)
        self.assertEqual([i["text"] for i in items], ["late"])

    def test_permission_relay(self):
        self.hub.updates("blender-props", 0, 0)  # an open panel that has not sent anything yet
        self.hub.post_user_message("unity", "MyGame", "hi")
        self.hub.permission_request({"request_id": "abcde", "tool_name": "Bash", "description": "run tests", "input_preview": "npm test"})
        for chat in ("unity-mygame", "blender-props"):
            self.assertEqual([i["kind"] for i in self.hub.updates(chat, 0, 0)][-1], "permission")
        self.assertFalse(self.hub.permission_verdict("abcde", "maybe"))
        self.assertTrue(self.hub.permission_verdict("abcde", "allow"))
        self.assertFalse(self.hub.permission_verdict("abcde", "deny"))  # first answer wins
        verdict = self.emitted.messages[-1]
        self.assertEqual(verdict, {"jsonrpc": "2.0", "method": "notifications/claude/channel/permission",
                                   "params": {"request_id": "abcde", "behavior": "allow"}})
        self.assertEqual(self.hub.updates("blender-props", 0, 0)[-1]["kind"], "permission_resolved")

    def test_window_without_subagent_gets_one_automatically(self):
        calls = []
        def fake_sync(agents_dir):
            calls.append(agents_dir)
            files = handlib.agents.build_agent_files([handlib.discovery.Instance(name="NewGame", hash="bbbb2222")], handlib.discovery.ServerConfig(transport="stdio"), [], None, "uv run")
            handlib.agents.write_agent_files(files, agents_dir)
        self.hub.auto_sync = fake_sync
        self.assertEqual(self.hub.resolve("unity", "NewGame"), ("unity-newgame", "unity-newgame"))
        self.assertEqual(len(calls), 1)
        self.hub.resolve("unity", "Ghost")  # no such window anywhere: tries once...
        self.hub.resolve("unity", "Ghost")  # ...then waits before trying again
        self.assertEqual(len(calls), 2)
        self.hub.resolve("unity", "MyGame")  # already has a subagent: no refresh
        self.assertEqual(len(calls), 2)

    def test_failed_auto_sync_does_not_break_resolve(self):
        def broken(agents_dir):
            raise SystemExit("hand-written file in the way")
        self.hub.auto_sync = broken
        self.assertEqual(self.hub.resolve("unity", "Other"), ("unity-other", None))

    def test_late_window_sees_open_permission(self):
        self.hub.permission_request({"request_id": "fghij", "tool_name": "Write", "description": "", "input_preview": ""})
        self.hub.post_user_message("unity", "MyGame", "hi")
        kinds = [i["kind"] for i in self.hub.updates("unity-mygame", 0, 0)]
        self.assertEqual(kinds, ["user", "permission"])


class Stage2HubTests(unittest.TestCase):
    """Lazy start backlog, window context, desktop-origin replies, first-use hook."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_agents(Path(self.tmp.name))
        self.emitted = Recorder()
        self.hub = cc.ChatHub(self.emitted, Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_messages_wait_until_claude_code_connects(self):
        result = self.hub.post_user_message("unity", "MyGame", "first message")
        self.assertFalse(result["delivered"])
        self.assertEqual(self.emitted.messages, [])
        self.hub.mark_initialized()
        self.assertEqual([m["params"]["content"] for m in self.emitted.messages], ["first message"])
        self.hub.post_user_message("unity", "MyGame", "second")
        self.assertEqual(len(self.emitted.messages), 2)

    def test_context_is_kept_per_window_newest_first(self):
        self.hub.update_context("blender", "props", "props@9877", {"file": "C:/Art/props.blend", "active": "Chair",
                                                                   "selection": ["Chair", "Table"]})
        time.sleep(0.01)
        chat_id = self.hub.update_context("unity", "MyGame", "", {"scene": "Main", "selection": ["Root/Player"], "focused": True,
                                                                  "junk": "dropped", "assets": list(map(str, range(50)))})
        self.assertEqual(chat_id, "unity-mygame")
        windows = self.hub.current_context()["windows"]
        self.assertEqual([w["chat_id"] for w in windows], ["unity-mygame", "blender-props"])
        self.assertEqual(windows[0]["agent"], "unity-mygame")
        self.assertEqual(windows[0]["selection"], ["Root/Player"])
        self.assertNotIn("junk", windows[0])
        self.assertEqual(len(windows[0]["assets"]), cc.MAX_CONTEXT_ITEMS)
        self.assertEqual(windows[1]["active"], "Chair")
        self.assertIn("last_used_seconds_ago", windows[1])
        # a repeat report that changes nothing and has no focus does not make a window "recent"
        self.hub.update_context("unity", "MyGame", "", {"scene": "Main", "selection": ["Root/Player"], "focused": False})
        self.hub.update_context("blender", "props", "props@9877", {"file": "C:/Art/props.blend", "active": "Table",
                                                                   "selection": ["Table"]})
        self.assertEqual([w["chat_id"] for w in self.hub.current_context()["windows"]], ["blender-props", "unity-mygame"])

    def test_desktop_reply_reaches_a_window_whose_panel_never_spoke(self):
        self.assertTrue(self.hub.reply("unity-mygame", "큐브를 만들었습니다", origin="desktop", request="큐브 하나 만들기"))
        self.assertFalse(self.hub.reply("unity-nobody", "x", origin="desktop"))
        [item] = self.hub.updates("unity-mygame", 0, 0)
        self.assertEqual((item["kind"], item["origin"], item["request"]), ("reply", "desktop", "큐브 하나 만들기"))

    def test_first_use_runs_once(self):
        calls = []
        self.hub.on_first_use = lambda: calls.append(1)
        mcp = cc.StdioMcp(io.StringIO(), io.StringIO(), Path(self.tmp.name))
        mcp.hub = self.hub
        mcp.call_tool({"name": "current_context", "arguments": {}})
        mcp.call_tool({"name": "current_context", "arguments": {}})
        self.hub.permission_request({"request_id": "abcde", "tool_name": "Bash"})
        self.assertEqual(calls, [1])
        self.assertTrue(self.hub.status()["in_use"])

    def test_first_use_failure_is_harmless(self):
        def boom():
            raise RuntimeError("no window")
        self.hub.on_first_use = boom
        self.hub.mark_in_use()
        self.assertTrue(self.hub.in_use)


class MinimizeTests(unittest.TestCase):
    def test_only_on_windows_and_only_the_supervisor_console(self):
        shown, asked = [], []
        find = lambda owners: asked.append(owners) or [111]
        self.assertFalse(cc.minimize_console({7}, find=find, show=shown.append, platform="linux"))
        self.assertTrue(cc.minimize_console({7, 8}, find=find, show=shown.append, platform="win32"))
        self.assertEqual((asked, shown), ([{7, 8}], [111]))
        self.assertFalse(cc.minimize_console({7}, find=lambda owners: [], show=shown.append, platform="win32"))
        # The console window belongs to the cmd.exe that runs start-supervisor, the parent of Claude Code (8).
        self.assertEqual(cc.console_owners(AncestorTests.TABLE, 8), {8, 7})

    @unittest.skipUnless(sys.platform == "win32", "Windows console windows")
    def test_finds_no_window_for_unknown_owners(self):
        self.assertEqual(cc.console_windows({0}), [])

    def test_config_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertTrue(cc.minimize_after_start(home))
            (home / "config.json").write_text(json.dumps({"minimize_after_start": False}), encoding="utf-8")
            self.assertFalse(cc.minimize_after_start(home))

    def test_claude_pid_is_found_through_wrappers(self):
        self.assertEqual(cc.claude_ancestor_pid(AncestorTests.TABLE, 10), 8)
        self.assertIsNone(cc.claude_ancestor_pid({10: (9, "python x"), 9: (1, "cmd.exe")}, 10))


class McpTests(unittest.TestCase):
    def run_session(self, lines):
        out = io.StringIO()
        mcp = cc.StdioMcp(io.StringIO("\n".join(json.dumps(m) for m in lines) + "\n"), out, Path("/nonexistent"))
        mcp.serve()
        return mcp, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_handshake_declares_channel(self):
        _, replies = self.run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
        ])
        init = replies[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["capabilities"]["experimental"], {"claude/channel": {}, "claude/channel/permission": {}})
        self.assertIn("`reply` 도구로 같은 `chat_id`에", init["instructions"])
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["reply", "current_context", "undo_request", "propose_protection"])
        self.assertEqual(replies[2], {"jsonrpc": "2.0", "id": 3, "result": {}})
        self.assertEqual(replies[3]["error"]["code"], -32601)

    def test_reply_tool(self):
        mcp, _ = self.run_session([])
        mcp.hub.logs["unity-mygame"] = []
        ok = mcp.call_tool({"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "done", "working": False}})
        bad = mcp.call_tool({"name": "reply", "arguments": {"chat_id": "nope", "text": "done"}})
        empty = mcp.call_tool({"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": " "}})
        self.assertEqual(ok, {"content": [{"type": "text", "text": "sent"}]})
        self.assertTrue(bad["isError"] and empty["isError"])
        desktop = mcp.call_tool({"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "done", "origin": "desktop",
                                                                "request": "큐브"}})
        self.assertEqual(desktop, {"content": [{"type": "text", "text": "sent"}]})
        self.assertEqual(mcp.hub.logs["unity-mygame"][-1]["origin"], "desktop")
        mcp.call_tool({"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "작업 중이에요", "working": True}})
        self.assertTrue(mcp.hub.logs["unity-mygame"][-1]["working"])
        self.assertIn("working", cc.REPLY_TOOL["inputSchema"]["properties"])
        # Which other windows a request changed is asked of every reply: the supervisor knows, the channel only guesses.
        self.assertIn("changed_windows", cc.REPLY_TOOL["inputSchema"]["required"])
        named = mcp.call_tool({"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "done", "working": False,
                                                              "changed_windows": ["unity-mygame", "hand_test"]}})
        self.assertIn("changed_windows names no window Claude Hand knows: hand_test.", named["content"][0]["text"])
        none = mcp.call_tool({"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "done", "working": False,
                                                             "changed_windows": []}})
        self.assertEqual(none, {"content": [{"type": "text", "text": "sent"}]})
        context = mcp.call_tool({"name": "current_context", "arguments": {}})
        self.assertEqual(json.loads(context["content"][0]["text"])["windows"], [])

    def test_a_reply_has_to_say_whether_the_work_goes_on(self):
        """A real supervisor, resumed for days, never said `working`: not on "on it", not on the result. The tool
        definition asking for it changed nothing, and neither did a hint in the result. A reply without it is sent
        back, the first few times."""
        mcp, _ = self.run_session([])
        mcp.hub.logs["unity-mygame"] = []
        say = lambda **more: mcp.call_tool({"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "만들고 있어요", **more}})
        self.assertEqual(set(cc.REPLY_TOOL["inputSchema"]["required"]), {"chat_id", "text", "working", "changed_windows"})
        asked = say()
        self.assertTrue(asked["isError"])
        self.assertIn("working: true", asked["content"][0]["text"])
        self.assertIn("working: false", asked["content"][0]["text"])
        self.assertEqual(mcp.hub.logs["unity-mygame"], [])  # nothing reached the panel
        self.assertEqual(say(working=True), {"content": [{"type": "text", "text": "sent"}]})
        self.assertEqual(say(working=False), {"content": [{"type": "text", "text": "sent"}]})
        self.assertEqual(say(origin="desktop", request="큐브"), {"content": [{"type": "text", "text": "sent"}]})  # a record, not an answer
        self.assertTrue(say(chat_id="nope")["isError"])  # an unknown chat is told so, not asked about working
        self.assertEqual(mcp.hub.working_asked, 1)
        for _ in range(cc.WORKING_ASKS - 1):
            self.assertTrue(say()["isError"])
        # A supervisor that still leaves it out is not made to send every answer twice for ever.
        self.assertEqual(say(), {"content": [{"type": "text", "text": "sent"}]})
        self.assertNotIn("working", mcp.hub.logs["unity-mygame"][-1])


class ProtectionTests(unittest.TestCase):
    """Protection rules as the channel has to do with them: it writes none. A rule the supervisor proposes is shown in
    the window's panel, where the user's press writes it; what the guard hook stopped is said in the panels; a
    locked window is handed no work."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "MyGame"
        (self.project / "ClaudeHand").mkdir(parents=True)
        unity = handlib.discovery.Instance(name="MyGame", hash="aaaa1111", path=str(self.project))
        blender = handlib.discovery.BlenderInstance(port=9877, filepath=str(root / "props.blend"))
        files = handlib.agents.build_agent_files([unity], handlib.discovery.ServerConfig(transport="stdio"), [blender], None, "uv run")
        handlib.agents.write_agent_files(files, root / "agents")
        self.mcp = cc.StdioMcp(io.StringIO(""), io.StringIO(), root / "agents")
        self.hub = self.mcp.hub
        self.hub.initialized = True
        self.hub.home = root / "home"
        self.logged = []
        self.hub.events = lambda event, **fields: self.logged.append((event, fields))

    def tearDown(self):
        self.tmp.cleanup()

    def rules(self, **rules):
        (self.project / "ClaudeHand" / "protect.json").write_text(json.dumps({"version": 1, **rules}), encoding="utf-8")

    def propose(self, **arguments):
        return self.mcp.call_tool({"name": "propose_protection", "arguments": arguments})

    def items(self, chat_id):
        return [{k: v for k, v in item.items() if k not in ("seq", "ts")} for item in self.hub.logs.get(chat_id, [])]

    def test_a_proposed_rule_is_shown_in_the_windows_panel_and_is_no_rule_yet(self):
        answer = self.propose(chat_id="blender-props", kind="object", value="Hero", lasting=True, why="'Hero는 건드리지 마'라고 하셔서")
        self.assertNotIn("isError", answer)
        self.assertIn("not a rule yet", answer["content"][0]["text"])
        offer, = self.items("blender-props")
        self.assertEqual((offer["kind"], offer["rule"]), ("protect_offer", {"kind": "object", "value": "Hero", "lasting": True}))
        self.assertEqual(offer["text"], "오브젝트 'Hero' 보호(바꾸기 · 지우기 금지) — 계속\n'Hero는 건드리지 마'라고 하셔서")
        self.assertFalse((Path(self.tmp.name) / "props.claude-hand.protect.json").exists())  # the channel writes no rule
        self.assertTrue(self.hub.protection("blender-props").empty())
        self.propose(chat_id="unity-mygame", kind="window", value="look", lasting=False)
        self.assertEqual(self.items("unity-mygame")[0]["text"], "이 창을 보기만 하기(읽기만 하고 바꾸지 않음) — 이번만(Claude Hand를 끌 때까지)")
        self.assertEqual([fields["text"] for event, fields in self.logged if event == "protect"],
                         ["제안: 오브젝트 'Hero' 보호(바꾸기 · 지우기 금지) — 계속", "제안: 이 창을 보기만 하기(읽기만 하고 바꾸지 않음) — 이번만(Claude Hand를 끌 때까지)"])

    def test_a_proposal_that_makes_no_sense_is_sent_back(self):
        for arguments, said in (({"chat_id": "nope", "kind": "object", "value": "Hero"}, "Unknown chat_id"),
                                ({"chat_id": "blender-props", "kind": "path", "value": "Assets/Art/**"}, "Paths are for Unity"),
                                ({"chat_id": "blender-props", "kind": "action", "value": "play_mode"}, "save_file"),
                                ({"chat_id": "unity-mygame", "kind": "action", "value": "save_file"}, "save_scene, delete_asset, play_mode"),
                                ({"chat_id": "unity-mygame", "kind": "window", "value": "open"}, "'look' or 'locked'"),
                                ({"chat_id": "unity-mygame", "kind": "path", "value": "Packages/x"}, "starts with Assets/"),
                                ({"chat_id": "unity-mygame", "kind": "object", "value": "collection:Rig"}, "Blender windows"),
                                ({"chat_id": "unity-mygame", "kind": "unlock", "value": "Player"}, "kind is one of")):
            answer = self.propose(lasting=True, **arguments)
            self.assertTrue(answer.get("isError"), arguments)
            self.assertIn(said, answer["content"][0]["text"], arguments)
        self.assertEqual((self.hub.logs, self.hub.offers), ({}, {}))

    def test_what_the_user_pressed_reaches_the_other_panels_and_the_supervisor(self):
        self.propose(chat_id="unity-mygame", kind="path", value="Assets/Art/**", lasting=True)
        offer_id = self.items("unity-mygame")[0]["offer_id"]
        self.assertFalse(self.hub.protection_verdict(offer_id, "maybe"))
        self.assertTrue(self.hub.protection_verdict(offer_id, "added"))
        self.assertFalse(self.hub.protection_verdict(offer_id, "added"))  # once
        self.assertEqual(self.items("unity-mygame")[-1], {"kind": "protect_resolved", "offer_id": offer_id, "behavior": "added"})
        emitted = []
        self.hub.emit = emitted.append
        self.hub.post_user_message("unity", "MyGame", "이제 큐브 만들어줘")
        self.assertIn("[사용자가 제안된 보호 규칙을 추가했음: 경로 'Assets/Art/**' 보호(고치기 · 지우기 · 옮기기 금지) — 계속]",
                      emitted[-1]["params"]["content"])

    def test_what_the_guard_stopped_is_said_in_the_panels(self):
        self.assertIsNone(self.hub.protected("unity", "Other@ffff0000", "manage_asset", "보호 규칙이 막았어요: x"))
        asked = self.hub.post_user_message("blender", "props", "이걸 Unity로 보내서 Hero 자리에 놓아줘", pin="props@9877")
        request = next(i["request"] for i in self.hub.logs["blender-props"] if i["kind"] == "user")
        self.hub.assign("unity-mygame", request)
        self.assertEqual(asked["chat_id"], "blender-props")
        told = self.hub.protected("unity", "MyGame@aaaa1111", "mcp__unity_mygame__manage_gameobject",
                                  "보호 규칙이 막았어요: 보호 오브젝트 'Hero' (manage_gameobject: Hero)", "aaa")
        self.assertEqual(told, {"chat_id": "unity-mygame"})
        self.assertEqual(self.items("unity-mygame")[-1], {"kind": "notice", "alert": True,
                                                         "text": "보호 규칙이 막았어요: 보호 오브젝트 'Hero' (manage_gameobject: Hero)"})
        self.assertEqual(self.items("blender-props")[-1]["text"],  # where it was asked, with the window's name
                         "MyGame (Unity): 보호 규칙이 막았어요: 보호 오브젝트 'Hero' (manage_gameobject: Hero)")
        event, fields = self.logged[-1]
        self.assertEqual((event, fields["request"], fields["chat_id"], fields["reason"]),
                         ("protected", request, "unity-mygame", "보호 규칙이 막았어요: 보호 오브젝트 'Hero' (manage_gameobject: Hero)"))

    def test_a_locked_window_is_handed_no_work(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        request = next(i["request"] for i in self.hub.logs["unity-mygame"] if i["kind"] == "user")
        self.rules(window="look")
        self.assertEqual(self.hub.assign("unity-mygame", request).get("locked"), None)  # looking there is work too
        self.rules(window="locked")
        self.assertEqual(self.hub.assign("unity-mygame", request), {"ours": True, "chat_id": "unity-mygame", "locked": True})
        self.assertEqual(self.logged[-1], ("refused", {"request": request, "chat_id": "unity-mygame", "tool": "(맡기기)",
                                                       "reason": "사용자가 잠근 창 (보호 규칙: 잠금)"}))
        session = self.hub.home / "protect" / "blender-9877.json"  # this run's rules count as well
        session.parent.mkdir(parents=True)
        session.write_text(json.dumps({"window": "locked"}), encoding="utf-8")
        self.assertTrue(self.hub.assign("blender-props", "").get("locked"))

    def test_the_supervisor_sees_each_windows_rules(self):
        self.rules(window="look", paths=["Assets/Art/**"], actions=["play_mode"])
        self.hub.update_context("unity", "MyGame", "", {"scene": "Main"})
        self.hub.update_context("blender", "props", "props@9877", {"file": "props.blend"})
        windows = {w["chat_id"]: w for w in self.hub.current_context()["windows"]}
        self.assertEqual(windows["unity-mygame"]["protection"], {"window": "look", "paths": ["Assets/Art/**"], "actions": ["play_mode"]})
        self.assertNotIn("protection", windows["blender-props"])

    def test_over_http(self):
        httpd = cc.start_http(self.hub, "t", port=18840)
        try:
            def post(path, body):
                request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}", data=json.dumps(body).encode("utf-8"),
                                                 method="POST", headers={"X-Claude-Hand-Token": "t", "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as error:
                    with error:
                        return error.code, json.loads(error.read())
            self.propose(chat_id="unity-mygame", kind="object", value="Player", lasting=True)
            offer_id = self.items("unity-mygame")[0]["offer_id"]
            self.assertEqual(post("/v1/protect", {"app": "unity", "window": "MyGame", "offer_id": offer_id, "behavior": "declined"}), (200, {"ok": True}))
            self.assertEqual(post("/v1/protect", {"offer_id": offer_id, "behavior": "declined"}), (409, {"ok": False}))
            self.assertEqual(post("/v1/protected", {"app": "unity", "pin": "MyGame@aaaa1111", "tool": "x", "text": "보호 규칙이 막았어요: y"}),
                             (200, {"ok": True, "chat_id": "unity-mygame"}))
            self.assertEqual(post("/v1/protected", {"app": "unity", "pin": "Other@ffff0000", "text": "y"})[0], 404)
        finally:
            httpd.shutdown()
            httpd.server_close()


class EndToEndTests(unittest.TestCase):
    """Starts chat_channel.py the way Claude Code does and drives it like a panel would."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        write_agents(root / "agents")
        env = {**os.environ, "CLAUDE_HAND_HOME": str(root / "home"), "CLAUDE_HAND_AGENTS_DIR": str(root / "agents"),
               "CLAUDE_HAND_CHAT_PORT": "18765", "CLAUDE_HAND_SUPERVISOR": "1"}
        self.proc = subprocess.Popen([sys.executable, str(TOOL)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env)
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(self.read()["id"], 1)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        server_file = root / "home" / "chat" / "server.json"
        for _ in range(50):
            if server_file.exists():
                break
            time.sleep(0.05)
        self.server = json.loads(server_file.read_text(encoding="utf-8"))
        self.server_file = server_file

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.wait(5)
        self.proc.stdout.close()
        self.tmp.cleanup()

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def read(self):
        return json.loads(self.proc.stdout.readline())

    def http(self, method, path, body=None, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.server['port']}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Claude-Hand-Token": self.server["token"] if token is None else token,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_panel_round_trip(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.http("GET", "/v1/status", token="wrong")
        self.assertEqual(denied.exception.code, 401)
        self.assertTrue(self.http("GET", "/v1/status")["claude_connected"])
        self.assertEqual(self.http("GET", "/v1/resolve?app=unity&window=MyGame")["chat_id"], "unity-mygame")

        posted = self.http("POST", "/v1/messages", {"app": "unity", "window": "MyGame", "text": "Player 복제해줘"})
        self.assertEqual(posted["chat_id"], "unity-mygame")
        note = self.read()
        self.assertEqual((note["method"], note["params"]["meta"]["agent"]), ("notifications/claude/channel", "unity-mygame"))

        self.send({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                   "params": {"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "복제했어요"}}})
        asked = self.read()["result"]  # sent back once: is this a status or the result?
        self.assertTrue(asked["isError"])
        self.assertIn("working: false", asked["content"][0]["text"])
        self.send({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                   "params": {"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "복제했어요", "working": False,
                                                             "changed_windows": []}}})
        self.assertEqual(self.read()["result"]["content"][0]["text"], "sent")
        updates = self.http("GET", "/v1/updates?chat_id=unity-mygame&after=0&wait=1")
        self.assertEqual([i["kind"] for i in updates["items"]], ["user", "reply"])

        self.send({"jsonrpc": "2.0", "method": "notifications/claude/channel/permission_request",
                   "params": {"request_id": "abcde", "tool_name": "Bash", "description": "x", "input_preview": "y"}})
        later = self.http("GET", f"/v1/updates?chat_id=unity-mygame&after={updates['next']}&wait=5")
        self.assertEqual(later["items"][0]["kind"], "permission")
        self.assertTrue(self.http("POST", "/v1/permission", {"request_id": "abcde", "behavior": "deny"})["ok"])
        self.assertEqual(self.read()["params"], {"request_id": "abcde", "behavior": "deny"})

    def test_server_file_removed_on_exit(self):
        status = json.loads((self.server_file.parent / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["mode"], "serving")
        self.proc.stdin.close()
        self.proc.wait(5)
        self.assertFalse(self.server_file.exists())
        self.assertEqual(json.loads((self.server_file.parent / "status.json").read_text(encoding="utf-8"))["mode"], "stopped")
        self.proc.stdin = open(os.devnull, "w")  # tearDown closes it again

    def test_second_start_leaves_a_live_supervisor_alone(self):
        root = Path(self.tmp.name)
        env = {**os.environ, "CLAUDE_HAND_HOME": str(root / "home"), "CLAUDE_HAND_AGENTS_DIR": str(root / "agents"),
               "CLAUDE_HAND_CHAT_PORT": "18765", "CLAUDE_HAND_SUPERVISOR": "1"}
        proc = subprocess.run([sys.executable, str(TOOL)], input="", capture_output=True, text=True, encoding="utf-8",
                              env=env, timeout=30)
        self.assertIn("passive", proc.stderr)
        self.assertEqual(json.loads(self.server_file.read_text(encoding="utf-8")), self.server)
        self.assertEqual(json.loads((self.server_file.parent / "status.json").read_text(encoding="utf-8"))["mode"], "serving")
        self.assertTrue(self.http("GET", "/v1/status")["ok"])



class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = Path(self.tmp.name) / "agents"
        write_agents(self.agents)
        self.hub = cc.ChatHub(lambda message: None, self.agents)

    def tearDown(self):
        self.tmp.cleanup()

    def test_subagent_actions_show_until_the_reply(self):
        self.assertIsNone(self.hub.report_action("unity", "Other@ffff0000", "manage_scene", ""))  # not a generated subagent
        self.hub.initialized = True
        self.hub.post_user_message("unity", "MyGame", "큐브 세 개를   일렬로 놓아줘")
        first = self.hub.report_action("unity", "MyGame@aaaa1111", "manage_gameobject", "create Cube")
        request = self.hub.logs["unity-mygame"][-1]["request"]  # the point is named after the request: its files are kept under it
        self.assertEqual(first, {"chat_id": "unity-mygame", "new_request": True, "label": "큐브 세 개를 일렬로 놓아줘", "point": request})
        again = self.hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "save")
        self.assertEqual((again["new_request"], again["point"]), (False, request))
        state = self.hub.poll("unity-mygame", 0, 0)
        self.assertEqual((state["progress"]["tool"], state["progress"]["detail"], state["progress"]["actions"]),
                         ("manage_scene", "save", 2))
        self.hub.reply("unity-mygame", "다 했어요")
        self.assertIsNone(self.hub.poll("unity-mygame", 0, 0)["progress"])
        # the next request (here asked in the Claude app, so no panel text) gets its own undo point
        later = self.hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "get_hierarchy")
        self.assertEqual((later["chat_id"], later["new_request"], later["label"]), ("unity-mygame", True, ""))
        self.assertTrue(later["point"].startswith("bar-"))  # nobody's request by name: the start of its work line stands for it
        self.assertEqual(self.hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "save")["point"], later["point"])

    def test_a_working_on_it_reply_is_not_the_answer(self):
        self.hub.initialized = True
        self.hub.post_user_message("unity", "MyGame", "큐브를 빨갛게", pin="MyGame@aaaa1111")
        self.hub.reply("unity-mygame", "작업 중이에요", working=True)  # before any work
        self.assertTrue(self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "")["new_request"])
        self.hub.reply("unity-mygame", "머티리얼 찾았어요, 바꿀게요", working=True)  # in the middle of it
        self.assertIsNotNone(self.hub.poll("unity-mygame", 0, 0)["progress"])  # the work line stays
        self.assertFalse(self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "set")["new_request"])  # one undo point
        self.assertEqual(self.hub._open_request("unity-mygame"), "큐브를 빨갛게")
        self.hub.reply("unity-mygame", "빨갛게 했어요")
        self.assertIsNone(self.hub.poll("unity-mygame", 0, 0)["progress"])
        self.assertEqual(self.hub._open_request("unity-mygame"), "")

    def test_a_waiting_panel_wakes_on_progress(self):
        rev = self.hub.poll("unity-mygame", 0, 0)["progress_rev"]
        timer = threading.Timer(0.2, lambda: self.hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "save"))
        timer.start()
        started = time.time()
        state = self.hub.poll("unity-mygame", 0, 5, progress_rev=rev)
        timer.join()
        self.assertLess(time.time() - started, 3)
        self.assertEqual(state["progress"]["tool"], "manage_scene")

    def test_stale_progress_disappears(self):
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "save")
        self.hub.progress["unity-mygame"]["updated"] -= cc.PROGRESS_TTL + 1
        self.assertIsNone(self.hub.poll("unity-mygame", 0, 0)["progress"])


class PreviewTests(unittest.TestCase):
    """A request made in one window that changed another: the reply gets a picture of the other window."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        write_agents(root / "agents")
        self.hub = cc.ChatHub(lambda message: None, root / "agents")
        self.hub.initialized = True
        self.shots = []

        def capture(app, pin, path):
            self.shots.append((app, pin))
            Path(path).write_bytes(b"png")
            return path
        self.hub.capture, self.hub.images_dir = capture, root / "images"

    def tearDown(self):
        self.tmp.cleanup()

    def images(self, chat_id, count=1):
        deadline = time.time() + 3
        while time.time() < deadline:
            found = [i for i in self.hub.logs.get(chat_id, []) if i["kind"] == "image"]
            if len(found) >= count:
                return found
            time.sleep(0.05)
        return [i for i in self.hub.logs.get(chat_id, []) if i["kind"] == "image"]

    def test_the_reply_shows_the_other_window_that_changed(self):
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
        self.hub.reply("blender-props", "보내는 중이에요")  # nothing changed elsewhere yet
        self.hub.report_action("blender", "props@9877", "execute_code", "export")  # this window: the user sees it
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.reply("blender-props", "Unity에 놓았어요")
        [image] = self.images("blender-props")
        self.assertEqual(self.shots, [("unity", "MyGame@aaaa1111")])
        self.assertEqual((image["app"], image["window"], image["caption"]), ("unity", "MyGame", "MyGame (Unity) 지금 모습"))
        self.assertTrue(Path(image["path"]).exists())
        self.hub.reply("blender-props", "더 필요한 게 있으면 말해 주세요")  # nothing new since the last picture
        time.sleep(0.3)
        self.assertEqual(len(self.shots), 1)

    def test_two_projects_worked_on_side_by_side_get_no_pictures_of_each_other(self):
        write_agents(Path(self.tmp.name) / "agents", [handlib.discovery.Instance(name="Level", hash="bbbb2222")])
        self.hub.post_user_message("unity", "MyGame", "큐브를 빨갛게", pin="MyGame@aaaa1111")
        self.hub.post_user_message("unity", "Level", "조명을 어둡게", pin="Level@bbbb2222")
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "red")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_gameobject", "light")
        self.hub.reply("unity-mygame", "빨갛게 했어요")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_scene", "save")  # still Level's own request
        self.hub.reply("unity-level", "어둡게 했어요")
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
        self.assertEqual(self.images("unity-mygame", 0) + self.images("unity-level", 0), [])
        self.hub.post_user_message("unity", "MyGame", "Level의 조명도 MyGame처럼", pin="MyGame@aaaa1111")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_gameobject", "light")  # asked from MyGame: shown there
        self.hub.reply("unity-mygame", "맞췄어요")
        [image] = self.images("unity-mygame")
        self.assertEqual((image["window"], self.shots), ("Level", [("unity", "Level@bbbb2222")]))
        self.assertEqual(self.images("unity-level", 0), [])

    def test_working_on_it_replies_keep_side_by_side_projects_apart(self):
        write_agents(Path(self.tmp.name) / "agents", [handlib.discovery.Instance(name="Level", hash="bbbb2222")])
        self.hub.post_user_message("unity", "MyGame", "큐브를 빨갛게", pin="MyGame@aaaa1111")
        self.hub.post_user_message("unity", "Level", "조명을 어둡게", pin="Level@bbbb2222")
        self.hub.reply("unity-mygame", "작업 중이에요", working=True)
        self.hub.reply("unity-level", "작업 중이에요", working=True)
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "red")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_gameobject", "light")
        self.hub.reply("unity-mygame", "빨갛게 했어요")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_scene", "save")
        self.hub.reply("unity-level", "어둡게 했어요")
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
        self.assertEqual(self.images("unity-mygame", 0) + self.images("unity-level", 0), [])

    def test_on_it_sent_without_working_keeps_side_by_side_projects_apart(self):
        """The supervisor forgets `working` now and then. Its "on it" then looked like the answer, the work that
        followed looked asked elsewhere, and each project got the other's picture."""
        write_agents(Path(self.tmp.name) / "agents", [handlib.discovery.Instance(name="Level", hash="bbbb2222")])
        self.hub.post_user_message("unity", "MyGame", "큐브를 빨갛게", pin="MyGame@aaaa1111")
        self.hub.post_user_message("unity", "Level", "조명을 어둡게", pin="Level@bbbb2222")
        self.hub.reply("unity-mygame", "빨갛게 하고 있어요. 잠시만 기다려 주세요.")
        self.hub.reply("unity-level", "어둡게 하고 있어요. 잠시만 기다려 주세요.")
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "red")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_gameobject", "light")
        self.hub.reply("unity-mygame", "빨갛게 했어요")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_scene", "save")
        self.hub.reply("unity-level", "어둡게 했어요")
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
        self.assertEqual(self.images("unity-mygame", 0) + self.images("unity-level", 0), [])
        self.assertEqual(self.hub.early, {})  # both were answered after their work: nothing is left open

    def test_on_it_without_working_in_one_project_does_not_show_it_in_the_other(self):
        write_agents(Path(self.tmp.name) / "agents", [handlib.discovery.Instance(name="Level", hash="bbbb2222")])
        self.hub.post_user_message("unity", "MyGame", "큐브를 빨갛게", pin="MyGame@aaaa1111")
        self.hub.reply("unity-mygame", "빨갛게 하고 있어요.")
        self.hub.post_user_message("unity", "Level", "조명을 어둡게", pin="Level@bbbb2222")  # asked while MyGame works
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "red")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_gameobject", "light")
        self.hub.reply("unity-level", "어둡게 했어요")
        self.hub.reply("unity-mygame", "빨갛게 했어요")
        time.sleep(0.3)
        self.assertEqual(self.shots, [])

    def test_an_answer_that_needed_no_work_stops_counting_after_a_while(self):
        self.hub.post_user_message("unity", "MyGame", "지금 뭐가 선택돼 있지?", pin="MyGame@aaaa1111")
        self.hub.reply("unity-mygame", "큐브예요")  # no work was seen: this may be "on it", so MyGame's request stays its own
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.reply("blender-props", "Unity에 놓았어요")  # unclear whose work that was: no picture
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
        self.hub.post_user_message("blender", "props", "하나 더 보내줘", pin="props@9877")
        self.hub.early["unity-mygame"]["ts"] -= cc.PROGRESS_TTL + 1  # MyGame's panel got no result: it was the answer after all
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.reply("blender-props", "하나 더 놓았어요")
        [image] = self.images("blender-props")
        self.assertEqual(image["window"], "MyGame")

    def test_with_two_panels_waiting_a_third_window_is_shown_only_where_the_supervisor_says(self):
        write_agents(Path(self.tmp.name) / "agents", [handlib.discovery.Instance(name="Level", hash="bbbb2222")])
        self.hub.post_user_message("unity", "MyGame", "큐브를 빨갛게", pin="MyGame@aaaa1111")
        self.hub.post_user_message("blender", "props", "의자를 Level로 보내줘", pin="props@9877")
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "red")
        self.hub.report_action("unity", "Level@bbbb2222", "ch_import_model", "")  # for which of the two? the channel cannot tell
        self.hub.reply("unity-mygame", "빨갛게 했어요")
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
        self.hub.reply("blender-props", "Level에 놓았어요", changed=["unity-level"])
        [image] = self.images("blender-props")
        self.assertEqual((image["window"], self.shots), ("Level", [("unity", "Level@bbbb2222")]))
        self.assertEqual(self.images("unity-mygame", 0), [])

    def test_with_two_panels_waiting_the_hand_out_says_whose_work_a_third_window_does(self):
        write_agents(Path(self.tmp.name) / "agents", [handlib.discovery.Instance(name="Level", hash="bbbb2222")])
        self.hub.post_user_message("unity", "MyGame", "큐브를 빨갛게", pin="MyGame@aaaa1111")
        self.hub.post_user_message("blender", "props", "의자를 Level로 보내줘", pin="props@9877")
        self.hub.assign("unity-level", self.hub.logs["blender-props"][-1]["request"])  # the supervisor's hook: [요청 …]
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "red")
        self.hub.report_action("unity", "Level@bbbb2222", "ch_import_model", "")
        self.hub.reply("unity-mygame", "빨갛게 했어요")
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
        self.hub.reply("blender-props", "Level에 놓았어요")  # even from a supervisor that does not name the windows
        [image] = self.images("blender-props")
        self.assertEqual((image["window"], self.shots), ("Level", [("unity", "Level@bbbb2222")]))

    def test_what_the_supervisor_names_goes_before_what_the_channel_saw(self):
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.reply("blender-props", "Unity는 그대로 뒀어요", changed=[])  # it says no other window changed
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
        self.hub.post_user_message("blender", "props", "다시 보내줘", pin="props@9877")
        self.hub.reply("blender-props", "보냈어요", changed=["blender-props", "unity-nobody", "unity-mygame", "unity-mygame"])
        [image] = self.images("blender-props")  # not itself, not a window nobody has, and each window once
        self.assertEqual((image["window"], self.shots), ("MyGame", [("unity", "MyGame@aaaa1111")]))

    def test_a_working_on_it_reply_gets_no_picture_but_the_result_does(self):
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.reply("blender-props", "Unity에 놓는 중이에요", working=True)
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
        self.hub.reply("blender-props", "Unity에 놓았어요")
        [image] = self.images("blender-props")
        self.assertEqual(image["window"], "MyGame")

    def test_a_window_that_was_only_looked_at_is_not_shown(self):
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
        for tool, detail in (("ch_scene_snapshot", ""), ("ch_editor_status", ""), ("ch_checkpoint", "list"), ("get_scene_info", ""),
                             ("find_gameobjects", "Chair"), ("ch_export_model", ""), ("ch_model_follow", "list")):
            self.assertTrue(cc.only_looks(tool, detail), tool)
            self.hub.report_action("unity", "MyGame@aaaa1111", tool, detail)
        self.hub.report_action("unity", "MyGame@aaaa1111", "execute_code", "return go.transform.position;", looks=True)  # code a guard has read
        self.hub.reply("blender-props", "중지했어요. Unity는 씬만 확인하고 멈췄어요.")
        time.sleep(0.3)
        self.assertEqual(self.shots, [])  # nothing there is different from before
        self.hub.post_user_message("blender", "props", "다시 보내줘", pin="props@9877")
        for tool, detail in (("ch_import_model", ""), ("ch_checkpoint", "undo"), ("execute_code", "x = 1"), ("manage_gameobject", "find"),
                             ("ch_model_follow", "set Assets/ClaudeHand/Imports/chair/chair.fbx")):
            self.assertFalse(cc.only_looks(tool, detail), tool)
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_scene_snapshot", "")
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.reply("blender-props", "Unity에 놓았어요")
        [image] = self.images("blender-props")
        self.assertEqual(image["window"], "MyGame")

    def test_no_picture_without_a_capture_or_for_desktop_replies(self):
        self.hub.post_user_message("blender", "props", "Unity 씬 저장해줘", pin="props@9877")
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "save")
        self.hub.reply("unity-mygame", "저장했어요", origin="desktop", request="Unity 씬 저장")  # the changed window itself
        self.hub.capture = lambda app, pin, path: None  # the window closed meanwhile
        self.hub.reply("blender-props", "저장했어요")
        time.sleep(0.3)
        self.assertEqual(self.images("blender-props"), [])
        self.assertEqual(self.images("unity-mygame"), [])

    def test_pictures_are_kept_with_the_history_and_old_files_go(self):
        history = Path(self.tmp.name) / "history"
        self.hub.use_history(history)
        self.hub.post_user_message("unity", "MyGame", "Blender 의자를 빨갛게", pin="MyGame@aaaa1111")
        self.hub.report_action("blender", "props@9877", "execute_code", "material")
        with unittest.mock.patch.object(cc, "IMAGES_KEPT", 1):
            for name in ("old-1.png", "old-2.png"):
                (self.hub.images_dir / name).parent.mkdir(parents=True, exist_ok=True)
                (self.hub.images_dir / name).write_bytes(b"x")
                os.utime(self.hub.images_dir / name, (1, 1))
            self.hub.reply("unity-mygame", "빨갛게 했어요")
            [image] = self.images("unity-mygame")
            time.sleep(0.2)
        self.assertEqual(sorted(p.name for p in self.hub.images_dir.glob("*.png")), [Path(image["path"]).name])
        restarted = cc.ChatHub(lambda message: None, Path(self.tmp.name) / "agents")
        restarted.use_history(history)
        self.assertEqual([i["kind"] for i in restarted.logs["unity-mygame"]], ["user", "reply", "image"])


class DialogWatchTests(unittest.TestCase):
    """A dialog stops the window doing the work: the chat whose request waits on it is told (and blinks)."""

    SAVE = {"title": "Scene(s) Have Been Modified", "text": "Do you want to save the changes?",
            "buttons": ["Save", "Don't Save", "Cancel"], "progress": False}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_agents(Path(self.tmp.name) / "agents")
        self.hub = cc.ChatHub(lambda message: None, Path(self.tmp.name) / "agents")
        self.hub.initialized = True
        self.shown, self.asked = [], []
        self.hub.find_dialogs = lambda app, pin: self.asked.append((app, pin)) or list(self.shown)

    def tearDown(self):
        self.tmp.cleanup()

    def notices(self, chat_id):
        return [i for i in self.hub.logs.get(chat_id, []) if i["kind"] == "notice"]

    def test_the_waiting_chat_is_told_once_per_dialog(self):
        self.hub.check_dialogs()
        self.assertEqual(self.asked, [])  # nobody is working: nothing to look at
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
        self.hub.reply("blender-props", "보내는 중이에요", working=True)  # still waiting on the result
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.check_dialogs()
        self.assertEqual(self.asked, [("unity", "MyGame@aaaa1111")])
        self.assertEqual(self.notices("blender-props"), [])
        self.shown[:] = [self.SAVE]
        self.hub.check_dialogs()
        self.hub.check_dialogs()
        [notice] = self.notices("blender-props")
        self.assertTrue(notice["alert"])
        self.assertIn("MyGame(Unity)가 대화상자 때문에 멈춰 있어요. Unity에서 직접 골라 주세요", notice["text"])
        self.assertIn("[버튼: Save / Don't Save / Cancel]", notice["text"])
        self.assertEqual(self.notices("unity-mygame"), [])  # the stopped window shows the dialog itself
        self.shown[:] = []
        self.hub.check_dialogs()
        self.shown[:] = [self.SAVE]
        self.hub.check_dialogs()
        self.assertEqual(len(self.notices("blender-props")), 2)  # it came back: told again

    def test_an_answered_request_is_not_told(self):
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.reply("blender-props", "다 했어요")
        self.shown[:] = [self.SAVE]
        self.hub.check_dialogs()
        self.assertEqual(self.notices("blender-props"), [])


class OverviewTests(unittest.TestCase):
    """Every open window and what goes on in it: the panels' "다른 창" lines and `claude_hand.py status`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_agents(Path(self.tmp.name), [handlib.discovery.Instance(name="Level", hash="bbbb2222")])
        self.hub = cc.ChatHub(lambda message: None, Path(self.tmp.name))
        self.hub.initialized = True

    def tearDown(self):
        self.tmp.cleanup()

    def states(self):
        return {s["chat_id"]: s["state"] for s in self.hub.overview()}

    def open_panel(self, app, window, pin=""):
        chat_id, _ = self.hub.resolve(app, window, pin)
        return self.hub.poll(chat_id, 0, 0)

    def test_a_window_is_listed_while_its_panel_is_open_or_its_subagent_works(self):
        self.assertEqual(self.hub.overview(), [])
        self.open_panel("unity", "MyGame")
        self.assertEqual(self.states(), {"unity-mygame": "idle"})
        self.hub.report_action("unity", "Level@bbbb2222", "manage_scene", "save", "aaa")  # asked in the Claude app: no panel there
        self.assertEqual(self.states(), {"unity-level": "working", "unity-mygame": "idle"})
        self.hub.reply("unity-level", "저장했어요", origin="desktop", request="씬 저장")
        self.assertEqual(self.states(), {"unity-mygame": "idle"})
        self.hub.seen["unity-mygame"] -= cc.SEEN_SECONDS + 1  # the chat window was closed
        self.assertEqual(self.hub.overview(), [])

    def test_states_and_whose_request_a_window_works_for(self):
        self.open_panel("unity", "MyGame")
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        [mine] = self.hub.overview()
        self.assertEqual((mine["state"], mine["request"], mine["window"], mine["app"]), ("waiting", "큐브 만들어줘", "MyGame", "unity"))
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_gameobject", "create Cube", "aaa")
        [mine] = self.hub.overview()
        self.assertEqual((mine["state"], mine["action"], mine["request"], mine.get("asker")),
                         ("working", "manage_gameobject create Cube", "큐브 만들어줘", None))
        self.hub.post_user_message("blender", "props", "의자를 Level로 보내줘", pin="props@9877")
        self.hub.assign("unity-level", self.hub.logs["blender-props"][-1]["request"])
        self.hub.report_action("unity", "Level@bbbb2222", "ch_import_model", "", "bbb")
        by_chat = {s["chat_id"]: s for s in self.hub.overview()}
        self.assertEqual((by_chat["unity-level"]["state"], by_chat["unity-level"]["asker"], by_chat["unity-level"]["request"]),
                         ("working", "blender-props", "의자를 Level로 보내줘"))
        self.assertEqual(by_chat["blender-props"]["state"], "waiting")  # nothing works in the Blender window itself
        self.hub.dialog_seen["unity-level"] = json.dumps([DialogWatchTests.SAVE])
        level = next(s for s in self.hub.overview() if s["chat_id"] == "unity-level")
        self.assertEqual(level["state"], "dialog")
        self.assertIn("Save", level["dialog"])
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.assertEqual(self.states()["unity-mygame"], "waiting")  # its subagent is done: the supervisor's turn
        self.hub.reply("unity-mygame", "만들었어요")
        self.assertEqual(self.states()["unity-mygame"], "idle")

    def test_a_panel_gets_the_other_windows_as_lines(self):
        self.open_panel("unity", "MyGame")
        self.open_panel("unity", "Level")
        self.assertEqual(self.hub.poll("unity-mygame", 0, 0)["others"], ["한가함: Level (Unity)"])
        self.hub.post_user_message("blender", "props", "의자를 Level로 보내줘", pin="props@9877")
        self.hub.assign("unity-level", self.hub.logs["blender-props"][-1]["request"])
        self.hub.report_action("unity", "Level@bbbb2222", "ch_import_model", "", "bbb")
        self.assertEqual(self.hub.poll("unity-mygame", 0, 0)["others"],
                         ["Level (Unity) · 작업 중 · props의 요청 · “의자를 Level로 보내줘”", "props (Blender) · 답 기다림 · “의자를 Level로 보내줘”"])
        self.assertEqual(self.hub.poll("unity-level", 0, 0)["others"],  # a window is not its own "other"
                         ["props (Blender) · 답 기다림 · “의자를 Level로 보내줘”", "한가함: MyGame (Unity)"])
        self.hub.dialog_seen["unity-level"] = json.dumps([DialogWatchTests.SAVE])
        self.assertRegex(self.hub.poll("unity-mygame", 0, 0)["others"][0], r"^Level \(Unity\) · 대화상자로 멈춤 · props의 요청 · .*Save")

    def test_a_waiting_panel_wakes_when_another_windows_state_changes_not_with_its_every_step(self):
        first = self.open_panel("unity", "MyGame")
        self.hub.report_action("unity", "Level@bbbb2222", "manage_scene", "save", "aaa")
        shown = self.hub.poll("unity-mygame", 0, 0, first["progress_rev"], first["others_rev"])
        self.assertNotEqual(shown["others_rev"], first["others_rev"])
        self.assertEqual(shown["others"], ["Level (Unity) · 작업 중"])
        self.hub.report_action("unity", "Level@bbbb2222", "manage_gameobject", "create", "aaa")  # one more step: the same state
        started = time.time()
        again = self.hub.poll("unity-mygame", 0, 0.3, shown["progress_rev"], shown["others_rev"])
        self.assertGreaterEqual(time.time() - started, 0.25)  # waited its time out
        self.assertEqual(again["others_rev"], shown["others_rev"])
        result = {}
        waiter = threading.Thread(target=lambda: result.update(self.hub.poll("unity-mygame", 0, 10, shown["progress_rev"], shown["others_rev"])))
        waiter.start()
        time.sleep(0.2)
        self.hub.agent_stopped("unity", "Level@bbbb2222", "aaa")  # Level is no longer at work
        waiter.join(5)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(result["others"], ["한가함: Level (Unity)"])

    def test_a_request_answered_on_it_without_the_flag_still_waits_once_its_work_is_handed_out(self):
        """What a real supervisor does: "on it" without `working`, then the hand-outs, one window after the other."""
        self.open_panel("blender", "props", "props@9877")
        self.hub.post_user_message("blender", "props", "의자를 Level로 보내줘", pin="props@9877")
        request = self.hub.logs["blender-props"][-1]["request"]
        self.hub.reply("blender-props", "보내고 있어요. 잠시만 기다려 주세요.")
        self.assertEqual(self.states()["blender-props"], "idle")  # for all the channel knows, that was the answer
        self.hub.assign("blender-props", request)
        self.assertEqual(self.states()["blender-props"], "waiting")  # work for it was handed out: it was "on it"
        self.hub.report_action("blender", "props@9877", "execute_code", "export", "aaa")
        self.assertEqual(self.states()["blender-props"], "working")
        self.hub.agent_stopped("blender", "props@9877", "aaa")
        self.assertEqual(self.states()["blender-props"], "waiting")  # between one subagent's turn and the next
        self.assertEqual(self.hub.poll("unity-mygame", 0, 0)["others"], ["props (Blender) · 답 기다림 · “의자를 Level로 보내줘”"])
        self.hub.assign("unity-level", request)
        self.hub.report_action("unity", "Level@bbbb2222", "ch_import_model", "", "bbb")
        self.hub.agent_stopped("unity", "Level@bbbb2222", "bbb")
        self.hub.reply("blender-props", "Level에 놓았어요")
        self.assertEqual(self.states()["blender-props"], "idle")

    def test_a_waiting_panel_hears_at_once_that_a_request_is_under_way(self):
        self.open_panel("blender", "props", "props@9877")
        self.hub.post_user_message("blender", "props", "의자를 Level로 보내줘", pin="props@9877")
        self.hub.reply("blender-props", "보내고 있어요.")
        shown = self.hub.poll("unity-mygame", 0, 0, -1, -1)
        self.assertEqual(shown["others"], ["한가함: props (Blender)"])
        result = {}
        waiter = threading.Thread(target=lambda: result.update(self.hub.poll("unity-mygame", 0, 10, shown["progress_rev"], shown["others_rev"])))
        waiter.start()
        time.sleep(0.2)
        self.hub.assign("blender-props", self.hub.logs["blender-props"][0]["request"])  # not at the panel's next ask, up to 20 s later
        waiter.join(5)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(result["others"], ["props (Blender) · 답 기다림 · “의자를 Level로 보내줘”"])

    def test_an_answer_that_needed_no_work_does_not_show_as_waiting(self):
        self.open_panel("unity", "MyGame")
        self.hub.post_user_message("unity", "MyGame", "지금 뭐가 선택돼 있지?")
        self.hub.reply("unity-mygame", "큐브예요")
        self.assertEqual(self.states(), {"unity-mygame": "idle"})

    def test_the_supervisor_sees_the_states_in_current_context(self):
        self.hub.update_context("unity", "MyGame", "MyGame@aaaa1111", {"scene": "Main", "selection": ["Player"], "focused": True})
        self.hub.post_user_message("unity", "MyGame", "이거 빨갛게")
        [window] = self.hub.current_context()["windows"]
        self.assertEqual((window["state"], window["request"]), ("waiting", "이거 빨갛게"))
        self.assertIn("state:", self.hub.current_context()["note"])

    def test_overview_over_http(self):
        httpd = cc.start_http(self.hub, "t", port=18798)
        try:
            def get(path):
                req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}", headers={"X-Claude-Hand-Token": "t"})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read())
            self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
            [window] = get("/v1/overview")["windows"]
            self.assertEqual((window["chat_id"], window["state"]), ("unity-mygame", "waiting"))
            updates = get("/v1/updates?chat_id=unity-level&after=0&wait=0&orev=-1")
            self.assertEqual(updates["others"], ["MyGame (Unity) · 답 기다림 · “큐브 만들어줘”"])
            self.assertIsInstance(updates["others_rev"], int)
        finally:
            httpd.shutdown()
            httpd.server_close()


class HistoryTests(unittest.TestCase):
    def test_panels_get_their_conversation_back_after_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents, history = Path(tmp) / "agents", Path(tmp) / "history"
            write_agents(agents)
            hub = cc.ChatHub(lambda message: None, agents)
            hub.use_history(history)
            hub.initialized = True
            hub.post_user_message("unity", "MyGame", "큐브 하나 만들어줘")
            hub.permission_request({"request_id": "abcde", "tool_name": "Bash", "description": "x", "input_preview": "y"})
            hub.reply("unity-mygame", "만들었어요")
            restarted = cc.ChatHub(lambda message: None, agents)
            restarted.use_history(history)
            items = restarted.logs["unity-mygame"]
            self.assertEqual([(i["kind"], i["text"]) for i in items], [("user", "큐브 하나 만들어줘"), ("reply", "만들었어요")])
            self.assertEqual([i["seq"] for i in items], [1, 2])  # numbered afresh; panels start over with a new server
            self.assertEqual(restarted.seq, 2)
            restarted.reply("unity-mygame", "다음 답")
            self.assertEqual(restarted.logs["unity-mygame"][-1]["seq"], 3)

    def test_history_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents, history = Path(tmp) / "agents", Path(tmp) / "history"
            write_agents(agents)
            hub = cc.ChatHub(lambda message: None, agents)
            hub.use_history(history)
            for n in range(cc.LOG_LIMIT + 20):
                hub.reply("unity-mygame", f"답 {n}")
            restarted = cc.ChatHub(lambda message: None, agents)
            restarted.use_history(history)
            self.assertEqual(len(restarted.logs["unity-mygame"]), cc.LOG_LIMIT)
            self.assertEqual(restarted.logs["unity-mygame"][-1]["text"], f"답 {cc.LOG_LIMIT + 19}")


class LongTextTests(unittest.TestCase):
    def test_long_text_is_cut_visibly(self):
        self.assertEqual(cc.clip("짧은 답", cc.MAX_TEXT), "짧은 답")
        cut = cc.clip("가" * (cc.MAX_TEXT + 500), cc.MAX_TEXT)
        self.assertEqual(len(cut), cc.MAX_TEXT)
        self.assertTrue(cut.endswith(f"앞의 {cc.MAX_TEXT}자만 보냅니다)"))


class ModeTests(unittest.TestCase):
    def run_tool(self, env_extra, stdin="", args=()):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "CLAUDE_HAND_HOME": str(Path(tmp) / "home"), "CLAUDE_HAND_AGENTS_DIR": str(Path(tmp) / "agents"),
                   "CLAUDE_HAND_CHAT_PORT": "18775", **env_extra}
            env.pop("CLAUDE_HAND_SUPERVISOR", None) if "CLAUDE_HAND_SUPERVISOR" not in env_extra else None
            proc = subprocess.run([sys.executable, str(TOOL), *args], input=stdin, capture_output=True, text=True,
                                  encoding="utf-8", env=env, timeout=30)
            return proc, (Path(tmp) / "home" / "chat" / "server.json").exists()

    def test_other_sessions_stay_passive(self):
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        proc, served = self.run_tool({}, stdin=init)
        self.assertEqual(json.loads(proc.stdout.splitlines()[0])["id"], 1)  # still a well-behaved MCP server
        self.assertFalse(served)  # but it never advertised itself to the panels
        self.assertIn("passive", proc.stderr)

    def test_self_check(self):
        proc, served = self.run_tool({}, args=["--check"])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(proc.returncode, 0, result)
        self.assertTrue(result["ok"] and result["port_ok"] and result["home_ok"])
        self.assertFalse(served)

    def test_busy_port_is_not_shared(self):
        hub = cc.ChatHub(lambda message: None, Path("."))
        first = cc.start_http(hub, "a", 18795)
        second = cc.start_http(hub, "b", 18795)
        try:
            self.assertNotEqual(first.server_address[1], second.server_address[1])
        finally:
            for httpd in (first, second):
                httpd.shutdown()
                httpd.server_close()

    def test_is_supervisor(self):
        self.assertTrue(cc.is_supervisor({"CLAUDE_HAND_SUPERVISOR": "1"}))
        self.assertFalse(cc.is_supervisor({}))
        self.assertFalse(cc.is_supervisor({"CLAUDE_HAND_SUPERVISOR": "0"}))  # what .mcp.json passes when unset

    def test_passive_start_is_recorded_for_the_panels(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "CLAUDE_HAND_HOME": str(Path(tmp) / "home"), "CLAUDE_HAND_AGENTS_DIR": str(Path(tmp) / "a")}
            env.pop("CLAUDE_HAND_SUPERVISOR", None)
            subprocess.run([sys.executable, str(TOOL)], input="", capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=30)
            status = json.loads((Path(tmp) / "home" / "chat" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["mode"], "passive")
        self.assertIn("start-supervisor", status["reason"])

    @unittest.skipIf(sys.platform == "win32", "uses ps and a symlinked launcher")
    def test_detects_a_claude_parent_started_with_the_channel(self):
        """A process whose argv[0] is .../claude and whose arguments load this server as a channel
        starts the channel server: it must serve the panels without CLAUDE_HAND_SUPERVISOR."""
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "claude"
            launcher.symlink_to(sys.executable)
            env = {**os.environ, "CLAUDE_HAND_HOME": str(Path(tmp) / "home"), "CLAUDE_HAND_AGENTS_DIR": str(Path(tmp) / "a"),
                   "CLAUDE_HAND_CHAT_PORT": "18785"}
            env.pop("CLAUDE_HAND_SUPERVISOR", None)
            child = f"import subprocess, sys; subprocess.run([sys.executable, {str(TOOL)!r}], input='', text=True)"
            for flags in (["--dangerously-load-development-channels", "server:claude-hand-chat"], []):
                subprocess.run([str(launcher), "-c", child, *flags], env=env, timeout=60, capture_output=True)
                status = json.loads((Path(tmp) / "home" / "chat" / "status.json").read_text(encoding="utf-8"))
                expected = "stopped" if flags else "passive"  # serving, then stopped when stdin closed
                self.assertEqual(status["mode"], expected, status)


class AncestorTests(unittest.TestCase):
    TABLE = {
        10: (9, "python tools/chat_channel.py"),
        9: (8, "uv run -q --no-project tools/chat_channel.py"),
        8: (7, '"C:\\Users\\me\\.local\\bin\\claude.exe" --dangerously-load-development-channels server:claude-hand-chat'),
        7: (1, "cmd.exe /c start-supervisor.cmd"),
    }

    def test_nearest_claude_with_channel_flag(self):
        self.assertIsNotNone(cc.channel_ancestor(self.TABLE, 10))

    def test_claude_without_flag_stops_the_search(self):
        table = dict(self.TABLE)
        table[8] = (7, "claude.exe")
        table[7] = (1, "claude --dangerously-load-development-channels server:claude-hand-chat")  # an outer session
        self.assertIsNone(cc.channel_ancestor(table, 10))

    def test_wrapper_text_is_not_claude(self):
        table = {10: (9, "python x"), 9: (1, "/bin/bash -c 'echo --channels server:claude-hand-chat' /root/.claude/x")}
        self.assertIsNone(cc.channel_ancestor(table, 10))

    def test_npm_installed_cli(self):
        self.assertTrue(cc.is_claude_code("node C:/Users/me/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/cli.js"))
        self.assertFalse(cc.is_claude_code("node server.js"))

    def test_decision_survives_a_broken_process_listing(self):
        def broken():
            raise OSError("no ps")
        serving, reason = cc.supervisor_decision(env={}, table_fn=broken)
        self.assertFalse(serving)
        self.assertIn("no ps", reason)
        self.assertTrue(cc.supervisor_decision(env={"CLAUDE_HAND_SUPERVISOR": "1"}, table_fn=broken)[0])

    def test_status_check_inside_the_supervisor_stays_passive(self):
        """chat-status, doctor and update run `claude mcp get`, which starts this server once and passes the
        supervisor's CLAUDE_HAND_SUPERVISOR=1 on to it; that copy must not take the panels over."""
        table = {
            20: (19, "python tools/chat_channel.py"),
            19: (18, "uv run -q --no-project tools/chat_channel.py"),
            18: (17, '"C:\\Users\\me\\AppData\\Roaming\\npm\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   '
                     "mcp get claude-hand-chat"),
            17: (8, 'C:\\Windows\\system32\\cmd.exe /c "C:\\Users\\me\\AppData\\Roaming\\npm\\claude.CMD" mcp get claude-hand-chat'),
            **self.TABLE,
        }
        supervisor_env = {"CLAUDE_HAND_SUPERVISOR": "1"}
        self.assertEqual(cc.supervisor_decision(env=supervisor_env, table_fn=lambda: table, pid=20),
                         (False, cc.HEALTH_CHECK_REASON))
        self.assertTrue(cc.supervisor_decision(env=supervisor_env, table_fn=lambda: table, pid=10)[0])


class WorkerTests(unittest.TestCase):
    """Who is at work in which window: rules the supervisor is told, backed by the channel where it can tell."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_agents(Path(self.tmp.name), more_unity=[handlib.discovery.Instance(name="Props", hash="bbbb2222")])
        self.emitted = Recorder()
        self.hub = cc.ChatHub(self.emitted, Path(self.tmp.name))
        self.hub.initialized = True

    def tearDown(self):
        self.tmp.cleanup()

    def act(self, agent_id="", pin="MyGame@aaaa1111"):
        return self.hub.report_action("unity", pin, "manage_gameobject", "create Cube", agent_id)

    def test_a_second_subagent_in_the_same_window_is_refused(self):
        self.assertNotIn("busy", self.act("aaa"))
        self.assertNotIn("busy", self.act("aaa"))  # the same one goes on
        second = self.act("bbb")
        self.assertEqual((second["busy"], second["other"]), (True, "aaa"))
        self.assertNotIn("busy", self.act("bbb", pin="Props@bbbb2222"))  # another window is another matter
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.assertNotIn("busy", self.act("bbb"))  # the first finished its turn: the window is free
        self.hub.workers["unity-mygame"]["last"] -= cc.LOCK_SECONDS + 1
        self.assertNotIn("busy", self.act("ccc"))  # a subagent that went quiet without stopping does not hold it forever

    def test_guards_that_report_no_agent_id_are_never_refused(self):
        self.assertNotIn("busy", self.act(""))
        self.assertNotIn("busy", self.act("aaa"))
        self.assertNotIn("busy", self.act(""))
        self.assertIsNone(self.hub.report_action("unity", "Ghost@ffffffff", "x", "", "aaa"))
        self.assertIsNone(self.hub.agent_stopped("unity", "Ghost@ffffffff", "aaa"))

    def test_the_tag_names_the_subagent_that_last_worked_in_the_window(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.assertNotIn("agent_id", self.emitted.messages[-1]["params"]["meta"])
        self.act("aaa")
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.hub.reply("unity-mygame", "만들었어요")
        self.hub.post_user_message("unity", "MyGame", "하나 더")
        meta = self.emitted.messages[-1]["params"]["meta"]
        self.assertEqual(meta["agent_id"], "aaa")  # the supervisor resumes it with SendMessage, whatever it still remembers
        self.assertTrue(all(key.isidentifier() for key in meta))

    def reminded(self, after):
        return self.hub.check_unanswered(time.time() + after)

    def test_the_supervisor_is_reminded_of_a_request_left_unanswered(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.act("aaa")
        self.assertEqual(self.reminded(cc.ANSWER_GRACE + 5), [])  # the subagent is still at work
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.assertEqual(self.reminded(cc.ANSWER_GRACE - 5), [])  # the supervisor gets a moment to answer
        before = len(self.emitted.messages)
        self.assertEqual(self.reminded(cc.ANSWER_GRACE + 5), ["unity-mygame"])
        note = self.emitted.messages[-1]
        self.assertEqual(len(self.emitted.messages), before + 1)
        self.assertEqual(note["method"], "notifications/claude/channel")
        self.assertTrue(note["params"]["content"].startswith("[claude-hand] "))
        self.assertIn("큐브 만들어줘", note["params"]["content"])
        self.assertEqual(note["params"]["meta"], {"chat_id": "unity-mygame", "app": "unity", "window": "MyGame", "agent": "unity-mygame",
                                                  "request": self.hub.logs["unity-mygame"][0]["request"], "reminder": "unanswered"})
        self.assertEqual(self.hub.updates("unity-mygame", 0, 0)[-1]["kind"], "notice")  # the user sees it was chased
        self.assertEqual(self.reminded(cc.STALL_SECONDS * 3), [])  # once per request

    def test_an_answered_request_is_not_chased(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.act("aaa")
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.hub.reply("unity-mygame", "작업 중이에요", working=True)
        self.assertEqual(self.reminded(cc.ANSWER_GRACE + 5), ["unity-mygame"])  # "working on it" is not the answer
        self.hub.post_user_message("unity", "MyGame", "하나 더")
        self.act("aaa")
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.hub.reply("unity-mygame", "만들었어요")
        self.assertEqual(self.reminded(cc.STALL_SECONDS * 3), [])

    def test_without_any_subagent_work_the_wait_is_longer(self):
        self.hub.post_user_message("unity", "MyGame", "안녕")
        self.assertEqual(self.reminded(cc.ANSWER_GRACE + 5), [])
        self.assertEqual(self.reminded(cc.STALL_SECONDS + 5), ["unity-mygame"])

    def test_requests_from_before_this_start_and_a_supervisor_still_starting_are_left_alone(self):
        self.hub.post_user_message("unity", "MyGame", "어제 한 말")
        self.hub.started = time.time() + 1  # as if shown again from history
        self.assertEqual(self.reminded(cc.STALL_SECONDS + 5), [])
        self.hub.started = 0.0
        self.hub.initialized = False
        self.assertEqual(self.reminded(cc.STALL_SECONDS + 5), [])

    def test_the_supervisor_gets_the_same_rules_from_the_channel_and_from_the_skill(self):
        rules = handlib.agents.prompt_template("chat-rules")
        self.assertEqual(cc.INSTRUCTIONS, rules)
        skill = handlib.agents.rendered_skill()
        self.assertIn(rules, skill)
        self.assertNotRegex(skill, r"\{\{\w+\}\}")
        for rule in ("`reply` 도구로", "`SendMessage`로", "`agent_id`", "`run_in_background`", "`working: true`", "`current_context`",
                     '`"desktop"`', "[claude-hand]", "[중지]를 눌렀습니다", "`changed_windows`", "`[요청 <번호>]`", "`[요청 없음]`"):
            self.assertIn(rule, rules)

    # ------------------------------------------------------------------ 중지

    def stop(self, window="MyGame"):
        return self.hub.request_stop("unity", window)

    def test_stop_refuses_the_next_call_and_tells_the_supervisor(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 백 개 만들어줘")
        self.assertNotIn("stop", self.act("aaa"))
        before = len(self.emitted.messages)
        self.assertEqual(self.stop(), {"chat_id": "unity-mygame", "stopping": True})
        note = self.emitted.messages[-1]
        self.assertEqual(len(self.emitted.messages), before + 1)
        self.assertTrue(note["params"]["content"].startswith("[claude-hand] 사용자가 이 채팅창에서 [중지]를 눌렀습니다"))
        self.assertIn("큐브 백 개 만들어줘", note["params"]["content"])
        self.assertEqual(note["params"]["meta"], {"chat_id": "unity-mygame", "app": "unity", "window": "MyGame", "agent": "unity-mygame",
                                                  "stop": "requested", "agent_id": "aaa",
                                                  # the request it stops: that turn's tokens are the request's too
                                                  "request": next(i["request"] for i in self.hub.logs["unity-mygame"] if i["kind"] == "user")})
        self.assertTrue(all(key.isidentifier() for key in note["params"]["meta"]))
        poll = self.hub.poll("unity-mygame", 0, 0)
        self.assertTrue(poll["progress"]["stopping"])  # the panel says so instead of offering the button again
        self.assertIn("중지를 요청했어요", poll["items"][-1]["text"])
        actions = poll["progress"]["actions"]
        refused = self.act("aaa")
        self.assertEqual((refused["stop"], refused["by"]), (True, "unity-mygame"))
        self.assertTrue(self.act("bbb")["stop"])  # a subagent handed the same request afterwards is refused too
        self.assertEqual(self.hub.poll("unity-mygame", 0, 0)["progress"]["actions"], actions)  # refused calls are not work
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.assertTrue(self.act("aaa")["stop"])  # it holds until the request is answered
        self.hub.reply("unity-mygame", "30개까지 만들고 멈췄어요")
        self.assertNotIn("stop", self.act("aaa"))
        self.assertEqual(self.hub.stops, {})

    def test_stop_covers_windows_working_for_that_request_but_not_a_window_with_its_own(self):
        self.hub.post_user_message("unity", "MyGame", "Props에 있는 의자 가져와")
        self.act("aaa", pin="Props@bbbb2222")  # Props works for MyGame's request
        self.stop()
        self.assertEqual(self.act("aaa", pin="Props@bbbb2222")["by"], "unity-mygame")
        self.hub.post_user_message("unity", "Props", "여기 바닥 색 바꿔줘")  # Props now has a request of its own
        self.assertNotIn("stop", self.act("ccc", pin="Props@bbbb2222"))
        self.assertTrue(self.act("bbb")["stop"])
        self.hub.post_user_message("unity", "MyGame", "아니다, 다시 해줘")  # a new message in the panel lifts its stop
        self.assertNotIn("stop", self.act("bbb"))

    def test_stop_leaves_a_side_by_side_project_alone_even_after_on_it_without_working(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 백 개 만들어줘")
        self.hub.post_user_message("unity", "Props", "바닥 색 바꿔줘")
        self.hub.reply("unity-props", "바꾸고 있어요. 잠시만 기다려 주세요.")  # "on it", sent without working
        self.act("aaa")
        self.stop()
        self.assertNotIn("stop", self.act("ccc", pin="Props@bbbb2222"))  # Props still works on its own panel's request
        self.assertTrue(self.act("aaa")["stop"])

    def test_stop_after_on_it_without_working_still_belongs_to_the_request(self):
        """What a real supervisor does: it answers "on it" without `working`, then hands the work out. The stop that
        followed was nobody's request: not in `trace`, and no bar to the other window working for it."""
        events = []
        self.hub.events = lambda event, **fields: events.append({"event": event, **fields})
        self.hub.post_user_message("blender", "props", "의자를 Props로 보내줘", pin="props@9877")
        theirs = self.request_of("blender-props")
        self.hub.reply("blender-props", "보내고 있어요. 잠시만 기다려 주세요.")
        self.hub.assign("unity-props", theirs)
        self.act("ccc", pin="Props@bbbb2222")
        self.assertTrue(self.hub.request_stop("blender", "props", "props@9877")["stopping"])
        self.assertIn("의자를 Props로 보내줘", self.emitted.messages[-1]["params"]["content"])  # the supervisor hears which request
        self.assertEqual([e["request"] for e in events if e["event"] == "stop"], [theirs])  # `trace` shows the stop under it
        self.assertEqual(self.act("ccc", pin="Props@bbbb2222")["by"], "blender-props")  # the window working for it is shut
        self.assertEqual(self.hub.assign("unity-props", theirs), {"ours": True, "chat_id": "unity-props", "stop": True})
        self.hub.assign("unity-mygame", "")  # asked in the Claude app meanwhile: none of that stop's business
        self.assertNotIn("stop", self.act("aaa"))

    def test_the_answer_to_stop_frees_the_windows_that_worked_for_the_request(self):
        """After 중지 the supervisor ends the subagents itself, and one that is killed runs no Stop hook: the window
        showed as at work for five more minutes, and was shut to another subagent."""
        self.hub.post_user_message("blender", "props", "의자를 Props로 보내줘", pin="props@9877")
        self.hub.reply("blender-props", "보내고 있어요.")
        self.hub.assign("unity-props", self.request_of("blender-props"))
        self.act("ccc", pin="Props@bbbb2222")
        self.hub.request_stop("blender", "props", "props@9877")
        self.hub.reply("blender-props", "중지했어요. 내보내기까지만 했어요.")
        self.assertEqual((self.hub.stops, self.hub.assigned, self.hub.early), ({}, {}, {}))
        self.assertIsNone(self.hub.poll("unity-props", 0, 0)["progress"])  # no work line left behind
        self.assertEqual({s["chat_id"]: s["state"] for s in self.hub.overview()}, {"unity-props": "idle"})
        self.assertNotIn("busy", self.act("ddd", pin="Props@bbbb2222"))  # free for another subagent
        # The same in the panel's own window, and with a stop pressed before any work was seen.
        self.hub.post_user_message("unity", "MyGame", "큐브 백 개 만들어줘")
        self.hub.reply("unity-mygame", "만들고 있어요.")
        self.act("aaa")
        self.stop()
        self.assertTrue(self.act("aaa")["stop"])
        self.hub.reply("unity-mygame", "중지했어요. 6개까지 만들었어요.")
        self.assertNotIn("busy", self.act("bbb"))
        self.hub.post_user_message("blender", "props", "하나 더 보내줘", pin="props@9877")
        self.hub.reply("blender-props", "보내고 있어요.")
        self.assertTrue(self.hub.request_stop("blender", "props", "props@9877")["stopping"])  # in the gap before the work starts
        self.hub.reply("blender-props", "중지했어요. 아직 아무것도 하지 않았어요.")
        self.assertEqual((self.hub.stops, self.hub.early), ({}, {}))  # answered: not kept open as another "on it"

    def test_one_undo_point_per_request_and_window_in_front_of_the_first_change(self):
        """Seen with a real supervisor: a window at work for another panel's request got a second undo point for it
        when its own panel was told "after this one", and a request that only looked left a point with nothing to undo."""
        self.hub.post_user_message("blender", "props", "의자를 Props로 보내줘", pin="props@9877")
        theirs = self.request_of("blender-props")
        self.hub.reply("blender-props", "보내고 있어요.")
        self.hub.assign("unity-props", theirs)
        look = self.hub.report_action("unity", "Props@bbbb2222", "ch_scene_snapshot", "", "ccc")
        self.assertFalse(look["new_request"])  # looking: nothing to undo yet
        first = self.hub.report_action("unity", "Props@bbbb2222", "ch_import_model", "", "ccc")
        self.assertEqual((first["new_request"], first["label"]), (True, "의자를 Props로 보내줘"))
        self.assertEqual((look["point"], first["point"]), (theirs, theirs))  # the point carries the id of the request asked in Blender
        self.hub.post_user_message("unity", "Props", "바닥 색 바꿔줘")  # the Props panel asks for something of its own meanwhile
        self.hub.reply("unity-props", "지금 하는 일이 끝나면 바로 할게요.")
        self.assertIsNotNone(self.hub.poll("unity-props", 0, 0)["progress"])  # the line of the work going on stays
        self.assertFalse(self.act("ccc", pin="Props@bbbb2222")["new_request"])  # still the same request: no second point
        self.hub.agent_stopped("unity", "Props@bbbb2222", "ccc")
        self.assertIsNone(self.hub.poll("unity-props", 0, 0)["progress"])  # the answer goes to the Blender panel: nothing else would take the line away
        self.hub.assign("ccc", theirs)  # one more turn for the same request (the supervisor resumes the subagent by its id)
        self.assertFalse(self.act("ccc", pin="Props@bbbb2222")["new_request"])
        self.hub.agent_stopped("unity", "Props@bbbb2222", "ccc")
        self.hub.reply("blender-props", "Props에 놓았어요")
        self.hub.assign("unity-props", self.request_of("unity-props"))  # now the Props panel's own request: its own point
        own = self.act("ccc", pin="Props@bbbb2222")
        self.assertEqual((own["new_request"], own["label"], own["point"]), (True, "바닥 색 바꿔줘", self.request_of("unity-props")))
        self.assertFalse(self.act("ccc", pin="Props@bbbb2222")["new_request"])

    def test_code_that_only_looks_sets_no_undo_point(self):
        """Seen with a real supervisor: "where is everything?" was answered with a tool that runs any code, which
        counted as a change. The request left an undo point with nothing to undo, and the panel's 되돌리기 did
        nothing the first time. The guard reads Blender's code and says when it only looks."""
        code = "execute_blender_code"
        self.hub.post_user_message("blender", "props", "뭐가 있는지 알려줘", pin="props@9877")
        self.hub.assign("blender-props", self.request_of("blender-props"))
        self.assertFalse(self.hub.report_action("blender", "props@9877", code, "print(bpy.data.filepath)", "bbb", looks=True)["new_request"])
        self.assertEqual(self.hub.poll("blender-props", 0, 0)["progress"]["actions"], 1)  # the work line shows it all the same
        self.hub.agent_stopped("blender", "props@9877", "bbb")
        self.hub.reply("blender-props", "Cube와 Sphere가 있어요")
        self.hub.post_user_message("blender", "props", "Cube를 옮겨줘")
        self.hub.assign("blender-props", self.request_of("blender-props"))
        self.assertFalse(self.hub.report_action("blender", "props@9877", code, "print(bpy.data.filepath)", "bbb", looks=True)["new_request"])
        move = self.hub.report_action("blender", "props@9877", code, "cube.location.x += 2", "bbb")
        self.assertEqual((move["new_request"], move["label"]), (True, "Cube를 옮겨줘"))  # in front of the first change
        self.assertFalse(self.hub.report_action("blender", "props@9877", code, "cube.location.x += 2", "bbb")["new_request"])

    def test_a_subagent_resumed_by_its_id_is_placed_after_a_restart(self):
        """A resumed supervisor goes on with the subagents of before, by id (SendMessage). A channel that had just
        started took such an id for none of its business, and the hand-out went by without a request."""
        history = Path(self.tmp.name) / "history"
        self.hub.use_history(history)
        self.hub.post_user_message("unity", "Props", "바닥 색 바꿔줘")
        self.act("ccc", pin="Props@bbbb2222")
        self.hub.agent_stopped("unity", "Props@bbbb2222", "ccc")
        self.hub.reply("unity-props", "바꿨어요")
        emitted = Recorder()
        restarted = cc.ChatHub(emitted, Path(self.tmp.name))
        restarted.use_history(history)
        restarted.initialized = True
        self.assertEqual(restarted.assign("ccc", None), {"ours": True, "chat_id": "unity-props", "missing": True})
        self.assertEqual(restarted.assign("zzz", None), {"ours": False})
        # The hook reads the subagent's name from the session's files: an id the channel never saw is placed too.
        self.assertEqual(restarted.assign("zzz", None, agent="unity-mygame"), {"ours": True, "chat_id": "unity-mygame", "missing": True})
        self.assertEqual(restarted.assign("zzz", "", agent="Explore"), {"ours": False})
        restarted.post_user_message("unity", "Props", "하나 더")
        self.assertNotIn("agent_id", emitted.messages[-1]["params"]["meta"])  # a new supervisor is not pointed at another session's agent
        self.assertEqual(restarted.logs["unity-props"][0]["text"], "바닥 색 바꿔줘")  # the history itself is as before

    def test_work_after_on_it_without_working_is_logged_under_its_own_request(self):
        events = []
        self.hub.events = lambda event, **fields: events.append({"event": event, **fields})
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.hub.post_user_message("unity", "Props", "바닥 색 바꿔줘")
        mine, props = (e["request"] for e in events if e["event"] == "message")
        self.hub.reply("unity-mygame", "만들고 있어요.")
        action = self.act("aaa")
        self.assertEqual((action["new_request"], action["label"]), (True, "큐브 만들어줘"))  # the undo point still names the request
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.hub.reply("unity-mygame", "만들었어요")
        self.assertEqual([e["request"] for e in events if e["event"] in ("action", "stopped", "reply")], [mine] * 4)
        self.assertNotEqual(mine, props)

    # ------------------------------------------------------------------ whose request (the supervisor's hook says)

    def request_of(self, chat_id):
        return next(i["request"] for i in reversed(self.hub.logs[chat_id]) if i["kind"] == "user")

    def test_the_supervisor_says_whose_request_a_windows_work_is_for(self):
        events = []
        self.hub.events = lambda event, **fields: events.append({"event": event, **fields})
        self.hub.post_user_message("unity", "MyGame", "큐브 백 개 만들어줘")
        self.hub.post_user_message("blender", "props", "의자를 Props로 보내줘", pin="props@9877")
        theirs = self.request_of("blender-props")
        # Two panels wait: by itself the channel cannot tell whose work Props is about to do.
        self.assertEqual(self.hub.assign("unity-props", theirs), {"ours": True, "chat_id": "unity-props", "asker": "blender-props"})
        action = self.act("ccc", pin="Props@bbbb2222")
        self.assertEqual((action["new_request"], action["label"]), (True, "의자를 Props로 보내줘"))  # the undo point names it
        self.assertEqual(self.hub.acted["unity-props"], {"blender-props": self.hub.seq})
        self.assertEqual([e["request"] for e in events if e["event"] in ("assigned", "action")], [theirs, theirs])
        self.stop()  # MyGame's 중지 leaves that work alone
        self.assertNotIn("stop", self.act("ccc", pin="Props@bbbb2222"))
        self.hub.request_stop("blender", "props", "props@9877")  # the asking panel's does not
        self.assertEqual(self.act("ccc", pin="Props@bbbb2222")["by"], "blender-props")
        # ... and no more of that request is handed out
        self.assertEqual(self.hub.assign("unity-props", theirs), {"ours": True, "chat_id": "unity-props", "stop": True})

    def test_a_hand_out_names_a_window_or_a_subagent_that_worked_there_and_ends_when_it_stops(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        mine = self.request_of("unity-mygame")
        self.assertEqual(self.hub.assign("general-purpose", mine), {"ours": False})  # not a window's subagent
        self.assertEqual(self.hub.assign("ccc", mine), {"ours": False})  # an id no window's subagent has reported
        self.assertEqual(self.hub.assign("unity-props", None), {"ours": True, "chat_id": "unity-props", "missing": True})  # no tag
        self.assertEqual(self.hub.assign("unity-props", "0101-000000-9"), {"ours": True, "chat_id": "unity-props", "unknown": True})
        self.assertEqual(self.hub.assigned, {})
        self.act("ccc", pin="Props@bbbb2222")  # not told: one panel waits, so the channel takes it for MyGame's
        self.hub.agent_stopped("unity", "Props@bbbb2222", "ccc")
        self.stop()
        self.assertEqual(self.act("ccc", pin="Props@bbbb2222")["by"], "unity-mygame")
        self.hub.agent_stopped("unity", "Props@bbbb2222", "ccc")
        # Resumed with SendMessage, the hook only has the subagent's id. [요청 없음]: asked in the terminal or the Claude app.
        self.assertEqual(self.hub.assign("ccc", ""), {"ours": True, "chat_id": "unity-props", "asker": ""})
        action = self.act("ccc", pin="Props@bbbb2222")  # nobody's panel request: MyGame's 중지 does not reach it
        self.assertEqual((action.get("stop"), action["label"]), (None, ""))
        self.hub.agent_stopped("unity", "Props@bbbb2222", "ccc")
        self.assertEqual(self.hub.assigned, {})  # that hand-out is over; the next one says anew whose it is
        self.assertEqual(self.act("ccc", pin="Props@bbbb2222")["by"], "unity-mygame")

    def test_a_hand_out_whose_subagent_never_said_it_stopped_does_not_hold_forever(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.hub.assign("unity-props", "")
        self.hub.assigned["unity-props"]["ts"] -= cc.PROGRESS_TTL + 1
        self.assertEqual(self.act("ccc", pin="Props@bbbb2222")["label"], "큐브 만들어줘")  # back to the channel's own guess
        self.assertEqual(self.hub.assigned, {})

    def test_assign_over_http(self):
        httpd = cc.start_http(self.hub, "t", port=18796)
        try:
            def post(body):
                req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/v1/assign", data=json.dumps(body).encode("utf-8"),
                                             method="POST", headers={"X-Claude-Hand-Token": "t", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read())
            self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
            self.assertEqual(post({"target": "unity-mygame"}), {"ok": True, "ours": True, "chat_id": "unity-mygame", "missing": True})
            self.assertEqual(post({"target": "unity-mygame", "request": self.request_of("unity-mygame")}),
                             {"ok": True, "ours": True, "chat_id": "unity-mygame", "asker": "unity-mygame"})
            self.assertEqual(post({"target": "Explore", "request": ""}), {"ok": True, "ours": False})
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_stop_does_not_outlast_a_supervisor_that_never_answers(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.act("aaa")
        self.stop()
        self.hub.stops["unity-mygame"]["ts"] -= cc.STOP_SECONDS + 1
        self.assertNotIn("stop", self.act("aaa"))

    def test_stop_with_nothing_going_on_does_nothing(self):
        before = len(self.emitted.messages)
        self.assertEqual(self.stop(), {"chat_id": "unity-mygame", "stopping": False})
        self.assertEqual(self.stop("Nobody"), {"chat_id": "unity-nobody", "stopping": False})
        self.assertEqual((len(self.emitted.messages), self.hub.stops), (before, {}))

    def test_stop_on_work_asked_elsewhere_ends_when_that_subagent_stops(self):
        self.act("aaa")  # asked in the Claude app: MyGame's panel waits on nothing, but shows the work line
        self.assertTrue(self.stop()["stopping"])
        self.assertNotIn("큐브", self.emitted.messages[-1]["params"]["content"])
        self.assertTrue(self.act("aaa")["stop"])
        self.assertNotIn("stop", self.act("ccc", pin="Props@bbbb2222"))  # only that window
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.assertNotIn("stop", self.act("bbb"))

    def test_stop_over_http(self):
        httpd = cc.start_http(self.hub, "t", port=18795)
        try:
            def post(body, token="t"):
                req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/v1/stop", data=json.dumps(body).encode("utf-8"),
                                             method="POST", headers={"X-Claude-Hand-Token": token, "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as exc:
                    with exc:
                        return exc.code, json.loads(exc.read())
            self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
            self.assertEqual(post({"app": "unity", "window": "MyGame"}), (200, {"ok": True, "chat_id": "unity-mygame", "stopping": True}))
            self.assertEqual(post({"app": "paint", "window": "MyGame"})[0], 400)
            self.assertEqual(post({"app": "unity", "window": "MyGame"}, token="wrong")[0], 401)
        finally:
            httpd.shutdown()
            httpd.server_close()


class UndoRequestTests(unittest.TestCase):
    """A request that changed several windows is undone in each of them: by the 되돌리기 of the panel it was asked in,
    or by the supervisor's undo_request tool for "cancel that"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = Path(self.tmp.name) / "agents"
        write_agents(self.agents, more_unity=[handlib.discovery.Instance(name="Props", hash="bbbb2222")])
        self.emitted = Recorder()
        self.hub = cc.ChatHub(self.emitted, self.agents)
        self.hub.initialized = True
        self.asked, self.answers = [], {}  # what each window was asked to undo, and what it says (default: undone)
        self.hub.undo_window = lambda app, pin, key: self.asked.append((pin, key)) or dict(self.answers.get(pin, {"state": "undone"}))

    def tearDown(self):
        self.tmp.cleanup()

    def request_of(self, chat_id):
        return next(i["request"] for i in reversed(self.hub.logs[chat_id]) if i["kind"] == "user")

    def work(self, pin, agent_id, tool="manage_gameobject", app="unity", request=None):
        """A window's subagent is handed work for a request, changes something and stops."""
        chat = next(a.name for a in handlib.agents.installed_agents(self.agents) if a.pin == pin)
        if request is not None:
            self.assertNotIn("busy", self.hub.assign(chat, request))
        action = self.hub.report_action(app, pin, tool, "", agent_id)
        self.hub.agent_stopped(app, pin, agent_id)
        return action

    def notices(self, chat_id):
        return [i["text"] for i in self.hub.logs.get(chat_id, []) if i["kind"] == "notice"]

    def send_model(self):
        """Asked in the Blender panel: Blender changes, and two Unity projects take the model."""
        self.hub.post_user_message("blender", "props", "의자를 고쳐서 두 프로젝트에 보내줘", pin="props@9877")
        request = self.request_of("blender-props")
        self.work("props@9877", "bbb", "execute_blender_code", "blender", request)
        self.work("MyGame@aaaa1111", "aaa", "ch_import_model", request=request)
        self.work("Props@bbbb2222", "ccc", "ch_import_model", request=request)
        self.hub.reply("blender-props", "두 프로젝트에 놓았어요", changed=["unity-mygame", "unity-props"])
        return request

    def test_the_request_remembers_the_windows_it_changed(self):
        self.hub.use_history(Path(self.tmp.name) / "history")
        self.hub.post_user_message("blender", "props", "의자 위치만 알려줘", pin="props@9877")
        self.work("props@9877", "bbb", "get_object_info", "blender", self.request_of("blender-props"))  # looked: no point
        self.hub.reply("blender-props", "(0, 0, 0)에 있어요")
        self.assertIsNone(self.hub.poll("blender-props", 0, 0)["undo"])
        request = self.send_model()
        item = next(i for i in self.hub.logs["blender-props"] if i.get("request") == request)
        self.assertEqual(item["points"], ["blender-props", "unity-mygame", "unity-props"])
        offer = self.hub.poll("blender-props", 0, 0)["undo"]
        self.assertEqual((offer["request"], offer["label"], offer["windows"], offer["ts"]),
                         (request, "의자를 고쳐서 두 프로젝트에 보내줘", ["MyGame (Unity)", "Props (Unity)"], item["ts"]))
        self.assertIsNone(self.hub.poll("unity-mygame", 0, 0)["undo"])  # not asked there: its own button undoes its own window
        # A request that changed only its own window is the panel's own business: nothing to offer for it ...
        self.hub.post_user_message("unity", "MyGame", "큐브 하나 만들어줘")
        self.work("MyGame@aaaa1111", "aaa", request=self.request_of("unity-mygame"))
        self.assertIsNone(self.hub.poll("unity-mygame", 0, 0)["undo"])
        # ... and the windows are still known after Claude Hand is started again.
        again = cc.ChatHub(lambda message: None, self.agents)
        again.use_history(Path(self.tmp.name) / "history")
        self.assertEqual(again.poll("blender-props", 0, 0)["undo"]["windows"], ["MyGame (Unity)", "Props (Unity)"])
        with unittest.mock.patch.object(cc, "OFFER_SECONDS", -1.0):
            self.assertIsNone(again.poll("blender-props", 0, 0)["undo"])  # not for ever

    def test_the_asking_panels_button_undoes_the_other_windows(self):
        request = self.send_model()
        self.answers["Props@bbbb2222"] = {"state": "blocked", "top": "바닥 색 바꿔줘 (10:20)"}
        self.answers["MyGame@aaaa1111"] = {"state": "undone", "summary": "파일 1개를 이전 것으로 되돌렸어요."}
        rev = self.hub.poll("blender-props", 0, 0)["progress_rev"]
        # The panel undid its own window itself and asks for the rest.
        done = self.hub.undo_request("blender-props", request, skip={"blender-props"})
        self.assertEqual(self.asked, [("MyGame@aaaa1111", request), ("Props@bbbb2222", request)])
        self.assertEqual([(r["chat_id"], r["state"]) for r in done["results"]], [("unity-mygame", "undone"), ("unity-props", "blocked")])
        self.assertEqual(self.notices("blender-props")[-1],
                         "'의자를 고쳐서 두 프로젝트에 보내줘' 요청 되돌리기: MyGame (Unity): 되돌렸어요 (파일 1개를 이전 것으로 되돌렸어요) / "
                         "Props (Unity): 그 뒤의 요청('바닥 색 바꿔줘 (10:20)')이 이 창을 바꿔서 건너뛰었어요. 그 창에서 먼저 되돌려 주세요")
        self.assertEqual(self.notices("unity-mygame"),  # the window that changed has it on record too
                         ["props (Blender) 채팅창의 요청 '의자를 고쳐서 두 프로젝트에 보내줘'을 여기서도 되돌렸어요. 파일 1개를 이전 것으로 되돌렸어요."])
        self.assertEqual(self.notices("unity-props"), [])
        state = self.hub.poll("blender-props", 0, 0)
        self.assertNotEqual(state["progress_rev"], rev)  # a waiting panel wakes and draws its button anew
        self.assertEqual(state["undo"]["windows"], ["Props (Unity)"])  # still to go back there, once that window lets it
        # The next message from that panel tells the supervisor, which still counts on what is gone.
        self.hub.post_user_message("blender", "props", "다시 보내줘", pin="props@9877")
        self.assertIn("[사용자가 채팅창에서 되돌렸음: '의자를 고쳐서 두 프로젝트에 보내줘' 요청 되돌리기: MyGame (Unity): 되돌렸어요",
                      self.emitted.messages[-1]["params"]["content"])
        self.hub.post_user_message("blender", "props", "하나 더", pin="props@9877")
        self.assertNotIn("되돌렸음", self.emitted.messages[-1]["params"]["content"])  # said once
        # Later that window is free: the same button finishes the job, and then has nothing left to offer.
        del self.answers["Props@bbbb2222"]
        self.hub.undo_request("blender-props", request, skip={"blender-props"})
        self.assertEqual(self.asked[2:], [("Props@bbbb2222", request)])  # MyGame is not asked again
        self.assertIsNone(self.hub.poll("blender-props", 0, 0)["undo"])
        self.assertEqual(self.hub.undo_request("blender-props", request), {"ok": False, "reason": "none"})

    def test_a_window_at_work_or_out_of_reach_is_passed_by(self):
        request = self.send_model()
        self.hub.post_user_message("unity", "MyGame", "큐브 백 개 만들어줘")
        self.hub.assign("unity-mygame", self.request_of("unity-mygame"))
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_gameobject", "create", "aaa")  # at work for a request of its own
        self.answers["Props@bbbb2222"] = {"state": "unreachable"}
        self.hub.undo_window = lambda app, pin, key, ask=self.hub.undo_window: 1 / 0 if app == "blender" else ask(app, pin, key)
        done = self.hub.undo_request("blender-props")  # as the supervisor's tool: every window, the asking one too
        self.assertEqual([(r["chat_id"], r["state"]) for r in done["results"]],
                         [("blender-props", "failed"), ("unity-mygame", "busy"), ("unity-props", "unreachable")])
        self.assertEqual(self.asked, [("Props@bbbb2222", request)])  # a window at work is not even asked
        self.assertIn("MyGame (Unity): 지금 작업 중이라 건너뛰었어요", done["text"])
        self.assertIn("props (Blender): 되돌리지 못했어요 (division by zero)", done["text"])
        self.assertEqual(self.hub.poll("blender-props", 0, 0)["undo"]["windows"], ["MyGame (Unity)", "Props (Unity)"])  # nothing went back

    def test_a_panel_that_undid_its_own_window_is_not_offered_again(self):
        request = self.send_model()
        self.assertEqual(self.hub.window_undone("unity", "MyGame", "", request), {"chat_id": "unity-mygame", "known": True})
        self.assertEqual(self.hub.poll("blender-props", 0, 0)["undo"]["windows"], ["Props (Unity)"])
        self.assertFalse(self.hub.window_undone("unity", "MyGame", "", request)["known"])
        self.assertFalse(self.hub.window_undone("unity", "MyGame", "", "")["known"])

    def test_the_supervisors_tool_undoes_the_newest_request_that_changed_something(self):
        request = self.send_model()
        self.hub.post_user_message("blender", "props", "방금 거 취소해줘", pin="props@9877")  # the newest message changed nothing
        done = self.hub.undo_request("blender-props", notify=False)
        self.assertEqual((done["request"], [r["state"] for r in done["results"]]), (request, ["undone"] * 3))
        self.assertEqual([pin for pin, _ in self.asked], ["props@9877", "MyGame@aaaa1111", "Props@bbbb2222"])
        self.assertEqual(self.notices("blender-props"), [])  # the supervisor answers that panel itself ...
        self.assertEqual(len(self.notices("unity-mygame")), 1)  # ... the other windows get their line
        self.hub.post_user_message("blender", "props", "고마워", pin="props@9877")
        self.assertNotIn("되돌렸음", self.emitted.messages[-1]["params"]["content"])  # it knows: it did it
        self.assertEqual(self.hub.undo_request("unity-mygame"), {"ok": False, "reason": "none"})  # nothing asked there changed a thing
        self.assertEqual(self.hub.undo_request("nobody"), {"ok": False, "reason": "none"})

    def test_a_second_press_waits_for_the_first(self):
        request = self.send_model()
        inside = []
        self.hub.undo_window = lambda app, pin, key: inside.append(self.hub.undo_request("blender-props", request)) or {"state": "undone"}
        self.assertTrue(self.hub.undo_request("blender-props", request)["ok"])
        self.assertEqual(inside, [{"ok": False, "reason": "running"}] * 3)

    def test_a_window_at_work_takes_no_other_requests_work(self):
        """One place per window says whose request its work is for. Work for another request handed to it meanwhile
        took that place: the rest of the first work, its undo point and its 중지 went down for the wrong request."""
        self.hub.post_user_message("blender", "props", "의자를 MyGame으로 보내줘", pin="props@9877")
        theirs = self.request_of("blender-props")
        self.assertNotIn("busy", self.hub.assign("unity-mygame", theirs))
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "", "aaa")
        self.hub.post_user_message("unity", "MyGame", "바닥 색 바꿔줘")
        own = self.request_of("unity-mygame")
        refused = self.hub.assign("unity-mygame", own)
        self.assertEqual((refused["busy"], refused["other"]), (True, "의자를 MyGame으로 보내줘"))
        self.assertEqual(self.hub.report_action("unity", "MyGame@aaaa1111", "manage_material", "", "aaa")["point"], theirs)  # still theirs
        self.assertNotIn("busy", self.hub.assign("aaa", theirs))  # more of the same request is no other request
        self.assertTrue(self.hub.assign("unity-mygame", "")["busy"])  # nor may work asked in the Claude app cut in
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.assertNotIn("busy", self.hub.assign("unity-mygame", own))  # done: the window is free
        # A subagent that never touched its window stops too: what it was handed must not keep the window shut.
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "ddd")
        self.assertNotIn("busy", self.hub.assign("unity-mygame", theirs))
        # One that was cut off never says it stopped: the window opens again after the time two subagents are kept apart.
        with unittest.mock.patch.object(cc, "LOCK_SECONDS", -1.0):
            self.assertNotIn("busy", self.hub.assign("unity-mygame", own))

    def test_the_panels_and_the_supervisor_reach_it(self):
        request = self.send_model()
        server = cc.start_http(self.hub, "t", port=18791)
        try:
            def post(path, body):
                req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", method="POST", data=json.dumps(body).encode(),
                                             headers={"X-Claude-Hand-Token": "t", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            self.assertTrue(post("/v1/undone", {"app": "unity", "window": "Props", "request": request})["known"])
            started = post("/v1/undo", {"app": "blender", "window": "props", "pin": "props@9877", "request": request, "skip_own": True})
            self.assertEqual((started["ok"], started["started"]), (True, True))
            deadline = time.time() + 5
            while not self.notices("blender-props") and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(self.asked, [("MyGame@aaaa1111", request)])  # not the panel's own window, nor the one that undid itself
            self.assertIn("MyGame (Unity): 되돌렸어요", self.notices("blender-props")[-1])
            nothing = post("/v1/undo", {"app": "unity", "window": "MyGame", "request": ""})
            self.assertEqual((nothing["ok"], nothing["started"], nothing["error"]), (False, False, "그 요청이 바꾼 다른 창이 없습니다"))
        finally:
            server.shutdown()
            server.server_close()
        # The supervisor's tool, over MCP.
        request = self.send_model()
        mcp = cc.StdioMcp(io.StringIO(), io.StringIO(), self.agents)
        mcp.hub = self.hub
        result = mcp.call_tool({"name": "undo_request", "arguments": {"chat_id": "blender-props"}})
        told = json.loads(result["content"][0]["text"])
        self.assertEqual((told["request"], [w["state"] for w in told["windows"]]), (request, ["undone"] * 3))
        self.assertIn("MyGame (Unity): 되돌렸어요", told["in_words"])
        again = mcp.call_tool({"name": "undo_request", "arguments": {"chat_id": "blender-props"}})
        self.assertTrue(again["isError"])
        self.assertIn("hand that undo to the window's subagent", again["content"][0]["text"])


class UndoInWindowTests(unittest.TestCase):
    """How one window is asked to undo a request by its id, and what its answer comes to."""

    def unity(self, answer):
        calls = []

        def request(entry, tool, params, timeout):
            calls.append((entry["hash"], tool, params))
            if isinstance(answer, Exception):
                raise answer
            return answer
        with unittest.mock.patch.object(cc, "find_project", lambda hash_: {"hash": hash_, "port": 1} if hash_ == "aaaa1111" else None), \
                unittest.mock.patch.object(cc, "unity_request", request):
            return cc.undo_in_window("unity", "MyGame@aaaa1111", "1123-101500-7"), calls, cc.undo_in_window("unity", "Gone@ffff0000", "x")

    def test_unity_through_the_bridge(self):
        done, calls, closed = self.unity({"success": True, "message": "Reverted", "data": {"state": "undone", "summary": "파일 1개를 이전 것으로 되돌렸어요."}})
        self.assertEqual(calls, [("aaaa1111", "ch_checkpoint", {"action": "undo_request", "request": "1123-101500-7"})])
        self.assertEqual((done["state"], done["summary"]), ("undone", "파일 1개를 이전 것으로 되돌렸어요."))
        self.assertEqual(closed, {"state": "unreachable"})  # that project is not open
        blocked, _, _ = self.unity({"success": True, "data": {"state": "blocked", "top": "바닥 색 (10:20)"}})
        self.assertEqual((blocked["state"], blocked["top"]), ("blocked", "바닥 색 (10:20)"))
        self.assertEqual(self.unity({"success": True, "data": {"state": "nothing"}})[0]["state"], "nothing")
        # A package from before undo_request: it must say no, never undo whatever is on top.
        old, _, _ = self.unity({"success": False, "code": "INVALID_ARGS", "error": "INVALID_ARGS",
                                "data": {"code": "INVALID_ARGS", "message": "action must be mark, undo or list.", "retry": "after_fix"}})
        self.assertEqual((old["state"], old["detail"]), ("failed", "action must be mark, undo or list."))
        self.assertEqual(self.unity({"success": True, "data": {"label": "x"}})[0]["state"], "failed")  # an answer it does not know
        self.assertEqual(self.unity(cc.BridgeError("UNREACHABLE", "Unity MyGame does not answer"))[0]["state"], "unreachable")
        self.assertEqual(self.unity(cc.BridgeError("FAILED", "boom"))[0], {"state": "failed", "detail": "boom"})

    def test_blender_through_the_chat_addon(self):
        sent = []

        def blender(reply):
            def request(port, command, timeout):
                sent.append((port, command))
                if isinstance(reply, Exception):
                    raise reply
                return reply
            with unittest.mock.patch.object(cc, "blender_request", request):
                return cc.undo_in_window("blender", "props@9877", "1123-101500-7")
        printed = lambda text: {"status": "success", "result": {"executed": True, "result": text}}
        self.assertEqual(blender(printed('CH_UNDO {"state": "undone", "detail": "되돌렸어요"}\n'))["state"], "undone")
        port, command = sent[0]
        self.assertEqual((port, command["type"]), (9877, "execute_code"))
        said = {}
        addon = type("Addon", (), {"undo_request": staticmethod(lambda request: said.update(request=request) or {"state": "nothing"})})
        exec(command["params"]["code"].replace("import claude_hand_chat, json\n", "import json\n"), {"claude_hand_chat": addon, "print": lambda text: said.update(out=text)})
        self.assertEqual(said, {"request": "1123-101500-7", "out": 'CH_UNDO {"state": "nothing"}'})
        blocked = blender(printed('CH_UNDO {"state": "blocked", "top": "의자 (10:20)"}'))
        self.assertEqual((blocked["state"], blocked["top"]), ("blocked", "의자 (10:20)"))
        # An add-on from before undo_request: an error, not an undo.
        old = blender({"status": "error", "message": "module 'claude_hand_chat' has no attribute 'undo_request'"})
        self.assertEqual((old["state"], old["detail"]), ("failed", "module 'claude_hand_chat' has no attribute 'undo_request'"))
        self.assertEqual(blender(printed("something else"))["state"], "failed")
        self.assertEqual(blender(ConnectionRefusedError()), {"state": "unreachable"})
        self.assertEqual(blender(None)["state"], "failed")


class CostTests(unittest.TestCase):
    """What a request took: a line under its result in the panel and one in the event log, once the supervisor's turn
    that answered is over (the tokens of that turn's last answer are in its transcript only then)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = Path(self.tmp.name) / "agents"
        write_agents(self.agents)
        self.hub = cc.ChatHub(Recorder(), self.agents)
        self.hub.initialized = True
        self.logged = []
        self.hub.events = lambda event, **fields: self.logged.append((event, fields))
        self.transcript = Path(self.tmp.name) / "projects" / "2bfcab59-32d4-4e90-8df7-37b79974a4e9.jsonl"
        self.transcript.parent.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, text="큐브 만들어줘"):
        self.hub.post_user_message("unity", "MyGame", text)
        return self.hub.logs["unity-mygame"][-1]["request"]

    def act(self, times=1):
        for _ in range(times):
            self.hub.report_action("unity", "MyGame@aaaa1111", "manage_gameobject", "create Cube", "aaa")
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")

    def waited(self, request, seconds):
        """As if the answer had come that long after the message."""
        self.hub.costs[request]["asked"] = self.hub.costs[request]["answered"] - seconds

    def turn(self, request, *answers):
        """The supervisor's turn for a panel message as Claude Code writes it to its transcript (see test_usage.py):
        answers are (seconds from now, tokens written, how it ended)."""
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"
        now = time.time()
        lines = [{"type": "user", "origin": {"kind": "channel"}, "timestamp": iso(now - 5),
                  "message": {"content": f'<channel source="claude-hand" chat_id="unity-mygame" request="{request}">x</channel>'}}]
        lines += [{"type": "assistant", "timestamp": iso(now + after),
                   "message": {"id": f"msg-{after}", "content": [{"type": "text", "text": "x"}], "stop_reason": stop,
                               "usage": {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 46000,
                                         "output_tokens": output}}} for after, output, stop in answers]
        with open(self.transcript, "a", encoding="utf-8", newline="\n") as f:
            f.write("".join(json.dumps(line) + "\n" for line in lines))

    def notices(self):
        return [i["text"] for i in self.hub.logs["unity-mygame"] if i["kind"] == "notice"]

    def costs_logged(self):
        return [fields for event, fields in self.logged if event == "cost"]

    def test_a_line_under_the_result_once_the_supervisors_turn_is_over(self):
        request = self.ask()
        self.assertNotIn("busy", self.hub.assign("unity-mygame", request, transcript=str(self.transcript)))  # the hook names the file
        self.assertEqual(self.hub.transcript, self.transcript)
        self.act(times=2)
        self.hub.reply("unity-mygame", "만들었어요")
        self.waited(request, 32.4)
        self.assertEqual(self.hub.check_costs(), [])  # nothing written yet: the turn is still going
        self.turn(request, (1, 100, "tool_use"))
        self.assertEqual((self.hub.check_costs(), self.notices()), ([], []))  # the reply call is in the file, the turn's end is not
        self.turn(request, (2, 20, "end_turn"))
        self.assertEqual(self.hub.check_costs(), [request])
        self.assertEqual(self.notices(), ["이 요청: 32초 · 동작 2번 · 토큰 출력 120 · 새 입력 60 · 다시 읽기 9.2만"])
        self.assertEqual((self.hub.check_costs(), len(self.notices()), self.hub.costs), ([], 1, {}))  # said once
        logged, = self.costs_logged()
        self.assertEqual({k: logged[k] for k in ("request", "chat_id", "seconds", "actions")},
                         {"request": request, "chat_id": "unity-mygame", "seconds": 32.4, "actions": 2})
        self.assertEqual(sorted(logged["tokens"]), ["감독"])
        self.assertEqual({k: logged["tokens"]["감독"][k] for k in ("calls", "output", "input", "cached")},
                         {"calls": 2, "output": 120, "input": 60, "cached": 92000})
        self.assertTrue(logged["text"].startswith("감독 "))
        self.assertIn("API 2번 출력 120 · 새 입력 60 · 다시 읽기 9.2만", logged["text"])
        json.dumps(logged)  # as the event log writes it

    def test_without_a_transcript_the_line_says_the_time_alone(self):
        request = self.ask()
        self.act()
        self.hub.reply("unity-mygame", "만들었어요")
        self.waited(request, 5)
        self.assertEqual(self.hub.check_costs(), [request])  # no file to wait for
        self.assertEqual(self.notices(), ["이 요청: 5초 · 동작 1번"])
        logged, = self.costs_logged()
        self.assertEqual((logged["tokens"], logged["text"], logged["seconds"]), (None, "", 5.0))
        # A transcript that cannot be found or read costs the tokens, never the line.
        self.hub.find_transcript = lambda: 1 / 0
        again = self.ask("하나 더")
        self.hub.reply("unity-mygame", "여기 있어요")
        self.waited(again, 61)
        with unittest.mock.patch.object(cc, "log", lambda *a: None):
            self.assertEqual(self.hub.check_costs(), [again])
        self.assertEqual(self.notices()[-1], "이 요청: 1분 1초")  # nothing was done in a window: no "동작"

    def test_a_turn_that_never_shows_its_end_is_not_waited_for_forever(self):
        self.hub.find_transcript = lambda: self.transcript  # where start-supervisor's session keeps it, before any hook said so
        request = self.ask()
        self.hub.reply("unity-mygame", "만들었어요")
        self.waited(request, 3)
        self.turn(request, (1, 100, "tool_use"))
        answered = self.hub.costs[request]["answered"]
        self.assertEqual(self.hub.check_costs(answered + cc.COST_WAIT - 1), [])
        self.assertEqual(self.hub.check_costs(answered + cc.COST_WAIT + 1), [request])
        self.assertEqual(self.notices(), ["이 요청: 3초 · 토큰 출력 100 · 새 입력 30 · 다시 읽기 4.6만"])  # what the file had by then

    def test_the_line_can_be_kept_out_of_the_panels(self):
        self.hub.show_cost = False  # config.json "show_cost": false
        request = self.ask()
        self.hub.reply("unity-mygame", "만들었어요")
        self.assertEqual((self.hub.check_costs(), self.notices()), ([request], []))
        self.assertEqual(len(self.costs_logged()), 1)  # trace and usage still have it

    def test_on_it_sent_as_if_it_were_the_result_gets_no_line_of_its_own(self):
        request = self.ask()
        self.hub.reply("unity-mygame", "지금 만들게요")  # no `working`, and nothing done yet: the channel takes it for "on it"
        self.hub.assign("unity-mygame", request)
        self.assertEqual(self.hub.check_costs(time.time() + cc.COST_WAIT + 1), [])  # handed out: the result is yet to come
        self.act(times=3)
        self.assertEqual(self.hub.check_costs(time.time() + cc.COST_WAIT + 1), [])  # worked and stopped: still no result
        self.hub.reply("unity-mygame", "만들었어요")
        self.waited(request, 40)
        self.assertEqual(self.hub.check_costs(), [request])
        self.assertEqual(self.notices(), ["이 요청: 40초 · 동작 3번"])
        # The result never comes (the supervisor was interrupted): once the channel stops waiting for it, the line says
        # what there is instead of staying behind for good.
        again = self.ask("하나 더")
        self.hub.reply("unity-mygame", "지금 만들게요")
        self.act()
        self.assertEqual(self.hub.check_costs(), [])
        self.assertEqual(self.hub.check_costs(time.time() + cc.PROGRESS_TTL + 1), [again])

    def test_a_line_that_comes_after_the_panels_next_message_says_which_request_it_is_about(self):
        self.hub.find_transcript = lambda: self.transcript
        first = self.ask("빨간 큐브를 원점에 하나 만들고 이름을 Box로 바꿔줘")
        self.act()
        self.hub.reply("unity-mygame", "만들었어요")
        self.waited(first, 12)
        self.assertEqual(self.hub.check_costs(), [])  # the supervisor's turn goes on (with another window's request, say)
        second = self.ask("하나 더")
        self.turn(first, (1, 100, "end_turn"))
        self.assertEqual(self.hub.check_costs(), [first])
        line, = self.notices()
        self.assertTrue(line.startswith("앞 요청 “빨간 큐브를 원점에 하나 만들고"), line)
        self.assertTrue(line.endswith("…”: 12초 · 동작 1번 · 토큰 출력 100 · 새 입력 30 · 다시 읽기 4.6만"), line)  # a few words of it, not all
        self.hub.reply("unity-mygame", "여기 있어요")
        self.waited(second, 3)
        self.turn(second, (2, 10, "end_turn"))
        self.assertEqual(self.hub.check_costs(), [second])
        self.assertTrue(self.notices()[-1].startswith("이 요청: 3초 · 토큰 출력 10 "))

    def test_only_a_panels_result_is_counted(self):
        self.hub.reply("unity-mygame", "Props 씬을 저장했어요", origin="desktop", request="씬 저장")  # asked in the Claude app
        self.assertEqual(self.hub.costs, {})
        request = self.ask()
        self.hub.reply("unity-mygame", "하는 중이에요", working=True)
        self.assertEqual((self.hub.costs, self.hub.check_costs()), ({}, []))
        self.hub.reply("unity-mygame", "만들었어요")
        self.assertEqual(list(self.hub.costs), [request])

    def test_an_answer_that_comes_while_the_files_are_read_is_the_one_to_wait_for(self):
        request = self.ask()
        self.hub.reply("unity-mygame", "만들었어요")
        hub = self.hub

        class Ledger:  # reading takes a moment, and meanwhile the supervisor answers the request anew
            def update(self):
                hub.costs[request] = dict(hub.costs[request])

            settled = lambda self, request, after: True
            cost = lambda self, request: {}
            resting = lambda self: (True, 0.0)
        self.hub._ledger = lambda: Ledger()
        self.assertEqual((self.hub.check_costs(), self.notices(), list(self.hub.costs)), ([], [], [request]))

    def test_the_watcher_says_it_without_being_asked(self):
        self.ask()
        self.hub.reply("unity-mygame", "만들었어요")
        said = threading.Event()

        def until_said():  # in check_dialogs' place: ends the watcher once the line is in the panel
            if self.notices():
                said.set()
                raise SystemExit
            return []
        self.hub.check_dialogs = until_said
        watcher = threading.Thread(target=self.hub.watch, args=(0.01,), daemon=True)
        watcher.start()
        self.assertTrue(said.wait(10))
        watcher.join(10)
        self.assertEqual((len(self.notices()), self.hub.poll("unity-mygame", 0, 0)["items"][-1]["cost"]), (1, True))

    def test_the_hook_says_where_the_transcript_is(self):
        self.assertEqual(self.hub.assign("Explore", None, transcript=str(self.transcript)), {"ours": False})  # any hand-out will do
        self.assertEqual(self.hub.transcript, self.transcript)
        self.hub.assign("Explore", None, transcript="C:/somewhere/else")  # not a transcript
        self.hub.assign("Explore", None)                                  # a hook from before this
        self.assertEqual(self.hub.transcript, self.transcript)
        self.turn("1123-120000-1", (1, 7, "end_turn"))
        first = self.hub._ledger()
        self.assertIs(self.hub._ledger(), first)  # one ledger: the files are read on from where it stopped
        other = self.transcript.with_name("3cfcab59-32d4-4e90-8df7-37b79974a4e9.jsonl")
        self.hub.assign("Explore", None, transcript=str(other))  # a supervisor that was started anew
        self.assertEqual(self.hub._ledger().transcript, other)
        self.hub.transcript, self.hub.find_transcript = None, None
        self.assertIsNone(self.hub._ledger())


class HeldTests(unittest.TestCase):
    """A message for the supervisor waits while its turn goes on: Claude Code slips what arrives during a turn into that
    turn, and the supervisor then does not take it for the user's word (it asked for a request sent right after a result
    to be sent again, 2026-11-24). Whether a turn goes on is read from the supervisor's transcript."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = Path(self.tmp.name) / "agents"
        write_agents(self.agents)
        self.emitted = Recorder()
        self.hub = cc.ChatHub(self.emitted, self.agents)
        self.hub.initialized = True
        self.logged = []
        self.hub.events = lambda event, **fields: self.logged.append((event, fields))
        self.transcript = Path(self.tmp.name) / "projects" / "2bfcab59-32d4-4e90-8df7-37b79974a4e9.jsonl"
        self.transcript.parent.mkdir()
        self.hub.find_transcript = lambda: self.transcript

    def tearDown(self):
        self.tmp.cleanup()

    def wrote(self, at, kind, stop=None, **more):
        """A line of the supervisor's transcript, written `at` (seconds since the epoch); stop: how an answer ended."""
        iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + f".{int(at * 1000) % 1000:03d}Z"
        line = {"type": kind, "timestamp": iso, **more}
        if kind == "assistant":
            line["message"] = {"id": f"msg-{at}", "content": [{"type": "text", "text": "x"}], "stop_reason": stop,
                               "usage": {"input_tokens": 1, "output_tokens": 1}}
        with open(self.transcript, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line) + "\n")

    def said(self):
        return [m["params"]["content"].split("\n")[0] for m in self.emitted.messages]

    def events_of(self, kind):
        return [fields for event, fields in self.logged if event == kind]

    def test_a_message_goes_at_once_when_nothing_says_a_turn_goes_on(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")  # the file is not there yet: nothing to go by
        self.assertEqual((self.said(), self.hub.held), (["큐브 만들어줘"], []))
        other = cc.ChatHub(Recorder(), self.agents)  # no transcript known at all: as it always was
        other.initialized = True
        other.post_user_message("unity", "MyGame", "하나 더")
        self.assertEqual((len(other.emit.messages), other.held), (1, []))

    def test_a_message_that_arrives_during_the_supervisors_turn_waits_for_its_end(self):
        start = time.time()
        self.hub.post_user_message("unity", "MyGame", "속도를 두 배로")
        self.wrote(start + 0.1, "user", origin={"kind": "channel"}, message={"content": "<channel>x</channel>"})
        self.wrote(start + 0.2, "assistant", stop="tool_use")  # at work: the reply call, a hand-out
        self.hub.post_user_message("unity", "MyGame", "큐브 하나 만들어줘")
        self.assertEqual((self.said(), len(self.hub.held)), (["속도를 두 배로"], 1))
        self.assertEqual(self.hub.logs["unity-mygame"][-1]["text"], "큐브 하나 만들어줘")  # the panel shows it as sent all the same
        self.assertEqual([e["text"] for e in self.events_of("held")], ["감독이 차례 중이라, 차례가 끝나면 넘김"])
        self.assertEqual(self.hub.check_held(start + 5), 0)  # the turn still goes on
        self.wrote(start + 6, "assistant", stop="end_turn")
        self.wrote(start + 6.1, "system", subtype="turn_duration")  # says nothing either way
        self.assertEqual(self.hub.check_held(start + 7), 0)  # just over: a turn that starts right behind it would show first
        self.assertEqual(self.hub.check_held(start + 7.6), 1)
        self.assertEqual((self.said(), self.hub.held), (["속도를 두 배로", "큐브 하나 만들어줘"], []))
        self.assertRegex(self.events_of("released")[0]["text"], r"^\d+\.\d초 기다림$")
        self.assertEqual(self.hub.check_held(start + 9), 0)  # nothing left

    def test_a_conversation_compacted_while_at_rest_keeps_nothing_back(self):
        """Claude Code writes the summary as a user line. Taken for a message, it left the supervisor "in a turn" until
        its next answer: the first request after it waited the whole HOLD_SECONDS (two hours after a compaction)."""
        start = time.time()
        self.wrote(start - 9000, "user", origin={"kind": "channel"}, message={"content": "<channel>x</channel>"})
        self.wrote(start - 8990, "assistant", stop="end_turn")
        self.wrote(start - 8000, "system", subtype="compact_boundary")
        self.wrote(start - 8000, "user", isCompactSummary=True, message={"content": "This session is being continued from a previous conversation"})
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.assertEqual((self.said(), self.hub.held), (["큐브 만들어줘"], []))

    def test_a_conversation_compacted_in_the_middle_of_a_turn_is_still_in_it(self):
        start = time.time()
        self.wrote(start - 20, "user", origin={"kind": "channel"}, message={"content": "<channel>x</channel>"})
        self.wrote(start - 15, "assistant", stop="tool_use")
        self.wrote(start - 10, "user", isCompactSummary=True, message={"content": "This session is being continued from a previous conversation"})
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        self.assertEqual((self.said(), len(self.hub.held)), ([], 1))

    def test_messages_go_in_the_order_they_came_one_turn_each(self):
        start = time.time()
        self.wrote(start - 1, "user", origin={"kind": "peer"}, message={"content": "report"})  # a subagent's report is being read
        self.hub.post_user_message("unity", "MyGame", "하나")
        self.hub.post_user_message("unity", "MyGame", "둘")
        self.assertEqual((self.said(), len(self.hub.held)), ([], 2))
        self.wrote(start + 1, "assistant", stop="end_turn")
        self.assertEqual((self.hub.check_held(start + 3), self.said()), (1, ["하나"]))
        self.assertEqual(self.hub.check_held(start + 6), 0)  # handed over, not in the transcript yet: its turn is about to start
        self.wrote(start + 6.5, "queue-operation", operation="enqueue")
        self.wrote(start + 6.6, "user", origin={"kind": "channel"}, message={"content": "<channel>하나</channel>"})
        self.assertEqual(self.hub.check_held(start + 9), 0)  # and now it goes on
        self.wrote(start + 12, "assistant", stop="end_turn")
        self.assertEqual((self.hub.check_held(start + 14), self.said()), (1, ["하나", "둘"]))

    def test_a_subagent_that_just_stopped_means_a_turn_is_about_to_start(self):
        start = time.time()
        self.wrote(start - 30, "assistant", stop="end_turn")  # at rest, waiting for its subagent
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_gameobject", "create Cube", "aaa")
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")  # its report is on its way to the supervisor
        self.hub.post_user_message("unity", "MyGame", "큐브 하나 더")
        self.assertEqual((self.said(), len(self.hub.held)), ([], 1))
        self.wrote(time.time() + 0.5, "user", origin={"kind": "task-notification"}, message={"content": "<task-id>aaa</task-id>"})
        self.wrote(time.time() + 4, "assistant", stop="end_turn")  # the result went to the panel; the turn is over
        self.assertEqual((self.hub.check_held(time.time() + 6), self.said()), (1, ["큐브 하나 더"]))
        # A report that never shows in the transcript does not keep messages back for good.
        self.hub.agent_stopped("unity", "MyGame@aaaa1111", "aaa")
        self.hub.post_user_message("unity", "MyGame", "또 하나")
        self.assertEqual(len(self.hub.held), 1)
        coming = max(self.hub.handed_at, self.hub.agent_stop_at)
        self.assertEqual(self.hub.check_held(coming + cc.ARRIVAL_SECONDS - 2), 0)
        self.assertEqual(self.hub.check_held(coming + cc.ARRIVAL_SECONDS + 1), 1)

    def test_no_message_waits_longer_than_the_longest_hold_and_stops_wait_like_the_rest(self):
        start = time.time()
        self.hub.post_user_message("unity", "MyGame", "큐브 서른 개")
        self.hub.report_action("unity", "MyGame@aaaa1111", "manage_gameobject", "create Cube", "aaa")
        self.wrote(start + 0.1, "user", origin={"kind": "channel"}, message={"content": "<channel>x</channel>"})
        self.wrote(start + 0.2, "assistant", stop="tool_use")
        self.assertTrue(self.hub.request_stop("unity", "MyGame")["stopping"])  # 중지: the block on the subagent's calls is at once ...
        self.assertEqual((len(self.said()), len(self.hub.held)), (1, 1))        # ... the word to the supervisor waits for its turn
        self.assertEqual(self.hub.check_held(start + cc.HOLD_SECONDS - 5), 0)
        self.assertEqual(self.hub.check_held(start + cc.HOLD_SECONDS + 5), 1)   # a turn that does not end: late is better than never
        self.assertIn("[중지]를 눌렀습니다", self.said()[-1])
        self.assertIn("그냥 넘김", self.events_of("released")[0]["text"])


if __name__ == "__main__":
    unittest.main()
