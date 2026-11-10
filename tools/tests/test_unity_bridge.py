"""Tests for tools/unity_bridge.py against a stand-in for ChBridge.cs (same line protocol)."""

import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_hand as ch  # noqa: E402
import unity_bridge as ub  # noqa: E402

TOOLS = [
    {"name": "ch_editor_status", "description": "status", "requires_polling": False, "poll_action": "status",
     "max_poll_seconds": 0, "parameters": [{"name": "echo", "type": "string", "description": "", "required": False}]},
    {"name": "ch_import_package", "description": "import", "requires_polling": True, "poll_action": "status",
     "max_poll_seconds": 60, "parameters": [{"name": "manifest", "type": "string", "description": "m", "required": True},
                                             {"name": "refs", "type": "array", "description": "", "required": False}]},
]


class FakeEditor:
    """Speaks ChBridge's protocol: one JSON line in, one out; checks the token; polls ch_import_package."""

    def __init__(self, token="secret"):
        self.token = token
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.calls = []
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                req = json.loads(data)
                self.calls.append((req["tool"], req.get("params")))
                if req.get("token") != self.token:
                    reply = {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "wrong token", "retry": "after_fix"}}
                elif req["tool"] == "__ping__":
                    reply = {"ok": True, "result": {"project": "hand_test", "hash": "49ac508c"}}
                elif req["tool"] == "ch_import_package":
                    status_calls = sum(1 for t, p in self.calls if t == "ch_import_package" and p.get("action") == "status")
                    done = req["params"].get("action") == "status" and status_calls >= 2
                    reply = {"ok": True, "result": {"_mcp_status": "complete", "message": "imported"} if done
                             else {"_mcp_status": "pending", "_mcp_poll_interval": 0.2}}
                else:
                    reply = {"ok": True, "result": {"success": True, "message": "ok", "data": {"echo": req["params"].get("echo")}}}
                conn.sendall((json.dumps(reply) + "\n").encode())

    def close(self):
        self.sock.close()


def register(directory: Path, port: int, token="secret", name="hand_test", hash_="49ac508c", path="C:/Projects/hand_test"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{hash_}.json").write_text(json.dumps({
        "format": ub.REGISTRY_FORMAT, "name": name, "project_path": path, "hash": hash_, "hash16": hash_ + "deadbeef",
        "port": port, "token": token, "unity_version": "6000.3.25f1", "tools": TOOLS}), encoding="utf-8")


class BridgeClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "unity"
        self.editor = FakeEditor()
        register(self.dir, self.editor.port)

    def tearDown(self):
        self.editor.close()
        self.tmp.cleanup()

    def test_find_project_by_hash_name_or_path(self):
        self.assertEqual(ub.find_project(hash_="49ac508c", directory=self.dir)["name"], "hand_test")
        self.assertIsNotNone(ub.find_project(hash_="49ac508cdeadbeef", directory=self.dir))  # HTTP-style 16 hex
        self.assertIsNotNone(ub.find_project(name="hand_test", directory=self.dir))
        self.assertIsNotNone(ub.find_project(path="c:\\Projects\\hand_test\\", directory=self.dir))
        self.assertIsNone(ub.find_project(hash_="ffffffff", directory=self.dir))

    def test_call_and_wrong_token(self):
        entry = ub.find_project(hash_="49ac508c", directory=self.dir)
        self.assertEqual(ub.request(entry, "ch_editor_status", {"echo": "hi"})["data"]["echo"], "hi")
        with self.assertRaises(ub.BridgeError) as denied:
            ub.request({**entry, "token": "nope"}, "ch_editor_status")
        self.assertEqual(denied.exception.code, "UNAUTHORIZED")

    def test_polled_tool_runs_to_completion(self):
        locate = lambda: ub.find_project(hash_="49ac508c", directory=self.dir)
        result = ub.run_tool(locate, TOOLS[1], {"manifest": "x.claudehand.json"}, sleep=lambda s: None)
        self.assertEqual(result["_mcp_status"], "complete")
        actions = [p.get("action") for t, p in self.editor.calls if t == "ch_import_package"]
        self.assertEqual(actions, [None, "status", "status"])
        self.assertTrue(all(p["manifest"] == "x.claudehand.json" for t, p in self.editor.calls if t == "ch_import_package"))

    def test_retries_through_a_script_reload(self):
        """Unity reloads: the old port closes and the registry is rewritten with a new one a moment later."""
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()
        register(self.dir, dead_port)
        threading.Timer(0.3, lambda: register(self.dir, self.editor.port)).start()
        locate = lambda: ub.find_project(hash_="49ac508c", directory=self.dir)
        result = ub.call_with_retry(locate, "ch_editor_status", {"echo": "after reload"}, attempts=10, wait=0.2)
        self.assertEqual(result["data"]["echo"], "after reload")

    def test_not_open_is_reported(self):
        with self.assertRaises(ub.BridgeError) as missing:
            ub.call_with_retry(lambda: None, "ch_editor_status", {}, attempts=2, wait=0)
        self.assertEqual(missing.exception.code, "NOT_RUNNING")


class DialogTests(unittest.TestCase):
    """A modal dialog stops the editor's main thread: calls give up at once and say what it asks."""

    SAVE = {"title": "Scene(s) Have Been Modified", "text": "Do you want to save the changes?",
            "buttons": ["Save", "Don't Save", "Cancel"], "progress": False}
    IMPORT = {"title": "Importing", "text": "Importing assets", "buttons": ["Cancel"], "progress": True}

    def test_describe_and_leave_out_progress_bars(self):
        self.assertEqual(ub.describe_dialogs([self.SAVE]), "'Scene(s) Have Been Modified' 창: Do you want to save the changes? "
                                                           "[버튼: Save / Don't Save / Cancel]")
        self.assertEqual(ub.waiting_dialogs({"pid": 7}, read=lambda pid: [self.IMPORT, self.SAVE]), [self.SAVE])
        self.assertEqual(ub.waiting_dialogs(None, read=lambda pid: [self.SAVE]), [])
        self.assertEqual(ub.waiting_dialogs({"pid": 7}, read=lambda pid: 1 / 0), [])  # reading windows failed: no dialog

    def test_a_call_gives_up_when_a_dialog_stops_the_editor(self):
        silent = socket.socket()  # accepts, never answers: an editor stuck in a dialog
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        try:
            entry = {"name": "hand_test", "port": silent.getsockname()[1], "token": "t", "pid": 1234}
            seen = []
            dialogs = lambda pid: seen.append(pid) or ([self.SAVE] if len(seen) >= 2 else [])
            with unittest.mock.patch.object(ub, "DIALOG_POLL_SECONDS", 0.1):
                with self.assertRaises(ub.BridgeError) as stopped:
                    ub.request(entry, "ch_editor_status", timeout=30, dialogs=dialogs)
            self.assertEqual(stopped.exception.code, "DIALOG")
            self.assertEqual(stopped.exception.retry, "check_first")  # it may still run once the dialog closes
            self.assertIn("[버튼: Save / Don't Save / Cancel]", stopped.exception.message)
            self.assertEqual(seen, [1234, 1234])
        finally:
            silent.close()

    def test_without_a_dialog_a_silent_editor_still_times_out(self):
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        try:
            entry = {"name": "hand_test", "port": silent.getsockname()[1], "token": "t", "pid": 1234}
            with unittest.mock.patch.object(ub, "DIALOG_POLL_SECONDS", 0.1):
                with self.assertRaises(ub.BridgeError) as late:
                    ub.request(entry, "ch_editor_status", timeout=0.4, dialogs=lambda pid: [])
            self.assertEqual(late.exception.code, "UNREACHABLE")
        finally:
            silent.close()


class BridgeServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "unity"
        self.editor = FakeEditor()
        register(self.dir, self.editor.port)

    def tearDown(self):
        self.editor.close()
        self.tmp.cleanup()

    def session(self, messages, hash_="49ac508c"):
        out = io.StringIO()
        server = ub.BridgeServer(io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"), out, hash_=hash_, directory=self.dir)
        server.serve()
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_tools_come_from_the_registry(self):
        replies = self.session([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(replies[0]["result"]["capabilities"], {"tools": {}})
        tools = {t["name"]: t for t in replies[1]["result"]["tools"]}
        self.assertEqual(sorted(tools), ["ch_bridge_status", "ch_editor_status", "ch_import_package"])
        schema = tools["ch_import_package"]["inputSchema"]
        self.assertEqual(schema["required"], ["manifest"])
        self.assertEqual(schema["properties"]["refs"], {"type": "array", "description": "", "items": {"type": "string"}})

    def test_call_status_and_unknown(self):
        replies = self.session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ch_editor_status", "arguments": {"echo": "x"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ch_bridge_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ch_nope", "arguments": {}}},
        ])
        self.assertEqual(json.loads(replies[0]["result"]["content"][0]["text"])["data"]["echo"], "x")
        self.assertTrue(json.loads(replies[1]["result"]["content"][0]["text"])["connected"])
        self.assertTrue(replies[2]["result"]["isError"])

    def test_project_not_open(self):
        replies = self.session([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ch_bridge_status", "arguments": {}}},
                                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], hash_="ffffffff")
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["ch_bridge_status"])


class GeneratorTests(unittest.TestCase):
    def test_agent_gets_the_bridge_server(self):
        inst = ch.Instance(name="hand_test", hash="49ac508c")
        text = ch.render_agent(inst, "unity-hand-test", ch.ServerConfig(transport="stdio"), "uv run -q --no-project", True, False)
        front = text.split("---")[1]
        self.assertIn('tools: "mcp__unity_hand_test__*, mcp__unity_hand_test_ch__*, Read, Glob, Grep"', front)
        self.assertIn("  - unity_hand_test_ch:", front)
        self.assertIn('"--hash", "49ac508c", "--project-name", "hand_test"', front)
        self.assertIn(ch.UNITY_BRIDGE_SCRIPT.as_posix(), front)
        self.assertNotIn("execute_custom_tool", text)
        self.assertIn("ch_bridge_status", text)

    def test_bridge_only_editors_are_discovered(self):
        mcp = [ch.Instance(name="A", hash="aaaa1111")]
        bridge = [ch.Instance(name="A", hash="aaaa1111", path="C:/A", source="bridge"), ch.Instance(name="B", hash="bbbb2222", source="bridge")]
        merged = ch.merge_instances(mcp, bridge)
        self.assertEqual([i.id for i in merged], ["A@aaaa1111", "B@bbbb2222"])
        self.assertEqual(merged[0].path, "C:/A")

    def test_discover_bridge_reads_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            register(Path(tmp), 1, name="hand_test")
            found = ch.discover_bridge(Path(tmp), alive_fn=lambda entry: True)
        self.assertEqual([(i.id, i.path, i.alive) for i in found], [("hand_test@49ac508c", "C:/Projects/hand_test", True)])


class SupervisorDenyTests(unittest.TestCase):
    def test_direct_servers_are_denied_to_the_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "claude.json"
            cfg.write_text(json.dumps({"mcpServers": {
                "unity-mcp": {"command": "C:/Users/me/.unity/relay/relay_win.exe", "args": ["--mcp"]},
                "UnityMCP": {"type": "http", "url": "http://127.0.0.1:8080/mcp"},
                "blender": {"command": "uvx", "args": ["mcp-for-blender"]},
                "github": {"command": "npx", "args": ["github-mcp"]},
                "claude-hand-chat": {"command": "uv", "args": ["run", "chat_channel.py"]}}}), encoding="utf-8")
            keys = ch.direct_app_servers([cfg])
            self.assertEqual(keys, ["UnityMCP", "blender", "unity-mcp"])
            root = Path(tmp) / "repo"
            root.mkdir()
            plan = ch.plan_chat_config(root, direct_servers=keys)
            settings = plan[root / ".claude" / "settings.local.json"]
            self.assertEqual(settings["permissions"]["deny"], ["mcp__UnityMCP__*", "mcp__blender__*", "mcp__unity-mcp__*"])
            ch.write_json_files(plan)
            self.assertEqual(ch.plan_chat_config(root, direct_servers=keys), {})


class DoctorMismatchTests(unittest.TestCase):
    def checks(self, via_bridge, via_mcp):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            versions = {"claude": "2.1.300", "uv": "uv", "git": "git", "node": "v20", "agent-desktop": "1"}
            with unittest.mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home),
                                                       "CLAUDE_HAND_HOME": str(home / ".claude-hand")}), \
                 unittest.mock.patch.object(ch.Path, "home", lambda: home), \
                 unittest.mock.patch.object(ch, "candidate_config_files", lambda cwd=None: []), \
                 unittest.mock.patch.object(ch, "discover_blender", lambda ports, request=None: []), \
                 unittest.mock.patch.object(ch, "find_blender_executables", lambda **kw: []), \
                 unittest.mock.patch.object(ch, "claude_mcp_get", lambda **kw: None), \
                 unittest.mock.patch.object(ch, "plan_chat_config", lambda root=None, runner=None, direct_servers=None: {}), \
                 unittest.mock.patch.object(ch, "discover_bridge", lambda directory=None, alive_fn=None: via_bridge), \
                 unittest.mock.patch.object(ch, "discover_stdio", lambda *a, **kw: via_mcp):
                return {c.item: c for c in ch.doctor_checks([], [9876], version=lambda *cmd: versions[cmd[0]])}

    def test_open_but_not_seen_by_mcp_for_unity(self):
        by_item = self.checks([ch.Instance(name="hand_test", hash="49ac508c", source="bridge")], [])
        self.assertFalse(by_item["MCP for Unity 연결"].ok)
        self.assertIn("Stdio", by_item["MCP for Unity 연결"].fix)
        self.assertNotIn("Claude Hand Tools 연결", by_item)
        self.assertEqual(by_item["연결된 Unity 에디터"].detail, "hand_test@49ac508c")  # still found for sync

    def test_seen_by_mcp_for_unity_only(self):
        by_item = self.checks([], [ch.Instance(name="hand_test", hash="49ac508c")])
        self.assertFalse(by_item["Claude Hand Tools 연결"].ok)
        self.assertIn("unity-install", by_item["Claude Hand Tools 연결"].fix)

    def test_both_paths_agree(self):
        same = [ch.Instance(name="hand_test", hash="49ac508c")]
        by_item = self.checks(same, same)
        self.assertNotIn("MCP for Unity 연결", by_item)
        self.assertNotIn("Claude Hand Tools 연결", by_item)


class UnityCallTests(unittest.TestCase):
    def test_cli_runs_a_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            editor = FakeEditor()
            try:
                register(Path(tmp) / "unity", editor.port)
                out = io.StringIO()
                with unittest.mock.patch.dict(os.environ, {"CLAUDE_HAND_HOME": tmp}), unittest.mock.patch("sys.stdout", out):
                    code = ch.main(["unity-call", "hand_test", "ch_editor_status", "echo=hello"])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out.getvalue())["data"]["echo"], "hello")
            finally:
                editor.close()


if __name__ == "__main__":
    unittest.main()
