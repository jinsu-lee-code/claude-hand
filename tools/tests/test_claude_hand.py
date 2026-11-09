"""Tests for tools/claude_hand.py. Run: python -m unittest discover -s tools/tests -v"""

import ast
import io
import json
import os
import re
import socket
import sys
import tempfile
import threading
import unittest
import unittest.mock
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

    def test_unity_ai_assistant_relay_is_skipped(self):
        """Unity's own AI Assistant registers itself as "unity-mcp"; the MCP for Unity entry must win."""
        cfg = self.detect({
            "mcpServers": {"unity-mcp": {"command": "C:\\Users\\me\\.unity/relay\\relay_win.exe", "args": ["--mcp"]}},
            "projects": {"C:/work/MyGame": {"mcpServers": {"UnityMCP": {
                "type": "stdio", "command": "uvx", "args": ["--from", "mcpforunityserver==10.3.0", "mcp-for-unity"]}}}}})
        self.assertEqual((cfg.transport, cfg.key, cfg.command), ("stdio", "UnityMCP", "uvx"))
        only_relay = self.detect({"mcpServers": {"unity-mcp": {"command": "/Users/me/.unity/relay/relay_mac", "args": ["--mcp"]}}})
        self.assertEqual(only_relay.origin, "default")

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
        self.assertIn('tools: "mcp__unity_mygame__*, mcp__unity_mygame_ch__*, Read, Glob, Grep"', front)
        self.assertIn("  - unity_mygame_ch:", front)
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
        out, reported = io.StringIO(), []
        event = json.dumps({"tool_name": "mcp__unity_mygame__ch_editor_status", "tool_input": {}})
        args = ch.build_parser().parse_args(["guard", "--pin", self.PIN, "--server", self.SERVER])
        with unittest.mock.patch.object(ch, "report_progress", lambda *a, **kw: reported.append(a)):
            ch.run_guard(args, io.StringIO(event), out)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(len(reported), 1)  # an allowed call is shown in the window's panel

    def test_allowed_calls_are_reported_to_the_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "chat").mkdir()
            (home / "chat" / "server.json").write_text(json.dumps({"port": 8765, "token": "t"}), encoding="utf-8")
            sent = []
            post = lambda req: sent.append((req.full_url, req.headers.get("X-claude-hand-token"), json.loads(req.data)))
            event = {"tool_name": "mcp__unity_mygame__manage_gameobject", "tool_input": {"action": "create", "name": "Cube"}}
            ch.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=post)
            ch.report_progress({"tool_name": "mcp__unity_mygame_ch__ch_scene_snapshot", "tool_input": {"depth": 2}},
                               self.PIN, self.SERVER, "unity", home=home, post=post)
            ch.report_progress({"tool_name": "Read", "tool_input": {}}, self.PIN, self.SERVER, "unity", home=home, post=post)
            ch.report_progress(event, self.PIN, self.SERVER, "unity", home=home, post=lambda req: 1 / 0)  # never raises
        self.assertEqual(sent[0], ("http://127.0.0.1:8765/v1/progress", "t",
                                   {"app": "unity", "pin": self.PIN, "tool": "manage_gameobject", "detail": "create Cube"}))
        self.assertEqual(sent[1][2]["tool"], "ch_scene_snapshot")
        self.assertEqual(len(sent), 2)  # not for tools outside the subagent's own servers

    def test_action_summary(self):
        self.assertEqual(ch.action_summary({"action": "create", "name": "Cube", "position": [0, 0, 0]}), "create Cube")
        self.assertEqual(ch.action_summary({"code": "import bpy\n\nbpy.ops.mesh.primitive_uv_sphere_add()"}),
                         "bpy.ops.mesh.primitive_uv_sphere_add()")
        self.assertEqual(ch.action_summary({}), "")

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



def make_unity_project(root: Path, deps=None, version="6000.3.2f1") -> Path:
    project = root / "MyGame"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    (project / "Packages").mkdir()
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        f"m_EditorVersion: {version}\nm_EditorVersionWithRevision: {version} (abc)\n", encoding="utf-8")
    manifest = {"dependencies": deps if deps is not None else {"com.unity.ugui": "2.0.0"}, "scopedRegistries": []}
    (project / "Packages" / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return project


class SetupHelperTests(unittest.TestCase):
    def test_unity_install_adds_both_and_keeps_other_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp))
            plan, manifest = ch.plan_unity_install(project, package_dir=Path(tmp) / "pkg")
            self.assertEqual(len(plan.changes), 2)
            ch.apply_unity_install(project, manifest, plan)
            written = json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8"))
            deps = written["dependencies"]
            self.assertEqual(deps["com.unity.ugui"], "2.0.0")
            self.assertEqual(deps[ch.MCP_FOR_UNITY_PACKAGE], ch.MCP_FOR_UNITY_GIT_URL)
            self.assertTrue(deps[ch.CLAUDE_HAND_PACKAGE].startswith("file:") and deps[ch.CLAUDE_HAND_PACKAGE].endswith("/pkg"))
            self.assertEqual(written["scopedRegistries"], [])
            backup = json.loads(Path(plan.backup).read_text(encoding="utf-8"))
            self.assertNotIn(ch.MCP_FOR_UNITY_PACKAGE, backup["dependencies"])
            again, manifest_again = ch.plan_unity_install(project, package_dir=Path(tmp) / "pkg")
            self.assertEqual((again.changes, manifest_again), ([], None))

    def test_unity_install_respects_existing_mcp_for_unity(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp), deps={ch.MCP_FOR_UNITY_PACKAGE: "https://example/fork.git"})
            plan, manifest = ch.plan_unity_install(project, package_dir=Path(tmp) / "pkg")
            self.assertEqual(manifest["dependencies"][ch.MCP_FOR_UNITY_PACKAGE], "https://example/fork.git")
            self.assertEqual(len(plan.changes), 1)

    def test_unity_install_detects_asset_store_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp))
            (project / "Assets" / "MCPForUnity").mkdir()
            plan, manifest = ch.plan_unity_install(project, package_dir=Path(tmp) / "pkg")
            self.assertNotIn(ch.MCP_FOR_UNITY_PACKAGE, manifest["dependencies"])
            self.assertTrue(any("Asset Store" in n for n in plan.notes))

    def test_unity_install_rejects_non_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                ch.plan_unity_install(Path(tmp))

    def test_unity_install_cli_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp))
            before = (project / "Packages" / "manifest.json").read_text(encoding="utf-8")
            out = io.StringIO()
            with unittest.mock.patch("sys.stdout", out):
                ch.main(["unity-install", str(project), "--dry-run"])
            self.assertEqual((project / "Packages" / "manifest.json").read_text(encoding="utf-8"), before)
            self.assertIn("(dry run)", out.getvalue())

    def test_project_version_and_editor_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp), version="6000.3.2f1")
            self.assertEqual(ch.read_project_version(project), "6000.3.2f1")
            hub = Path(tmp) / "Hub" / "Editor"
            exe = hub / "6000.3.2f1" / "Editor" / "Unity.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
            self.assertEqual(ch.find_unity_editor("6000.3.2f1", [hub], platform="win32"), exe)
            self.assertIsNone(ch.find_unity_editor("2022.3.1f1", [hub], platform="win32"))

    def test_blender_lookup_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            pf = Path(tmp) / "Program Files"
            for version in ("4.2", "5.0"):
                exe = pf / "Blender Foundation" / f"Blender {version}" / "blender.exe"
                exe.parent.mkdir(parents=True)
                exe.write_text("", encoding="utf-8")
            found = ch.find_blender_executables(env={"ProgramFiles": str(pf)}, platform="win32", which=lambda _: None)
            self.assertEqual([p.parent.name for p in found], ["Blender 5.0", "Blender 4.2"])

    def test_blender_addon_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "Blender"
            legacy = base / "4.1" / "scripts" / "addons" / ch.BLENDER_ADDON_FILE
            extension = base / "4.2" / "extensions" / "user_default" / ch.BLENDER_ADDON_FILE
            for f in (legacy, extension):
                f.parent.mkdir(parents=True)
                f.write_text("", encoding="utf-8")
            (base / "config").mkdir()
            self.assertEqual(ch.find_blender_addon_installs(base), [extension, legacy])

    def test_blender_open_command(self):
        cmd = ch.blender_open_command(Path("C:/B/blender.exe"), Path("C:/Art/props.blend"), 9877)
        self.assertEqual(cmd[:3], [str(Path("C:/B/blender.exe")), str(Path("C:/Art/props.blend")), "--python-expr"])
        ast.parse(cmd[3])
        self.assertIn("blendermcp_port = 9877", cmd[3])
        self.assertEqual(ch.blender_open_command(Path("blender"), None, 9876)[1], "--python-expr")

    def test_node_major(self):
        self.assertEqual(ch.node_major("v22.3.0"), 22)
        self.assertIsNone(ch.node_major(None))

    def test_doctor_reports_gaps_with_fixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            project = make_unity_project(Path(tmp))
            versions = {"claude": "2.1.0", "uv": "uv 0.9", "git": None, "node": "v16.0.0", "agent-desktop": None}
            with unittest.mock.patch.dict("os.environ", {"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home)}), \
                 unittest.mock.patch.object(ch.Path, "home", lambda: home), \
                 unittest.mock.patch.object(ch, "candidate_config_files", lambda cwd=None: []), \
                 unittest.mock.patch.object(ch, "discover_blender", lambda ports, request=None: []), \
                 unittest.mock.patch.object(ch, "find_blender_executables", lambda **kw: []), \
                 unittest.mock.patch.object(ch, "chat_server_registered", lambda **kw: False), \
                 unittest.mock.patch.object(ch, "plan_chat_config", lambda root, runner="", direct_servers=None: {root / ".mcp.json": {}}):
                checks = ch.doctor_checks([project], [9876], version=lambda *cmd: versions[cmd[0]])
        by_item = {c.item: c for c in checks}
        self.assertTrue(by_item["Claude Code CLI (claude 명령)"].ok)
        self.assertFalse(by_item["git (Unity가 MCP for Unity를 받을 때 필요)"].ok)
        self.assertIn("git-scm.com", by_item["git (Unity가 MCP for Unity를 받을 때 필요)"].fix)  # winget may be missing
        self.assertFalse(by_item["Node.js 18 이상 (agent-desktop용)"].ok)
        self.assertEqual(by_item["agent-desktop CLI"].fix, "npm install -g agent-desktop")
        self.assertIn("mcp-for-blender setup", by_item["MCP for Blender 애드온"].fix)
        self.assertIn("claude mcp add", by_item["Claude 설정에 MCP for Unity 등록"].fix)
        self.assertIn("chat-setup", by_item["채팅 채널 등록과 답장 허용 (claude-hand 폴더)"].fix)
        self.assertTrue(by_item["Claude Code 2.1.234 이상 (채팅창의 허용/거부 버튼)"].fix == "claude update")  # 2.1.0 is too old
        package = by_item["프로젝트 MyGame: 패키지"]
        self.assertFalse(package.ok)
        self.assertIn("unity-install", package.fix)



class ChatSetupTests(unittest.TestCase):
    def test_channel_is_registered_for_this_folder_only(self):
        cmd = ch.chat_add_command("uv run -q --no-project")
        self.assertEqual(cmd[:7], ["claude", "mcp", "add", "--scope", "local", ch.CHAT_SERVER_NAME, "--"])
        self.assertEqual(cmd[7:], ["uv", "run", "-q", "--no-project", str(ch.CHAT_CHANNEL_SCRIPT)])
        self.assertTrue(ch.CHAT_CHANNEL_SCRIPT.exists() and ch.CHAT_ADDON_SOURCE.exists())
        self.assertIn(f"server:{ch.CHAT_SERVER_NAME}", ch.SUPERVISOR_COMMAND)

    def test_supervisor_scripts_use_the_same_command(self):
        for name in ("start-supervisor.cmd", "start-supervisor.sh"):
            script = (ch.REPO_ROOT / name).read_text(encoding="utf-8")
            self.assertIn(ch.SUPERVISOR_COMMAND, script)
            self.assertIn("CLAUDE_HAND_SUPERVISOR=1", script)
            self.assertIn("MCP_TIMEOUT", script)

    def test_chat_config_is_merged_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
            (root / ".claude" / "settings.local.json").write_text(
                json.dumps({"permissions": {"allow": ["Bash(git status)"]}, "model": "keep-me"}), encoding="utf-8")
            plan = ch.plan_chat_config(root, "uv run -q --no-project", direct_servers=[])
            self.assertEqual(sorted(p.name for p in plan), [".mcp.json", "settings.local.json"])
            ch.write_json_files(plan)
            mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(mcp["mcpServers"]["other"], {"command": "x"})
            entry = mcp["mcpServers"][ch.CHAT_SERVER_NAME]
            self.assertEqual(entry["command"], "uv")
            self.assertEqual(entry["env"], {"CLAUDE_HAND_SUPERVISOR": "${CLAUDE_HAND_SUPERVISOR:-0}"})
            self.assertEqual(entry["args"][:3], ["run", "-q", "--no-project"])
            self.assertTrue(entry["args"][-1].endswith("/tools/chat_channel.py") and Path(entry["args"][-1]).is_absolute())
            settings = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["permissions"]["allow"],
                             ["Bash(git status)", ch.CHAT_REPLY_RULE, ch.CHAT_CONTEXT_RULE, ch.CHAT_PROGRESS_RULE])
            self.assertEqual(settings["enabledMcpjsonServers"], [ch.CHAT_SERVER_NAME])
            self.assertEqual(settings["model"], "keep-me")
            self.assertEqual(ch.plan_chat_config(root, "uv run -q --no-project", direct_servers=[]), {})  # second run changes nothing

    def test_chat_config_refuses_broken_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".mcp.json").write_text("{oops", encoding="utf-8")
            with self.assertRaises(SystemExit):
                ch.plan_chat_config(Path(tmp))

    def test_tool_allow_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ch.write_json_files(ch.plan_tool_allow(root, ["unity_mygame", "blender_props"]))
            path = root / ".claude" / "settings.local.json"
            rules = json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"]
            self.assertEqual(rules, ["mcp__unity_mygame__*", "mcp__blender_props__*"])
            self.assertEqual(ch.plan_tool_allow(root, ["unity_mygame", "blender_props"]), {})
            ch.write_json_files(ch.plan_tool_allow(root, ["unity_mygame"], prune=True))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"], ["mcp__unity_mygame__*"])

    def test_prewarm_reads_the_check_result(self):
        class Done:
            stdout = 'Installed Python 3.12\n{"ok": true, "python": "3.12.1", "port": 8765}\n'
            stderr = ""
        seen = {}
        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return Done()
        result = ch.prewarm_chat_channel(Path("/repo"), "uv run -q --no-project", run=fake_run)
        self.assertTrue(result["ok"])
        self.assertEqual(seen["cmd"][-1], "--check")
        self.assertTrue(seen["cmd"][-2].endswith("chat_channel.py"))

    def test_claude_version(self):
        self.assertEqual(ch.parse_claude_version("2.1.319 (Claude Code)"), (2, 1, 319))
        self.assertLess(ch.parse_claude_version("2.1.99"), ch.MIN_CLAUDE_FOR_PERMISSION_RELAY)
        self.assertIsNone(ch.parse_claude_version(None))

    def test_registration_check(self):
        class Done:
            def __init__(self, code):
                self.returncode = code
                self.stdout = "claude-hand-chat:\n  Scope: Project config\n" if code == 0 else 'No MCP server named "x"'
                self.stderr = ""
        self.assertTrue(ch.chat_server_registered(run=lambda *a, **k: Done(0), which=lambda _: "claude"))
        self.assertFalse(ch.chat_server_registered(run=lambda *a, **k: Done(1), which=lambda _: "claude"))
        self.assertIsNone(ch.chat_server_registered(which=lambda _: None))

    def test_chat_addon_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "Blender"
            for version in ("4.2", "4.10", "3.6"):
                (base / version / "scripts" / "addons").mkdir(parents=True)
            self.assertEqual(ch.blender_chat_targets(base), [base / "4.10" / "scripts" / "addons"])  # newest, numerically
            mcp = base / "4.2" / "scripts" / "addons" / ch.BLENDER_ADDON_FILE
            mcp.write_text("", encoding="utf-8")
            self.assertEqual(ch.blender_chat_targets(base), [mcp.parent])  # next to MCP for Blender
            installed = mcp.parent / (ch.CHAT_ADDON_MODULE + ".py")
            installed.write_text("", encoding="utf-8")
            self.assertEqual(ch.find_chat_addon_installs(base), [installed])

    def test_install_chat_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with unittest.mock.patch("sys.stdout", out), \
                 unittest.mock.patch.object(ch, "find_blender_executables", lambda **kw: [Path("blender")]):
                ch.main(["blender-install-chat", "--addons-dir", tmp, "--dry-run"])
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertIn("--background", out.getvalue())

    def test_enable_script_is_valid_python(self):
        ast.parse(ch.ENABLE_CHAT_ADDON)
        self.assertIn(f"addon_utils.enable('{ch.CHAT_ADDON_MODULE}'", ch.ENABLE_CHAT_ADDON)



class UpdateAndStatusTests(unittest.TestCase):
    class Done:
        def __init__(self, out, code=0):
            self.stdout, self.stderr, self.returncode = out, "", code

    def test_claude_mcp_get_parsing(self):
        local = "claude-hand-chat:\n  Scope: Local config (private to you in this project)\n  Status: ✓ Connected\n"
        pending = "claude-hand-chat:\n  Scope: Project config (shared via .mcp.json)\n  Status: ⏸ Pending approval\n"
        entry = ch.claude_mcp_get(run=lambda *a, **k: self.Done(local), which=lambda _: "claude")
        self.assertEqual((entry.exists, entry.scope, entry.pending), (True, "local", False))
        entry = ch.claude_mcp_get(run=lambda *a, **k: self.Done(pending), which=lambda _: "claude")
        self.assertEqual((entry.scope, entry.pending), ("project", True))
        missing = ch.claude_mcp_get(run=lambda *a, **k: self.Done('No MCP server named "claude-hand-chat".'), which=lambda _: "claude")
        self.assertFalse(missing.exists)
        self.assertIsNone(ch.claude_mcp_get(which=lambda _: None))

    def write_chat_files(self, home, server=None, status=None):
        (home).mkdir(parents=True, exist_ok=True)
        if server:
            (home / "server.json").write_text(json.dumps(server), encoding="utf-8")
        if status:
            (home / "status.json").write_text(json.dumps(status), encoding="utf-8")

    def report(self, tmp, mcp_entry=None, fetch=None, mcp_json=None):
        root = Path(tmp) / "repo"
        root.mkdir(exist_ok=True)
        if mcp_json is not None:
            (root / ".mcp.json").write_text(json.dumps(mcp_json), encoding="utf-8")
        return "\n".join(ch.chat_status_report(Path(tmp) / "chat", mcp_entry=lambda: mcp_entry, fetch=fetch, root=root))

    GOOD_MCP = {"mcpServers": {"claude-hand-chat": {"command": "uv", "args": [], "env": {"CLAUDE_HAND_SUPERVISOR": "x"}}}}

    def test_status_serving(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_chat_files(Path(tmp) / "chat", server={"port": 8765, "token": "t"})
            text = self.report(tmp, fetch=lambda req: {"claude_connected": True, "chats": ["unity-mygame"]}, mcp_json=self.GOOD_MCP)
        self.assertIn("OK 감독 세션이 채팅을 받고 있습니다", text)
        self.assertNotIn("!!", text)

    def test_status_passive_explains_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_chat_files(Path(tmp) / "chat", status={"mode": "passive", "reason": "채널 플래그 없이 연 Claude Code 세션"})
            text = self.report(tmp, mcp_json=self.GOOD_MCP)
        self.assertIn("대기 모드", text)
        self.assertIn("start-supervisor.cmd", text)

    def test_status_flags_old_local_registration_and_old_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = {"mcpServers": {"claude-hand-chat": {"command": "uv", "args": []}}}
            text = self.report(tmp, mcp_entry=ch.McpEntry(True, scope="local"), mcp_json=old)
        self.assertIn("local 범위", text)
        self.assertIn("이전 버전 형식", text)
        self.assertIn("한 번도 시작되지 않았습니다", text)

    def test_status_stale_server_file(self):
        def down(req):
            raise OSError("connection refused")
        with tempfile.TemporaryDirectory() as tmp:
            self.write_chat_files(Path(tmp) / "chat", server={"port": 8765, "token": "t"})
            text = self.report(tmp, fetch=down, mcp_json=self.GOOD_MCP)
        self.assertIn("응답하지 않습니다", text)

    def test_generated_agents_round_trip(self):
        unity = ch.Instance(name="My Game", hash="aaaa1111", path="C:/Projects/My Game")
        blender = ch.BlenderInstance(port=9877, filepath="C:/Art/props (v2).blend")
        files = ch.build_agent_files([unity], ch.ServerConfig(transport="stdio"), [blender], None, "uv run")
        back = {name: ch.parse_generated_agent(text) for name, text in files.items()}
        self.assertEqual((back["unity-my-game.md"].id, back["unity-my-game.md"].path), (unity.id, unity.path))
        self.assertEqual((back["blender-props-v2.md"].port, back["blender-props-v2.md"].filepath), (9877, blender.filepath))
        untitled = ch.build_agent_files([], None, [ch.BlenderInstance(port=9878)], None, "uv run")
        self.assertEqual(ch.parse_generated_agent(next(iter(untitled.values()))).filepath, "")

    def test_stale_checks_and_update_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents, skills = Path(tmp) / "agents", Path(tmp) / "skills"
            inst = ch.Instance(name="MyGame", hash="aaaa1111")
            ch.write_agent_files(ch.build_agent_files([inst], ch.ServerConfig(transport="stdio"), [], None, "uv run -q --no-project"), agents)
            agent_file = agents / "unity-mygame.md"
            agent_file.write_text(agent_file.read_text(encoding="utf-8").replace("## 작업 순서", "## old"), encoding="utf-8")
            (skills / "claude-hand").mkdir(parents=True)
            (skills / "claude-hand" / "SKILL.md").write_text("old skill", encoding="utf-8")
            (skills / "claude-hand-unity").mkdir()
            (skills / "claude-hand-unity" / "SKILL.md").write_text("---\nname: claude-hand-unity\n---\n", encoding="utf-8")
            with unittest.mock.patch.object(ch, "candidate_config_files", lambda cwd=None: []), \
                 unittest.mock.patch.object(ch, "find_chat_addon_installs", lambda base=None: []):
                items = {c.item for c in ch.stale_checks(agents, skills)}
                self.assertIn("설치된 감독 스킬이 저장소 버전과 다름", items)
                self.assertIn("이전 버전으로 만든 하위 세션 정의", items)
                out = io.StringIO()
                with unittest.mock.patch("sys.stdout", out), \
                     unittest.mock.patch.object(ch, "claude_mcp_get", lambda **kw: None), \
                     unittest.mock.patch.object(ch, "read_chat_files", lambda home=None: (None, None)), \
                     unittest.mock.patch.object(ch, "discover_stdio", lambda *a, **k: []), \
                     unittest.mock.patch.object(ch, "discover_blender", lambda *a, **k: []), \
                     unittest.mock.patch.object(ch, "REPO_ROOT", Path(tmp) / "repo"):
                    ch.main(["update", "--agents-dir", str(agents), "--skills-dir", str(skills)])
                self.assertEqual(ch.stale_checks(agents, skills), [])
            self.assertFalse((skills / "claude-hand-unity").exists())
            self.assertIn("## 작업 순서", agent_file.read_text(encoding="utf-8"))



class EverydayStartTests(unittest.TestCase):
    def test_start_scripts(self):
        cmd = (ch.REPO_ROOT / "start-supervisor.cmd").read_bytes()
        self.assertFalse(cmd.startswith(b"\xef\xbb\xbf"), "a BOM breaks the first line of a batch file")
        text = cmd.decode("utf-8")
        self.assertIn("\r\n", text)
        self.assertLess(text.index("chcp 65001"), text.index("echo [Claude Hand]"))  # Korean messages need the UTF-8 code page
        self.assertLess(text.index("preflight"), text.index("claude --dangerously"))
        inside = False
        for line in text.splitlines():
            t = line.strip()
            if t.startswith("if ") and t.endswith("("):
                inside = True
            elif t == ")":
                inside = False
            elif inside and t.startswith("echo"):
                self.assertNotRegex(t, r"[()]", "a ) inside an if-block ends the block early")
        sh = (ch.REPO_ROOT / "start-supervisor.sh").read_text(encoding="utf-8")
        self.assertLess(sh.index("preflight"), sh.index("claude --dangerously"))

    def test_user_config_records_the_start_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "config.json").write_text(json.dumps({"keep": 1}), encoding="utf-8")
            ch.write_user_config(home, root=Path("/repo"), start_script=Path("/repo/start-supervisor.cmd"))
            config = json.loads((home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config, {"keep": 1, "repo": "/repo", "start_script": "/repo/start-supervisor.cmd"})

    def test_supervisor_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertFalse(ch.supervisor_running(home))
            (home / "chat").mkdir()
            (home / "chat" / "server.json").write_text(json.dumps({"port": 1, "token": "t"}), encoding="utf-8")
            self.assertTrue(ch.supervisor_running(home, fetch=lambda req: {"ok": True}))
            def down(req):
                raise OSError("refused")
            self.assertFalse(ch.supervisor_running(home, fetch=down))

    def run_preflight(self, tmp, running=False):
        home = Path(tmp) / "home"
        out = io.StringIO()
        refreshed = []
        with unittest.mock.patch.dict("os.environ", {"CLAUDE_HAND_HOME": str(home)}), \
             unittest.mock.patch.object(ch.Path, "home", lambda: Path(tmp)), \
             unittest.mock.patch.object(ch, "supervisor_running", lambda home=None, fetch=None: running), \
             unittest.mock.patch.object(ch, "plan_chat_config", lambda root=None, runner=None, direct_servers=None: {}), \
             unittest.mock.patch.object(ch, "refresh_agents", lambda **kw: refreshed.append(kw) or ch.SyncResult()), \
             unittest.mock.patch.object(ch, "LAUNCH_ARGS_FILE", Path(tmp) / ".launch-args"), \
             unittest.mock.patch("sys.stdout", out):
            code = ch.main(["preflight"])
        return code, out.getvalue(), refreshed, home

    def test_preflight_first_and_later_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, text, refreshed, home = self.run_preflight(tmp)
            self.assertEqual(code, 0)
            self.assertIn("닫지 마세요", text)
            self.assertIn("처음에는", text)
            self.assertEqual(len(refreshed), 1)
            self.assertTrue((Path(tmp) / ".claude" / "agents").is_dir())  # exists before Claude Code starts
            self.assertTrue((Path(tmp) / ".claude" / "skills" / "claude-hand" / "SKILL.md").exists())
            self.assertTrue((home / "config.json").exists())
            _, again, _, _ = self.run_preflight(tmp)
            self.assertNotIn("처음에는", again)

    def test_supervisor_resumes_its_own_conversation(self):
        a, b, c, d = (f"{n}" * 8 + "-1111-1111-1111-111111111111" for n in "abcd")
        used = lambda sid: sid == b  # only b has a transcript
        made = lambda: d
        # a first start with this version carries on from the newest earlier Claude Hand conversation
        self.assertEqual(ch.supervisor_session({}, transcript=used, latest=lambda: c, new_id=made), (["--resume", c], c, True))
        # nothing earlier: a new conversation with an id chosen now
        self.assertEqual(ch.supervisor_session({}, transcript=used, latest=lambda: None, new_id=made), (["--session-id", d], d, False))
        # the remembered one has a transcript: open it again
        self.assertEqual(ch.supervisor_session({"supervisor_session": b}, transcript=used, latest=lambda: c, new_id=made),
                         (["--resume", b], b, True))
        # remembered but never used (new-session, or closed before any message): start that one, not an older one
        self.assertEqual(ch.supervisor_session({"supervisor_session": a}, transcript=used, latest=lambda: c, new_id=made),
                         (["--session-id", a], a, False))

    def test_earlier_supervisor_conversation_is_found_by_its_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, config = Path(tmp) / "claude-hand", Path(tmp) / "cfg"
            root.mkdir()
            folder = config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))
            folder.mkdir(parents=True)
            supervisor = folder / "aaaaaaaa-1111-1111-1111-111111111111.jsonl"
            supervisor.write_text('{"type":"custom-title","customTitle":"Claude Hand","sessionId":"x"}\n', encoding="utf-8")
            os.utime(supervisor, (1_000_000, 1_000_000))
            (folder / "bbbbbbbb-1111-1111-1111-111111111111.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")  # newer, plain
            self.assertEqual(ch.latest_supervisor_session(root, config), supervisor.stem)
            self.assertEqual(ch.session_transcript(supervisor.stem, config), supervisor)
            self.assertIsNone(ch.session_transcript("cccccccc-1111-1111-1111-111111111111", config))
            self.assertIsNone(ch.session_transcript("../../etc", config))

    def test_new_session_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            out = io.StringIO()
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_HAND_HOME": str(home)}), \
                 unittest.mock.patch.object(ch, "supervisor_running", lambda home=None, fetch=None: False), \
                 unittest.mock.patch("sys.stdout", out):
                ch.main(["new-session"])
            session = ch.read_user_config(home)["supervisor_session"]
            self.assertRegex(session, r"^[0-9a-f-]{36}$")
            self.assertEqual(ch.supervisor_session({"supervisor_session": session}, transcript=lambda s: None)[0],
                             ["--session-id", session])
            self.assertIn("새 대화", out.getvalue())

    def test_preflight_stops_a_second_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, text, refreshed, _ = self.run_preflight(tmp, running=True)
        self.assertEqual(code, 3)
        self.assertIn("이미 실행 중", text)
        self.assertEqual(refreshed, [])

    def test_shortcut_files(self):
        home = Path("/home/me")
        mac = ch.shortcut_files("darwin", home)
        linux = ch.shortcut_files("linux", home)
        self.assertEqual(list(mac), [home / "Desktop" / "Claude Hand.command"])
        self.assertIn(ch.START_SCRIPT.as_posix(), next(iter(mac.values())))
        self.assertIn("Terminal=true", next(iter(linux.values())))

    @unittest.skipIf(sys.platform == "win32", "Windows shortcuts are .lnk files made by PowerShell")
    def test_shortcut_command_creates_and_removes(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(ch.Path, "home", lambda: Path(tmp)), \
                unittest.mock.patch("sys.stdout", io.StringIO()):
            ch.main(["shortcut"])
            created = list((Path(tmp) / "Desktop").iterdir())
            self.assertEqual(len(created), 1)
            self.assertTrue(os.access(created[0], os.X_OK))
            ch.main(["shortcut", "--remove"])
            self.assertEqual(list((Path(tmp) / "Desktop").iterdir()), [])



class RemoteControlTests(unittest.TestCase):
    def test_launch_args(self):
        on = '--remote-control --name "Claude Hand"'
        self.assertEqual(ch.launch_args({}, env={}), on)  # on by default
        self.assertEqual(ch.launch_args({"remote_control": True}, env={}), on)
        self.assertEqual(ch.launch_args({"remote_control": False}, env={}), "")
        self.assertEqual(ch.launch_args({}, env={"ANTHROPIC_API_KEY": "x"}), "")  # cannot work with this login: left out

    def test_problems(self):
        self.assertEqual(ch.remote_control_problems({}), [])
        self.assertEqual(len(ch.remote_control_problems({"ANTHROPIC_API_KEY": "x", "CLAUDE_CODE_USE_BEDROCK": "1"})), 2)
        self.assertIn("ANTHROPIC_BASE_URL", ch.remote_control_problems({"ANTHROPIC_BASE_URL": "http://proxy"})[0])
        # the Claude desktop app puts the official address in its sessions' environment
        self.assertEqual(ch.remote_control_problems({"ANTHROPIC_BASE_URL": "https://api.anthropic.com/"}), [])

    def test_on_off_and_preflight_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clean = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE_CODE_USE"))}
            with unittest.mock.patch.dict("os.environ", {**clean, "CLAUDE_HAND_HOME": str(home)}, clear=True), \
                 unittest.mock.patch.object(ch, "supervisor_running", lambda home=None, fetch=None: False), \
                 unittest.mock.patch("sys.stdout", io.StringIO()):
                ch.main(["remote-control", "on"])
                self.assertTrue(ch.read_user_config(home)["remote_control"])
                self.assertIn("start_script", ch.read_user_config(home))
                runner = EverydayStartTests()
                _, text, _, _ = runner.run_preflight(tmp)
                session = ch.read_user_config(home)["supervisor_session"]  # no earlier Claude Hand conversation here
                self.assertEqual((Path(tmp) / ".launch-args").read_text(encoding="utf-8").strip(),
                                 f"--session-id {session} " + ch.launch_args({"remote_control": True}))
                ch.main(["remote-control", "off"])
                self.assertFalse(ch.read_user_config(home)["remote_control"])
        self.assertIn("Code 탭", text)

    def test_start_scripts_pass_the_launch_args(self):
        cmd = (ch.REPO_ROOT / "start-supervisor.cmd").read_text(encoding="utf-8")
        self.assertIn('set /p CH_ARGS=<".launch-args"', cmd)
        self.assertIn("%CH_ARGS% %*", cmd)
        self.assertIn("$CH_ARGS", (ch.REPO_ROOT / "start-supervisor.sh").read_text(encoding="utf-8"))
        self.assertIn('in (".launch-unset") do set "%%v="', cmd)
        self.assertIn("< .launch-unset", (ch.REPO_ROOT / "start-supervisor.sh").read_text(encoding="utf-8"))
        self.assertLess(cmd.index(".launch-unset"), cmd.index("claude --dangerously"))

    def test_a_parent_sessions_variables_are_dropped(self):
        """Started from a Claude desktop app session (`start`), the supervisor inherited its session id,
        CLAUDE_CODE_CHILD_SESSION and the app's messaging socket; settings the user made stay."""
        env = {"CLAUDECODE": "1", "CLAUDE_PID": "10600", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_CODE_CHILD_SESSION": "1",
               "CLAUDE_CODE_MESSAGING_SOCKET": r"\\.\pipe\x", "CLAUDE_AGENT_SDK_VERSION": "0.3", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop",
               "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_GIT_BASH_PATH": "C:/Git/bin/bash.exe", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "9",
               "CLAUDE_HAND_HOME": "C:/h", "ANTHROPIC_BASE_URL": "https://api.anthropic.com", "PATH": "C:/bin"}
        dropped = ch.inherited_session_env(env, persistent={"CLAUDE_CODE_MAX_OUTPUT_TOKENS"})
        self.assertEqual(dropped, ["CLAUDECODE", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT",
                                   "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID"])
        self.assertEqual(ch.inherited_session_env({"PATH": "C:/bin"}, persistent=set()), [])

    @unittest.skipIf(sys.platform == "win32", "POSIX shell")
    def test_shell_keeps_the_quoted_name_together(self):
        import subprocess
        script = 'CH_ARGS=\'--remote-control --name "Claude Hand"\'; set -- --resume x; eval "set -- $CH_ARGS \\"\\$@\\""; printf "[%s]" "$@"'
        out = subprocess.run(["sh", "-c", script], capture_output=True, text=True).stdout
        self.assertEqual(out, "[--remote-control][--name][Claude Hand][--resume][x]")



class DesktopModeTests(unittest.TestCase):
    def test_installed_skill_knows_where_claude_hand_is(self):
        text = ch.rendered_skill(Path("/opt/claude-hand"))
        self.assertNotIn("{{CLAUDE_HAND}}", text)
        self.assertIn('uv run "/opt/claude-hand/tools/claude_hand.py" sync', text)
        self.assertIn("{{CLAUDE_HAND}}", ch.SKILL_SOURCE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            installed = ch.install_skill(Path(tmp))
            self.assertEqual(installed.read_text(encoding="utf-8"), ch.rendered_skill())

    def test_old_desktop_mode_is_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "config.json").write_text(json.dumps({"mode": "desktop", "remote_control": False}), encoding="utf-8")
            out = io.StringIO()
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_HAND_HOME": str(home)}), unittest.mock.patch("sys.stdout", out):
                ch.main(["mode", "panels"])
            config = ch.read_user_config(home)
            self.assertNotIn("mode", config)
            self.assertFalse(config["remote_control"])  # other choices are kept
            self.assertIn("하나", out.getvalue())


class StartCommandTests(unittest.TestCase):
    def run_start(self, env=None, running=False, popen=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            launched = []
            base = {k: v for k, v in os.environ.items() if k != "CLAUDE_HAND_SUPERVISOR" and not k.startswith(("ANTHROPIC", "CLAUDE_CODE_USE"))}
            with unittest.mock.patch.dict("os.environ", {**base, "CLAUDE_HAND_HOME": tmp, **(env or {})}, clear=True), \
                 unittest.mock.patch.object(ch, "supervisor_running", lambda home=None, fetch=None: running), \
                 unittest.mock.patch.object(ch, "open_start_window", popen or (lambda: launched.append(1))), \
                 unittest.mock.patch("sys.stdout", out):
                code = ch.main(["start"])
            return code, out.getvalue(), launched

    def test_opens_the_window_and_says_where_to_look(self):
        code, text, launched = self.run_start()
        self.assertEqual((code, launched), (0, [1]))
        self.assertIn("I am using this for local development", text)
        self.assertIn("Code 탭", text)

    def test_already_running_or_inside_the_supervisor(self):
        for kwargs in ({"running": True}, {"env": {"CLAUDE_HAND_SUPERVISOR": "1"}}):
            code, text, launched = self.run_start(**kwargs)
            self.assertEqual((code, launched), (0, []), kwargs)

    def test_window_commands(self):
        calls = []
        popen = lambda cmd, **kw: calls.append((cmd, kw.get("creationflags", 0)))
        self.assertEqual(ch.open_start_window(Path("C:/ch/start-supervisor.cmd"), "win32", popen)[:4], ["cmd.exe", "/c", "start", "Claude Hand"])
        self.assertEqual(ch.open_start_window(Path("/ch/start-supervisor.sh"), "darwin", popen)[:3], ["open", "-a", "Terminal"])
        self.assertEqual(len(calls), 2)

    def test_minimize_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_HAND_HOME": tmp}), unittest.mock.patch("sys.stdout", io.StringIO()):
                ch.main(["minimize", "off"])
                import chat_channel
                self.assertFalse(chat_channel.minimize_after_start(Path(tmp)))
                ch.main(["minimize", "on"])
                self.assertTrue(chat_channel.minimize_after_start(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
