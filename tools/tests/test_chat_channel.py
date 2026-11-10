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
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["reply", "current_context"])
        self.assertEqual(replies[2], {"jsonrpc": "2.0", "id": 3, "result": {}})
        self.assertEqual(replies[3]["error"]["code"], -32601)

    def test_reply_tool(self):
        mcp, _ = self.run_session([])
        mcp.hub.logs["unity-mygame"] = []
        ok = mcp.call_tool({"name": "reply", "arguments": {"chat_id": "unity-mygame", "text": "done"}})
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
        context = mcp.call_tool({"name": "current_context", "arguments": {}})
        self.assertEqual(json.loads(context["content"][0]["text"])["windows"], [])


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
        self.assertEqual(first, {"chat_id": "unity-mygame", "new_request": True, "label": "큐브 세 개를 일렬로 놓아줘"})
        self.assertFalse(self.hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "save")["new_request"])
        state = self.hub.poll("unity-mygame", 0, 0)
        self.assertEqual((state["progress"]["tool"], state["progress"]["detail"], state["progress"]["actions"]),
                         ("manage_scene", "save", 2))
        self.hub.reply("unity-mygame", "다 했어요")
        self.assertIsNone(self.hub.poll("unity-mygame", 0, 0)["progress"])
        # the next request (here asked in the Claude app, so no panel text) gets its own undo point
        self.assertEqual(self.hub.report_action("unity", "MyGame@aaaa1111", "manage_scene", "get_hierarchy"),
                         {"chat_id": "unity-mygame", "new_request": True, "label": ""})

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

    def test_a_working_on_it_reply_gets_no_picture_but_the_result_does(self):
        self.hub.post_user_message("blender", "props", "의자를 Unity로 보내줘", pin="props@9877")
        self.hub.report_action("unity", "MyGame@aaaa1111", "ch_import_model", "")
        self.hub.reply("blender-props", "Unity에 놓는 중이에요", working=True)
        time.sleep(0.3)
        self.assertEqual(self.shots, [])
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
        self.assertEqual(note["params"]["meta"], {"chat_id": "unity-mygame", "app": "unity", "window": "MyGame",
                                                  "agent": "unity-mygame", "reminder": "unanswered"})
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
                     '`"desktop"`', "[claude-hand]"):
            self.assertIn(rule, rules)


if __name__ == "__main__":
    unittest.main()
