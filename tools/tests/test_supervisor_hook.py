"""Tests for tools/supervisor_hook.py, the supervisor's PreToolUse hook on Agent and SendMessage.
Run: python -m unittest discover -s tools/tests -v"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
os.environ["CLAUDE_HAND_LOG"] = "off"  # the tests leave the real event log alone
import chat_channel as cc  # noqa: E402
import handlib.agents  # noqa: E402
import handlib.discovery  # noqa: E402
import supervisor_hook as hook  # noqa: E402


def agent_call(prompt, subagent="unity-mygame"):
    """The hook input as Claude Code 2.1 sends it for the Agent tool (checked with a real session)."""
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent",
            "tool_input": {"description": "cube", "prompt": prompt, "subagent_type": subagent}}


def send_message(message, to="a0e1826e13107d88f"):
    return {"hook_event_name": "PreToolUse", "tool_name": "SendMessage",
            "tool_input": {"to": to, "summary": "more", "message": message, "type": "message", "recipient": to,
                           "recipient_kind": "agent", "content": message}}


class TagTests(unittest.TestCase):
    def test_the_request_tag(self):
        self.assertEqual(hook.tag_of("[요청 1111-052233-12] 큐브를 만들어라"), "1111-052233-12")
        self.assertEqual(hook.tag_of("할 일:\n[ 요청: 0101-000000-3 ]\n..."), "0101-000000-3")
        self.assertEqual(hook.tag_of("[요청 없음] 씬을 저장해라"), "")
        self.assertEqual(hook.tag_of("[요청 none]"), "")
        self.assertIsNone(hook.tag_of("큐브를 만들어라"))
        self.assertIsNone(hook.tag_of("[요청 큐브]"))  # not an id: the same as no tag
        self.assertIsNone(hook.tag_of(None))

    def test_which_calls_hand_work_out(self):
        self.assertEqual(hook.handed_out(agent_call("[요청 없음] x")), ("unity-mygame", "[요청 없음] x"))
        self.assertEqual(hook.handed_out({**agent_call("x"), "tool_name": "Task"}), ("unity-mygame", "x"))
        self.assertEqual(hook.handed_out(send_message("한 번 더")), ("a0e1826e13107d88f", "한 번 더"))
        self.assertIsNone(hook.handed_out({"tool_name": "Read", "tool_input": {"file_path": "x"}}))


class DecisionTests(unittest.TestCase):
    def decide(self, event, answer):
        asked = []
        reason = hook.decision(event, lambda path, body: asked.append((path, body)) or answer)
        return reason, asked

    def test_tells_the_channel_and_lets_the_call_through(self):
        reason, asked = self.decide(agent_call("[요청 1111-052233-12] 큐브"), {"ours": True, "chat_id": "unity-mygame", "asker": "unity-mygame"})
        self.assertIsNone(reason)
        self.assertEqual(asked, [("/v1/assign", {"target": "unity-mygame", "agent": "", "request": "1111-052233-12"})])
        reason, asked = self.decide(send_message("[요청 없음] 이어서"), {"ours": True, "chat_id": "unity-mygame", "asker": ""})
        self.assertEqual((reason, asked), (None, [("/v1/assign", {"target": "a0e1826e13107d88f", "agent": "", "request": ""})]))

    def test_an_agent_resumed_by_its_id_is_named_from_the_sessions_files(self):
        """A resumed supervisor goes on with the subagents of days ago, by id. Claude Code keeps what each one is next
        to the session's transcript (the layout of 2.1, checked with a real session)."""
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "2bfcab59-32d4-4e90-8df7-37b79974a4e9.jsonl"
            subagents = transcript.with_suffix("") / "subagents"
            subagents.mkdir(parents=True)
            (subagents / "agent-aab83db2fc71cb6c4.meta.json").write_text(json.dumps(
                {"agentType": "unity-mygame", "isBuiltIn": False, "description": "Create cylinder in Unity"}), encoding="utf-8")
            (subagents / "agent-broken.meta.json").write_text("not json", encoding="utf-8")
            (subagents / "agent-list.meta.json").write_text("[1]", encoding="utf-8")
            event = {**send_message("이어서", to="aab83db2fc71cb6c4"), "transcript_path": str(transcript)}
            self.assertEqual(hook.agent_type(event, "aab83db2fc71cb6c4"), "unity-mygame")
            _, asked = self.decide(event, {"ours": True, "chat_id": "unity-mygame", "missing": True})
            self.assertEqual(asked, [("/v1/assign", {"target": "aab83db2fc71cb6c4", "agent": "unity-mygame", "request": None})])
            for unknown in ("ffff", "broken", "list", "../../x", ""):
                self.assertEqual(hook.agent_type(event, unknown), "", unknown)
            self.assertEqual(hook.agent_type({"transcript_path": ""}, "aab83db2fc71cb6c4"), "")
            self.assertEqual(hook.agent_type({}, "aab83db2fc71cb6c4"), "")

    def test_refuses_a_hand_out_the_channel_cannot_place(self):
        missing, asked = self.decide(agent_call("큐브를 만들어라"), {"ours": True, "chat_id": "unity-mygame", "missing": True})
        self.assertEqual(asked[0][1]["request"], None)
        self.assertIn("unity-mygame", missing)
        self.assertIn("`[요청 <번호>]`", missing)
        self.assertIn("`[요청 없음]`", missing)
        unknown, _ = self.decide(agent_call("[요청 0101-000000-9] 큐브"), {"ours": True, "chat_id": "unity-mygame", "unknown": True})
        self.assertIn("0101-000000-9", unknown)
        stopped, _ = self.decide(agent_call("[요청 1111-052233-12] 큐브"), {"ours": True, "chat_id": "unity-mygame", "stop": True})
        self.assertIn("[중지]", stopped)

    def test_other_subagents_other_tools_and_no_claude_hand_are_none_of_its_business(self):
        self.assertEqual(self.decide(agent_call("찾아봐", "Explore"), {"ours": False})[0], None)
        self.assertEqual(self.decide(agent_call("큐브"), {})[0], None)  # no channel answered
        reason, asked = self.decide({"tool_name": "Bash", "tool_input": {"command": "ls"}}, {"ours": True, "missing": True})
        self.assertEqual((reason, asked), (None, []))


class HookTests(unittest.TestCase):
    def run_main(self, text, post):
        out = io.StringIO()
        with unittest.mock.patch("sys.stderr", io.StringIO()):
            code = hook.main(io.StringIO(text), out, post)
        return code, out.getvalue()

    def test_a_refusal_is_a_deny_decision_and_everything_else_is_silence(self):
        code, out = self.run_main(json.dumps(agent_call("큐브")), lambda path, body: {"ours": True, "chat_id": "unity-mygame", "missing": True})
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual((code, decision["hookEventName"], decision["permissionDecision"]), (0, "PreToolUse", "deny"))
        out.encode("ascii")  # safe whatever code page the hook shell uses
        self.assertEqual(self.run_main(json.dumps(agent_call("[요청 없음] 큐브")), lambda path, body: {"ours": True}), (0, ""))

    def test_it_never_stands_in_the_way_when_it_cannot_do_its_job(self):
        def down(path, body):
            raise OSError("connection refused")
        self.assertEqual(self.run_main(json.dumps(agent_call("큐브")), down), (0, ""))
        self.assertEqual(self.run_main("not json", down), (0, ""))
        self.assertEqual(self.run_main("[1, 2]", down), (0, ""))


class ProcessTests(unittest.TestCase):
    """The hook as Claude Code starts it, against a channel server that really listens."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        files = handlib.agents.build_agent_files([handlib.discovery.Instance(name="MyGame", hash="aaaa1111")],
                                                 handlib.discovery.ServerConfig(transport="stdio"), [], None, "uv run")
        handlib.agents.write_agent_files(files, root / "agents")
        self.hub = cc.ChatHub(lambda message: None, root / "agents")
        self.hub.initialized = True
        self.httpd = cc.start_http(self.hub, "t", port=18797)
        (root / "home" / "chat").mkdir(parents=True)
        (root / "home" / "chat" / "server.json").write_text(json.dumps({"port": self.httpd.server_address[1], "token": "t"}), encoding="utf-8")
        self.env = {**os.environ, "CLAUDE_HAND_HOME": str(root / "home")}

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def run_hook(self, event, env=None):
        data = event if isinstance(event, bytes) else json.dumps(event, ensure_ascii=False).encode("utf-8")
        return subprocess.run([sys.executable, str(TOOLS / "supervisor_hook.py")], input=data, capture_output=True, timeout=60,
                              env=env or self.env)

    def test_a_tagged_hand_out_reaches_the_channel_and_an_untagged_one_is_refused(self):
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        request = self.hub.logs["unity-mygame"][-1]["request"]
        proc = self.run_hook(agent_call("큐브를 만들어라"))
        self.assertEqual(proc.returncode, 0)
        decision = json.loads(proc.stdout.decode("ascii"))["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("`[요청 <번호>]`", decision["permissionDecisionReason"])
        self.assertEqual(self.hub.assigned, {})
        proc = self.run_hook(agent_call(f"[요청 {request}] 큐브를 만들어라"))
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))
        self.assertEqual(self.hub.assigned["unity-mygame"]["asker"], "unity-mygame")
        proc = self.run_hook(agent_call("코드에서 찾아봐", "Explore"))  # not a window's subagent: no tag asked of it
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))

    def test_an_agent_id_the_channel_never_saw_is_placed_by_the_sessions_files(self):
        """The first hand-out after Claude Hand starts, from a supervisor that goes on with its subagents of before."""
        transcript = Path(self.tmp.name) / "projects" / "2bfcab59.jsonl"
        subagents = transcript.with_suffix("") / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "agent-aab83db2fc71cb6c4.meta.json").write_text(json.dumps({"agentType": "unity-mygame"}), encoding="utf-8")
        self.hub.post_user_message("unity", "MyGame", "큐브 만들어줘")
        request = self.hub.logs["unity-mygame"][-1]["request"]
        event = {**send_message("큐브를 만들어라", to="aab83db2fc71cb6c4"), "transcript_path": str(transcript)}
        proc = self.run_hook(event)
        self.assertEqual(json.loads(proc.stdout.decode("ascii"))["hookSpecificOutput"]["permissionDecision"], "deny")
        proc = self.run_hook({**event, "tool_input": {**event["tool_input"], "message": f"[요청 {request}]\n큐브를 만들어라"}})
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))
        self.assertEqual(self.hub.assigned["unity-mygame"]["asker"], "unity-mygame")
        self.hub.assigned.clear()
        proc = self.run_hook(send_message("큐브를 만들어라", to="aab83db2fc71cb6c4"))  # no such files: none of the channel's business
        self.assertEqual((proc.returncode, proc.stdout, self.hub.assigned), (0, b"", {}))

    def test_without_claude_hand_or_with_garbage_the_call_goes_ahead(self):
        nowhere = {**os.environ, "CLAUDE_HAND_HOME": str(Path(self.tmp.name) / "nobody-home")}
        for event in (agent_call("큐브를 만들어라"), b"\xff\xfe not utf-8", {"tool_name": "Read"}):
            proc = self.run_hook(event, nowhere)
            self.assertEqual((proc.returncode, proc.stdout), (0, b""), event)
        dead = {**os.environ, "CLAUDE_HAND_HOME": str(Path(self.tmp.name) / "dead")}
        (Path(self.tmp.name) / "dead" / "chat").mkdir(parents=True)
        (Path(self.tmp.name) / "dead" / "chat" / "server.json").write_text(json.dumps({"port": 9, "token": "t"}), encoding="utf-8")
        proc = self.run_hook(agent_call("큐브를 만들어라"), dead)  # a server.json left behind by a supervisor that closed
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))


if __name__ == "__main__":
    unittest.main()
