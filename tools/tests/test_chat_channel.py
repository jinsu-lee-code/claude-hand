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
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_channel as cc  # noqa: E402
import claude_hand as ch  # noqa: E402

TOOL = Path(__file__).resolve().parents[1] / "chat_channel.py"


def write_agents(directory: Path) -> None:
    """Agent files exactly as `claude_hand.py sync` writes them."""
    unity = ch.Instance(name="MyGame", hash="aaaa1111")
    blender = ch.BlenderInstance(port=9877, filepath="C:/Art/props.blend")
    files = ch.build_agent_files([unity], ch.ServerConfig(transport="stdio"), [blender], None, "uv run")
    ch.write_agent_files(files, directory)


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
            agents = cc.load_agents(Path(tmp))
        self.assertEqual(sorted((a.name, a.app, a.pin) for a in agents),
                         [("blender-props", "blender", "props@9877"), ("unity-mygame", "unity", "MyGame@aaaa1111")])

    def test_resolve_window(self):
        agents = [cc.AgentEntry("unity-mygame", "unity", "MyGame@aaaa1111"), cc.AgentEntry("blender-props", "blender", "props@9877")]
        self.assertEqual(cc.resolve_window(agents, "unity", "MyGame"), ("unity-mygame", "unity-mygame"))
        self.assertEqual(cc.resolve_window(agents, "blender", "props", "props@9877"), ("blender-props", "blender-props"))
        self.assertEqual(cc.resolve_window(agents, "blender", "props", "props@9999"), ("blender-props", None))
        self.assertEqual(cc.resolve_window(agents, "unity", "Other Game"), ("unity-other-game", None))

    def test_same_project_name_twice_is_not_guessed(self):
        agents = [cc.AgentEntry("unity-mygame-aaaa", "unity", "MyGame@aaaa1111"), cc.AgentEntry("unity-mygame-cccc", "unity", "MyGame@cccc3333")]
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
            files = ch.build_agent_files([ch.Instance(name="NewGame", hash="bbbb2222")], ch.ServerConfig(transport="stdio"), [], None, "uv run")
            ch.write_agent_files(files, agents_dir)
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
        self.assertIn("reply tool", init["instructions"])
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


if __name__ == "__main__":
    unittest.main()
