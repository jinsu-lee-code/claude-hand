"""Tests for tools/claude_hand.py. Run: python -m unittest discover -s tools/tests -v"""

import ast
import io
import json
import re
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_hand as ch  # noqa: E402


def write_status(directory: Path, hash_: str, project_path: str, port: int, **extra):
    data = {"unity_port": port, "project_path": project_path, "unity_version": "6000.3.2f1", "reloading": False}
    data.update(extra)
    (directory / f"unity-mcp-status-{hash_}.json").write_text(json.dumps(data), encoding="utf-8")


class DiscoveryTests(unittest.TestCase):
    def test_project_name_matches_mcp_for_unity_rule(self):
        self.assertEqual(ch.project_name_from_assets_path("C:/Projects/MyGame/Assets"), "MyGame")
        self.assertEqual(ch.project_name_from_assets_path("C:\\Projects\\Tool Kit\\Assets\\"), "Tool Kit")
        self.assertEqual(ch.project_name_from_assets_path(""), "Unknown")

    def test_stdio_status_files_become_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_status(d, "aaaa1111", "C:/Projects/MyGame/Assets", 6401)
            write_status(d, "bbbb2222", "C:/Projects/Props/Assets", 6402)
            (d / "unity-mcp-status-broken.json").write_text("{not json", encoding="utf-8")
            found = ch.discover_stdio(d, probe=lambda port: port == 6401)
        self.assertEqual([i.id for i in found], ["MyGame@aaaa1111", "Props@bbbb2222"])
        self.assertEqual(found[0].path, "C:/Projects/MyGame")
        self.assertTrue(found[0].alive)
        self.assertFalse(found[1].alive)

    def test_reloading_editor_counts_as_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_status(d, "cccc3333", "C:/P/Reload/Assets", 6403, reloading=True)
            found = ch.discover_stdio(d, probe=lambda port: False)
        self.assertTrue(found[0].alive)

    def test_http_discovery(self):
        payload = {"success": True, "instances": [{"project": "MyGame", "hash": "aaaa1111", "unity_version": "6000.3"}]}
        seen = []

        def fake_fetch(url):
            seen.append(url)
            return payload

        found = ch.discover_http("http://localhost:8080/mcp", fetch=fake_fetch)
        self.assertEqual(seen, ["http://localhost:8080/api/instances"])
        self.assertEqual([i.id for i in found], ["MyGame@aaaa1111"])

    def test_http_discovery_survives_server_down(self):
        def down(url):
            raise OSError("connection refused")

        self.assertEqual(ch.discover_http("http://localhost:8080/mcp", fetch=down), [])


class ConfigDetectionTests(unittest.TestCase):
    def detect(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.json"
            f.write_text(json.dumps(data), encoding="utf-8")
            return ch.detect_server_config([f])

    def test_stdio_entry_is_copied(self):
        cfg = self.detect({"mcpServers": {"unityMCP": {
            "command": "C:/Users/me/AppData/Local/Microsoft/WinGet/Links/uvx.exe",
            "args": ["--from", "mcpforunityserver", "mcp-for-unity", "--transport", "stdio"],
            "env": {"FOO": "1"}}}})
        self.assertEqual(cfg.transport, "stdio")
        self.assertTrue(cfg.command.endswith("uvx.exe"))
        self.assertEqual(cfg.env, {"FOO": "1"})

    def test_http_entry(self):
        cfg = self.detect({"mcpServers": {"unityMCP": {"type": "http", "url": "http://localhost:8080/mcp"}}})
        self.assertEqual((cfg.transport, cfg.url), ("http", "http://localhost:8080/mcp"))

    def test_per_project_servers_in_claude_json(self):
        cfg = self.detect({"projects": {"C:/work": {"mcpServers": {"my-unity": {"command": "uvx", "args": ["mcp-for-unity"]}}}}})
        self.assertEqual((cfg.transport, cfg.key), ("stdio", "my-unity"))

    def test_unrelated_servers_are_ignored(self):
        cfg = self.detect({"mcpServers": {"github": {"command": "npx", "args": ["github-mcp"]}}})
        self.assertEqual(cfg.origin, "default")

    def test_missing_files_fall_back_to_default(self):
        cfg = ch.detect_server_config([Path("/nonexistent/config.json")])
        self.assertEqual((cfg.transport, cfg.command, cfg.args), ("stdio", "uvx", ch.DEFAULT_STDIO_ARGS))


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.a = ch.Instance(name="MyGame", hash="aaaa1111", path="C:/Projects/MyGame")
        self.b = ch.Instance(name="Props Lib", hash="bbbb2222")

    def test_names_and_server_keys(self):
        names = ch.agent_names([self.a, self.b])
        self.assertEqual(names, {"MyGame@aaaa1111": "unity-mygame", "Props Lib@bbbb2222": "unity-props-lib"})
        self.assertEqual(ch.server_key("unity-props-lib"), "unity_props_lib")

    def test_same_folder_name_gets_hash_suffix(self):
        twin = ch.Instance(name="MyGame", hash="cccc3333")
        names = ch.agent_names([self.a, twin])
        self.assertEqual(sorted(names.values()), ["unity-mygame-aaaa", "unity-mygame-cccc"])

    def test_pinned_args_replace_existing_default(self):
        args = ["--from", "x", "--default-instance", "Old@1", "--transport", "stdio", "--default-instance=Old@2"]
        self.assertEqual(ch.pinned_args(args, "MyGame@aaaa1111"),
                         ["--from", "x", "--transport", "stdio", "--default-instance", "MyGame@aaaa1111"])

    def test_stdio_agent_file(self):
        server = ch.ServerConfig(transport="stdio")
        text = ch.render_agent(self.a, "unity-mygame", server, "uv run -q --no-project", allowlist=True, focus_nudge=False)
        front = text.split("---")[1]
        self.assertIn("name: unity-mygame", front)
        self.assertIn('tools: "mcp__unity_mygame__*, Read, Glob, Grep"', front)
        self.assertIn("  - unity_mygame:", front)
        self.assertIn('"--default-instance", "MyGame@aaaa1111"', front)
        self.assertIn('UNITY_MCP_DISABLE_FOCUS_NUDGE: "1"', front)
        self.assertIn('matcher: "mcp__.*"', front)
        self.assertIn("guard --pin \\\"MyGame@aaaa1111\\\" --server unity_mygame --transport stdio", front)
        self.assertIn(ch.GENERATED_MARKER, text)
        self.assertNotIn("set_active_instance(instance=", text)

    def test_http_agent_file_pins_with_set_active_instance(self):
        server = ch.ServerConfig(transport="http", url="http://localhost:8080/mcp")
        text = ch.render_agent(self.a, "unity-mygame", server, "uv run", allowlist=True, focus_nudge=False)
        self.assertIn("type: http", text)
        self.assertIn('url: "http://localhost:8080/mcp"', text)
        self.assertIn('set_active_instance(instance="MyGame@aaaa1111")', text)
        self.assertNotIn("--default-instance", text)

    def test_disallow_mode_blocks_parent_server(self):
        server = ch.ServerConfig(transport="stdio", key="unityMCP")
        text = ch.render_agent(self.a, "unity-mygame", server, "uv run", allowlist=False, focus_nudge=True)
        self.assertIn('disallowedTools: "mcp__unityMCP__*"', text)
        self.assertNotIn("tools: ", text.split("disallowedTools")[0])
        self.assertNotIn("UNITY_MCP_DISABLE_FOCUS_NUDGE", text)

    def test_frontmatter_values_are_valid_json_strings(self):
        # Every quoted scalar we emit must round-trip, or YAML parsing would break.
        server = ch.ServerConfig(transport="stdio", env={"PATH_HINT": 'C:\\Program Files\\"uv"'})
        text = ch.render_agent(self.b, "unity-props-lib", server, "uv run", allowlist=True, focus_nudge=False)
        for line in text.split("---")[1].splitlines():
            if ': "' in line:
                json.loads(line.split(": ", 1)[1])

    def test_sync_writes_skips_and_prunes(self):
        server = ch.ServerConfig(transport="stdio")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            first = ch.sync_agents([self.a, self.b], d, server, "uv run")
            self.assertEqual(len(first.written), 2)
            second = ch.sync_agents([self.a, self.b], d, server, "uv run")
            self.assertEqual((len(second.written), len(second.unchanged)), (0, 2))
            third = ch.sync_agents([self.a], d, server, "uv run", prune=True)
            self.assertEqual([p.name for p in third.removed], ["unity-props-lib.md"])
            self.assertEqual(sorted(p.name for p in d.iterdir()), ["unity-mygame.md"])

    def test_sync_refuses_to_overwrite_hand_written_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "unity-mygame.md").write_text("---\nname: unity-mygame\n---\nmine\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                ch.sync_agents([self.a], d, ch.ServerConfig(transport="stdio"), "uv run")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "agents"
            result = ch.sync_agents([self.a], d, ch.ServerConfig(transport="stdio"), "uv run", dry_run=True)
            self.assertEqual(len(result.written), 1)
            self.assertFalse(d.exists())

    def test_skill_source_exists(self):
        self.assertTrue(ch.SKILL_SOURCE.exists())
        self.assertIn("name: claude-hand", ch.SKILL_SOURCE.read_text(encoding="utf-8"))


class GuardTests(unittest.TestCase):
    PIN = "MyGame@aaaa1111"
    SERVER = "unity_mygame"

    def decide(self, tool, tool_input=None, transport="stdio"):
        return ch.guard_decision({"tool_name": tool, "tool_input": tool_input or {}}, self.PIN, self.SERVER, transport)

    def test_own_tools_pass(self):
        self.assertIsNone(self.decide("mcp__unity_mygame__ch_scene_snapshot", {"depth": 2}))
        self.assertIsNone(self.decide("mcp__unity_mygame__manage_scene", {"action": "get_hierarchy", "unity_instance": "MyGame@aaaa1111"}))
        self.assertIsNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "MyGame"}))
        self.assertIsNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "aaaa"}))

    def test_non_mcp_tools_pass(self):
        self.assertIsNone(self.decide("Read", {"file_path": "x"}))

    def test_other_project_is_blocked(self):
        self.assertIsNotNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "Props@bbbb2222"}))
        self.assertIsNotNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "6402"}))
        self.assertIsNotNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "aa"}))
        self.assertIsNotNone(self.decide("mcp__unity_mygame__manage_scene", {"unity_instance": "MyGame@bbbb"}))

    def test_nested_batch_commands_are_checked(self):
        batch = {"commands": [{"tool": "manage_scene", "params": {"unity_instance": "Props@bbbb2222"}}]}
        self.assertIsNotNone(self.decide("mcp__unity_mygame__batch_execute", batch))

    def test_other_servers_are_blocked(self):
        self.assertIsNotNone(self.decide("mcp__unityMCP__manage_scene", {}))
        self.assertIsNotNone(self.decide("mcp__unity_props__ch_scene_snapshot", {}))

    def test_set_active_instance(self):
        self.assertIsNotNone(self.decide("mcp__unity_mygame__set_active_instance", {"instance": self.PIN}, "stdio"))
        self.assertIsNone(self.decide("mcp__unity_mygame__set_active_instance", {"instance": self.PIN}, "http"))
        self.assertIsNotNone(self.decide("mcp__unity_mygame__set_active_instance", {"instance": "Props@bbbb2222"}, "http"))

    def test_run_guard_output_format(self):
        out = io.StringIO()
        event = json.dumps({"tool_name": "mcp__unityMCP__manage_scene", "tool_input": {}})
        args = ch.build_parser().parse_args(["guard", "--pin", self.PIN, "--server", self.SERVER])
        self.assertEqual(ch.run_guard(args, io.StringIO(event), out), 0)
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual((decision["hookEventName"], decision["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertTrue(decision["permissionDecisionReason"])

    def test_allowed_call_prints_nothing(self):
        out = io.StringIO()
        event = json.dumps({"tool_name": "mcp__unity_mygame__ch_editor_status", "tool_input": {}})
        args = ch.build_parser().parse_args(["guard", "--pin", self.PIN, "--server", self.SERVER])
        ch.run_guard(args, io.StringIO(event), out)
        self.assertEqual(out.getvalue(), "")

    def test_broken_input_fails_closed(self):
        out = io.StringIO()
        args = ch.build_parser().parse_args(["guard", "--pin", self.PIN, "--server", self.SERVER])
        ch.run_guard(args, io.StringIO("{not json"), out)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")



class FakeBlenderAddon:
    """Minimal stand-in for the MCP for Blender add-on socket: bare JSON in, bare JSON out."""

    def __init__(self, filepath="C:/Art/character.blend"):
        self.filepath = filepath
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.seen = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                command = json.loads(conn.recv(65536).decode("utf-8"))
                self.seen.append(command["type"])
                result = {
                    "ping": {"pong": True},
                    "get_addon_info": {"blender_version": "5.1.0"},
                    "execute_code": {"executed": True, "result": self.filepath + "\n"},
                }[command["type"]]
                body = json.dumps({"status": "success", "result": result}).encode("utf-8")
                conn.sendall(body[:7])  # split the reply to exercise partial reads
                conn.sendall(body[7:])

    def close(self):
        self.sock.close()


class BlenderTests(unittest.TestCase):
    def test_parse_ports(self):
        self.assertEqual(ch.parse_ports("9876-9878,9900,9877"), [9876, 9877, 9878, 9900])
        self.assertEqual(ch.parse_ports("80,70000"), [])

    def test_instance_names(self):
        self.assertEqual(ch.BlenderInstance(port=9876, filepath="C:\\Art\\Hero Rig.blend").name, "Hero Rig")
        self.assertEqual(ch.BlenderInstance(port=9877).name, "untitled-9877")
        self.assertEqual(ch.BlenderInstance(port=9877).id, "untitled-9877@9877")

    def test_discovery_against_fake_addon(self):
        addon = FakeBlenderAddon()
        try:
            closed = socket.socket()
            closed.bind(("127.0.0.1", 0))
            unused_port = closed.getsockname()[1]
            closed.close()
            found = ch.discover_blender([unused_port, addon.port])
        finally:
            addon.close()
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].port, found[0].filepath, found[0].blender_version), (addon.port, "C:/Art/character.blend", "5.1.0"))
        self.assertEqual(addon.seen, ["ping", "get_addon_info", "execute_code"])

    def test_discovery_skips_other_listeners(self):
        def not_mcp_for_blender(port, command, timeout):
            return {"jsonrpc": "2.0", "error": "unknown"}

        self.assertEqual(ch.discover_blender([9876], request=not_mcp_for_blender), [])

    def detect(self, servers):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.json"
            f.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
            return ch.detect_blender_config([f])

    def test_detect_github_addon_server(self):
        cfg = self.detect({"unityMCP": {"command": "uvx", "args": ["mcp-for-unity"]},
                           "blender": {"command": "uvx", "args": ["blender-mcp"], "env": {"BLENDER_HOST": "localhost"}}})
        self.assertEqual((cfg.command, cfg.args, cfg.env, cfg.warning), ("uvx", ["blender-mcp"], {"BLENDER_HOST": "localhost"}, ""))

    def test_other_blender_server_falls_back_with_warning(self):
        cfg = self.detect({"blender-lab": {"command": "python", "args": ["C:/lab/blmcp/server.py"]}})
        self.assertEqual((cfg.command, cfg.args), ("uvx", ch.DEFAULT_BLENDER_ARGS))
        self.assertIn("blender-lab", cfg.warning)

    def test_no_blender_entry_uses_default(self):
        cfg = self.detect({"github": {"command": "npx", "args": ["x"]}})
        self.assertEqual((cfg.command, cfg.args, cfg.warning), ("uvx", ch.DEFAULT_BLENDER_ARGS, ""))

    def test_with_port_replaces_existing(self):
        self.assertEqual(ch.with_port(["mcp-for-blender", "--port", "1111", "--port=2222", "--host", "h"], 9877),
                         ["mcp-for-blender", "--host", "h", "--port", "9877"])

    def test_agent_file(self):
        inst = ch.BlenderInstance(port=9877, filepath="C:/Art/character.blend")
        server = ch.ServerConfig(transport="stdio", command="uvx", args=["mcp-for-blender"], key="blender")
        text = ch.render_blender_agent(inst, "blender-character", server, "uv run -q --no-project", allowlist=True)
        front = text.split("---")[1]
        self.assertIn("name: blender-character", front)
        self.assertIn('tools: "mcp__blender_character__*, Read, Glob, Grep"', front)
        self.assertIn('args: ["mcp-for-blender", "--port", "9877"]', front)
        self.assertIn('BLENDER_PORT: "9877"', front)
        self.assertIn("--server blender_character --transport stdio --app blender", front)
        for line in front.splitlines():
            if ': "' in line:
                json.loads(line.split(": ", 1)[1])
        self.assertNotIn("__", re.sub(r"mcp__\w+__|__\w+__\*", "", text.split(ch.GENERATED_MARKER)[1]).replace("__init__", ""))

    def test_recipes_are_valid_python(self):
        blocks = re.findall(r"```python\n(.*?)```", ch.BLENDER_PROMPT, re.S)
        self.assertEqual(len(blocks), 3)
        for code in blocks:
            ast.parse(code)

    def test_names_for_duplicate_files(self):
        a = ch.BlenderInstance(port=9876, filepath="C:/a/scene.blend")
        b = ch.BlenderInstance(port=9877, filepath="C:/b/scene.blend")
        self.assertEqual(sorted(ch.blender_agent_names([a, b]).values()), ["blender-scene-9876", "blender-scene-9877"])

    def test_sync_writes_and_prunes_both_apps(self):
        unity = ch.Instance(name="MyGame", hash="aaaa1111")
        blender = ch.BlenderInstance(port=9876, filepath="C:/Art/character.blend")
        server = ch.ServerConfig(transport="stdio")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            first = ch.sync_agents([unity], d, server, "uv run", blender=[blender])
            self.assertEqual(sorted(p.name for p in first.written), ["blender-character.md", "unity-mygame.md"])
            later = ch.sync_agents([unity], d, server, "uv run", blender=[], prune=True)
            self.assertEqual([p.name for p in later.removed], ["blender-character.md"])

    def test_guard_blender_mode(self):
        own = {"tool_name": "mcp__blender_character__execute_code", "tool_input": {"code": "print(1)"}}
        other = {"tool_name": "mcp__blender_props__execute_code", "tool_input": {}}
        self.assertIsNone(ch.guard_decision(own, "character@9876", "blender_character", "stdio", "blender"))
        reason = ch.guard_decision(other, "character@9876", "blender_character", "stdio", "blender")
        self.assertIn("Blender", reason)


if __name__ == "__main__":
    unittest.main()
