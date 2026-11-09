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

    def test_late_window_sees_open_permission(self):
        self.hub.permission_request({"request_id": "fghij", "tool_name": "Write", "description": "", "input_preview": ""})
        self.hub.post_user_message("unity", "MyGame", "hi")
        kinds = [i["kind"] for i in self.hub.updates("unity-mygame", 0, 0)]
        self.assertEqual(kinds, ["user", "permission"])


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
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["reply"])
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
        self.proc.stdin.close()
        self.proc.wait(5)
        self.assertFalse(self.server_file.exists())
        self.proc.stdin = open(os.devnull, "w")  # tearDown closes it again



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

    def test_is_supervisor(self):
        self.assertTrue(cc.is_supervisor({"CLAUDE_HAND_SUPERVISOR": "1"}))
        self.assertFalse(cc.is_supervisor({}))


if __name__ == "__main__":
    unittest.main()
