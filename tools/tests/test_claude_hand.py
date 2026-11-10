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
os.environ["CLAUDE_HAND_LOG"] = "off"  # the tests leave the real event log alone
import claude_hand as ch  # noqa: E402
import handlib.agents  # noqa: E402
import handlib.capture  # noqa: E402
import handlib.chatsetup  # noqa: E402
import handlib.checks  # noqa: E402
import handlib.constants  # noqa: E402
import handlib.discovery  # noqa: E402
import handlib.install  # noqa: E402
import handlib.launch  # noqa: E402
import handlib.link  # noqa: E402
import handlib.settings  # noqa: E402


def write_status(directory: Path, hash_: str, project_path: str, port: int, **extra):
    data = {"unity_port": port, "project_path": project_path, "unity_version": "6000.3.2f1", "reloading": False}
    data.update(extra)
    (directory / f"unity-mcp-status-{hash_}.json").write_text(json.dumps(data), encoding="utf-8")


class DiscoveryTests(unittest.TestCase):
    def test_project_name_matches_mcp_for_unity_rule(self):
        self.assertEqual(handlib.discovery.project_name_from_assets_path("C:/Projects/MyGame/Assets"), "MyGame")
        self.assertEqual(handlib.discovery.project_name_from_assets_path("C:\\Projects\\Tool Kit\\Assets\\"), "Tool Kit")
        self.assertEqual(handlib.discovery.project_name_from_assets_path(""), "Unknown")

    def test_stdio_status_files_become_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_status(d, "aaaa1111", "C:/Projects/MyGame/Assets", 6401)
            write_status(d, "bbbb2222", "C:/Projects/Props/Assets", 6402)
            (d / "unity-mcp-status-broken.json").write_text("{not json", encoding="utf-8")
            found = handlib.discovery.discover_stdio(d, probe=lambda port: port == 6401)
        self.assertEqual([i.id for i in found], ["MyGame@aaaa1111", "Props@bbbb2222"])
        self.assertEqual(found[0].path, "C:/Projects/MyGame")
        self.assertTrue(found[0].alive)
        self.assertFalse(found[1].alive)

    def test_reloading_editor_counts_as_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_status(d, "cccc3333", "C:/P/Reload/Assets", 6403, reloading=True)
            found = handlib.discovery.discover_stdio(d, probe=lambda port: False)
        self.assertTrue(found[0].alive)

    def test_http_discovery(self):
        payload = {"success": True, "instances": [{"project": "MyGame", "hash": "aaaa1111", "unity_version": "6000.3"}]}
        seen = []

        def fake_fetch(url):
            seen.append(url)
            return payload

        found = handlib.discovery.discover_http("http://localhost:8080/mcp", fetch=fake_fetch)
        self.assertEqual(seen, ["http://localhost:8080/api/instances"])
        self.assertEqual([i.id for i in found], ["MyGame@aaaa1111"])

    def test_http_discovery_survives_server_down(self):
        def down(url):
            raise OSError("connection refused")

        self.assertEqual(handlib.discovery.discover_http("http://localhost:8080/mcp", fetch=down), [])


class ConfigDetectionTests(unittest.TestCase):
    def detect(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.json"
            f.write_text(json.dumps(data), encoding="utf-8")
            return handlib.discovery.detect_server_config([f])

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
        cfg = handlib.discovery.detect_server_config([Path("/nonexistent/config.json")])
        self.assertEqual((cfg.transport, cfg.command, cfg.args), ("stdio", "uvx", handlib.constants.DEFAULT_STDIO_ARGS))


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.a = handlib.discovery.Instance(name="MyGame", hash="aaaa1111", path="C:/Projects/MyGame")
        self.b = handlib.discovery.Instance(name="Props Lib", hash="bbbb2222")

    def test_names_and_server_keys(self):
        names = handlib.agents.agent_names([self.a, self.b])
        self.assertEqual(names, {"MyGame@aaaa1111": "unity-mygame", "Props Lib@bbbb2222": "unity-props-lib"})
        self.assertEqual(handlib.agents.server_key("unity-props-lib"), "unity_props_lib")

    def test_same_folder_name_gets_hash_suffix(self):
        twin = handlib.discovery.Instance(name="MyGame", hash="cccc3333")
        names = handlib.agents.agent_names([self.a, twin])
        self.assertEqual(sorted(names.values()), ["unity-mygame-aaaa", "unity-mygame-cccc"])

    def test_pinned_args_replace_existing_default(self):
        args = ["--from", "x", "--default-instance", "Old@1", "--transport", "stdio", "--default-instance=Old@2"]
        self.assertEqual(handlib.agents.pinned_args(args, "MyGame@aaaa1111"),
                         ["--from", "x", "--transport", "stdio", "--default-instance", "MyGame@aaaa1111"])

    def test_agents_point_at_their_notes(self):
        unity = handlib.agents.render_agent(self.a, "unity-mygame", handlib.discovery.ServerConfig(transport="stdio"), "uv run", allowlist=True, focus_nudge=False)
        self.assertIn("`C:/Projects/MyGame/ClaudeHand/NOTES.md`", unity)
        self.assertNotIn("메모", handlib.agents.render_agent(self.b, "unity-props-lib", handlib.discovery.ServerConfig(transport="stdio"), "uv run",
                                                 allowlist=True, focus_nudge=False))  # no known path: nothing to point at
        blender = handlib.agents.render_blender_prompt(handlib.discovery.BlenderInstance(port=9877, filepath="C:/Art/character.blend"), "blender_character")
        self.assertIn("`C:/Art/character.claude-hand.md`", blender)
        self.assertNotIn("메모", handlib.agents.render_blender_prompt(handlib.discovery.BlenderInstance(port=9877, filepath=""), "blender_x"))
        self.assertEqual(handlib.agents.unity_notes_path("C:\\Projects\\MyGame\\"), "C:\\Projects\\MyGame/ClaudeHand/NOTES.md")

    def test_prompts_come_from_the_template_files(self):
        fill = handlib.agents.fill
        self.assertEqual(fill("a {{X}} b\n{{?ON}}- kept {{X}}\n{{?OFF}}- dropped\nend", flags={"ON": True}, X="C:\\x"),
                         "a C:\\x b\n- kept C:\\x\nend")
        self.assertEqual(fill("{{A}}", A="{{B}}"), "{{B}}")  # a value is put in as it is, never filled again
        with self.assertRaises(KeyError):
            fill("{{MISSING}}")
        http = handlib.agents.render_agent_prompt(self.a, "unity_mygame", "http")
        stdio = handlib.agents.render_agent_prompt(self.b, "unity_props_lib", "stdio")
        blender = handlib.agents.render_blender_prompt(handlib.discovery.BlenderInstance(port=9877, filepath="C:/Art/character.blend"), "blender_character")
        for text in (http, stdio, blender):
            self.assertNotRegex(text, r"\{\{\??\w+\}\}")  # nothing left unfilled
        self.assertIn('set_active_instance(instance="MyGame@aaaa1111")', http)
        self.assertNotIn("set_active_instance", stdio)
        self.assertIn("**MyGame** (경로: `C:/Projects/MyGame`) 하나만", http)
        self.assertIn("**Props Lib** 하나만", stdio)

    def test_stdio_agent_file(self):
        server = handlib.discovery.ServerConfig(transport="stdio")
        text = handlib.agents.render_agent(self.a, "unity-mygame", server, "uv run -q --no-project", allowlist=True, focus_nudge=False)
        front = text.split("---")[1]
        self.assertIn("name: unity-mygame", front)
        self.assertIn('tools: "mcp__unity_mygame__*, mcp__unity_mygame_ch__*, Read, Glob, Grep"', front)
        self.assertIn("  - unity_mygame_ch:", front)
        self.assertIn("  - unity_mygame:", front)
        self.assertIn('"--default-instance", "MyGame@aaaa1111"', front)
        self.assertIn('UNITY_MCP_DISABLE_FOCUS_NUDGE: "1"', front)
        self.assertIn('matcher: "mcp__.*"', front)
        self.assertIn("guard.py\\\" --pin \\\"MyGame@aaaa1111\\\" --server unity_mygame --transport stdio --app unity", front)
        self.assertIn("  Stop:\n    - hooks:\n        - type: command\n", front)
        self.assertIn('--transport stdio --app unity --stop"', front)  # the same script tells the channel the subagent stopped
        self.assertIn(handlib.constants.GENERATED_MARKER, text)
        self.assertNotIn("set_active_instance(instance=", text)

    def test_hook_starts_the_guard_by_full_path(self):
        """A hook that cannot start does not stop the call, so it must not depend on the hook shell's PATH."""
        found = {"uv": "C:\\Users\\me\\.local\\bin\\uv.EXE", "py": "C:\\Program Files\\Python\\py.exe"}
        which = lambda name: found.get(name)
        self.assertEqual(handlib.agents.resolved_runner("uv run -q --no-project", which), "C:/Users/me/.local/bin/uv.EXE run -q --no-project")
        self.assertEqual(handlib.agents.resolved_runner("py -3", which), "py -3")  # a path with spaces cannot be quoted for both shells
        self.assertEqual(handlib.agents.resolved_runner("python3", which), "python3")  # not found: left for the shell to find
        self.assertEqual(handlib.agents.resolved_runner("/opt/uv/uv run", lambda name: 1 / 0), "/opt/uv/uv run")  # already a path
        command = handlib.agents.hook_command("no-such-runner -x", "MyGame@aaaa1111", "unity_mygame", "stdio")
        self.assertEqual(command, f'no-such-runner -x "{handlib.constants.GUARD_SCRIPT.as_posix()}" --pin "MyGame@aaaa1111" '
                                  "--server unity_mygame --transport stdio --app unity")
        self.assertTrue(handlib.constants.GUARD_SCRIPT.exists())

    def test_http_agent_file_pins_with_set_active_instance(self):
        server = handlib.discovery.ServerConfig(transport="http", url="http://localhost:8080/mcp")
        text = handlib.agents.render_agent(self.a, "unity-mygame", server, "uv run", allowlist=True, focus_nudge=False)
        self.assertIn("type: http", text)
        self.assertIn('url: "http://localhost:8080/mcp"', text)
        self.assertIn('set_active_instance(instance="MyGame@aaaa1111")', text)
        self.assertNotIn("--default-instance", text)

    def test_disallow_mode_blocks_parent_server(self):
        server = handlib.discovery.ServerConfig(transport="stdio", key="unityMCP")
        text = handlib.agents.render_agent(self.a, "unity-mygame", server, "uv run", allowlist=False, focus_nudge=True)
        self.assertIn('disallowedTools: "mcp__unityMCP__*"', text)
        self.assertNotIn("tools: ", text.split("disallowedTools")[0])
        self.assertNotIn("UNITY_MCP_DISABLE_FOCUS_NUDGE", text)

    def test_frontmatter_values_are_valid_json_strings(self):
        # Every quoted scalar we emit must round-trip, or YAML parsing would break.
        server = handlib.discovery.ServerConfig(transport="stdio", env={"PATH_HINT": 'C:\\Program Files\\"uv"'})
        text = handlib.agents.render_agent(self.b, "unity-props-lib", server, "uv run", allowlist=True, focus_nudge=False)
        for line in text.split("---")[1].splitlines():
            if ': "' in line:
                json.loads(line.split(": ", 1)[1])

    def test_sync_writes_skips_and_prunes(self):
        server = handlib.discovery.ServerConfig(transport="stdio")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            first = handlib.agents.sync_agents([self.a, self.b], d, server, "uv run")
            self.assertEqual(len(first.written), 2)
            second = handlib.agents.sync_agents([self.a, self.b], d, server, "uv run")
            self.assertEqual((len(second.written), len(second.unchanged)), (0, 2))
            third = handlib.agents.sync_agents([self.a], d, server, "uv run", prune=True)
            self.assertEqual([p.name for p in third.removed], ["unity-props-lib.md"])
            self.assertEqual(sorted(p.name for p in d.iterdir()), ["unity-mygame.md"])

    def test_sync_refuses_to_overwrite_hand_written_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "unity-mygame.md").write_text("---\nname: unity-mygame\n---\nmine\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                handlib.agents.sync_agents([self.a], d, handlib.discovery.ServerConfig(transport="stdio"), "uv run")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "agents"
            result = handlib.agents.sync_agents([self.a], d, handlib.discovery.ServerConfig(transport="stdio"), "uv run", dry_run=True)
            self.assertEqual(len(result.written), 1)
            self.assertFalse(d.exists())

    def test_skill_source_exists(self):
        self.assertTrue(handlib.constants.SKILL_SOURCE.exists())
        self.assertIn("name: claude-hand", handlib.constants.SKILL_SOURCE.read_text(encoding="utf-8"))


class PreviewCaptureTests(unittest.TestCase):
    PIN = "MyGame@aaaa1111"

    def test_previews_go_through_claude_hands_own_ways_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "shot.png")

            def fake_unity(entry, tool, params, timeout):
                Path(params["path"]).write_bytes(b"png")
                return {"success": True, "data": {"path": params["path"]}}
            with unittest.mock.patch.dict(sys.modules, {"unity_bridge": unittest.mock.Mock(
                    find_project=lambda hash_: {"hash": hash_} if hash_ == "aaaa1111" else None)}):
                self.assertEqual(handlib.capture.capture_window("unity", self.PIN, path, unity_request=fake_unity), path)
                self.assertIsNone(handlib.capture.capture_window("unity", "Other@ffff0000", path, unity_request=fake_unity))  # not open
            sent = []

            def fake_blender(port, command, timeout):
                sent.append((port, command["params"]["code"]))
                return {"status": "success", "result": {"result": json.dumps({"path": path, "width": 640, "height": 480})}}
            self.assertEqual(handlib.capture.capture_window("blender", "props@9876", path, blender=fake_blender), path)
            self.assertEqual(sent[0][0], 9876)
            self.assertIn(f"path = area.spaces.active, {path!r}", sent[0][1])
            no_view = lambda *a: {"status": "success", "result": {"result": '{"error": "no 3D viewport is open"}'}}
            self.assertIsNone(handlib.capture.capture_window("blender", "props@9876", path + "x", blender=no_view))
            self.assertIsNone(handlib.capture.capture_window("blender", "props@9876", path, blender=lambda *a: 1 / 0))  # Blender closed



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
        self.assertEqual(handlib.discovery.parse_ports("9876-9878,9900,9877"), [9876, 9877, 9878, 9900])
        self.assertEqual(handlib.discovery.parse_ports("80,70000"), [])

    def test_instance_names(self):
        self.assertEqual(handlib.discovery.BlenderInstance(port=9876, filepath="C:\\Art\\Hero Rig.blend").name, "Hero Rig")
        self.assertEqual(handlib.discovery.BlenderInstance(port=9877).name, "untitled-9877")
        self.assertEqual(handlib.discovery.BlenderInstance(port=9877).id, "untitled-9877@9877")

    def test_discovery_against_fake_addon(self):
        addon = FakeBlenderAddon()
        try:
            closed = socket.socket()
            closed.bind(("127.0.0.1", 0))
            unused_port = closed.getsockname()[1]
            closed.close()
            found = handlib.discovery.discover_blender([unused_port, addon.port])
        finally:
            addon.close()
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].port, found[0].filepath, found[0].blender_version), (addon.port, "C:/Art/character.blend", "5.1.0"))
        self.assertEqual(addon.seen, ["ping", "get_addon_info", "execute_code"])

    def test_discovery_skips_other_listeners(self):
        def not_mcp_for_blender(port, command, timeout):
            return {"jsonrpc": "2.0", "error": "unknown"}

        self.assertEqual(handlib.discovery.discover_blender([9876], request=not_mcp_for_blender), [])

    def detect(self, servers):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.json"
            f.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
            return handlib.discovery.detect_blender_config([f])

    def test_detect_github_addon_server(self):
        cfg = self.detect({"unityMCP": {"command": "uvx", "args": ["mcp-for-unity"]},
                           "blender": {"command": "uvx", "args": ["blender-mcp"], "env": {"BLENDER_HOST": "localhost"}}})
        self.assertEqual((cfg.command, cfg.args, cfg.env, cfg.warning), ("uvx", ["blender-mcp"], {"BLENDER_HOST": "localhost"}, ""))

    def test_other_blender_server_falls_back_with_warning(self):
        cfg = self.detect({"blender-lab": {"command": "python", "args": ["C:/lab/blmcp/server.py"]}})
        self.assertEqual((cfg.command, cfg.args), ("uvx", handlib.constants.DEFAULT_BLENDER_ARGS))
        self.assertIn("blender-lab", cfg.warning)

    def test_no_blender_entry_uses_default(self):
        cfg = self.detect({"github": {"command": "npx", "args": ["x"]}})
        self.assertEqual((cfg.command, cfg.args, cfg.warning), ("uvx", handlib.constants.DEFAULT_BLENDER_ARGS, ""))

    def test_with_port_replaces_existing(self):
        self.assertEqual(handlib.agents.with_port(["mcp-for-blender", "--port", "1111", "--port=2222", "--host", "h"], 9877),
                         ["mcp-for-blender", "--host", "h", "--port", "9877"])

    def test_agent_file(self):
        inst = handlib.discovery.BlenderInstance(port=9877, filepath="C:/Art/character.blend")
        server = handlib.discovery.ServerConfig(transport="stdio", command="uvx", args=["mcp-for-blender"], key="blender")
        text = handlib.agents.render_blender_agent(inst, "blender-character", server, "uv run -q --no-project", allowlist=True)
        front = text.split("---")[1]
        self.assertIn("name: blender-character", front)
        self.assertIn('tools: "mcp__blender_character__*, Read, Glob, Grep"', front)
        self.assertIn('args: ["mcp-for-blender", "--port", "9877"]', front)
        self.assertIn('BLENDER_PORT: "9877"', front)
        self.assertIn("--server blender_character --transport stdio --app blender", front)
        for line in front.splitlines():
            if ': "' in line:
                json.loads(line.split(": ", 1)[1])
        self.assertNotIn("__", re.sub(r"mcp__\w+__|__\w+__\*", "", text.split(handlib.constants.GENERATED_MARKER)[1]).replace("__init__", ""))

    def test_transfers_are_addon_functions_the_prompt_only_names(self):
        """They were code blocks in the prompt, typed out again by the subagent for every transfer."""
        prompt = handlib.agents.prompt_template("blender-subagent")
        addon = ast.parse(handlib.constants.CHAT_ADDON_SOURCE.read_text(encoding="utf-8"))
        defined = {node.name for node in addon.body if isinstance(node, ast.FunctionDef)}
        called = set(re.findall(r"claude_hand_chat\.(\w+)\(", prompt))
        self.assertEqual(called, {"undo_last_request", "export_blend", "import_blend", "export_fbx", "import_model"})
        self.assertLessEqual(called, defined)
        self.assertNotIn("```python", prompt)

    def test_names_for_duplicate_files(self):
        a = handlib.discovery.BlenderInstance(port=9876, filepath="C:/a/scene.blend")
        b = handlib.discovery.BlenderInstance(port=9877, filepath="C:/b/scene.blend")
        self.assertEqual(sorted(handlib.agents.blender_agent_names([a, b]).values()), ["blender-scene-9876", "blender-scene-9877"])

    def test_sync_writes_and_prunes_both_apps(self):
        unity = handlib.discovery.Instance(name="MyGame", hash="aaaa1111")
        blender = handlib.discovery.BlenderInstance(port=9876, filepath="C:/Art/character.blend")
        server = handlib.discovery.ServerConfig(transport="stdio")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            first = handlib.agents.sync_agents([unity], d, server, "uv run", blender=[blender])
            self.assertEqual(sorted(p.name for p in first.written), ["blender-character.md", "unity-mygame.md"])
            later = handlib.agents.sync_agents([unity], d, server, "uv run", blender=[], prune=True)
            self.assertEqual([p.name for p in later.removed], ["blender-character.md"])



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
            plan, manifest = handlib.install.plan_unity_install(project, package_dir=Path(tmp) / "pkg", source="folder")
            self.assertEqual(len(plan.changes), 2)
            handlib.install.apply_unity_install(project, manifest, plan)
            written = json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8"))
            deps = written["dependencies"]
            self.assertEqual(deps["com.unity.ugui"], "2.0.0")
            self.assertEqual(deps[handlib.constants.MCP_FOR_UNITY_PACKAGE], handlib.constants.MCP_FOR_UNITY_GIT_URL)
            self.assertTrue(deps[handlib.constants.CLAUDE_HAND_PACKAGE].startswith("file:") and deps[handlib.constants.CLAUDE_HAND_PACKAGE].endswith("/pkg"))
            self.assertEqual(written["scopedRegistries"], [])
            backup = json.loads(Path(plan.backup).read_text(encoding="utf-8"))
            self.assertNotIn(handlib.constants.MCP_FOR_UNITY_PACKAGE, backup["dependencies"])
            again, manifest_again = handlib.install.plan_unity_install(project, package_dir=Path(tmp) / "pkg", source="folder")
            self.assertEqual((again.changes, manifest_again), ([], None))

    def test_unity_install_respects_existing_mcp_for_unity(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp), deps={handlib.constants.MCP_FOR_UNITY_PACKAGE: "https://example/fork.git"})
            plan, manifest = handlib.install.plan_unity_install(project, package_dir=Path(tmp) / "pkg", source="folder")
            self.assertEqual(manifest["dependencies"][handlib.constants.MCP_FOR_UNITY_PACKAGE], "https://example/fork.git")
            self.assertEqual(len(plan.changes), 1)

    def test_new_installs_get_the_tested_mcp_for_unity_release(self):
        """A package that follows #main changes under the project whenever its lock file is made anew."""
        self.assertTrue(handlib.constants.MCP_FOR_UNITY_GIT_URL.endswith("#" + handlib.constants.MCP_FOR_UNITY_TESTED))
        self.assertFalse(handlib.install.follows_a_branch(handlib.constants.MCP_FOR_UNITY_GIT_URL))
        self.assertFalse(handlib.install.follows_a_branch("https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#81542a7e86292b5c"))
        self.assertFalse(handlib.install.follows_a_branch("file:C:/packages/unity-mcp"))  # not a git address
        self.assertFalse(handlib.install.follows_a_branch("10.3.0"))  # a registry version
        main = "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main"
        self.assertTrue(handlib.install.follows_a_branch(main))
        self.assertTrue(handlib.install.follows_a_branch("https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity"))
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp), deps={handlib.constants.MCP_FOR_UNITY_PACKAGE: main})
            plan, manifest = handlib.install.plan_unity_install(project, package_dir=Path(tmp) / "pkg", source="folder")
            self.assertEqual(manifest["dependencies"][handlib.constants.MCP_FOR_UNITY_PACKAGE], main)  # left alone unless asked
            self.assertTrue(any("--pin-mcp-for-unity" in note for note in plan.notes))
            plan, manifest = handlib.install.plan_unity_install(project, package_dir=Path(tmp) / "pkg", source="folder", pin_mcp_for_unity=True)
            self.assertEqual(manifest["dependencies"][handlib.constants.MCP_FOR_UNITY_PACKAGE], handlib.constants.MCP_FOR_UNITY_GIT_URL)

    def test_the_unity_package_does_not_need_mcp_for_unity_to_compile(self):
        """It borrowed six of its types once; a rename there would have taken the bridge and the chat window down."""
        package = handlib.constants.CLAUDE_HAND_PACKAGE_DIR
        asmdef = json.loads((package / "Editor" / "ClaudeHand.Editor.asmdef").read_text(encoding="utf-8"))
        self.assertEqual(asmdef["references"], [])
        for source in (package / "Editor").glob("*.cs"):
            code = "\n".join(line for line in source.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("//"))
            self.assertNotRegex(code, r"using MCPForUnity|McpForUnityTool|McpJobStateStore", source.name)
            self.assertTrue(source.with_name(source.name + ".meta").exists(), f"{source.name} needs a .meta file")
        manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        self.assertIn("com.unity.nuget.newtonsoft-json", manifest["dependencies"])  # the one package it does need

    def test_a_released_checkout_installs_the_package_from_github(self):
        """From a folder path the project's manifest names this PC, and the project opens nowhere else."""
        url = handlib.constants.CLAUDE_HAND_PACKAGE_GIT_URL
        self.assertEqual(url, "https://github.com/jinsu-lee-code/claude-hand.git?path=/unity/com.claudehand.tools#v" + handlib.constants.VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp))
            plan = lambda **kw: handlib.install.plan_unity_install(project, package_dir=Path(tmp) / "pkg", **kw)
            released, manifest = plan(released=lambda: True)
            self.assertEqual(manifest["dependencies"][handlib.constants.CLAUDE_HAND_PACKAGE], url)
            self.assertFalse(any("this PC" in note for note in released.notes))
            working, manifest = plan(released=lambda: False)
            self.assertTrue(manifest["dependencies"][handlib.constants.CLAUDE_HAND_PACKAGE].startswith("file:"))
            self.assertTrue(any("this PC" in note for note in working.notes))  # said, not left to be found out
            self.assertEqual(plan(source="release", released=lambda: False)[1]["dependencies"][handlib.constants.CLAUDE_HAND_PACKAGE], url)
            self.assertTrue(plan(source="folder", released=lambda: True)[1]["dependencies"][handlib.constants.CLAUDE_HAND_PACKAGE].startswith("file:"))

    def test_released_checkout_means_exactly_this_versions_tag(self):
        class Done:
            def __init__(self, out, code=0):
                self.stdout, self.returncode = out, code
        tag = "v" + handlib.constants.VERSION
        check = lambda run, git="git": handlib.install.released_checkout(run=run, git=git)
        self.assertTrue(check(lambda cmd, **kw: Done(tag + "\n")))
        self.assertFalse(check(lambda cmd, **kw: Done("v0.0.1\n")))  # another release
        self.assertFalse(check(lambda cmd, **kw: Done("", 128)))  # a branch tip, or not a git checkout
        self.assertFalse(check(lambda cmd, **kw: 1 / 0, git=""))  # no git: nothing is run
        self.assertFalse(check(lambda cmd, **kw: (_ for _ in ()).throw(OSError("gone"))))

    def test_everything_carries_the_one_version(self):
        import chat_channel
        import unity_bridge
        version = handlib.constants.VERSION
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        package = json.loads((handlib.constants.CLAUDE_HAND_PACKAGE_DIR / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], version)
        self.assertEqual(handlib.checks.addon_version(handlib.constants.CHAT_ADDON_SOURCE.read_text(encoding="utf-8")), version)
        self.assertEqual((chat_channel.SERVER_VERSION, unity_bridge.SERVER_VERSION), (version, version))

    def test_old_transfer_files_leave_the_exchange_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "exchange"
            (folder / "sub").mkdir(parents=True)
            now = 1_800_000_000.0
            old, new = now - 40 * 86400, now - 2 * 86400
            for name, when in (("rock_1.unitypackage", old), ("rock_1.claudehand.json", old), ("hero_1.fbx", old), ("set_1.blend", old),
                               ("capture-20260101-000000.png", old), ("notes.txt", old), ("sub/deep.fbx", old), ("hero_2.fbx", new)):
                (folder / name).write_bytes(b"x" * 10)
                os.utime(folder / name, (when, when))
            find = lambda days: sorted(p.name for p in handlib.launch.old_exchange_files(days, folder, now))
            self.assertEqual(find(30), ["capture-20260101-000000.png", "hero_1.fbx", "rock_1.claudehand.json", "rock_1.unitypackage", "set_1.blend"])
            self.assertEqual(find(1), sorted(find(30) + ["hero_2.fbx"]))
            self.assertEqual(find(0), [])  # 0: never cleaned
            self.assertEqual(handlib.launch.clean_exchange(30, folder, now, dry_run=True), (5, 50))
            self.assertTrue((folder / "hero_1.fbx").exists())
            self.assertEqual(handlib.launch.clean_exchange(30, folder, now), (5, 50))
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["hero_2.fbx", "notes.txt", "sub"])  # only what claude-hand wrote
            self.assertTrue((folder / "sub" / "deep.fbx").exists())
            self.assertEqual(handlib.launch.clean_exchange(30, Path(tmp) / "gone", now), (0, 0))
        self.assertEqual(handlib.launch.exchange_keep_days({}), handlib.launch.EXCHANGE_KEEP_DAYS)
        self.assertEqual(handlib.launch.exchange_keep_days({"exchange_keep_days": 0}), 0)
        self.assertEqual(handlib.launch.exchange_keep_days({"exchange_keep_days": "soon"}), handlib.launch.EXCHANGE_KEEP_DAYS)
        self.assertEqual(handlib.link.exchange_dir({"CLAUDE_HAND_HOME": "/h", "CLAUDE_HAND_EXCHANGE_DIR": "/x"}), Path("/x"))
        self.assertEqual(handlib.link.exchange_dir({"CLAUDE_HAND_HOME": "/h"}), Path("/h") / "exchange")

    def test_unity_install_detects_asset_store_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_unity_project(Path(tmp))
            (project / "Assets" / "MCPForUnity").mkdir()
            plan, manifest = handlib.install.plan_unity_install(project, package_dir=Path(tmp) / "pkg", source="folder")
            self.assertNotIn(handlib.constants.MCP_FOR_UNITY_PACKAGE, manifest["dependencies"])
            self.assertTrue(any("Asset Store" in n for n in plan.notes))

    def test_unity_install_rejects_non_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                handlib.install.plan_unity_install(Path(tmp))

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
            self.assertEqual(handlib.install.read_project_version(project), "6000.3.2f1")
            hub = Path(tmp) / "Hub" / "Editor"
            exe = hub / "6000.3.2f1" / "Editor" / "Unity.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
            self.assertEqual(handlib.install.find_unity_editor("6000.3.2f1", [hub], platform="win32"), exe)
            self.assertIsNone(handlib.install.find_unity_editor("2022.3.1f1", [hub], platform="win32"))

    def test_blender_lookup_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            pf = Path(tmp) / "Program Files"
            for version in ("4.2", "5.0"):
                exe = pf / "Blender Foundation" / f"Blender {version}" / "blender.exe"
                exe.parent.mkdir(parents=True)
                exe.write_text("", encoding="utf-8")
            found = handlib.install.find_blender_executables(env={"ProgramFiles": str(pf)}, platform="win32", which=lambda _: None)
            self.assertEqual([p.parent.name for p in found], ["Blender 5.0", "Blender 4.2"])

    def test_blender_addon_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "Blender"
            legacy = base / "4.1" / "scripts" / "addons" / handlib.constants.BLENDER_ADDON_FILE
            extension = base / "4.2" / "extensions" / "user_default" / handlib.constants.BLENDER_ADDON_FILE
            for f in (legacy, extension):
                f.parent.mkdir(parents=True)
                f.write_text("", encoding="utf-8")
            (base / "config").mkdir()
            self.assertEqual(handlib.install.find_blender_addon_installs(base), [extension, legacy])

    def test_blender_open_command(self):
        cmd = handlib.install.blender_open_command(Path("C:/B/blender.exe"), Path("C:/Art/props.blend"), 9877)
        self.assertEqual(cmd[:3], [str(Path("C:/B/blender.exe")), str(Path("C:/Art/props.blend")), "--python-expr"])
        ast.parse(cmd[3])
        self.assertIn("blendermcp_port = 9877", cmd[3])
        self.assertEqual(handlib.install.blender_open_command(Path("blender"), None, 9876)[1], "--python-expr")

    def test_node_major(self):
        self.assertEqual(handlib.install.node_major("v22.3.0"), 22)
        self.assertIsNone(handlib.install.node_major(None))

    def test_doctor_reports_gaps_with_fixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            project = make_unity_project(Path(tmp))
            versions = {"claude": "2.1.0", "uv": "uv 0.9", "git": None, "node": "v16.0.0", "agent-desktop": None}
            with unittest.mock.patch.dict("os.environ", {"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home)}), \
                 unittest.mock.patch.object(Path, "home", lambda: home), \
                 unittest.mock.patch.object(handlib.discovery, "candidate_config_files", lambda cwd=None: []), \
                 unittest.mock.patch.object(handlib.discovery, "discover_blender", lambda ports, request=None: []), \
                 unittest.mock.patch.object(handlib.install, "find_blender_executables", lambda **kw: []), \
                 unittest.mock.patch.object(handlib.chatsetup, "chat_server_registered", lambda **kw: False), \
                 unittest.mock.patch.object(handlib.chatsetup, "plan_chat_config", lambda root, runner="", direct_servers=None: {root / ".mcp.json": {}}):
                checks = handlib.checks.doctor_checks([project], [9876], version=lambda *cmd: versions[cmd[0]])
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
        cmd = handlib.chatsetup.chat_add_command("uv run -q --no-project")
        self.assertEqual(cmd[:7], ["claude", "mcp", "add", "--scope", "local", handlib.constants.CHAT_SERVER_NAME, "--"])
        self.assertEqual(cmd[7:], ["uv", "run", "-q", "--no-project", str(handlib.constants.CHAT_CHANNEL_SCRIPT)])
        self.assertTrue(handlib.constants.CHAT_CHANNEL_SCRIPT.exists() and handlib.constants.CHAT_ADDON_SOURCE.exists())
        self.assertIn(f"server:{handlib.constants.CHAT_SERVER_NAME}", handlib.constants.SUPERVISOR_COMMAND)

    def test_supervisor_scripts_use_the_same_command(self):
        for name in ("start-supervisor.cmd", "start-supervisor.sh"):
            script = (handlib.constants.REPO_ROOT / name).read_text(encoding="utf-8")
            self.assertIn(handlib.constants.SUPERVISOR_COMMAND, script)
            self.assertIn("CLAUDE_HAND_SUPERVISOR=1", script)
            self.assertIn("MCP_TIMEOUT", script)

    def test_chat_config_is_merged_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
            (root / ".claude" / "settings.local.json").write_text(
                json.dumps({"permissions": {"allow": ["Bash(git status)"]}, "model": "keep-me"}), encoding="utf-8")
            plan = handlib.chatsetup.plan_chat_config(root, "uv run -q --no-project", direct_servers=[])
            self.assertEqual(sorted(p.name for p in plan), [".mcp.json", "settings.local.json"])
            handlib.settings.write_json_files(plan)
            mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(mcp["mcpServers"]["other"], {"command": "x"})
            entry = mcp["mcpServers"][handlib.constants.CHAT_SERVER_NAME]
            self.assertEqual(entry["command"], "uv")
            self.assertEqual(entry["env"], {"CLAUDE_HAND_SUPERVISOR": "${CLAUDE_HAND_SUPERVISOR:-0}"})
            self.assertEqual(entry["args"][:3], ["run", "-q", "--no-project"])
            self.assertTrue(entry["args"][-1].endswith("/tools/chat_channel.py") and Path(entry["args"][-1]).is_absolute())
            settings = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["permissions"]["allow"],
                             ["Bash(git status)", handlib.constants.CHAT_REPLY_RULE, handlib.constants.CHAT_CONTEXT_RULE])
            self.assertEqual(settings["enabledMcpjsonServers"], [handlib.constants.CHAT_SERVER_NAME])
            self.assertEqual(settings["model"], "keep-me")
            self.assertEqual(handlib.chatsetup.plan_chat_config(root, "uv run -q --no-project", direct_servers=[]), {})  # second run changes nothing

    def test_chat_config_refuses_broken_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".mcp.json").write_text("{oops", encoding="utf-8")
            with self.assertRaises(SystemExit):
                handlib.chatsetup.plan_chat_config(Path(tmp))

    def test_tool_allow_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handlib.settings.write_json_files(handlib.settings.plan_tool_allow(root, ["unity_mygame", "blender_props"]))
            path = root / ".claude" / "settings.local.json"
            rules = json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"]
            self.assertEqual(rules, ["mcp__unity_mygame__*", "mcp__blender_props__*"])
            self.assertEqual(handlib.settings.plan_tool_allow(root, ["unity_mygame", "blender_props"]), {})
            handlib.settings.write_json_files(handlib.settings.plan_tool_allow(root, ["unity_mygame"], prune=True))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"], ["mcp__unity_mygame__*"])

    def test_notes_allow_rules(self):
        # Windows paths as Claude Code matches them (checked with claude -p 2.1.319: //c/... allows Read, Edit and Write).
        self.assertEqual(handlib.settings.permission_path("C:\\Projects\\MyGame/ClaudeHand/NOTES.md"), "//c/Projects/MyGame/ClaudeHand/NOTES.md")
        self.assertEqual(handlib.settings.permission_path("/home/me/art/a.claude-hand.md"), "//home/me/art/a.claude-hand.md")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = [handlib.agents.unity_notes_path("C:/Projects/MyGame"), handlib.agents.blender_notes_path(""), handlib.agents.blender_notes_path("D:/Art/prop.blend")]
            handlib.settings.write_json_files(handlib.settings.plan_notes_allow(root, notes))
            rules = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))["permissions"]["allow"]
            self.assertEqual(rules, ["Read(//c/Projects/MyGame/ClaudeHand/NOTES.md)", "Edit(//c/Projects/MyGame/ClaudeHand/NOTES.md)",
                                     "Read(//d/Art/prop.claude-hand.md)", "Edit(//d/Art/prop.claude-hand.md)"])
            self.assertEqual(handlib.settings.plan_notes_allow(root, notes), {})  # unsaved .blend: no rule; second run changes nothing

    def test_sync_writes_notes_rules_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, agents = Path(tmp) / "repo", Path(tmp) / "agents"
            server = handlib.discovery.ServerConfig(transport="stdio")
            game = handlib.discovery.Instance(name="MyGame", hash="aaaa1111", path="C:/Projects/MyGame")
            handlib.agents.sync_agents([game], agents, server, "uv run")
            self.assertFalse((root / ".claude" / "settings.local.json").exists())
            handlib.agents.sync_agents([game], agents, server, "uv run", notes_root=root)
            text = (root / ".claude" / "settings.local.json").read_text(encoding="utf-8")
            self.assertIn("Edit(//c/Projects/MyGame/ClaudeHand/NOTES.md)", text)

    def test_prewarm_reads_the_check_result(self):
        class Done:
            stdout = 'Installed Python 3.12\n{"ok": true, "python": "3.12.1", "port": 8765}\n'
            stderr = ""
        seen = {}
        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return Done()
        result = handlib.chatsetup.prewarm_chat_channel(Path("/repo"), "uv run -q --no-project", run=fake_run)
        self.assertTrue(result["ok"])
        self.assertEqual(seen["cmd"][-1], "--check")
        self.assertTrue(seen["cmd"][-2].endswith("chat_channel.py"))

    def test_claude_version(self):
        self.assertEqual(handlib.chatsetup.parse_claude_version("2.1.319 (Claude Code)"), (2, 1, 319))
        self.assertLess(handlib.chatsetup.parse_claude_version("2.1.99"), handlib.constants.MIN_CLAUDE_FOR_PERMISSION_RELAY)
        self.assertIsNone(handlib.chatsetup.parse_claude_version(None))

    def test_registration_check(self):
        class Done:
            def __init__(self, code):
                self.returncode = code
                self.stdout = "claude-hand-chat:\n  Scope: Project config\n" if code == 0 else 'No MCP server named "x"'
                self.stderr = ""
        self.assertTrue(handlib.chatsetup.chat_server_registered(run=lambda *a, **k: Done(0), which=lambda _: "claude"))
        self.assertFalse(handlib.chatsetup.chat_server_registered(run=lambda *a, **k: Done(1), which=lambda _: "claude"))
        self.assertIsNone(handlib.chatsetup.chat_server_registered(which=lambda _: None))

    def test_chat_addon_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "Blender"
            for version in ("4.2", "4.10", "3.6"):
                (base / version / "scripts" / "addons").mkdir(parents=True)
            self.assertEqual(handlib.install.blender_chat_targets(base), [base / "4.10" / "scripts" / "addons"])  # newest, numerically
            mcp = base / "4.2" / "scripts" / "addons" / handlib.constants.BLENDER_ADDON_FILE
            mcp.write_text("", encoding="utf-8")
            self.assertEqual(handlib.install.blender_chat_targets(base), [mcp.parent])  # next to MCP for Blender
            installed = mcp.parent / (handlib.constants.CHAT_ADDON_MODULE + ".py")
            installed.write_text("", encoding="utf-8")
            self.assertEqual(handlib.install.find_chat_addon_installs(base), [installed])

    def test_install_chat_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with unittest.mock.patch("sys.stdout", out), \
                 unittest.mock.patch.object(handlib.install, "find_blender_executables", lambda **kw: [Path("blender")]):
                ch.main(["blender-install-chat", "--addons-dir", tmp, "--dry-run"])
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertIn("--background", out.getvalue())

    def test_enable_script_is_valid_python(self):
        ast.parse(handlib.install.ENABLE_CHAT_ADDON)
        self.assertIn(f"addon_utils.enable('{handlib.constants.CHAT_ADDON_MODULE}'", handlib.install.ENABLE_CHAT_ADDON)



class SelftestTests(unittest.TestCase):
    def test_reads_unittest_output(self):
        self.assertEqual(handlib.checks.unittest_summary("....\n------\nRan 187 tests in 16.4s\n\nOK (skipped=24)\n"),
                         (True, "187개 통과 (건너뜀 24)"))
        failed = "FAIL: test_a (m.C.test_a)\nERROR: test_b (m.C.test_b)\nRan 9 tests in 1s\n\nFAILED (failures=1, errors=1)\n"
        self.assertEqual(handlib.checks.unittest_summary(failed), (False, "실패: test_a, test_b"))
        self.assertEqual(handlib.checks.unittest_summary("Traceback ...\nModuleNotFoundError: No module named 'bpy'\n"),
                         (False, "끝까지 돌지 못함: ModuleNotFoundError: No module named 'bpy'"))

    def test_knows_this_checkouts_versions(self):
        self.assertTrue({"ch_capture", "ch_checkpoint", "ch_editor_status", "ch_scene_snapshot"} <= set(handlib.checks.repo_unity_tools()))
        self.assertRegex(handlib.checks.addon_version(handlib.constants.CHAT_ADDON_SOURCE.read_text(encoding="utf-8")), r"^\d+\.\d+\.\d+$")

    def test_checks_the_open_windows_without_changing_them(self):
        import unity_bridge
        wanted = handlib.checks.repo_unity_tools()
        live = {"name": "MyGame", "hash": "aaaa1111", "_mtime": 2, "tools": [{"name": t} for t in wanted if t != "ch_capture"]}
        gone = {"name": "Old", "hash": "bbbb2222", "_mtime": 1, "tools": []}
        calls = []

        def unity(entry, tool, params=None, timeout=0):
            calls.append((entry["name"], tool))
            if entry is gone:
                raise unity_bridge.BridgeError("UNREACHABLE", "closed")
            if tool == "ch_capture":
                Path(params["path"]).write_bytes(b"png")
            return {"success": True}
        blender = lambda port, command, timeout: {"status": "success", "result": {"result": json.dumps({"enabled": True, "version": [0, 1, 0]})}}
        checks = handlib.checks.selftest_checks([9876], run_tests=False, registry=[gone, live], unity_request=unity, blender=blender,
                                    blenders=[handlib.discovery.BlenderInstance(port=9876, filepath="C:/Art/props.blend")],
                                    supervisor_on=False, stale=[], hooks=[])
        by_item = {c.item: c for c in checks}
        self.assertFalse(by_item["MyGame: Claude Hand Tools"].ok)  # compiled before ch_capture existed
        self.assertIn("ch_capture", by_item["MyGame: Claude Hand Tools"].detail)
        self.assertTrue(by_item["MyGame: 결과 미리보기 찍기"].ok)
        self.assertNotIn(("Old", "ch_capture"), calls)  # a closed editor is skipped, not reported
        self.assertFalse(by_item["props@9876: 채팅 애드온"].ok)
        self.assertIn("저장소", by_item["props@9876: 채팅 애드온"].detail)
        self.assertIsNone(by_item["감독 세션"].ok)  # Claude Hand off is not a failure
        self.assertTrue(by_item["하위 세션 정의·감독 스킬·Blender 애드온 파일"].ok)
        self.assertEqual({tool for _, tool in calls} - {"__ping__", "ch_capture"}, set())  # nothing that changes a scene
        current = {**live, "tools": [{"name": t} for t in wanted], "version": handlib.constants.VERSION}
        older = {**current, "name": "Older", "hash": "cccc3333", "version": "0.0.1"}
        checks = handlib.checks.selftest_checks([], run_tests=False, registry=[current, older], unity_request=unity, blenders=[],
                                                supervisor_on=False, stale=[], hooks=[])
        by_item = {c.item: c for c in checks}
        self.assertTrue(by_item["MyGame: Claude Hand Tools"].ok)
        self.assertIn(handlib.constants.VERSION, by_item["MyGame: Claude Hand Tools"].detail)
        self.assertFalse(by_item["Older: Claude Hand Tools"].ok)  # an editor that has not compiled this checkout's package yet
        self.assertIn("0.0.1", by_item["Older: Claude Hand Tools"].detail)

    def test_runs_each_subagents_guard_hook_as_claude_code_does(self):
        class Done:
            def __init__(self, out="", code=0, err=""):
                self.stdout, self.stderr, self.returncode = out, err, code
        deny = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                   "permissionDecisionReason": "x"}})
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            handlib.agents.sync_agents([handlib.discovery.Instance(name="MyGame", hash="aaaa1111"), handlib.discovery.Instance(name="Props", hash="bbbb2222")], d,
                           handlib.discovery.ServerConfig(transport="stdio"), "python3")
            (d / "mine.md").write_text("---\nname: mine\n---\nhand-written\n", encoding="utf-8")
            ran = []

            def run(cmd, input=None, **kwargs):
                ran.append((cmd, json.loads(input)))
                if "unity_props" in cmd[-1]:
                    return Done(code=127, err="bash: uv: command not found")  # the hook never started: the call would go through
                return Done(deny)
            checks = {c.item: c for c in handlib.checks.hook_checks(d, run=run, shell=["sh", "-c"])}
            unknown = handlib.checks.hook_checks(d, run=run, shell=[])  # Windows without Git Bash: cannot tell
        self.assertEqual(sorted(checks), ["unity-mygame: 안전장치 훅", "unity-props: 안전장치 훅"])  # generated files only
        self.assertTrue(checks["unity-mygame: 안전장치 훅"].ok)
        self.assertFalse(checks["unity-props: 안전장치 훅"].ok)
        self.assertIn("127", checks["unity-props: 안전장치 훅"].detail)
        self.assertIn("command not found", checks["unity-props: 안전장치 훅"].detail)
        cmd, event = ran[0]
        self.assertEqual(cmd[:2], ["sh", "-c"])
        self.assertEqual(cmd[2], handlib.agents.hook_command("python3", "MyGame@aaaa1111", "unity_mygame", "stdio"))  # the installed command as written
        self.assertEqual(event["tool_name"], "mcp__claude_hand_selftest__probe")  # refused before anything is reported or marked
        self.assertEqual([c.ok for c in unknown], [None, None])

    def test_runs_the_unity_selftest_in_a_closed_project(self):
        """selftest --unity-project: Blender exports two versions of a model, Unity runs ChSelfTest without a window,
        Blender reads what Unity sent back. Here both programs are stand-ins."""
        class Done:
            def __init__(self, out="", code=0):
                self.stdout, self.stderr, self.returncode = out, "", code
        ast.parse(handlib.checks.MODEL_FIXTURES_EXPR.format(addon="a.py", folder="tmp"))
        ast.parse(handlib.checks.MODEL_TAKE_EXPR.format(addon="a.py", manifest="m.json"))
        self.assertTrue({"ch_import_model", "ch_export_model"} <= set(handlib.checks.repo_unity_tools()))
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "MyGame"
            (project / "Assets").mkdir(parents=True)
            self.assertFalse(handlib.checks.unity_batch_checks(project, editor=Path("Unity.exe"), blender_exe=False)[0].ok)  # not a project
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.25f1\n", encoding="utf-8")
            ran = []

            def run(cmd, env=None, **kwargs):
                ran.append(cmd)
                if cmd[0] == "blender.exe":
                    if "export_fbx" in cmd[-1]:
                        return Done('Blender 5.2\nFIXTURES ["C:/x/a.claudehand.json", "C:/x/b.claudehand.json"]\nBlender quit\n')
                    return Done('TOOK {"added": ["Rock"], "file": "C:/x/out.fbx"}\n')
                self.assertEqual((env["CLAUDE_HAND_SELFTEST_MODEL_A"], env["CLAUDE_HAND_SELFTEST_MODEL_B"]),
                                 ("C:/x/a.claudehand.json", "C:/x/b.claudehand.json"))
                self.assertTrue(Path(env["CLAUDE_HAND_EXCHANGE_DIR"]).is_dir())  # its own exchange folder, not the user's
                Path(env["CLAUDE_HAND_SELFTEST_OUT"]).write_text(json.dumps({"ok": False, "exported_manifest": "C:/x/out.claudehand.json", "checks": [
                    {"name": "new model: imported and placed", "ok": True, "detail": "{...}"},
                    {"name": "same source again: replaced in place", "ok": False, "detail": "a second model appeared"}]}), encoding="utf-8")
                return Done(code=1)
            checks = handlib.checks.unity_batch_checks(project, run=run, editor=Path("Unity.exe"), blender_exe="blender.exe")
            by_item = {c.item: c for c in checks}
            self.assertEqual([cmd[0] for cmd in ran], ["blender.exe", "Unity.exe", "blender.exe"])
            self.assertEqual(ran[1][1:5], ["-batchmode", "-projectPath", str(project), "-executeMethod"])
            self.assertTrue(by_item["Blender가 보낸 모델(FBX)로 시험"].ok)
            self.assertTrue(by_item["new model: imported and placed"].ok)
            self.assertEqual((by_item["same source again: replaced in place"].ok, by_item["same source again: replaced in place"].detail),
                             (False, "a second model appeared"))
            self.assertTrue(by_item["Unity가 보낸 모델을 Blender가 받음 (import_model)"].ok)
            self.assertTrue(all(c.area == "Unity 안 테스트 (MyGame)" for c in checks))

            def broken(cmd, env=None, **kwargs):  # the package does not compile: no result file, the reason is in the log
                Path(cmd[cmd.index("-logFile") + 1]).write_text("Assets/x.cs(3,5): error CS0136: A local variable named 'uses'...\n", encoding="utf-8")
                return Done(code=1)
            failed = handlib.checks.unity_batch_checks(project, run=broken, editor=Path("Unity.exe"), blender_exe=False)
            self.assertEqual([c.ok for c in failed], [False])
            self.assertIn("CS0136", failed[0].detail)

            (project / "Temp").mkdir()
            (project / "Temp" / "UnityLockfile").write_text("", encoding="utf-8")  # open in Unity: cannot be opened a second time
            skipped = handlib.checks.unity_batch_checks(project, run=run, editor=Path("Unity.exe"), blender_exe="blender.exe")
            self.assertEqual([c.ok for c in skipped], [None])
            ran.clear()
            handlib.checks.selftest_checks([], run_tests=False, registry=[], blenders=[], supervisor_on=False, stale=[], hooks=[],
                                           unity_projects=[project], unity_batch=lambda p, progress=None: ran.append(p) or [])
            self.assertEqual(ran, [project])

    def test_ci_reports_a_failure_as_an_annotation(self):
        """tools/tests/ci.py (what GitHub Actions runs): a job's log needs a sign-in, an annotation does not."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ci
        line = ci.annotation(self, "Traceback (most recent call last):\n  File x\nAssertionError: 50% != 'C:\\x'\n")
        self.assertEqual(line, f"::error title={self.id()}::Traceback (most recent call last):%0A  File x%0AAssertionError: 50%25 != 'C:\\x'")
        self.assertEqual(len(line.splitlines()), 1)  # one workflow command
        self.assertLessEqual(len(ci.annotation(self, "x" * 9000)), ci.TRACE_CHARS + 200)
        workflow = (handlib.constants.REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/tests/ci.py", workflow)

    def test_the_guard_refuses_the_selftest_probe_for_both_apps(self):
        import guard
        self.assertIsNotNone(guard.guard_decision(handlib.checks.HOOK_PROBE, "MyGame@aaaa1111", "unity_mygame", "stdio", "unity"))
        self.assertIsNotNone(guard.guard_decision(handlib.checks.HOOK_PROBE, "props@9876", "blender_props", "stdio", "blender"))

    def test_hook_shell(self):
        self.assertEqual(handlib.checks.hook_shell(platform="linux"), ["sh", "-c"])
        with tempfile.TemporaryDirectory() as tmp:
            bash = Path(tmp) / "Git" / "bin" / "bash.exe"
            bash.parent.mkdir(parents=True)
            bash.write_bytes(b"")
            git = Path(tmp) / "Git" / "cmd" / "git.exe"
            git.parent.mkdir()
            git.write_bytes(b"")
            self.assertEqual(handlib.checks.hook_shell({}, "win32", lambda name: str(git)), [str(bash.resolve()), "-c"])
            self.assertEqual(handlib.checks.hook_shell({"CLAUDE_CODE_GIT_BASH_PATH": str(bash)}, "win32", lambda name: None), [str(bash), "-c"])
            self.assertIsNone(handlib.checks.hook_shell({}, "win32", lambda name: None))


class UpdateAndStatusTests(unittest.TestCase):
    class Done:
        def __init__(self, out, code=0):
            self.stdout, self.stderr, self.returncode = out, "", code

    def test_claude_mcp_get_parsing(self):
        local = "claude-hand-chat:\n  Scope: Local config (private to you in this project)\n  Status: ✓ Connected\n"
        pending = "claude-hand-chat:\n  Scope: Project config (shared via .mcp.json)\n  Status: ⏸ Pending approval\n"
        entry = handlib.chatsetup.claude_mcp_get(run=lambda *a, **k: self.Done(local), which=lambda _: "claude")
        self.assertEqual((entry.exists, entry.scope, entry.pending), (True, "local", False))
        entry = handlib.chatsetup.claude_mcp_get(run=lambda *a, **k: self.Done(pending), which=lambda _: "claude")
        self.assertEqual((entry.scope, entry.pending), ("project", True))
        missing = handlib.chatsetup.claude_mcp_get(run=lambda *a, **k: self.Done('No MCP server named "claude-hand-chat".'), which=lambda _: "claude")
        self.assertFalse(missing.exists)
        self.assertIsNone(handlib.chatsetup.claude_mcp_get(which=lambda _: None))

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
        return "\n".join(handlib.chatsetup.chat_status_report(Path(tmp) / "chat", mcp_entry=lambda: mcp_entry, fetch=fetch, root=root))

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
            text = self.report(tmp, mcp_entry=handlib.chatsetup.McpEntry(True, scope="local"), mcp_json=old)
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
        unity = handlib.discovery.Instance(name="My Game", hash="aaaa1111", path="C:/Projects/My Game")
        blender = handlib.discovery.BlenderInstance(port=9877, filepath="C:/Art/props (v2).blend")
        files = handlib.agents.build_agent_files([unity], handlib.discovery.ServerConfig(transport="stdio"), [blender], None, "uv run")
        infos = {name: handlib.agents.read_agent(text) for name, text in files.items()}
        back = {name: info.instance() for name, info in infos.items()}
        self.assertEqual((back["unity-my-game.md"].id, back["unity-my-game.md"].path), (unity.id, unity.path))
        self.assertEqual((back["blender-props-v2.md"].port, back["blender-props-v2.md"].filepath), (9877, blender.filepath))
        self.assertEqual((infos["unity-my-game.md"].name, infos["unity-my-game.md"].app, infos["unity-my-game.md"].server),
                         ("unity-my-game", "unity", "unity_my_game"))
        self.assertEqual((infos["blender-props-v2.md"].app, infos["blender-props-v2.md"].pin), ("blender", "props (v2)@9877"))
        untitled = handlib.agents.build_agent_files([], None, [handlib.discovery.BlenderInstance(port=9878)], None, "uv run")
        self.assertEqual(handlib.agents.read_agent(next(iter(untitled.values()))).instance().filepath, "")
        self.assertIsNone(handlib.agents.read_agent("---\nname: mine\n---\nhand written\n"))  # not sync's: left alone

    def test_what_a_file_is_for_does_not_depend_on_its_wording(self):
        """The chat channel and `update` read one line made for them, not the hook command or the prompt's sentences."""
        unity = handlib.discovery.Instance(name="My Game", hash="aaaa1111", path="C:/Projects/My Game")
        text = handlib.agents.render_agent(unity, "unity-my-game", handlib.discovery.ServerConfig(transport="stdio"), "uv run", True, False)
        front, body = text.split(handlib.constants.GENERATED_MARKER)
        reworded = front.replace("--pin", "--window").replace("--app unity", "") + handlib.constants.GENERATED_MARKER + \
            body.replace("(경로: `C:/Projects/My Game`)", "").replace("Unity 프로젝트 전담", "다른 제목")
        info = handlib.agents.read_agent(reworded)
        self.assertEqual((info.app, info.pin, info.path), ("unity", "My Game@aaaa1111", "C:/Projects/My Game"))

    def test_files_from_before_the_meta_line_are_still_read(self):
        """Until `update` writes them again."""
        unity = handlib.discovery.Instance(name="My Game", hash="aaaa1111", path="C:/Projects/My Game")
        blender = handlib.discovery.BlenderInstance(port=9877, filepath="C:/Art/props.blend")
        files = handlib.agents.build_agent_files([unity], handlib.discovery.ServerConfig(transport="stdio"), [blender], None, "uv run")
        old = {name: "\n".join(line for line in text.splitlines() if not line.startswith(handlib.agents.META_PREFIX))
               for name, text in files.items()}
        self.assertNotEqual(old, files)
        info = handlib.agents.read_agent(old["unity-my-game.md"])
        self.assertEqual((info.app, info.pin, info.path, info.server), ("unity", "My Game@aaaa1111", "C:/Projects/My Game", "unity_my_game"))
        info = handlib.agents.read_agent(old["blender-props.md"])
        self.assertEqual((info.app, info.pin, info.path), ("blender", "props@9877", "C:/Art/props.blend"))

    def test_installed_agents_reads_a_file_again_only_when_it_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            inst = handlib.discovery.Instance(name="MyGame", hash="aaaa1111")
            handlib.agents.sync_agents([inst], d, handlib.discovery.ServerConfig(transport="stdio"), "uv run")
            (d / "mine.md").write_text("---\nname: mine\n---\nhand written\n", encoding="utf-8")
            first = handlib.agents.installed_agents(d)
            self.assertEqual([(a.name, a.pin, a.file.name) for a in first], [("unity-mygame", "MyGame@aaaa1111", "unity-mygame.md")])
            with unittest.mock.patch.object(handlib.agents, "read_agent", lambda text: 1 / 0):  # nothing changed: nothing read
                self.assertEqual(handlib.agents.installed_agents(d), first)
            handlib.agents.sync_agents([inst, handlib.discovery.Instance(name="Props", hash="bbbb2222")], d,
                                       handlib.discovery.ServerConfig(transport="stdio"), "uv run")
            self.assertEqual([a.name for a in handlib.agents.installed_agents(d)], ["unity-mygame", "unity-props"])
            (d / "unity-props.md").unlink()
            self.assertEqual([a.name for a in handlib.agents.installed_agents(d)], ["unity-mygame"])
        self.assertEqual(handlib.agents.installed_agents(Path(tmp) / "gone"), [])

    def test_stale_checks_and_update_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents, skills = Path(tmp) / "agents", Path(tmp) / "skills"
            inst = handlib.discovery.Instance(name="MyGame", hash="aaaa1111")
            handlib.agents.write_agent_files(handlib.agents.build_agent_files([inst], handlib.discovery.ServerConfig(transport="stdio"), [], None, "uv run -q --no-project"), agents)
            agent_file = agents / "unity-mygame.md"
            agent_file.write_text(agent_file.read_text(encoding="utf-8").replace("## 작업 순서", "## old"), encoding="utf-8")
            (skills / "claude-hand").mkdir(parents=True)
            (skills / "claude-hand" / "SKILL.md").write_text("old skill", encoding="utf-8")
            (skills / "claude-hand-unity").mkdir()
            (skills / "claude-hand-unity" / "SKILL.md").write_text("---\nname: claude-hand-unity\n---\n", encoding="utf-8")
            with unittest.mock.patch.object(handlib.discovery, "candidate_config_files", lambda cwd=None: []), \
                 unittest.mock.patch.object(handlib.install, "find_chat_addon_installs", lambda base=None: []):
                items = {c.item for c in handlib.checks.stale_checks(agents, skills)}
                self.assertIn("설치된 감독 스킬이 저장소 버전과 다름", items)
                self.assertIn("이전 버전으로 만든 하위 세션 정의", items)
                out = io.StringIO()
                with unittest.mock.patch("sys.stdout", out), \
                     unittest.mock.patch.object(handlib.chatsetup, "claude_mcp_get", lambda **kw: None), \
                     unittest.mock.patch.object(handlib.link, "read_chat_files", lambda home=None: (None, None)), \
                     unittest.mock.patch.object(handlib.discovery, "discover_stdio", lambda *a, **k: []), \
                     unittest.mock.patch.object(handlib.discovery, "discover_blender", lambda *a, **k: []), \
                     unittest.mock.patch.object(handlib.constants, "REPO_ROOT", Path(tmp) / "repo"):
                    ch.main(["update", "--agents-dir", str(agents), "--skills-dir", str(skills)])
                self.assertEqual(handlib.checks.stale_checks(agents, skills), [])
            self.assertFalse((skills / "claude-hand-unity").exists())
            self.assertIn("## 작업 순서", agent_file.read_text(encoding="utf-8"))



class EverydayStartTests(unittest.TestCase):
    def test_start_scripts(self):
        cmd = (handlib.constants.REPO_ROOT / "start-supervisor.cmd").read_bytes()
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
        sh = (handlib.constants.REPO_ROOT / "start-supervisor.sh").read_text(encoding="utf-8")
        self.assertLess(sh.index("preflight"), sh.index("claude --dangerously"))

    def test_user_config_records_the_start_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "config.json").write_text(json.dumps({"keep": 1}), encoding="utf-8")
            handlib.launch.write_user_config(home, root=Path("/repo"), start_script=Path("/repo/start-supervisor.cmd"))
            config = json.loads((home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config, {"keep": 1, "repo": "/repo", "start_script": "/repo/start-supervisor.cmd"})

    def test_supervisor_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertFalse(handlib.link.supervisor_running(home))
            (home / "chat").mkdir()
            (home / "chat" / "server.json").write_text(json.dumps({"port": 1, "token": "t"}), encoding="utf-8")
            self.assertTrue(handlib.link.supervisor_running(home, fetch=lambda req: {"ok": True}))
            def down(req):
                raise OSError("refused")
            self.assertFalse(handlib.link.supervisor_running(home, fetch=down))

    def run_preflight(self, tmp, running=False):
        home = Path(tmp) / "home"
        out = io.StringIO()
        refreshed = []
        with unittest.mock.patch.dict("os.environ", {"CLAUDE_HAND_HOME": str(home)}), \
             unittest.mock.patch.object(Path, "home", lambda: Path(tmp)), \
             unittest.mock.patch.object(handlib.link, "supervisor_running", lambda home=None, fetch=None: running), \
             unittest.mock.patch.object(handlib.chatsetup, "plan_chat_config", lambda root=None, runner=None, direct_servers=None: {}), \
             unittest.mock.patch.object(handlib.agents, "refresh_agents", lambda **kw: refreshed.append(kw) or handlib.agents.SyncResult()), \
             unittest.mock.patch.object(handlib.constants, "LAUNCH_ARGS_FILE", Path(tmp) / ".launch-args"), \
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
        self.assertEqual(handlib.launch.supervisor_session({}, transcript=used, latest=lambda: c, new_id=made), (["--resume", c], c, True))
        # nothing earlier: a new conversation with an id chosen now
        self.assertEqual(handlib.launch.supervisor_session({}, transcript=used, latest=lambda: None, new_id=made), (["--session-id", d], d, False))
        # the remembered one has a transcript: open it again
        self.assertEqual(handlib.launch.supervisor_session({"supervisor_session": b}, transcript=used, latest=lambda: c, new_id=made),
                         (["--resume", b], b, True))
        # remembered but never used (new-session, or closed before any message): start that one, not an older one
        self.assertEqual(handlib.launch.supervisor_session({"supervisor_session": a}, transcript=used, latest=lambda: c, new_id=made),
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
            self.assertEqual(handlib.launch.latest_supervisor_session(root, config), supervisor.stem)
            self.assertEqual(handlib.launch.session_transcript(supervisor.stem, config), supervisor)
            self.assertIsNone(handlib.launch.session_transcript("cccccccc-1111-1111-1111-111111111111", config))
            self.assertIsNone(handlib.launch.session_transcript("../../etc", config))

    def test_new_session_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            out = io.StringIO()
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_HAND_HOME": str(home)}), \
                 unittest.mock.patch.object(handlib.link, "supervisor_running", lambda home=None, fetch=None: False), \
                 unittest.mock.patch("sys.stdout", out):
                ch.main(["new-session"])
            session = handlib.launch.read_user_config(home)["supervisor_session"]
            self.assertRegex(session, r"^[0-9a-f-]{36}$")
            self.assertEqual(handlib.launch.supervisor_session({"supervisor_session": session}, transcript=lambda s: None)[0],
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
        mac = handlib.launch.shortcut_files("darwin", home)
        linux = handlib.launch.shortcut_files("linux", home)
        self.assertEqual(list(mac), [home / "Desktop" / "Claude Hand.command"])
        self.assertIn(handlib.constants.START_SCRIPT.as_posix(), next(iter(mac.values())))
        self.assertIn("Terminal=true", next(iter(linux.values())))

    @unittest.skipIf(sys.platform == "win32", "Windows shortcuts are .lnk files made by PowerShell")
    def test_shortcut_command_creates_and_removes(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(Path, "home", lambda: Path(tmp)), \
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
        self.assertEqual(handlib.launch.launch_args({}, env={}), on)  # on by default
        self.assertEqual(handlib.launch.launch_args({"remote_control": True}, env={}), on)
        self.assertEqual(handlib.launch.launch_args({"remote_control": False}, env={}), "")
        self.assertEqual(handlib.launch.launch_args({}, env={"ANTHROPIC_API_KEY": "x"}), "")  # cannot work with this login: left out

    def test_problems(self):
        self.assertEqual(handlib.launch.remote_control_problems({}), [])
        self.assertEqual(len(handlib.launch.remote_control_problems({"ANTHROPIC_API_KEY": "x", "CLAUDE_CODE_USE_BEDROCK": "1"})), 2)
        self.assertIn("ANTHROPIC_BASE_URL", handlib.launch.remote_control_problems({"ANTHROPIC_BASE_URL": "http://proxy"})[0])
        # the Claude desktop app puts the official address in its sessions' environment
        self.assertEqual(handlib.launch.remote_control_problems({"ANTHROPIC_BASE_URL": "https://api.anthropic.com/"}), [])

    def test_on_off_and_preflight_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clean = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE_CODE_USE"))}
            with unittest.mock.patch.dict("os.environ", {**clean, "CLAUDE_HAND_HOME": str(home)}, clear=True), \
                 unittest.mock.patch.object(handlib.link, "supervisor_running", lambda home=None, fetch=None: False), \
                 unittest.mock.patch("sys.stdout", io.StringIO()):
                ch.main(["remote-control", "on"])
                self.assertTrue(handlib.launch.read_user_config(home)["remote_control"])
                self.assertIn("start_script", handlib.launch.read_user_config(home))
                runner = EverydayStartTests()
                _, text, _, _ = runner.run_preflight(tmp)
                session = handlib.launch.read_user_config(home)["supervisor_session"]  # no earlier Claude Hand conversation here
                self.assertEqual((Path(tmp) / ".launch-args").read_text(encoding="utf-8").strip(),
                                 f"--session-id {session} " + handlib.launch.launch_args({"remote_control": True}))
                ch.main(["remote-control", "off"])
                self.assertFalse(handlib.launch.read_user_config(home)["remote_control"])
        self.assertIn("Code 탭", text)

    def test_start_scripts_pass_the_launch_args(self):
        cmd = (handlib.constants.REPO_ROOT / "start-supervisor.cmd").read_text(encoding="utf-8")
        self.assertIn('set /p CH_ARGS=<".launch-args"', cmd)
        self.assertIn("%CH_ARGS% %*", cmd)
        self.assertIn("$CH_ARGS", (handlib.constants.REPO_ROOT / "start-supervisor.sh").read_text(encoding="utf-8"))
        self.assertIn('in (".launch-unset") do set "%%v="', cmd)
        self.assertIn("< .launch-unset", (handlib.constants.REPO_ROOT / "start-supervisor.sh").read_text(encoding="utf-8"))
        self.assertLess(cmd.index(".launch-unset"), cmd.index("claude --dangerously"))

    def test_a_parent_sessions_variables_are_dropped(self):
        """Started from a Claude desktop app session (`start`), the supervisor inherited its session id,
        CLAUDE_CODE_CHILD_SESSION and the app's messaging socket; settings the user made stay."""
        env = {"CLAUDECODE": "1", "CLAUDE_PID": "10600", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_CODE_CHILD_SESSION": "1",
               "CLAUDE_CODE_MESSAGING_SOCKET": r"\\.\pipe\x", "CLAUDE_AGENT_SDK_VERSION": "0.3", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop",
               "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_GIT_BASH_PATH": "C:/Git/bin/bash.exe", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "9",
               "CLAUDE_HAND_HOME": "C:/h", "ANTHROPIC_BASE_URL": "https://api.anthropic.com", "PATH": "C:/bin"}
        dropped = handlib.launch.inherited_session_env(env, persistent={"CLAUDE_CODE_MAX_OUTPUT_TOKENS"})
        self.assertEqual(dropped, ["CLAUDECODE", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT",
                                   "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID"])
        self.assertEqual(handlib.launch.inherited_session_env({"PATH": "C:/bin"}, persistent=set()), [])

    @unittest.skipIf(sys.platform == "win32", "POSIX shell")
    def test_shell_keeps_the_quoted_name_together(self):
        import subprocess
        script = 'CH_ARGS=\'--remote-control --name "Claude Hand"\'; set -- --resume x; eval "set -- $CH_ARGS \\"\\$@\\""; printf "[%s]" "$@"'
        out = subprocess.run(["sh", "-c", script], capture_output=True, text=True).stdout
        self.assertEqual(out, "[--remote-control][--name][Claude Hand][--resume][x]")



class DesktopModeTests(unittest.TestCase):
    def test_installed_skill_knows_where_claude_hand_is(self):
        text = handlib.agents.rendered_skill(Path("/opt/claude-hand"))
        self.assertNotRegex(text, r"\{\{\w+\}\}")
        self.assertIn('uv run "/opt/claude-hand/tools/claude_hand.py" sync', text)
        self.assertIn("{{CLAUDE_HAND}}", handlib.constants.SKILL_SOURCE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            installed = handlib.agents.install_skill(Path(tmp))
            self.assertEqual(installed.read_text(encoding="utf-8"), handlib.agents.rendered_skill())

    def test_old_desktop_mode_is_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "config.json").write_text(json.dumps({"mode": "desktop", "remote_control": False}), encoding="utf-8")
            out = io.StringIO()
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_HAND_HOME": str(home)}), unittest.mock.patch("sys.stdout", out):
                ch.main(["mode", "panels"])
            config = handlib.launch.read_user_config(home)
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
                 unittest.mock.patch.object(handlib.link, "supervisor_running", lambda home=None, fetch=None: running), \
                 unittest.mock.patch.object(handlib.launch, "open_start_window", popen or (lambda: launched.append(1))), \
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
        self.assertEqual(handlib.launch.open_start_window(Path("C:/ch/start-supervisor.cmd"), "win32", popen)[:4], ["cmd.exe", "/c", "start", "Claude Hand"])
        self.assertEqual(handlib.launch.open_start_window(Path("/ch/start-supervisor.sh"), "darwin", popen)[:3], ["open", "-a", "Terminal"])
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
