"""tools/unity_pin/sitecustomize.py: a subagent's MCP for Unity server sees its own Unity editor and no other.

Run: uv run -q --no-project python -m unittest discover -s tools/tests
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
os.environ["CLAUDE_HAND_LANG"] = "ko"  # the words here are the Korean ones, whatever this PC is set to
import handlib.agents  # noqa: E402
import handlib.checks  # noqa: E402
import handlib.constants  # noqa: E402
import handlib.discovery  # noqa: E402

PIN_FILE = handlib.constants.UNITY_PIN_DIR / "sitecustomize.py"
SCENARIOS = Path(__file__).with_name("unity_pin_scenarios.py")

# A server laid out like MCP for Unity's, as far as the pin reaches into it.
FAKE_SERVER = '''
import os
from pathlib import Path


class Found:
    def __init__(self, hash_):
        self.hash, self.id = hash_, "Project@" + hash_


class PortDiscovery:
    @staticmethod
    def get_registry_dir():
        return Path(os.environ["FAKE_REGISTRY"])

    @staticmethod
    def discover_all_unity_instances():
        return [Found("aaaa1111"), Found("bbbb2222")]

    @staticmethod
    def discover_unity_port():
        return 6400  # whichever editor answers

    @staticmethod
    def _try_probe_unity_mcp(port):
        return True  # connects to whatever port it is given
'''
FAKE_CONNECTION = '''
_unity_connection_pool = None


class UnityConnection:
    def connect(self, connect_timeout=None):
        return True

    def disconnect(self):
        pass

    def receive_full_response(self, sock):
        return b"{}"
'''
ASK = ("from transport.legacy.port_discovery import PortDiscovery as D\n"
       "print('sees', [found.hash for found in D.discover_all_unity_instances()])\n"
       "try:\n    print('port', D.discover_unity_port())\nexcept ConnectionError as error:\n    print('port none:', error)\n"
       "print('connects to', [port for port in (6400, 6401) if D._try_probe_unity_mcp(port)])\n")
MINE = "C:/Projects/MyGame/Assets"
MINE_HASH = hashlib.sha1(MINE.encode("utf-8")).hexdigest()[:8]


def load_pin():
    """The module as Python imports it at startup; with no editor named it changes nothing."""
    spec = importlib.util.spec_from_file_location("claude_hand_unity_pin_under_test", PIN_FILE)
    module = importlib.util.module_from_spec(spec)
    with unittest.mock.patch.dict(os.environ):
        os.environ.pop(handlib.constants.UNITY_PIN_ENV, None)
        spec.loader.exec_module(module)
    return module


class PinTests(unittest.TestCase):
    def discovery(self, folder: Path, hashes):
        class Found:
            def __init__(self, hash_):
                self.hash = hash_

        class Discovery:
            probed = []
            get_registry_dir = staticmethod(lambda: folder)
            discover_all_unity_instances = staticmethod(lambda: [Found(h) for h in hashes])
            discover_unity_port = staticmethod(lambda: 6400)
            _try_probe_unity_mcp = staticmethod(lambda port: Discovery.probed.append(port) or True)
        return Discovery

    def test_it_connects_to_no_other_editor_and_not_twice_to_its_own(self):
        """An editor closes its other connections when a new one arrives: a look by socket cuts whoever works there."""
        pin = load_pin()
        with tempfile.TemporaryDirectory() as tmp:
            folder, connected = Path(tmp), [False]
            (folder / "unity-mcp-status-aaaa1111.json").write_text(json.dumps({"unity_port": 6401}))
            (folder / "unity-mcp-status-bbbb2222.json").write_text(json.dumps({"unity_port": 6400}))
            found = self.discovery(folder, [])
            pin.pin_discovery(found, "aaaa1111", lambda: connected[0])
            self.assertEqual((found._try_probe_unity_mcp(6400), found.probed), (False, []))  # the other editor's port: not touched
            self.assertEqual((found._try_probe_unity_mcp(6401), found.probed), (True, [6401]))
            connected[0] = True  # a call is on its way over the one connection: a second would cut it
            self.assertEqual((found._try_probe_unity_mcp(6401), found.probed), (True, [6401]))
            (folder / "unity-mcp-status-aaaa1111.json").unlink()
            self.assertEqual((found._try_probe_unity_mcp(6401), found.probed), (False, [6401]))  # no editor of its own: nothing to look at

    def connection(self, answer, connects=True):
        class Socket:
            def __init__(self):
                self.sent, self.timeouts = [], []

            def gettimeout(self):
                return 300.0

            def settimeout(self, value):
                self.timeouts.append(value)

            def sendall(self, data):
                self.sent.append(data)

        class Connection:
            def __init__(self):
                self.sock, self.port, self.use_framing, self._io_lock, self.made = None, 6401, True, threading.Lock(), 0

            def connect(self, connect_timeout=None):
                self.made += 1
                self.sock = Socket() if connects else None
                return connects

            def disconnect(self):
                self.sock = None

            def receive_full_response(self, sock):
                if isinstance(answer, Exception):
                    raise answer
                return json.dumps(answer).encode("utf-8")
        return Connection

    def test_a_new_connection_is_used_only_once_the_editor_says_it_is_the_one(self):
        """A port is not an identity: an editor that cannot bind its own again moves to the lowest free one."""
        pin = load_pin()
        said = lambda assets: {"status": "success", "result": {"success": True, "data": {"assetsPath": assets, "projectRoot": assets[:-7]}}}
        mine = self.connection(said(MINE))
        pin.pin_connection(mine, MINE_HASH)
        conn = mine()
        self.assertTrue(conn.connect(5))
        frame = conn.sock.sent[0]
        self.assertEqual((struct.unpack(">Q", frame[:8])[0], json.loads(frame[8:])), (len(frame) - 8, {"type": "get_project_info", "params": {}}))
        self.assertEqual(conn.sock.timeouts, [5.0, 300.0])  # waits as long as the caller would, then as it was
        self.assertTrue(conn.connect())
        self.assertEqual((conn.made, len(conn.sock.sent)), (1, 1))  # a socket stays with the editor it reached: asked once
        for answer in (said("C:/Projects/Props/Assets"), {"status": "error", "error": "Unknown command"}, {"status": "success", "result": {}},
                       ConnectionResetError("closed"), ValueError("not json")):
            other = self.connection(answer)
            pin.pin_connection(other, MINE_HASH)
            conn = other()
            with unittest.mock.patch.object(sys, "stderr"):
                self.assertFalse(conn.connect(), answer)
            self.assertIsNone(conn.sock, answer)  # and nothing is sent over it
        old = self.connection({"status": "success", "result": {"projectRoot": MINE[:-7]}})  # a package that answers without "data"
        pin.pin_connection(old, MINE_HASH)
        self.assertTrue(old().connect())
        unreachable = self.connection(said(MINE), connects=False)
        pin.pin_connection(unreachable, MINE_HASH)
        self.assertFalse(unreachable().connect())
        self.assertEqual(pin.project_hash("C:/Users/mbc/Desktop/Unity/hand_test/Assets"), "49ac508c")  # as MCP for Unity names that project

    def test_the_server_sees_its_own_editor_and_no_other(self):
        pin = load_pin()
        with tempfile.TemporaryDirectory() as tmp:
            both = self.discovery(Path(tmp), ["bbbb2222", "aaaa1111"])
            pin.pin_discovery(both, "aaaa1111")
            self.assertEqual([found.hash for found in both.discover_all_unity_instances()], ["aaaa1111"])
            away = self.discovery(Path(tmp), ["bbbb2222"])  # its own editor does not answer: nothing, never the other one
            pin.pin_discovery(away, "aaaa1111")
            self.assertEqual(away.discover_all_unity_instances(), [])

    def test_the_port_is_its_own_editors_or_none(self):
        pin = load_pin()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            found = self.discovery(folder, [])
            pin.pin_discovery(found, "aaaa1111")
            with self.assertRaises(ConnectionError):  # the server's own answer was 6400, whoever listens there
                found.discover_unity_port()
            (folder / "unity-mcp-port-bbbb2222.json").write_text(json.dumps({"unity_port": 6400}))
            (folder / "unity-mcp-status-bbbb2222.json").write_text(json.dumps({"unity_port": 6400}))
            with self.assertRaises(ConnectionError):  # another editor's files are not its own
                found.discover_unity_port()
            (folder / "unity-mcp-port-aaaa1111.json").write_text(json.dumps({"unity_port": 6401}))
            self.assertEqual(found.discover_unity_port(), 6401)
            (folder / "unity-mcp-status-aaaa1111.json").write_text(json.dumps({"unity_port": 6402, "reloading": True}))
            self.assertEqual(found.discover_unity_port(), 6402)  # the status file is the newer word; answering or not
            (folder / "unity-mcp-status-aaaa1111.json").write_text("{half written")
            self.assertEqual(found.discover_unity_port(), 6401)
            (folder / "unity-mcp-port-aaaa1111.json").write_text(json.dumps({"unity_port": True}))
            with self.assertRaises(ConnectionError):
                found.discover_unity_port()


class StartupTests(unittest.TestCase):
    """Python imports the file by itself when the folder is on PYTHONPATH: what the agent files rely on."""

    def run_python(self, code: str, server: str | None = FAKE_SERVER, pin: str | None = "Mine@aaaa1111", check: bool = False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if server is not None:
                package = root / "site" / "transport" / "legacy"
                package.mkdir(parents=True)
                (package.parent / "__init__.py").write_text("")
                (package / "__init__.py").write_text("")
                (package / "port_discovery.py").write_text(server)
                (package / "unity_connection.py").write_text(FAKE_CONNECTION)
            (root / "registry").mkdir()
            (root / "registry" / "unity-mcp-port-aaaa1111.json").write_text(json.dumps({"unity_port": 6401}))
            env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "CLAUDE_HAND_UNITY_PIN"))}
            env.update(handlib.agents.pinned_env({"PYTHONPATH": str(root / "site")}, pin or ""), FAKE_REGISTRY=str(root / "registry"))
            if pin is None:
                del env[handlib.constants.UNITY_PIN_ENV]
            if check:
                env[handlib.constants.UNITY_PIN_CHECK_ENV] = "1"
            return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60)

    def test_a_started_server_is_pinned(self):
        done = self.run_python(ASK)
        self.assertEqual((done.returncode, done.stdout.split("\n")[:3], done.stderr), (0, ["sees ['aaaa1111']", "port 6401", "connects to [6401]"], ""))

    def test_nothing_changes_without_an_editor_to_pin_to(self):
        done = self.run_python(ASK, pin=None)
        self.assertEqual(done.stdout.split("\n")[:3], ["sees ['aaaa1111', 'bbbb2222']", "port 6400", "connects to [6400, 6401]"])

    def test_doctor_asks_and_the_server_does_not_start(self):
        done = self.run_python("print('the server started')", check=True)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, handlib.constants.UNITY_PIN_OK))

    def test_a_server_laid_out_differently_runs_as_before_and_says_so(self):
        other = "class PortDiscovery:\n    discover_all_unity_instances = staticmethod(lambda: ['whatever'])\n"
        done = self.run_python("from transport.legacy.port_discovery import PortDiscovery as D; print(D.discover_all_unity_instances())", server=other)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "['whatever']"))
        self.assertIn("is not kept to its editor (aaaa1111): failed (AttributeError", done.stderr)
        asked = self.run_python("print('the server started')", server=other, check=True)
        self.assertEqual(asked.returncode, 1)
        self.assertIn("claude-hand unity pin failed (AttributeError", asked.stdout)
        self.assertNotIn("the server started", asked.stdout)

    def test_another_python_is_left_alone(self):
        """uv's own helper processes inherit the variables: they must neither be stopped nor made to complain."""
        for check in (False, True):
            done = self.run_python("print('ran')", server=None, check=check)
            self.assertEqual((done.returncode, done.stdout.strip(), done.stderr), (0, "ran", ""))


class AgentFileTests(unittest.TestCase):
    def test_pinned_env(self):
        folder = handlib.constants.UNITY_PIN_DIR.as_posix()
        self.assertTrue(PIN_FILE.is_file())
        self.assertEqual(handlib.agents.pinned_env({}, "MyGame@aaaa1111"), {"PYTHONPATH": folder, "CLAUDE_HAND_UNITY_PIN": "MyGame@aaaa1111"})
        mine = {"PYTHONPATH": f"C:/libs;{folder};D:/more", "KEEP": "1"}
        self.assertEqual(handlib.agents.pinned_env(mine, "A@1", ";"), {"PYTHONPATH": f"{folder};C:/libs;D:/more", "KEEP": "1", "CLAUDE_HAND_UNITY_PIN": "A@1"})
        self.assertEqual(mine["PYTHONPATH"], f"C:/libs;{folder};D:/more")  # the registered server's own settings are not touched

    def test_only_a_stdio_server_gets_it(self):
        inst = handlib.discovery.Instance(name="MyGame", hash="aaaa1111", path="C:/Projects/MyGame")
        stdio = handlib.agents.render_agent(inst, "unity-mygame", handlib.discovery.ServerConfig(transport="stdio"), "uv run", allowlist=True, focus_nudge=True)
        front = stdio.split("---")[1]
        self.assertIn(f'      env:\n        CLAUDE_HAND_UNITY_PIN: "MyGame@aaaa1111"\n        PYTHONPATH: "{handlib.constants.UNITY_PIN_DIR.as_posix()}"\n', front)
        http = handlib.agents.render_agent(inst, "unity-mygame", handlib.discovery.ServerConfig(transport="http", url="http://localhost:8080/mcp"),
                                           "uv run", allowlist=True, focus_nudge=True)
        self.assertNotIn("CLAUDE_HAND_UNITY_PIN", http)


class DoctorTests(unittest.TestCase):
    CFG = handlib.discovery.ServerConfig(transport="stdio", command="uvx", origin="test", env={"KEEP": "1"},
                                         args=["--offline", "--from", "mcpforunityserver==10.3.0", "mcp-for-unity"])

    def test_doctor_says_whether_the_server_is_pinned(self):
        ran = []

        def run(out, code=0):
            return lambda command, **kwargs: ran.append((command, kwargs)) or unittest.mock.Mock(returncode=code, stdout=out, stderr="")

        self.assertEqual(handlib.checks.unity_server_pinned(self.CFG, run(handlib.constants.UNITY_PIN_OK + "\n"), {"PATH": "x"}), (True, "맡은 창만 봄", ""))
        command, kwargs = ran[-1]
        self.assertEqual(command, ["uvx", "--offline", "--from", "mcpforunityserver==10.3.0", "mcp-for-unity"])  # as a subagent starts it
        env = kwargs["env"]
        self.assertEqual((env["PATH"], env["KEEP"], env["CLAUDE_HAND_UNITY_PIN_CHECK"], env["UNITY_MCP_SKIP_STARTUP_CONNECT"]), ("x", "1", "1", "1"))
        self.assertEqual(env["PYTHONPATH"], handlib.constants.UNITY_PIN_DIR.as_posix())
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        ok, seen, fix = handlib.checks.unity_server_pinned(self.CFG, run("claude-hand unity pin failed (AttributeError: x)\n", 1), {})
        self.assertEqual((ok, seen), (False, "claude-hand unity pin failed (AttributeError: x)"))
        self.assertIn("Unity 창을 하나만", fix)
        ok, seen, fix = handlib.checks.unity_server_pinned(self.CFG, run("the server's own output\n"), {})  # not a Python server, or -I
        self.assertEqual((ok, seen), (False, "서버가 고정 장치를 읽지 않음"))

        def hangs(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 60)
        self.assertEqual(handlib.checks.unity_server_pinned(self.CFG, hangs, {})[0], False)
        import inspect
        self.assertIsNone(inspect.signature(handlib.checks.doctor_checks).parameters["server_pinned"].default)


class RealServerTests(unittest.TestCase):
    """With the MCP for Unity server's own code, when it is installed here: by itself it sends a subagent's commands
    to another editor; started the way claude-hand starts it, it never does."""

    @classmethod
    def setUpClass(cls):
        uvx = shutil.which("uvx")
        version = handlib.constants.MCP_FOR_UNITY_TESTED.lstrip("v")
        cls.python = None
        if uvx:
            try:
                found = subprocess.run([uvx, "--offline", "--from", f"mcpforunityserver=={version}", "python", "-c", "import sys; print(sys.executable)"],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, stdin=subprocess.DEVNULL)
                cls.python = found.stdout.strip().splitlines()[-1] if found.returncode == 0 and found.stdout.strip() else None
            except (OSError, subprocess.TimeoutExpired):
                pass
        if not cls.python:
            raise unittest.SkipTest(f"mcpforunityserver=={version} is not installed here (uvx --offline)")

    def scenarios(self, pinned: bool) -> dict:
        env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "CLAUDE_HAND_UNITY_PIN", "UNITY_MCP_"))}
        if pinned:
            env.update(handlib.agents.pinned_env({}, "ProjA@" + hashlib.sha1(b"C:/Proj/ProjA/Assets").hexdigest()[:8]))
        done = subprocess.run([self.python, str(SCENARIOS), "--quick"], env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
        lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
        self.assertTrue(lines, done.stdout[-2000:] + done.stderr[-2000:])
        return json.loads(lines[-1][len("RESULT "):])

    def test_commands_stay_with_the_pinned_editor(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            alone, pinned = pool.map(self.scenarios, (False, True))
        self.assertEqual((alone["pinned"], pinned["pinned"]), (False, True))
        self.assertEqual((alone["where"]["both answer"], pinned["where"]["both answer"]), ("own", "own"))
        # What the pin is for. If these stop reaching the other editor in a later server, the server no longer needs the pin.
        for scenario in ("reloading for over a minute", "reloading for over a minute, later", "closed", "reload while a command is on its way",
                         "port taken over"):
            self.assertEqual(alone["where"][scenario], "other", scenario)
        self.assertIn("both answer", alone["connected_to_other"])  # by itself it looks at every editor, and that cuts who works there
        self.assertEqual(pinned["reached_other"], [])
        for scenario in ("reloading for over a minute, later", "reload while a command is on its way, later", "port taken over, later",
                         "ports swapped"):
            self.assertEqual(pinned["where"][scenario], "own", scenario)  # and it finds its editor again
        # The only sockets it opens to the other editor are to the port its own editor's files still name; what answers
        # there is asked who it is, and not used.
        self.assertLessEqual(set(pinned["connected_to_other"]), {"port taken over", "ports swapped"})


class DiscoveryTests(unittest.TestCase):
    """claude-hand's own look at which editors are open (sync, list, doctor, the chat channel) opens no socket."""

    def status(self, folder: Path, hash_: str, port: int, **more) -> Path:
        file = folder / f"unity-mcp-status-{hash_}.json"
        file.write_text(json.dumps({"unity_port": port, "project_path": f"C:/P/{hash_}/Assets", **more}), encoding="utf-8")
        return file

    def test_an_editor_is_up_while_its_status_file_is_fresh(self):
        self.assertFalse(hasattr(handlib.discovery, "probe_port"))
        self.assertNotIn("socket", vars(handlib.discovery))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.status(folder, "aaaa1111", 6400)
            self.status(folder, "bbbb2222", 6401, reason="stopped")  # the bridge gave up after a reload
            self.status(folder, "cccc3333", 6402, reloading=True)
            self.status(folder, "dddd4444", 6400)  # a second file naming 6400: it speaks for its own editor only
            now = time.time()
            alive = lambda at: {inst.hash: inst.alive for inst in handlib.discovery.discover_stdio(folder, now=at)}
            old = now - 3600
            os.utime(folder / "unity-mcp-status-dddd4444.json", (old, old))
            self.assertEqual(alive(now + 1), {"aaaa1111": True, "bbbb2222": False, "cccc3333": True})  # dddd4444 lost the port to the fresher file
            later = now + handlib.discovery.HEARTBEAT_FRESH + 5  # no heartbeat for a while: gone, but a reload may take longer
            self.assertEqual(alive(later), {"aaaa1111": False, "bbbb2222": False, "cccc3333": True})
            self.assertEqual(alive(now + handlib.discovery.RELOAD_PATIENCE + 5)["cccc3333"], False)


if __name__ == "__main__":
    unittest.main()
