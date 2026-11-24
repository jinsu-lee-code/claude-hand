# SPDX-License-Identifier: MIT
# Copyright (c) 2026 jinsu-lee-code. This file is installed on its own, so it says so itself; the full text is in
# LICENSE at the root of https://github.com/jinsu-lee-code/claude-hand
"""Claude Hand Chat: talk to this Blender window's claude-hand subagent from the 3D Viewport sidebar.

It is also what that subagent calls, through MCP for Blender's execute_code, for the things claude-hand does in
Blender: undo points (mark_undo_point, undo_last_request) and moving objects to another Blender or to and from Unity
(export_blend, import_blend, export_fbx, import_model).

And one thing it does with no Claude at all: a model that was told in Unity to follow this file is sent there again
whenever the file is saved, straight to that Unity editor's own bridge (see "models that follow this file").

Messages go to the claude-hand-chat channel (tools/chat_channel.py in the claude-hand repo), which
the supervisor Claude Code session runs; the supervisor hands them to blender-<file> and answers here.
Networking runs on a background thread; everything that touches bpy runs on the main thread
through a bpy.app.timers callback.

Install: `uv run tools/claude_hand.py blender-install-chat` (or Preferences > Add-ons > Install).
"""

bl_info = {
    "name": "Claude Hand Chat",
    "author": "claude-hand",
    "version": (0, 17, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > Claude Hand",
    "description": "Chat with this Blender window's claude-hand subagent",
    "category": "Interface",
}

import base64
import contextlib
import datetime
import functools
import hashlib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import bpy
import bpy.utils.previews

POLL_WAIT_SECONDS = 20
NO_REPLY_HINT_SECONDS = 60
START_WAIT_SECONDS = 120
CONTEXT_MIN_INTERVAL = 0.5   # seconds between context reports
CONTEXT_REFRESH = 60.0       # resend even when nothing changed
CONTEXT_ITEMS = 20
VISIBLE_ENTRIES = 12
WRAP_WIDTH = 38
KEPT_ENTRIES = 60       # recent history only: the conversation itself lives in Claude Code
PANEL_LINES = 40        # history lines drawn; older entries fold so the input box stays within reach
ENTRY_LINES = 14        # lines of one message; the rest is behind "전체 복사"
PROGRESS_STALE = 300.0  # seconds without progress news after which "작업 중" is no longer shown (a long import is one action)
PANEL_MARGIN_PX = 50.0  # category tabs, box and label padding at UI scale 1
LABEL_HEADROOM = 1.2    # blf's width at the label size can come out a little narrower than the drawn label
CHAR_PX = 6.0           # without blf: width of a narrow character at 11 px (wide ones such as Korean count twice)
SHOWN_KINDS = ("user", "reply", "permission", "notice", "image")
IMAGE_LINES = 8         # what a result preview counts for when the panel decides how much history fits
ALERT_FRESH_SECONDS = 30.0  # older items (history shown again after a restart) never blink the taskbar
UNDO_KEPT = 20          # undo points kept for the newest requests
UNDO_PROP = "claude_hand_undo"  # on every scene: "<this run>:<newest undo point set before this state>"
SENT_AS = "claude_hand_sent_as"  # on an object that went to Unity: the name it went under, which Unity knows its model by
FOLLOW_TOOL = "ch_model_follow"  # Claude Hand Tools' tool for models that follow their .blend (0.16 and later)
REALIZE = "ClaudeHandRealize"    # the modifier and node group a Geometry Nodes object has while it is exported (_realized)
TEXTURE_FORMATS = (".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".exr", ".hdr", ".psd")  # image files Unity reads as they are
BAKE_SIZE = 1024                 # pixels a side, of an image baked from a material's nodes
BAKED = {"base_color_texture": "기본 색", "metallic_smoothness_texture": "금속 · 거칠기", "normal_texture": "노멀", "emission_texture": "발광"}
TREE_LAYOUT = {"name", "label", "location", "location_absolute", "width", "height", "dimensions", "select", "parent", "hide", "color",
               "use_custom_color", "rna_type", "type", "inputs", "outputs", "internal_links", "warning_propagation"}  # of a node: not what it does
UNITY_LOOK_SECONDS = 5.0         # how often the panel looks whether another Unity editor has opened
HEAVY_SECONDS = 1.0              # a model whose export took longer than this is exported on a save only when it was touched
BUSY_TRIES, BUSY_WAIT = 8, 2.5   # how long a send waits for a Unity that is compiling or reloading its scripts

_entries = []            # main thread only
_status = ["연결 중..."]  # main thread only
_last_user = [0.0]       # main thread only
_server_missing = [False]  # main thread only
_start_requested = [0.0]   # main thread only
_pending = [None]          # main thread only: (text, context) typed while Claude Hand was off
_progress = [None]         # main thread only: (what the subagent is doing, when it arrived) while it works
_others = []               # main thread only: what the other open windows are busy with, a line each (from the channel)
_context_sent = {"time": 0.0, "signature": None, "server_pid": None, "busy": False}  # main thread only
_inbox = queue.Queue()   # (event, payload) from worker threads
_identity = {"window": None, "pin": None}  # written on the main thread, read by the poller
_identity_lock = threading.Lock()
_stop = threading.Event()
_poller = [None]
_marks = []               # main thread only: undo points of Claude's requests, newest last
#                           ({"seq", "label", "time", "request": the request's name, "stamp": when it was set})
_offer = [None]           # main thread only: a request asked here that changed other windows too, as the channel tells it
_mark_seq = [0]           # main thread only
_undo_note = [""]         # main thread only: goes along with the next message after "되돌리기"
_undo_run = f"{os.getpid()}-{int(time.time())}"  # undo points of another Blender run never match
_native_input = [None]    # main thread only: the Windows input box (a PowerShell process) while it is open
_previews = [None]        # main thread only: thumbnails of result previews (bpy.utils.previews), by file path
# Main thread only: the models in Unity that came from this file, and the looks that send the ones that follow it.
_follow = {
    "path": "",        # this file's path at the last look: a save under another name shows against it
    "moved_from": "",  # ... and what it was before such a save, until an open Unity has been told
    "due": None,       # why a look is due ("save", "undo", "open", "unity", "look"), or None
    "busy": 0,         # how many worker threads are talking to Unity (a look, a button of the panel): no look starts meanwhile
    "rows": [],        # what the panel lists: one dict per model (_row)
    "editors": [],     # the Unity editors that are open, as the panel offers them: [(name, hash)]
    "unity": None,     # (hash, pid) of those editors at the last look; None before the first
    "looked": 0.0,     # when the editors were last looked for
    "seen": set(),     # (hash, asset) of the models compared with Unity once since this file was opened
    "models": {},      # (hash, asset) -> {"seq", "snapshot", "took"}: as of the last time each was found in step
    "status": "",      # the panel's line about the last look
}
# Main thread only: what Blender said was changed, and when (a running count): objects by name, meshes and materials
# by (kind, name), and "other" for what cannot be told to be anybody's. "own": a look is exporting right now.
_touched = {"seq": 0, "objects": {}, "data": {}, "other": 0, "own": False}


# --------------------------------------------------------------------------- server access (any thread)


def server_file() -> Path:
    home = os.environ.get("CLAUDE_HAND_HOME") or str(Path.home() / ".claude-hand")
    return Path(home) / "chat" / "server.json"


def read_server():
    try:
        return json.loads(server_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def describe_missing_server():
    """Why there is no server.json, from the status.json the channel writes on every start."""
    try:
        status = json.loads((server_file().parent / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = {}
    if status.get("mode") == "passive":
        return f"채널 서버가 대기 중입니다: {status.get('reason')}. 아래 버튼으로 켜거나, 메시지를 보내면 자동으로 켜집니다."
    if status.get("mode") == "stopped":
        return "Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다."
    return ("Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다. 켜도 연결되지 않으면 "
            "claude-hand 폴더에서 uv run tools/claude_hand.py chat-status 를 실행하세요.")


STARTING_MESSAGE = ("Claude Hand 창을 여는 중입니다. 그 창에 확인 질문이 보이면 화살표 키로 고르고 Enter를 눌러 주세요"
                    "(개발용 채널 경고는 I am using this for local development). 연결되면 이 패널이 이어서 진행합니다.")


def starting():
    return bool(_start_requested[0]) and time.time() - _start_requested[0] < START_WAIT_SECONDS


def call(server, method, path, body=None, timeout=10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{server['port']}{path}", data=data, method=method,
        headers={"X-Claude-Hand-Token": server.get("token", ""), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:  # 401 / 409 still carry a JSON body
        try:
            return json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return {"ok": False, "error": str(exc)}


def poll_loop():
    """Background thread: long-polls the channel for this window's replies and permission prompts."""
    chat_id, agent, after, server_pid, progress_rev, others_rev = None, None, 0, None, -1, -1
    offer = None
    while not _stop.is_set():
        server = read_server()
        with _identity_lock:
            window, pin = _identity["window"], _identity["pin"]
        if not server or not window:
            _inbox.put(("missing", True))
            _inbox.put(("status", None))  # the main thread knows whether a start is under way
            _stop.wait(1)
            continue
        if server.get("pid") != server_pid:  # a new supervisor session starts with an empty history
            server_pid, chat_id, agent, after, progress_rev, others_rev = server.get("pid"), None, None, 0, -1, -1
            offer = None
            _inbox.put(("reset", None))
        try:
            if chat_id is None or agent is None:
                query = urllib.parse.urlencode({"app": "blender", "window": window, "pin": pin})
                resolved = call(server, "GET", f"/v1/resolve?{query}")
                if resolved.get("ok") is False:
                    server_pid = None
                    _stop.wait(1)
                    continue
                chat_id, agent = resolved.get("chat_id"), resolved.get("agent")
                _inbox.put(("status", f"담당: {agent}" if agent else
                            "이 Blender 창의 하위 세션이 없습니다. uv run tools/claude_hand.py sync 를 실행하세요."))
            query = urllib.parse.urlencode({"chat_id": chat_id, "after": after, "prev": progress_rev, "orev": others_rev,
                                            "wait": POLL_WAIT_SECONDS})
            _inbox.put(("missing", False))
            updates = call(server, "GET", f"/v1/updates?{query}", timeout=POLL_WAIT_SECONDS + 15)
            if updates.get("ok") is False:
                server_pid = None
                _stop.wait(1)
                continue
            if updates.get("items"):
                _inbox.put(("items", updates["items"]))
            if "progress_rev" in updates and updates["progress_rev"] != progress_rev:
                progress_rev = updates["progress_rev"]
                _inbox.put(("progress", updates.get("progress")))
            if "others_rev" in updates and updates["others_rev"] != others_rev:  # another window's state changed
                others_rev = updates["others_rev"]
                _inbox.put(("others", updates.get("others") or []))
            if updates.get("undo") != offer:  # a request asked here that changed other windows (or no longer does)
                offer = updates.get("undo")
                _inbox.put(("offer", offer))
            after = updates.get("next", after)
        except (OSError, ValueError) as exc:
            _inbox.put(("missing", True))
            _inbox.put(("status", f"감독 세션에 연결할 수 없습니다: {exc}"))
            _stop.wait(3)


def post_in_background(path, body, on_done_event):
    def run():
        server = read_server()
        if not server:
            _inbox.put((on_done_event, {"ok": False}) if on_done_event == "context_sent" else ("status", "Claude Hand가 꺼져 있습니다."))
            return
        try:
            _inbox.put((on_done_event, call(server, "POST", path, body)))
        except (OSError, ValueError) as exc:
            _inbox.put(("status", f"보내지 못했습니다: {exc}"))
            if on_done_event == "context_sent":
                _inbox.put((on_done_event, {"ok": False}))
    threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------- main thread


def window_identity():
    """Same naming as `claude_hand.py sync`: <.blend name or untitled-PORT>@<MCP for Blender port>."""
    scene = bpy.context.scene
    port = int(getattr(scene, "blendermcp_port", 9876)) if scene else 9876
    stem = Path(bpy.data.filepath).stem if bpy.data.filepath else ""
    name = stem or f"untitled-{port}"
    return name, f"{name}@{port}"


def build_context():
    layer = bpy.context.view_layer
    selected = [o.name for o in layer.objects if o.select_get()][:10]
    active = layer.objects.active
    lines = [f"파일: {bpy.data.filepath or '(저장 안 됨)'}", f"씬: {bpy.context.scene.name}"]
    if active is not None:
        lines.append(f"활성 오브젝트: {active.name} ({active.type}, 모드 {active.mode})")
    if selected:
        lines.append("선택한 오브젝트: " + ", ".join(selected))
    return "\n".join(lines)


def send_text(text, include_context=True):
    text = (text or "").strip()
    if not text or _pending[0] is not None:
        return False
    context = build_context() if include_context else ""
    if not read_server() or _server_missing[0]:
        # Claude Hand is off (or its server.json is left over from a crash): keep the message, start it,
        # and send once it answers.
        _pending[0] = (text, context)
        if not starting():
            start_supervisor()
        return True
    post_message(text, context)
    return True


def post_message(text, context):
    if _undo_note[0]:  # the supervisor would otherwise still count on what was undone
        context, _undo_note[0] = "\n".join(filter(None, [_undo_note[0], context])), ""
    window, pin = window_identity()
    _last_user[0] = time.time()
    post_in_background("/v1/messages", {"app": "blender", "window": window, "pin": pin, "text": text, "context": context}, "sent")


def collect_context():
    """What report_context sends: the file, scene, active and selected objects."""
    layer = bpy.context.view_layer
    active = layer.objects.active
    return {
        "file": bpy.data.filepath or "(저장 안 됨)",
        "scene": bpy.context.scene.name,
        "active": active.name if active is not None else "",
        "mode": active.mode if active is not None else "",
        "selection": [o.name for o in layer.objects if o.select_get()][:CONTEXT_ITEMS],
    }


def report_context(now=None):
    """Tell the supervisor what is selected here (its current_context tool), only while Claude Hand is on,
    at most twice a second and only on change, plus a refresh every minute. Main thread."""
    now = now or time.time()
    state = _context_sent
    if state["busy"] or now - state["time"] < CONTEXT_MIN_INTERVAL:
        return False
    server = read_server()
    if not server:
        return False
    try:
        context = collect_context()
    except (AttributeError, RuntimeError):  # no scene or view layer during file loading
        return False
    signature = json.dumps(context, sort_keys=True)
    if signature == state["signature"] and server.get("pid") == state["server_pid"] and now - state["time"] < CONTEXT_REFRESH:
        return False
    window, pin = window_identity()
    state.update(time=now, signature=signature, server_pid=server.get("pid"), busy=True)
    post_in_background("/v1/context", {"app": "blender", "window": window, "pin": pin, "context": context}, "context_sent")
    return True


def find_start_script():
    """start-supervisor next to the claude-hand checkout, from ~/.claude-hand/config.json (written by setup)."""
    try:
        config = json.loads((server_file().parent.parent / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    script = config.get("start_script")
    return script if script and Path(script).exists() else None


def start_supervisor():
    script = find_start_script()
    if not script:
        _status[0] = "start-supervisor를 찾지 못했습니다. 바탕화면의 Claude Hand 아이콘을 누르세요."
        return False
    if sys.platform == "win32":
        cmd = ["cmd", "/c", "start", "Claude Hand", script]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    elif sys.platform == "darwin":
        cmd, flags = ["open", "-a", "Terminal", script], 0
    else:
        cmd, flags = ["x-terminal-emulator", "-e", script], 0
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        _status[0] = f"Claude Hand를 시작하지 못했습니다: {exc}"
        return False
    _start_requested[0] = time.time()
    _status[0] = STARTING_MESSAGE
    return True


# --------------------------------------------------------------------------- Windows input box
#
# Blender's own text fields and the Korean IME do not get along on Windows: a syllable shows only once the next one
# starts, and the space or symbol that ends a syllable is lost. That happens inside Blender before Python sees any key,
# so the panel offers a native Windows text box instead. It prints each message as one base64 (UTF-8) line.

NATIVE_INPUT_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$utf8 = [Text.Encoding]::UTF8
$title = $utf8.GetString([Convert]::FromBase64String('__TITLE__'))
$parentId = __PARENT__
$out = [Console]::OpenStandardOutput()
function Send-Line([string]$text) {
  $bytes = [Text.Encoding]::ASCII.GetBytes([Convert]::ToBase64String($utf8.GetBytes($text)) + "`n")
  $out.Write($bytes, 0, $bytes.Length)
  $out.Flush()
}
$form = New-Object Windows.Forms.Form
$form.Text = $title
$form.TopMost = $true
$form.StartPosition = 'Manual'
$cursor = [Windows.Forms.Cursor]::Position
$form.Location = New-Object Drawing.Point ([Math]::Max(0, $cursor.X - 230)), ([Math]::Max(0, $cursor.Y - 200))
$form.ClientSize = New-Object Drawing.Size 460, 150
$form.Font = New-Object Drawing.Font 'Malgun Gothic', 10
$box = New-Object Windows.Forms.TextBox
$box.Multiline = $true
$box.AcceptsReturn = $true
$box.ScrollBars = 'Vertical'
$box.Dock = 'Fill'
$bar = New-Object Windows.Forms.FlowLayoutPanel
$bar.Dock = 'Bottom'
$bar.AutoSize = $true
$bar.FlowDirection = 'RightToLeft'
$send = New-Object Windows.Forms.Button
$send.Text = '보내기'
$send.AutoSize = $true
$hint = New-Object Windows.Forms.Label
$hint.Text = 'Enter 보내기 · Shift+Enter 줄바꿈 · Esc 닫기'
$hint.AutoSize = $true
$hint.Padding = New-Object Windows.Forms.Padding 0, 7, 0, 0
$bar.Controls.Add($send)
$bar.Controls.Add($hint)
$form.Controls.Add($bar)
$form.Controls.Add($box)
$box.BringToFront()
$doSend = {
  $text = $box.Text.Trim()
  if ($text) { Send-Line $text; $box.Clear() }
  $box.Focus() | Out-Null
}
$send.Add_Click($doSend)
$box.Add_KeyDown({
  param($s, $e)
  if ($e.KeyCode -eq 'Return' -and -not $e.Shift) {
    $e.SuppressKeyPress = $true
    $form.BeginInvoke([Action]$doSend) | Out-Null  # after the IME has put the last syllable in
  } elseif ($e.KeyCode -eq 'Escape') {
    $form.Close()
  }
})
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({ if (-not (Get-Process -Id $parentId -ErrorAction SilentlyContinue)) { $form.Close() } })
$timer.Start()
$form.Add_Shown({ $form.Activate(); $box.Focus() | Out-Null })
[void]$form.ShowDialog()
"""


def native_input_command(title, parent_pid):
    """The PowerShell command line for the input box; values go in as base64 or digits, never as script text."""
    script = (NATIVE_INPUT_PS.replace("__TITLE__", base64.b64encode(title.encode("utf-8")).decode("ascii"))
              .replace("__PARENT__", str(int(parent_pid))))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded]


def read_native_input(lines):
    """Background thread: each line the input box prints is a message for this window."""
    for raw in lines:
        try:
            text = base64.b64decode(raw.strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if text.strip():
            _inbox.put(("native_text", text))


def open_native_input():
    """Open the Windows input box, or bring it to the front when it is open already. Main thread."""
    proc = _native_input[0]
    if proc is not None and proc.poll() is None:
        _bring_to_front(proc.pid)
        return True
    window, _ = window_identity()
    try:
        proc = subprocess.Popen(native_input_command(f"Claude Hand · {window}", os.getpid()), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        _status[0] = f"입력창을 열지 못했습니다: {exc}"
        return False
    _native_input[0] = proc
    threading.Thread(target=read_native_input, args=(proc.stdout,), name="claude-hand-input", daemon=True).start()
    return True


def _windows_of(pid):
    """Windows: the visible top-level windows of a process, front first."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found


def _bring_to_front(pid):
    import ctypes
    user32 = ctypes.windll.user32
    for hwnd in _windows_of(pid)[:1]:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


# --------------------------------------------------------------------------- "done" alert (main thread)


def wants_attention(item, now):
    """A fresh reply to a request made here, a permission prompt, or a dialog that stopped the window doing the work:
    worth calling the user back for. Not history shown again after a restart, and not replies to work asked in the
    Claude app (the user is there), nor a "working on it" message sent before the result."""
    if now - float(item.get("ts") or 0) > ALERT_FRESH_SECONDS:
        return False
    kind = item.get("kind")
    return kind == "permission" or (kind == "reply" and item.get("origin") != "desktop" and not item.get("working")) or \
        (kind == "notice" and bool(item.get("alert")))


def flash_taskbar():
    """Windows: blink Blender's taskbar button until Blender comes to the front, unless it is in front already (or
    the user is typing in this panel's input box). Returns whether it blinked."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(owner))
    box = _native_input[0]
    if owner.value == os.getpid() or (box is not None and owner.value == box.pid):
        return False
    windows = _windows_of(os.getpid())
    if not windows:
        return False

    class FlashInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

    info = FlashInfo(ctypes.sizeof(FlashInfo), windows[0], 0x3 | 0xC, 0, 0)  # FLASHW_ALL | FLASHW_TIMERNOFG
    user32.FlashWindowEx(ctypes.byref(info))
    return True


def close_native_input():
    proc, _native_input[0] = _native_input[0], None
    if proc is not None and proc.poll() is None:
        proc.terminate()


# --------------------------------------------------------------------------- undo points (main thread)
#
# Blender has no Python access to its undo history, so an undo point is a value in the data itself: every scene gets
# UNDO_PROP = this run and the point's number, and an undo step is pushed. States after the point carry that number
# (or a newer one); undoing until the number is older and redoing once lands exactly on the point.


def _undo_op(op, **kwargs):
    """Run an ed.* undo operator from a timer or MCP for Blender's execute_code, whose context has no window."""
    windows = bpy.context.window_manager.windows
    if not windows:
        return {"CANCELLED"}  # background Blender: no undo history
    override = {"window": windows[0], "screen": windows[0].screen}
    if not hasattr(bpy.context, "temp_override"):  # Blender before 3.2
        return op(override, **kwargs) if op.poll(override) else {"CANCELLED"}
    with bpy.context.temp_override(**override):
        return op(**kwargs) if op.poll() else {"CANCELLED"}


def state_seq():
    """The newest of this run's undo points that the current state comes after (0 for none)."""
    newest = 0
    for scene in bpy.data.scenes:
        run, _, seq = str(getattr(scene, UNDO_PROP, "")).rpartition(":")
        if run == _undo_run and seq.isdigit():
            newest = max(newest, int(seq))
    return newest


def describe_mark(mark):
    return f"{mark['label']} ({mark['time']})" if mark["label"] else f"{mark['time']} 요청"


def mark_undo_point(label="", request=""):
    """Set an undo point for a new request. The subagent's guard hook calls this through MCP for Blender's execute_code
    just before the request's first Blender action. request: the request's name as the chat channel knows it (its
    id), so that a request which changed several windows can be undone in each of them by that name (undo_request)."""
    seq = _mark_seq[0] + 1
    for scene in bpy.data.scenes:
        setattr(scene, UNDO_PROP, f"{_undo_run}:{seq}")
    if _undo_op(bpy.ops.ed.undo_push, message=f"Claude Hand: {label or '요청'}"[:60]) != {"FINISHED"}:
        return "undo point not set: no undo history here"
    _mark_seq[0] = seq
    _marks.append({"seq": seq, "label": " ".join(str(label).split())[:40].rstrip(), "time": time.strftime("%H:%M"),
                   "request": str(request or ""), "stamp": time.time()})
    del _marks[:-UNDO_KEPT]
    _redraw()
    return f"undo point {seq} set"


def walk_back(seq, current, undo, redo, limit):
    """Undo until the state is from before undo point seq, then redo once: the state as it was when the point was set.
    Returns the undo steps taken, or None after stepping forward again to where it started (the point is no longer in
    the history, which keeps Preferences > System > Undo Steps, 32 by default)."""
    steps = 0
    while current() >= seq:
        if steps >= limit or undo() != {"FINISHED"}:
            for _ in range(steps):
                redo()
            return None
        steps += 1
    redo()
    return steps


def undo_last_request(note=False):
    """Revert everything done in this Blender since the newest undo point. Shows the outcome in the panel and returns
    it. note: also tell the supervisor with the next message (the panel's button; a subagent knows already)."""
    return _undo_newest(note)[1]


def _undo_newest(note=False):
    """(whether it went back, what the panel shows)."""
    if not _marks:
        return False, _notice("되돌릴 Claude 요청이 없습니다.")
    mark = _marks[-1]
    if state_seq() < mark["seq"]:  # Blender's own undo already went back past it
        _marks.pop()
        return False, _notice(f"'{describe_mark(mark)}' 요청은 이미 되돌려져 있습니다.")
    _undo_op(bpy.ops.ed.undo_push, message="Claude Hand: 되돌리기 전")  # so Ctrl+Shift+Z can bring the work back
    limit = bpy.context.preferences.edit.undo_steps + 2
    steps = walk_back(mark["seq"], state_seq, lambda: _undo_op(bpy.ops.ed.undo), lambda: _undo_op(bpy.ops.ed.redo), limit)
    if steps is None:
        return False, _notice(f"'{describe_mark(mark)}' 요청을 되돌리지 못했습니다. 그 뒤로 실행 취소 단계가 너무 많아 "
                              "Blender의 실행 취소 기록에서 밀려났습니다.")
    _marks.pop()
    if note:
        _note_for_claude(f"[사용자가 채팅창에서 '{describe_mark(mark)}' 요청 이후의 Blender 변경을 실행 취소로 되돌렸음]")
    _follow_due("undo")  # a model that follows this file goes back in Unity too, though nothing was saved
    return True, _notice(f"되돌렸어요: '{describe_mark(mark)}' 이후의 변경")


def undo_request(request):
    """Undo the request of this name here, but only when its undo point is the newest one: the chat channel calls
    this in every window a request changed (the 되돌리기 of the panel it was asked in, or the supervisor's
    undo_request tool). A point of another request on top is left alone: undoing goes back in order, and that newer
    request would go with it. Returns {"state": "undone" | "nothing" | "blocked" | "failed", ...} for the channel."""
    request = str(request or "")
    while _marks and state_seq() < _marks[-1]["seq"]:  # Blender's own undo already went back past these
        _marks.pop()
    if not request or all(mark.get("request") != request for mark in _marks):
        return {"state": "nothing"}
    top = _marks[-1]
    if top.get("request") != request:
        return {"state": "blocked", "top": describe_mark(top)}
    done, text = _undo_newest()
    return {"state": "undone" if done else "failed", "detail": text}


def undo_plan():
    """What the panel's 되돌리기 takes back now: (this window's newest undo point or None, the channel's offer or
    None). The offer is a request asked in this panel that changed other windows too; it comes first unless this
    window's newest point is of a later request (that one is undone here first, as before)."""
    mark, offer = (_marks[-1] if _marks else None), _offer[0]
    if not offer:
        return mark, None
    here = mark is not None and bool(mark.get("request")) and mark["request"] == offer.get("request")
    if mark is None or here or mark.get("stamp", 0.0) <= float(offer.get("ts") or 0.0):
        return (mark if here else None), offer
    return mark, None


def describe_plan(mark, offer):
    """What the button says: the request, and for one that reached other windows, where it goes back."""
    if offer is None:
        return describe_mark(mark)
    others = ", ".join(str(w) for w in offer.get("windows") or [])
    return f"{offer.get('label') or '요청'} ({'이 창 + ' if mark is not None else ''}{others})"


def _notice(text):
    _entries.append({"kind": "notice", "text": text})
    del _entries[:-KEPT_ENTRIES]
    _redraw()
    return text


def _undo_from_button():
    mark, offer = undo_plan()
    window, pin = window_identity()
    if mark is not None:
        done, _ = _undo_newest(note=True)
        if done and offer is None and mark.get("request"):  # so no other panel still offers this window with that request
            post_in_background("/v1/undone", {"app": "blender", "window": window, "pin": pin, "request": mark["request"]}, "undone_sent")
    if offer is not None:  # the other windows: the channel undoes the request in each, and says here how it went
        post_in_background("/v1/undo", {"app": "blender", "window": window, "pin": pin, "request": offer.get("request", ""),
                                        "skip_own": True}, "undo_sent")
    return None


@bpy.app.handlers.persistent
def _forget_undo_points(*_args):
    """Opening a file starts a new undo history."""
    _marks.clear()


# --------------------------------------------------------------------------- transfers (what the subagent calls)
#
# Moving objects to another Blender or to Unity, through the exchange folder. The subagent runs these with MCP for
# Blender's execute_code, e.g. `import claude_hand_chat; print(claude_hand_chat.export_fbx(["Hero"], "hero"))`.
# They were code blocks in the subagent's prompt once, typed out again for every transfer; here they are tested as
# they run. Paths in the results use forward slashes, so they print the same as they are passed on.


def exchange_dir():
    """Where Unity and Blender windows leave files for each other: CLAUDE_HAND_EXCHANGE_DIR, else exchange/ in
    claude-hand's home folder (the same rule as Claude Hand Tools in Unity and tools/handlib/link.py)."""
    home = os.environ.get("CLAUDE_HAND_HOME") or os.path.join(os.path.expanduser("~"), ".claude-hand")
    path = os.environ.get("CLAUDE_HAND_EXCHANGE_DIR") or os.path.join(home, "exchange")
    os.makedirs(path, exist_ok=True)
    return path


def _transfer_id(name):
    """(the name made safe for files and asset paths, that name with the time: one id per transfer)"""
    name = (re.sub(r"[^A-Za-z0-9_-]+", "_", str(name)).strip("_") or "transfer")[:48]
    return name, name + "_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _with_children(roots):
    """The named objects and everything parented under them, each once, in order."""
    if isinstance(roots, str):
        roots = [roots]
    found = []
    for name in roots:
        root = bpy.data.objects.get(name)
        if root is None:
            raise ValueError(f"이 파일에 '{name}' 오브젝트가 없습니다. get_scene_info로 정확한 이름을 확인하세요.")
        for obj in (root, *root.children_recursive):
            if obj not in found:
                found.append(obj)
    if not found:
        raise ValueError("보낼 오브젝트 이름을 하나 이상 주세요.")
    return list(roots), found


def _write_manifest(name, transfer_id, data):
    """`source` and `roots` are what the receiving side knows a transfer by: the same ones again are a newer version of
    the same thing (Unity's ch_import_model then replaces its model in place), whatever `name` says this time."""
    path = os.path.join(exchange_dir(), transfer_id + ".claudehand.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**data, "name": name, "transfer_id": transfer_id, "source": bpy.data.filepath,
                   "blender_version": bpy.app.version_string}, f, ensure_ascii=False, indent=2)
    return path.replace("\\", "/")


def export_blend(roots, name="transfer"):
    """Write the named objects, their children and the data they use (meshes, materials) to a .blend in the exchange
    folder for another Blender. Returns {"manifest": path to hand on, "objects": names written}. Changes nothing here."""
    roots, objects = _with_children(roots)
    name, transfer_id = _transfer_id(name)
    bpy.data.libraries.write(os.path.join(exchange_dir(), transfer_id + ".blend"), set(objects), path_remap="ABSOLUTE", fake_user=True)
    manifest = _write_manifest(name, transfer_id, {"format": "claude-hand/blend@1", "blend_file": transfer_id + ".blend", "roots": roots,
                                                   "objects": sorted(o.name for o in objects)})
    return {"manifest": manifest, "objects": sorted(o.name for o in objects)}


def import_blend(manifest):
    """Append the objects another Blender wrote with export_blend into the current scene. Returns {"added": the names
    they have here, "renamed": {name there: name here} where Blender had to add .001 because the name was taken}."""
    manifest = str(manifest).strip().strip('"')
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != "claude-hand/blend@1":
        raise ValueError(f"이 manifest는 '{data.get('format')}' 형식이라 Blender로 받을 수 없습니다. export_blend가 만든 것만 됩니다.")
    blend = os.path.join(os.path.dirname(manifest), data["blend_file"])
    with bpy.data.libraries.load(blend, link=False) as (src, dst):
        wanted = [n for n in src.objects if n in data["objects"]]
        dst.objects = list(wanted)
    added, renamed = [], {}
    for there, obj in zip(wanted, dst.objects):
        if obj is None:
            continue
        bpy.context.scene.collection.objects.link(obj)
        added.append(obj.name)
        if obj.name != there:
            renamed[there] = obj.name
    return {"added": added, "renamed": renamed}


def _stable_hash(key):
    """In place of Python's hash(), which gives another number in every Blender run."""
    return int.from_bytes(hashlib.blake2b(repr(key).encode("utf-8"), digest_size=8).digest(), "little", signed=True)


@contextlib.contextmanager
def _same_numbers():
    """While this is open, Blender's FBX exporter numbers what it writes by name alone. It makes those numbers with
    hash(), which Python seeds anew in every run, so the same model came out as other bytes after each restart of
    Blender, and whether Unity has this already could not be told from the file. The exporter's own hash() is back
    afterwards, with the numbers it had handed out, so an export the user starts is as it always was. With an
    exporter built another way nothing is done: the file is right all the same, only different in every run."""
    try:
        from io_scene_fbx import fbx_utils
        kept = dict(fbx_utils._keys_to_uuids), dict(fbx_utils._uuids_to_keys)
    except Exception:
        yield
        return
    fbx_utils._keys_to_uuids.clear()
    fbx_utils._uuids_to_keys.clear()
    fbx_utils.hash = _stable_hash  # the module's own name now comes before the built-in
    try:
        yield
    finally:
        vars(fbx_utils).pop("hash", None)
        for mine, theirs in zip(kept, (fbx_utils._keys_to_uuids, fbx_utils._uuids_to_keys)):
            theirs.clear()
            theirs.update(mine)


WRITTEN_AT = ((b"Year", 1970), (b"Month", 1), (b"Day", 1), (b"Hour", 0), (b"Minute", 0), (b"Second", 0), (b"Millisecond", 0))


def _without_time(path):
    """Take the time of writing out of an FBX (CreationTimeStamp, seven whole numbers near its start: nothing reads
    them), so that two exports of the same thing are the same file."""
    with open(path, "r+b") as f:
        head = bytearray(f.read(4096))
        at = head.find(b"CreationTimeStamp")
        if at < 0:
            return
        for field, value in WRITTEN_AT:
            key = bytes([len(field)]) + field + b"I"  # a node's name, then one int32
            found = head.find(key, at, at + 400)
            if found >= 0:
                head[found + len(key):found + len(key) + 4] = value.to_bytes(4, "little")
        f.seek(0)
        f.write(head)


def _fcurves(action):
    """An action's curves, whichever way this Blender keeps them (in layers since 4.4)."""
    try:
        return list(action.fcurves)
    except AttributeError:
        return [curve for layer in action.layers for strip in layer.strips for bag in strip.channelbags for curve in bag.fcurves]


def _takes(objects):
    """How the animation is to be cut into clips: (options for the exporter, the clips Unity is to keep or None for
    all that come).
      - One rig with actions and nothing else animated (a character): a clip for each of the rig's actions. The
        exporter also tries every other action of the file on the rig ("Rig|SomeoneElsesAction"); the list names the
        rig's own, by the action's name alone.
      - Several things animated, or things that move with no action of their own (a rigid body, a shape key): the
        scene's frame range as one clip, with everything in it as it is at each frame. Clip by clip per object, the
        exporter would give every action tried on every object, none of them the scene as it plays.
      - Nothing moves: no clip."""
    def acts(thing):
        return thing is not None and thing.animation_data is not None and thing.animation_data.action is not None

    rigs = [o for o in objects if o.type == "ARMATURE" and acts(o)]
    others = [o for o in objects if o.type != "ARMATURE" and acts(o)]
    if len(rigs) == 1 and not others:
        rig, bones = rigs[0], set(rigs[0].pose.bones.keys())

        def own(action):
            if action == rig.animation_data.action or any(strip.action == action for track in rig.animation_data.nla_tracks for strip in track.strips):
                return True
            posed = [c.data_path.split('"')[1] for c in _fcurves(action) if c.data_path.startswith('pose.bones["')]
            return bool(posed) and all(name in bones for name in posed)

        return {}, [{"take": f"{rig.name}|{action.name}", "name": action.name} for action in bpy.data.actions if own(action)]
    moved = rigs or others or any(getattr(o, "rigid_body", None) is not None and o.rigid_body.type == "ACTIVE" for o in objects) or \
        any(o.type == "MESH" and acts(o.data.shape_keys) for o in objects)
    return (dict(bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False) if moved else {}), None


@contextlib.contextmanager
def _realized(objects):
    """While this is open, a mesh whose Geometry Nodes put out instances has them as real geometry: an FBX holds no
    instances, and the mesh would arrive empty. One more modifier at the end of the stack does it, and is taken off
    again (nothing of it stays in the file, nor in its undo history)."""
    nodes = {o.name for o in objects if o.type == "MESH" and any(m.type == "NODES" for m in o.modifiers)}
    makers = set()
    if nodes and hasattr(bpy.types, "GeometryNodeRealizeInstances"):
        for instance in bpy.context.evaluated_depsgraph_get().object_instances:
            if instance.is_instance and instance.parent is not None and instance.parent.original.name in nodes:
                makers.add(instance.parent.original.name)
    if not makers:
        yield
        return
    group = bpy.data.node_groups.new(REALIZE, "GeometryNodeTree")
    added = []
    try:
        group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        taken, realize, given = (group.nodes.new(kind) for kind in ("NodeGroupInput", "GeometryNodeRealizeInstances", "NodeGroupOutput"))
        group.links.new(taken.outputs[0], realize.inputs[0])
        group.links.new(realize.outputs[0], given.inputs[0])
        for obj in objects:
            if obj.name in makers:
                modifier = obj.modifiers.new(REALIZE, "NODES")
                modifier.node_group = group
                added.append((obj, modifier.name))
        bpy.context.view_layer.update()
        yield
    finally:
        for obj, name in added:
            modifier = obj.modifiers.get(name)
            if modifier is not None:
                obj.modifiers.remove(modifier)
        bpy.data.node_groups.remove(group)


def _shape(objects):
    """Where each object is and how far each mesh reaches, as the scene is now, in Unity's axes (Blender's x, y, z are
    Unity's -x, z, -y): what Unity holds the model it imported against (ch_import_model's shape_check), so that a
    model that arrives turned, moved or scaled is told of instead of being found by eye. A mesh's reach is that of
    the corners of the box around it in its own space, which Unity can work out the same way without reading
    vertices. That box is measured from the mesh as it is exported (modifiers applied), not read from
    Object.bound_box: in an open window that can be the box of the mesh before subdivision (GPU subdivision), which
    the exported mesh does not fill. A mesh that a rig or a shape key bends is checked by where it is only: its
    shape depends on the pose."""
    import numpy
    from mathutils import Vector

    def unity(v):
        return [round(-v[0], 5), round(v[2], 5), round(-v[1], 5)]

    def box(there):
        """The corners of the box around the mesh's vertices, in the object's own space (None: no vertices)."""
        mesh = there.to_mesh()
        try:
            if not len(mesh.vertices):
                return None
            at = numpy.empty(len(mesh.vertices) * 3, dtype=numpy.float32)
            mesh.vertices.foreach_get("co", at)
            low, high = at.reshape(-1, 3).min(axis=0), at.reshape(-1, 3).max(axis=0)
            return [Vector((float(x), float(y), float(z))) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
        finally:
            there.to_mesh_clear()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    parts = {}
    for obj in objects:
        there = obj.evaluated_get(depsgraph)
        world = there.matrix_world
        part = parts[obj.name] = {"at": unity(world.translation)}
        bent = obj.type == "MESH" and (obj.data.shape_keys is not None or any(m.type == "ARMATURE" for m in obj.modifiers))
        around = box(there) if obj.type == "MESH" and not bent else None
        if around:
            corners = [unity(world @ corner) for corner in around]
            part["low"] = [min(c[i] for c in corners) for i in range(3)]
            part["high"] = [max(c[i] for c in corners) for i in range(3)]
    return {"parts": parts}


def _write_fbx(objects, path):
    """Write the objects as an FBX for Unity, and return what Unity is to check the model against (_shape).
    The exporter turns nothing: the file only says that its axes are
    Blender's own (-Y forward, Z up), and Unity bakes the conversion into the data when it imports (ch_import_model
    does, for a manifest that says "axes": "blender"). That way no part arrives with a quarter turn of its own, however
    deep it sits and whether it is animated or skinned; the exporter's own "apply transform" breaks from the third
    level down. No bones are added at the ends. Images do not go inside: the materials and their images go next to the
    file (_materials). Nothing in the scene is touched: the exporter is handed what to take instead of having it
    selected, so the selection, the active object and the mode stay as they are (also in the middle of editing a
    mesh). The same objects give the same file whenever and in whichever Blender run they are written (_same_numbers,
    _without_time): the file itself tells whether Unity has this already. One exception is a mesh with a Bevel
    modifier, whose UVs Blender works out a little differently every time."""
    layer = bpy.context.view_layer
    missing = [o.name for o in objects if o.name not in layer.objects]
    if missing:
        raise ValueError("지금 뷰 레이어에 없는 오브젝트는 내보낼 수 없습니다: " + ", ".join(missing))
    options = dict(filepath=path, use_selection=True, apply_scale_options="FBX_SCALE_ALL", axis_forward="-Y", axis_up="Z",
                   use_space_transform=False, add_leaf_bones=False, path_mode="STRIP", embed_textures=False, **_takes(objects)[0])
    active = layer.objects.active
    mode = active.mode if active is not None else "OBJECT"
    shape = None
    with _same_numbers(), _realized(objects):
        shape = _shape(objects)
        if hasattr(bpy.context, "temp_override"):
            with bpy.context.temp_override(selected_objects=list(objects)):
                bpy.ops.export_scene.fbx(**options)
        else:  # Blender before 3.2: select them, and put the selection back
            selected = {o.name for o in layer.objects if o.select_get()}
            for obj in layer.objects:
                obj.select_set(obj in objects)
            try:
                bpy.ops.export_scene.fbx(**options)
            finally:
                for obj in layer.objects:
                    obj.select_set(obj.name in selected)
    if active is not None and layer.objects.active == active and active.mode != mode:
        bpy.ops.object.mode_set(mode=mode)  # an older exporter leaves the mesh that was being edited in Object Mode
    _without_time(path)
    return shape


# What a material is made of does not get to Unity inside an FBX: Unity leaves the images in there alone, and reads
# neither metallic nor roughness. So it is said next to the file, in the manifest: for each material the values and
# images its Principled BSDF reads, in the terms every engine's standard material has (what glTF calls metallic-
# roughness). Unity's ch_import_model makes its own kind of material of that. Only what is read straight from a value
# or an image goes; an input fed through other nodes is named in the notes, for the user to know.


def _link(socket):
    """The link that feeds this input, looking through reroutes (None: nothing does)."""
    while socket is not None and socket.is_linked:
        link = socket.links[0]
        if link.from_node.type != "REROUTE":
            return link
        socket = link.from_node.inputs[0]
    return None


def _reads(socket):
    """What a shader input reads: ("value", its own value), ("image", the Image Texture node, channel: "" for its
    colour, else one of "RGBA") or ("node", the node in between, which is more than can be said in a manifest)."""
    link = _link(socket)
    if link is None:
        value = socket.default_value
        return ("value", float(value) if isinstance(value, (int, float)) else [float(c) for c in value])
    node, out = link.from_node, link.from_socket
    if node.type == "TEX_IMAGE" and node.image is not None:
        return ("image", node, "A" if out.identifier == "Alpha" else "")
    if node.type in ("SEPARATE_COLOR", "SEPRGB") and getattr(node, "mode", "RGB") == "RGB":
        inner = _link(node.inputs[0])
        if inner is not None and inner.from_node.type == "TEX_IMAGE" and inner.from_node.image is not None and inner.from_socket.identifier != "Alpha":
            return ("image", inner.from_node, "RGB"[min(list(node.outputs).index(out), 2)])
    return ("node", node)


def _image_stamp(image):
    """What tells one state of an image from another without reading its pixels."""
    path = bpy.path.abspath(image.filepath, library=image.library) if image.filepath else ""
    try:
        stat = os.stat(path)
        on_disk = [stat.st_mtime_ns, stat.st_size]
    except OSError:
        on_disk = [0, 0]
    return [image.name, path.replace("\\", "/"), *on_disk, bool(image.is_dirty), image.packed_file.size if image.packed_file else 0, *image.size]


def _pixels(image):
    """The image as rows of RGBA numbers (height, width, 4), as they are stored."""
    import numpy

    width, height = image.size
    flat = numpy.empty(width * height * image.channels, dtype=numpy.float32)
    image.pixels.foreach_get(flat)
    flat = flat.reshape(height, width, image.channels)
    if image.channels == 4:
        return flat
    full = numpy.ones((height, width, 4), dtype=numpy.float32)
    full[:, :, :3] = flat[:, :, :3] if image.channels == 3 else flat[:, :, :1]
    return full


def _save_pixels(pixels, target, like=None):
    """Write rows of RGBA numbers as a .png (or .exr for an image that holds more than 8 bits)."""
    height, width = pixels.shape[:2]
    made = bpy.data.images.new("claude_hand_texture", width, height, alpha=True, float_buffer=target.endswith(".exr"))
    try:
        if like is not None:
            try:
                made.colorspace_settings.name = like.colorspace_settings.name
            except TypeError:
                pass
        made.pixels.foreach_set(pixels.ravel())
        made.filepath_raw = target
        made.file_format = "OPEN_EXR" if target.endswith(".exr") else "PNG"
        made.save()
    finally:
        bpy.data.images.remove(made)


def _file_name(stem, extension, files):
    """A file name that none of the textures written so far has."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "image"
    taken = {entry["file"].lower() for entry in files.values() if entry}
    name, n = stem + extension, 2
    while name.lower() in taken:
        name, n = f"{stem}_{n}{extension}", n + 1
    return name


def _texture(image, folder, files):
    """The image as a file for Unity: {"file", "stamp"}, or None when it holds nothing (its file is gone). A file on
    disk goes as it is; an image that is packed into the .blend, was made here and never saved, or was painted on
    since, is written from what Blender holds. With `folder` the file is written there; without, only named."""
    if image.name in files:
        return files[image.name]
    if not image.size[0] or not image.size[1]:
        files[image.name] = None
        return None
    source = bpy.path.abspath(image.filepath, library=image.library) if image.filepath else ""
    extension = os.path.splitext(source)[1].lower()
    as_it_is = image.source == "FILE" and not image.is_dirty and extension in TEXTURE_FORMATS and \
        (image.packed_file is not None or os.path.isfile(source))
    name = _file_name(os.path.splitext(image.name)[0], extension if as_it_is else ".exr" if image.is_float else ".png", files)
    entry = files[image.name] = {"file": name, "stamp": _image_stamp(image)}
    if folder:
        target = os.path.join(folder, name)
        if as_it_is and image.packed_file is not None:
            with open(target, "wb") as f:
                f.write(image.packed_file.data)
        elif as_it_is:
            shutil.copyfile(source, target)
        else:
            _save_pixels(_pixels(image), target, image)
    return entry


def _tree_stamp(tree, seen=None):
    """A node tree as a short hash that changes when anything that decides its look does: which nodes there are, how
    each is set, what feeds what. Not where a node sits or how it is labelled. It stands in for an image that is
    baked from the nodes: whether Unity has that image already can be told without baking it again."""
    seen = set() if seen is None else seen
    if tree.name in seen:
        return ""
    seen.add(tree.name)
    told = []
    for node in sorted(tree.nodes, key=lambda n: n.name):
        said = [node.name, node.bl_idname]
        for prop in node.bl_rna.properties:
            key = prop.identifier
            if key in TREE_LAYOUT or key.startswith("bl_") or key.startswith("show_"):
                continue
            value = getattr(node, key, None)
            if prop.type in ("BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"):
                if isinstance(value, (set, frozenset)):
                    value = sorted(value)
                said.append(f"{key}={value if isinstance(value, (str, bool, int, float, list)) or value is None else tuple(value)}")
            elif isinstance(value, bpy.types.Image):
                said.append(f"{key}={_image_stamp(value)}")
            elif isinstance(value, bpy.types.NodeTree):
                said.append(f"{key}={_tree_stamp(value, seen)}")
            elif isinstance(value, bpy.types.ColorRamp):
                said.append(f"{key}={value.interpolation},{value.color_mode},{[(e.position, tuple(e.color)) for e in value.elements]}")
            elif isinstance(value, bpy.types.CurveMapping):
                said.append(f"{key}={[[tuple(p.location) for p in curve.points] for curve in value.curves]}")
            elif isinstance(value, bpy.types.ID):
                said.append(f"{key}={value.name}")
        for inlet in node.inputs:
            value = getattr(inlet, "default_value", None)
            if value is not None and not isinstance(value, (str, bool, int, float)):
                value = tuple(value)
            said.append(f"{inlet.identifier}:{value}:{[(link.from_node.name, link.from_socket.identifier) for link in inlet.links]}")
        told.append("|".join(said))
    return hashlib.sha256("\n".join(told).encode("utf-8")).hexdigest()[:16]


LATER = "오브젝트 모드가 아니라서 이번에는 굽지 않았습니다(Unity에 먼저 구운 이미지가 있으면 그것이 그대로 쓰입니다). 오브젝트 모드에서 저장하거나 보내면 구워집니다."


def _bakes_now():
    """Blender bakes in Object Mode only. In another mode a baked image is named but not written: Unity keeps the one
    it has, and the image's stamp says "pending", so that it is sent once the mode allows."""
    return bpy.context.mode == "OBJECT"


def _why_not_baked(users):
    """Why an input that is fed through nodes cannot be baked for these objects at all ("" when it can)."""
    if not users:
        return "이 재질을 쓰는 메시가 없어서"
    hidden = [o.name for o in users if o.hide_render or o.hide_viewport]
    if hidden:  # Blender bakes only what a render would show
        return f"{', '.join(hidden[:3])}이(가) 렌더나 뷰포트에서 꺼져 있어서(아웃라이너의 카메라 · 모니터 아이콘)"
    bare = [o.name for o in users if not o.data.uv_layers]
    if bare:
        return f"{', '.join(bare[:3])}에 UV 맵이 없어서"
    if not hasattr(bpy.context.scene, "cycles"):
        return "Cycles가 꺼져 있어서(굽는 데 씁니다)"
    return ""


def _bake_pixels(material, users, source, size, colour):
    """What the nodes behind one shader input come to, over the UV maps of the objects that use the material, as rows
    of RGBA numbers. `source` is the output socket that feeds the input; None bakes the surface normal instead (what a
    Bump node or a mix of normals comes to). Blender bakes it: the value goes through an Emission shader for the
    time of the bake, with Cycles. The selection and the active object are not touched; the render engine, the
    material's nodes and the other materials of those objects (each needs somewhere to bake to) are put back as
    they were."""
    scene, tree = bpy.context.scene, material.node_tree
    output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
    surface = output.inputs["Surface"].links[0].from_socket
    # What is active and selected is noted before any node is made here: a new node is selected as it is made, and
    # one that has been taken away again must not be written to (Blender dies of that, some of the time).
    active, chosen = tree.nodes.active, [n for n in tree.nodes if n.select]
    image = bpy.data.images.new("claude_hand_bake", size, size, alpha=False, is_data=not colour)
    spare = bpy.data.images.new("claude_hand_bake_spare", 4, 4)
    target = tree.nodes.new("ShaderNodeTexImage")
    target.image = image
    engine, samples = scene.render.engine, scene.cycles.samples
    others, emit = [], None
    try:
        for obj in users:
            for slot in obj.material_slots:
                other = slot.material
                if other is None or other == material or other.node_tree is None or any(other == o for o, _, _ in others):
                    continue
                was = other.node_tree.nodes.active
                node = other.node_tree.nodes.new("ShaderNodeTexImage")
                node.image = spare
                others.append((other, node, was))
                other.node_tree.nodes.active = node
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 4
        for node in tree.nodes:
            node.select = False
        target.select = True
        tree.nodes.active = target
        if source is not None:
            emit = tree.nodes.new("ShaderNodeEmission")
            tree.links.new(source, emit.inputs["Color"])
            tree.links.new(emit.outputs[0], output.inputs["Surface"])
        with bpy.context.temp_override(active_object=users[0], object=users[0], selected_objects=list(users)):
            bpy.ops.object.bake(type="EMIT" if source is not None else "NORMAL", use_clear=True)
        return _pixels(image).copy()
    finally:
        tree.links.new(surface, output.inputs["Surface"])
        if emit is not None:
            tree.nodes.remove(emit)
        tree.nodes.remove(target)
        for node in chosen:
            node.select = True
        tree.nodes.active = active
        for other, node, was in others:
            other.node_tree.nodes.remove(node)
            other.node_tree.nodes.active = was
        scene.cycles.samples = samples
        scene.render.engine = engine
        bpy.data.images.remove(image)
        bpy.data.images.remove(spare)


def _metallic_smoothness(material, metallic, roughness, folder, files, bake=None):
    """Metallic and roughness as the one image Unity's standard materials read them from: metallic in red, smoothness
    (one minus roughness) in alpha. Each of the two is ("image", node, channel), ("value", number) or ("baked", the
    shader input that is fed through nodes: baked for `bake`'s objects at its size)."""
    import numpy

    sources = [(kind[1].image, kind[2] or "R") if kind[0] == "image" else None for kind in (metallic, roughness)]
    key = "metallic-smoothness:" + material.name
    name = _file_name(material.name + "_MetallicSmoothness", ".png", files)
    baked = any(kind[0] == "baked" for kind in (metallic, roughness))
    stamp = [[_image_stamp(s[0]), s[1]] if s else [_tree_stamp(material.node_tree), bake["size"]] if kind[0] == "baked" else kind[1]
             for s, kind in zip(sources, (metallic, roughness))] + (["pending"] if baked and not _bakes_now() else [])
    entry = files[key] = {"file": name, "stamp": stamp, **({"baked": True, "later": not _bakes_now()} if baked else {})}
    if folder and not (baked and not _bakes_now()):
        sizes = [tuple(s[0].size) for s in sources if s] + [(bake["size"], bake["size"]) for kind in (metallic, roughness) if kind[0] == "baked"]
        width, height = (max(size[i] for size in sizes) for i in (0, 1))

        def plane(source, kind):
            if kind[0] == "value":
                return numpy.full((height, width), kind[1], dtype=numpy.float32)
            if kind[0] == "baked":
                rows = _bake_pixels(material, bake["users"], _link(kind[1]).from_socket, bake["size"], False)[:, :, 0]
            else:
                rows = _pixels(source[0])[:, :, "RGBA".index(source[1])]
            if rows.shape != (height, width):  # the two differ in size: the smaller one's nearest pixel
                rows = rows[(numpy.arange(height) * rows.shape[0] // height)[:, None], (numpy.arange(width) * rows.shape[1] // width)[None, :]]
            return rows

        packed = numpy.empty((height, width, 4), dtype=numpy.float32)
        packed[:, :, :3] = plane(sources[0], metallic)[:, :, None]
        packed[:, :, 3] = 1.0 - plane(sources[1], roughness)
        _save_pixels(packed, os.path.join(folder, name), next((s[0] for s in sources if s), None))
    return entry


def _laid_on_uv(node):
    """Whether an Image Texture node lays its picture over the UV map as it is, or through a Mapping node that only
    scales and shifts it: what a Unity material can do as well. Anything else (object or generated coordinates, a
    turn, other nodes in front) is a picture only Blender can lay that way."""
    def uv(link):
        return link is None or link.from_node.type == "UVMAP" or (link.from_node.type == "TEX_COORD" and link.from_socket.identifier == "UV")

    link = _link(node.inputs["Vector"])
    if uv(link):
        return True
    mapping = link.from_node
    return mapping.type == "MAPPING" and mapping.vector_type == "POINT" and not any(mapping.inputs[i].is_linked for i in ("Location", "Rotation", "Scale")) and \
        not any(abs(c) > 1e-6 for c in mapping.inputs["Rotation"].default_value) and uv(_link(mapping.inputs["Vector"]))


def _uv(node, notes, what):
    """How an Image Texture node's picture is laid over the UV map ({"scale", "offset"}; None: as it is). A picture
    that is laid another way (see _laid_on_uv) and was not baked goes laid on the UV map, and that is noted."""
    link = _link(node.inputs["Vector"])
    if not _laid_on_uv(node):
        notes.append(f"{what}: 이미지가 UV가 아닌 좌표({link.from_socket.name})에 놓여 있어서, UV 그대로 놓은 것으로 갔습니다." if link.from_node.type == "TEX_COORD"
                     else f"{what}: 이미지를 놓는 좌표가 '{link.from_node.bl_label}' 노드를 거쳐서, UV 그대로 놓은 것으로 갔습니다.")
        return None
    if link is None or link.from_node.type != "MAPPING":
        return None
    scale, offset = link.from_node.inputs["Scale"].default_value, link.from_node.inputs["Location"].default_value
    return {"scale": [float(scale[0]), float(scale[1])], "offset": [float(offset[0]), float(offset[1])]}


def _material(material, folder, files, bake=None):
    """One material for the manifest ("notes": what of it could not be carried). `bake`: {"users": the meshes that
    use it, "size"}: an input that is fed through other nodes (a noise, a colour ramp, a bump) is baked for those
    objects into an image of that size, which then goes like any image; None: such an input is not carried, and
    noted. A baked image is as the nodes are at the time: it does not move or change in Unity as nodes would."""
    described = {"name": material.name, "base_color": [float(c) for c in material.diffuse_color], "metallic": float(material.metallic),
                 "roughness": float(material.roughness), "two_sided": not material.use_backface_culling, "notes": []}
    notes = described["notes"]
    tree = material.node_tree if material.use_nodes else None
    if tree is None:
        return described
    output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    surface = _link(output.inputs["Surface"]) if output is not None else None
    shader = surface.from_node if surface is not None and surface.from_node.type == "BSDF_PRINCIPLED" else None
    if shader is None:
        notes.append("Principled BSDF 하나로 된 재질이 아니라서, 뷰포트에 보이는 색 · 금속 · 거칠기 값만 갔습니다.")
        return described

    def through(what, read):
        notes.append(f"{what}: '{read[1].bl_label}' 노드를 거쳐서 못 옮겼습니다.")

    def unbakeable(what, read):
        """Notes why this node-fed input is not baked, when it is not."""
        why = "굽기가 꺼져 있어서" if bake is None else _why_not_baked(bake["users"])
        if why:
            notes.append(f"{what}: '{read[1].bl_label}' 노드를 거치는데 {why} 못 옮겼습니다.")
        return bool(why)

    def cooked(what, role, source, read, colour):
        """The input as an image baked from the nodes that feed it ({"file", "stamp", "baked"}); None, with a note why,
        when that cannot be. `source`: the socket that feeds it (None: the surface normal)."""
        if unbakeable(what, read):
            return None
        key = f"baked:{material.name}:{role}"
        entry = files[key] = {"file": _file_name(f"{material.name}_{role}", ".png", files), "baked": True,
                              "stamp": [_tree_stamp(tree), bake["size"]] + ([] if _bakes_now() else ["pending"])}
        if folder and not _bakes_now():
            notes.append(f"{what}: {LATER}")
        elif folder:
            try:
                _save_pixels(_bake_pixels(material, bake["users"], source, bake["size"], colour), os.path.join(folder, entry["file"]))
            except RuntimeError as exc:
                files[key] = None
                notes.append(f"{what}: 굽지 못했습니다({(str(exc).strip().splitlines() or ['?'])[-1][:120]}).")
                return None
        return dict(entry)

    def picture(read, what):
        entry = _texture(read[1].image, folder, files)
        if entry is None:
            notes.append(f"{what}: 이미지 '{read[1].image.name}'의 파일이 없어서 못 옮겼습니다.")
            return None
        placed = _uv(read[1], notes, what)
        return {"file": entry["file"], "stamp": entry["stamp"], **({"uv": placed} if placed else {}),
                **({"filter": "point"} if read[1].interpolation == "Closest" else {})}  # pixels kept as squares (pixel art)

    def skewed(read):
        """An image that is not laid on the UV map, where it can be baked: then it is, like any input fed through nodes."""
        return read[0] == "image" and bake is not None and not _laid_on_uv(read[1]) and not _why_not_baked(bake["users"])

    base = _reads(shader.inputs["Base Color"])
    if base[0] == "value":
        described["base_color"] = base[1]
    else:
        if base[0] == "image" and not base[2] and not skewed(base):
            described["base_color_texture"] = picture(base, "기본 색")
        else:
            feed = _link(shader.inputs["Base Color"])
            described["base_color_texture"] = cooked("기본 색", "BaseColor", feed.from_socket, ("node", feed.from_node), True)
        if described["base_color_texture"] is not None:  # else the viewport colour stands in
            described["base_color"] = [1.0, 1.0, 1.0, 1.0]

    pair = []
    for inlet, key, what in (("Metallic", "metallic", "금속"), ("Roughness", "roughness", "거칠기")):
        read = _reads(shader.inputs[inlet])
        if read[0] == "value":
            described[key] = read[1]
        elif skewed(read):
            read = ("baked", shader.inputs[inlet])
        elif read[0] == "node":
            read = ("value", described[key]) if unbakeable(what, read) else ("baked", shader.inputs[inlet])
        elif not read[1].image.size[0]:
            notes.append(f"{what}: 이미지 '{read[1].image.name}'의 파일이 없어서 못 옮겼습니다.")
            read = ("value", described[key])
        pair.append(read)
    if any(read[0] != "value" for read in pair):
        try:
            entry = _metallic_smoothness(material, pair[0], pair[1], folder, files, bake)
            if folder and entry.get("later"):
                notes.append(f"금속 · 거칠기: {LATER}")
            described["metallic_smoothness_texture"] = {key: value for key, value in entry.items() if key in ("file", "stamp", "baked")}
            described["metallic"], described["roughness"] = (1.0 if read[0] != "value" else read[1] for read in pair)
        except RuntimeError as exc:
            files["metallic-smoothness:" + material.name] = None
            notes.append(f"금속 · 거칠기: 굽지 못했습니다({(str(exc).strip().splitlines() or ['?'])[-1][:120]}).")

    normal = _link(shader.inputs["Normal"])
    if normal is not None:
        read = _reads(normal.from_node.inputs["Color"]) if normal.from_node.type == "NORMAL_MAP" else None
        if read is not None and read[0] == "image" and not read[2] and not normal.from_node.inputs["Strength"].is_linked and not skewed(read):
            described["normal_texture"] = picture(read, "노멀")
            described["normal_strength"] = float(normal.from_node.inputs["Strength"].default_value)
        else:  # a bump, a mix of normal maps: the normal the surface ends up with
            described["normal_texture"] = cooked("노멀", "Normal", None, ("node", normal.from_node), False)
            if described["normal_texture"] is not None:
                described["normal_strength"] = 1.0

    colour, strength = shader.inputs.get("Emission Color") or shader.inputs.get("Emission"), shader.inputs.get("Emission Strength")
    power = _reads(strength) if strength is not None else ("value", 1.0)
    glow = _reads(colour) if colour is not None else ("value", [0.0, 0.0, 0.0, 1.0])
    if power[0] != "value":
        through("발광 세기", power if power[0] == "node" else ("node", _link(strength).from_node))
    elif power[1] > 0 and (glow[0] != "value" or any(c > 0 for c in glow[1][:3])):
        lit = None
        if glow[0] == "image" and not glow[2] and not skewed(glow):
            lit = picture(glow, "발광")
        elif glow[0] != "value":
            feed = _link(colour)
            lit = cooked("발광", "Emission", feed.from_socket, ("node", feed.from_node), True)
        if glow[0] == "value" or lit is not None:
            described["emission_strength"] = power[1]
            described["emission_color"] = glow[1][:3] if glow[0] == "value" else [1.0, 1.0, 1.0]
            if glow[0] != "value":
                described["emission_texture"] = lit

    alpha = _reads(shader.inputs["Alpha"])
    cut = getattr(material, "blend_method", "BLEND") in ("CLIP", "HASHED") or getattr(material, "surface_render_method", "") == "DITHERED"
    if alpha[0] == "value" and alpha[1] < 1.0:
        described["alpha"] = alpha[1]
        described["blend"] = "clip" if cut and getattr(material, "blend_method", "") == "CLIP" else "blend"
    elif alpha[0] == "image" and alpha[2] == "A" and base[0] == "image" and alpha[1].image == base[1].image and \
            not (described.get("base_color_texture") or {"baked": True}).get("baked"):  # a baked picture has no alpha of its own
        described["alpha_from_base_color_texture"] = True
        described["blend"] = "clip" if cut else "blend"
    elif alpha[0] != "value":
        through("투명도", alpha if alpha[0] == "node" else ("node", alpha[1]))
    if described.get("blend") == "clip":
        described["alpha_clip"] = float(getattr(material, "alpha_threshold", 0.5))
    return {key: value for key, value in described.items() if value is not None}


def _materials(objects, folder=None, bake=False, size=BAKE_SIZE):
    """(the manifest's "materials", the notes on what could not be carried, a hash of the lot). With `folder` the
    image files are written there; without, they are only named, which is enough to tell whether anything changed
    since Unity got them (the hash covers each image's file time and size, not its pixels: an image that was painted
    on and not saved yet counts as changed once; and for an image baked from nodes, the nodes as they are set).
    `bake`: inputs that are fed through other nodes are baked into images `size` pixels a side (see _material)."""
    names, users = [], {}
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            if slot.material is None:
                continue
            if slot.material.name not in names:
                names.append(slot.material.name)
            if obj.type == "MESH" and obj not in users.setdefault(slot.material.name, []):
                users[slot.material.name].append(obj)
    if folder:
        os.makedirs(folder, exist_ok=True)
    files, found, notes = {}, [], []
    for name in names:
        described = _material(bpy.data.materials[name], folder, files, {"users": users.get(name, []), "size": size} if bake else None)
        notes += [f"{name}: {note}" for note in described.pop("notes")]
        found.append(described)
    return found, notes, hashlib.sha256(json.dumps(found, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _beside(objects, transfer_id, write, humanoid=False, bake=True):
    """What the manifest says besides the model file: how the file's axes lie, the materials, where their images are,
    which of those images were baked from nodes, and whether the rig is a person's (Unity's Humanoid). `write`: also
    write the images (and bake the ones that are to be baked: without it they are only named)."""
    folder = os.path.join(exchange_dir(), transfer_id + ".textures")
    materials, notes, digest = _materials(objects, folder if write else None, bake)
    clips = _takes(objects)[1]
    # Baked this time: one that waits for Object Mode ("pending") is among the notes instead, and is not said to be baked.
    baked = [f"{material['name']}: {what}" for material in materials for key, what in BAKED.items()
             if (material.get(key) or {}).get("baked") and (material[key].get("stamp") or [None])[-1] != "pending"]
    return {"axes": "blender", "materials": materials, "materials_hash": digest, "textures_dir": transfer_id + ".textures",
            "not_carried": notes, "baked": baked, **({"rig": "humanoid"} if humanoid else {}), **({"clips": clips} if clips is not None else {})}


def sent_as(obj):
    """The name this object went to Unity under ("" when it never did)."""
    value = obj.get(SENT_AS)
    return value if isinstance(value, str) else ""


def object_for(key):
    """The object Unity knows as `key`: the one that was sent under that name, whatever it is called by now, so
    renaming an object does not cut it off from its model. A copy of it carries the mark too: then it is the one that
    still has the name, and when neither has, none (which was meant cannot be told: the panel asks). An object from
    before there were marks is found by its name."""
    marked = [o for o in bpy.data.objects if sent_as(o) == key]
    if len(marked) == 1:
        return marked[0]
    named = bpy.data.objects.get(key)
    if marked:
        return named if named is not None and named in marked else None
    return named


def key_of(obj):
    """The name Unity knows this object's model by: the one it was sent under while that is still this object's (a
    copy of it is another thing), else its own name, or that with a number when an object that was renamed since
    still goes by it."""
    key = sent_as(obj)
    if key and object_for(key) == obj:
        return key
    key, n = obj.name, 2
    while any(sent_as(other) == key for other in bpy.data.objects if other != obj):
        key, n = f"{obj.name}#{n}", n + 1
    return key


def _mark_sent(obj):
    key = key_of(obj)
    if sent_as(obj) != key:
        obj[SENT_AS] = key
    return key


def export_fbx(roots, name="model", humanoid=False, bake=True):
    """Export the named objects and their children as an FBX in the exchange folder, for Unity, with what their
    materials are made of next to it (values and image files; see _materials). Returns {"manifest": path for Unity's
    ch_import_model, "objects": names exported, "not_carried": what of the materials could not go, "baked": which
    material inputs went as an image baked from their nodes; both for the user to know}. `humanoid`: the rig is a
    person's, and Unity is to set it up as Humanoid. `bake=False`: inputs fed through nodes are not baked (and so
    not carried). The selection, the active object
    and the mode are left as they are. Sending the same objects of the same saved .blend again makes Unity replace the
    model it already has in place, also after they were renamed here: each object sent keeps the name it went under
    as a custom property (claude_hand_sent_as). From a file that was never saved Unity cannot tell, and adds a new
    model."""
    roots, objects = _with_children(roots)
    name, transfer_id = _transfer_id(name)
    shape = _write_fbx(objects, os.path.join(exchange_dir(), transfer_id + ".fbx"))
    keys = [_mark_sent(bpy.data.objects[root]) for root in roots]
    look = _beside(objects, transfer_id, True, humanoid, bake)
    manifest = _write_manifest(name, transfer_id, {"format": "claude-hand/model@1", "model_file": transfer_id + ".fbx", "roots": keys,
                                                   "objects": sorted(o.name for o in objects), "check": shape, **look})
    result = {"manifest": manifest, "objects": sorted(o.name for o in objects), "not_carried": look["not_carried"], "baked": look["baked"]}
    if not bpy.data.filepath:
        result["note"] = "이 .blend는 저장된 적이 없어, 같은 것을 다시 보내면 Unity에 새 모델이 하나 더 생깁니다. 저장한 뒤 보내면 제자리에서 바뀝니다."
    return result


def import_model(manifest):
    """Bring a model that Unity (ch_export_model) or another Blender (export_fbx) left in the exchange folder into the
    current scene: a claude-hand/model@1 manifest, or the .fbx / .obj / .glb file itself. Returns {"added": the names
    the new objects have here, "file": what was read}. The new objects end up selected, as after File > Import."""
    path = str(manifest).strip().strip('"')
    if path.lower().endswith(".claudehand.json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("format") != "claude-hand/model@1":
            raise ValueError(f"이 manifest는 '{data.get('format')}' 형식이라 모델로 받을 수 없습니다. "
                             "다른 Blender가 export_blend로 보낸 것은 import_blend로 받으세요.")
        path = os.path.join(os.path.dirname(path), data.get("model_file") or "")
    if not os.path.isfile(path):
        raise ValueError(f"모델 파일이 없습니다: {path}")
    extension = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if extension == ".fbx":
        # Blender 4.5 and later read FBX themselves (faster, and what File > Import uses); before that, the add-on.
        (bpy.ops.wm.fbx_import if "fbx_import" in dir(bpy.ops.wm) else bpy.ops.import_scene.fbx)(filepath=path)
    elif extension == ".obj":
        (bpy.ops.wm.obj_import if "obj_import" in dir(bpy.ops.wm) else bpy.ops.import_scene.obj)(filepath=path)
    elif extension in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        raise ValueError(f"'{extension}' 파일은 받을 수 없습니다. FBX, OBJ, GLB만 됩니다.")
    return {"added": sorted(o.name for o in bpy.data.objects if o not in before), "file": path.replace("\\", "/")}


# --------------------------------------------------------------------------- models that follow this file
#
# A model that went to Unity can be told there to follow this .blend (Claude Hand Tools' ch_model_follow keeps that
# with the model: the project that gets changed has the say). From then on this add-on sends it again by itself
# whenever the file is saved, straight to that Unity editor's own bridge. No Claude takes part: it costs no tokens
# and no minute of waiting, and it works with Claude Hand closed.
#
# One "look" does it all: ask every open Unity editor which of its models came from this file, export the ones that
# follow, and send those that are not what Unity has already. A look is due after a save, after 되돌리기, when a
# file is opened, when a Unity editor opens, and when the panel asks. Everything that touches bpy runs on the main
# thread (from tick); talking to Unity runs on a worker thread, and its answers come back through the inbox.


class UnityError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code, self.message = code, message


def unity_projects():
    """The Unity editors that are open with Claude Hand Tools new enough for models that follow their .blend, as
    their bridges register them in ~/.claude-hand/unity (ChBridge.cs): [{"name", "hash", "port", "token", "pid"}]."""
    home = os.environ.get("CLAUDE_HAND_HOME") or os.path.join(os.path.expanduser("~"), ".claude-hand")
    folder, found = os.path.join(home, "unity"), []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return found
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("format") == "claude-hand/unity-bridge@1" and \
                any(isinstance(tool, dict) and tool.get("name") == FOLLOW_TOOL for tool in entry.get("tools") or []):
            found.append(entry)
    return found


def _unity_once(entry, tool, params, timeout):
    payload = json.dumps({"token": entry.get("token", ""), "tool": tool, "params": params, "timeout": int(max(5, timeout - 5))}) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", int(entry["port"])), 1.0) as sock:
            sock.sendall(payload.encode("utf-8"))
            sock.settimeout(timeout)
            buffer = b""
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
        reply = json.loads(buffer.decode("utf-8") or "{}")
    except (OSError, KeyError, ValueError) as exc:
        raise UnityError("UNREACHABLE", str(exc)) from exc
    if not reply.get("ok"):
        error = reply.get("error") or {}
        raise UnityError(error.get("code", "FAILED"), error.get("message", ""))
    result = reply.get("result") or {}
    if result.get("success") is False:  # the tool itself said no
        raise UnityError(result.get("code", "FAILED"), (result.get("data") or {}).get("message", ""))
    return result.get("data") or {}


def unity_call(entry, tool, params, timeout=30.0, tries=1):
    """One call to a Unity editor's own bridge: a line of JSON in, a line out (as tools/unity_bridge.py does it for
    the subagents). Returns the tool's data, or raises UnityError. With tries, it waits for an editor that is
    compiling, and for one that reloads its scripts: its socket is away for a moment and comes back on another port,
    so the registry is read again. Any thread."""
    last = UnityError("UNREACHABLE", "")
    for attempt in range(tries):
        if attempt:
            time.sleep(BUSY_WAIT)
            entry = next((e for e in unity_projects() if e.get("hash") == entry.get("hash")), None)
            if entry is None:  # closed meanwhile
                break
        try:
            return _unity_once(entry, tool, params, timeout)
        except UnityError as exc:
            last = exc
            if exc.code not in ("EDITOR_BUSY", "UNREACHABLE", "UNAUTHORIZED"):
                raise
    raise last


def _why(exc):
    """A UnityError in the user's words."""
    return {"EDITOR_BUSY": "Unity가 컴파일 중이에요", "UNREACHABLE": "Unity가 응답하지 않아요",
            "TIMEOUT": "Unity가 제때 답하지 않았어요(대화상자가 떠 있을 수 있어요)",
            "CONFLICT": "그 프로젝트에서 Claude가 일하는 중이에요. 끝난 뒤 다시 해 주세요",
            "NOT_FOUND": "그 프로젝트의 Claude Hand Tools가 오래된 버전이에요"}.get(exc.code) or exc.message or exc.code


def _follow_due(reason):
    """A look is due. A save outranks the other reasons: it sends whatever state the file is in."""
    if _follow["due"] != "save":
        _follow["due"] = reason


def _same_path(one, other):
    return os.path.normcase(os.path.abspath(one)) == os.path.normcase(os.path.abspath(other)) if one and other else one == other


@bpy.app.handlers.persistent
def _on_saved(*args):
    """After a save. Saving a copy elsewhere (Save Copy) is not a save of this file; saving under another name is,
    and takes the models along: Unity is told with the next look that they belong to the new file."""
    path = bpy.data.filepath
    if args and isinstance(args[0], str) and args[0] and not _same_path(args[0], path):
        return
    if _follow["path"] and path and not _same_path(_follow["path"], path):
        _follow["moved_from"] = _follow["path"]
    _follow["path"] = path
    _follow_due("save")


@bpy.app.handlers.persistent
def _on_loaded(*_args):
    """Another file: nothing known of the one before holds, and what is saved in it may be news to Unity."""
    _follow.update(path=bpy.data.filepath, moved_from="", due=None, rows=[], seen=set(), models={}, status="")
    _touched.update(seq=0, objects={}, data={}, other=0)
    _follow_due("open")


@bpy.app.handlers.persistent
def _on_touched(scene, depsgraph):
    """What Blender says was changed (moved, edited, a modifier or material value set): kept as a running count, so
    a look can tell whether anything was done to a model since it was last found in step with Unity. Selecting and
    the like come with no such flag. Counted only while a model follows this file, and not what a look's own export
    makes Blender work out anew."""
    if not _follow["models"] or _touched["own"]:
        return
    updates = [u for u in depsgraph.updates if u.is_updated_geometry or u.is_updated_transform or u.is_updated_shading]
    material_named = any(type(u.id).__name__ == "Material" for u in updates)
    for update in updates:
        kind = type(update.id).__name__
        if kind == "ShaderNodeTree" and material_named and getattr(update.id, "is_embedded_data", False):
            continue  # a material's own nodes, told of along with the material: the material stands for them
        _touched["seq"] += 1
        if isinstance(update.id, bpy.types.Object):
            _touched["objects"][update.id.name] = _touched["seq"]
        elif kind in ("Mesh", "Material"):  # whose it is shows when a model is looked at (_made_of)
            _touched["data"][(kind, update.id.name)] = _touched["seq"]
        else:  # a node group, a shape key, an action: whose it is does not show here, so it counts for every model
            _touched["other"] = _touched["seq"]


def _follow_tick(now):
    """From tick: start the look that is due, and notice a Unity editor that was not open before."""
    if not _follow["path"]:
        _follow["path"] = bpy.data.filepath  # the file the add-on was turned on in
    if _follow["busy"]:
        return
    if now - _follow["looked"] >= UNITY_LOOK_SECONDS:
        _follow["looked"] = now
        editors = unity_projects()
        _follow["editors"] = [(str(e.get("name")), str(e.get("hash"))) for e in editors]
        open_now = {(e.get("hash"), e.get("pid")) for e in editors}
        if _follow["unity"] is None or open_now - _follow["unity"]:
            _follow_due("unity")
        _follow["unity"] = open_now
        if not editors and _follow["rows"]:
            _follow["rows"] = []
            _redraw()
    if _follow["due"]:
        reason, _follow["due"] = _follow["due"], None
        _start_look(reason)


WHY_LOOKED = {"save": "저장", "undo": "되돌리기", "open": "파일 열기", "unity": "Unity 열림", "look": "확인"}


def _start_look(reason):
    path, editors = bpy.data.filepath, unity_projects()
    _follow["editors"] = [(str(e.get("name")), str(e.get("hash"))) for e in editors]
    if not path or not editors:
        _follow["rows"] = []
        return
    # What is saved goes to Unity, or the state right after a 되돌리기. A file with changes that are not saved yet
    # is only listed when it is opened or a Unity opens: its next save sends.
    send = reason in ("save", "undo", "look") or not bpy.data.is_dirty
    _follow["busy"] += 1
    threading.Thread(target=_look, args=(editors, path, _follow["moved_from"], reason, send), daemon=True).start()


def _look(editors, path, moved_from, reason, send):
    """Worker thread: ask each open Unity editor for its models that came from Blender."""
    found = []
    for entry in editors:
        params = {"action": "list", "source": path, "from_panel": True}
        if moved_from:
            params["moved_from"] = moved_from
        try:
            found.append((entry, unity_call(entry, FOLLOW_TOOL, params, timeout=20).get("models") or [], None))
        except UnityError as exc:
            found.append((entry, [], exc))
    _inbox.put(("follow_listed", {"found": found, "path": path, "moved_from": moved_from, "reason": reason, "send": send}))


def _row(entry, model):
    """One model of a Unity project as the panel lists it, with the objects here that it is made of ("objects": their
    names, None when they cannot be told) and what stands in the way of sending it ("problem")."""
    row = {"project": str(entry.get("name")), "hash": str(entry.get("hash")), "asset": str(model.get("asset_path")),
           "name": str(model.get("name")), "roots": [str(key) for key in model.get("roots") or []], "follow": bool(model.get("follow")),
           "here": bool(model.get("here")), "source": str(model.get("source") or ""), "objects": [], "problem": "", "missing": ""}
    for key in row["roots"]:
        obj = object_for(key)
        if obj is None:
            twins = [o.name for o in bpy.data.objects if sent_as(o) == key]
            row.update(objects=None, missing=key, problem=(
                f"'{key}'(으)로 보낸 오브젝트가 둘이에요({', '.join(twins[:3])}). 이 모델로 갈 것을 고르고 [선택한 오브젝트로 잇기]를 누르세요."
                if twins else f"이 파일에 '{key}' 오브젝트가 없어요. 이름을 바꿨으면 그 오브젝트를 고르고 [선택한 오브젝트로 잇기]를 누르세요."))
            break
        row["objects"].append(obj.name)
    row["label"] = _names(row["objects"] or row["roots"])
    return row


def _names(names):
    """A model by the objects it is made of here, as the user knows it: "Chair", "Chair, Table", "Chair, Table 외 2개"."""
    return ", ".join(names[:2]) + (f" 외 {len(names) - 2}개" if len(names) > 2 else "")


def _snapshot(objects):
    """What Blender does not tell of when it changes: the names of the objects that go, and the image files their
    materials read (an image painted on or edited elsewhere, then saved)."""
    images = set()
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            tree = slot.material.node_tree if slot.material is not None else None
            for node in (tree.nodes if tree is not None else ()):
                image = getattr(node, "image", None)
                if image is None or not image.filepath:
                    continue
                path = bpy.path.abspath(image.filepath, library=image.library)
                try:
                    stat = os.stat(path)
                    images.add((path, stat.st_mtime_ns, stat.st_size))
                except OSError:
                    images.add((path, 0, 0))
    return tuple(sorted(o.name for o in objects)), tuple(sorted(images))


def _made_of(objects):
    """The meshes and materials these objects are made of, as _on_touched names them: a change to another model's
    material is not a change to this one (it used to count for every model, which sent a mesh with a Bevel again
    each time, and exported a heavy one for nothing)."""
    parts = set()
    for obj in objects:
        if obj.type == "MESH" and obj.data is not None:
            parts.add(("Mesh", obj.data.name))
        for slot in getattr(obj, "material_slots", ()):
            if slot.material is not None:
                parts.add(("Material", slot.material.name))
    return parts


def _file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _export_followed(row, objects, textures=False):
    """Export a model that follows this file, under a name of its own in the exchange folder (always the same name:
    the file is not kept, and its name gets into the FBX). Returns (manifest, the files written, the hash of its
    materials). The materials' images are written only with `textures`: to tell whether Unity has this already, the
    model file and that hash are enough."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(row["asset"]))[0]).strip("_") or "model"
    transfer_id = f"follow_{row['hash']}_{stem}"[:80]
    model = os.path.join(exchange_dir(), transfer_id + ".fbx")
    _touched["own"] = True
    try:
        shape = _write_fbx(objects, model)
        bpy.context.view_layer.update()  # what the export itself stirred up is told now, and passed over (_on_touched)
    finally:
        _touched["own"] = False
    look = _beside(objects, transfer_id, textures)
    manifest = _write_manifest(row["name"], transfer_id, {"format": "claude-hand/model@1", "model_file": transfer_id + ".fbx", "check": shape,
                                                         "roots": row["roots"], "objects": sorted(o.name for o in objects), **look})
    return manifest, [model, manifest, os.path.join(exchange_dir(), look["textures_dir"])], look["materials_hash"]


def _discard(files):
    for path in files:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass


def _compare(row, model, first_look):
    """Main thread: export a model that follows and say whether it is to be sent ({"row", "manifest", "files"}) or not
    (None). It is sent when the file that comes out is not the one Unity has (its hash is in the list). Two things
    keep that from sending what did not change:
      - A model whose export took long is not exported at all while Blender tells of nothing done to it.
      - A mesh with a Bevel modifier comes out differently every time. So when the file differs though nothing was
        done to the model, it is exported once more: two exports that differ from each other say nothing changed."""
    key = (row["hash"], row["asset"])
    known = _follow["models"].setdefault(key, {"seq": -1, "snapshot": None, "took": 0.0})
    _, objects = _with_children(row["objects"])
    snapshot, seq = _snapshot(objects), _touched["seq"]
    touched = first_look or known["seq"] < 0 or snapshot != known["snapshot"] or _touched["other"] > known["seq"] or \
        any(_touched["objects"].get(o.name, 0) > known["seq"] for o in objects) or \
        any(_touched["data"].get(part, 0) > known["seq"] for part in _made_of(objects))
    if not touched and known["took"] > HEAVY_SECONDS:
        return None
    started = time.perf_counter()
    manifest, files, look = _export_followed(row, objects)
    known["took"] = time.perf_counter() - started
    written = _file_hash(files[0])
    same_look = look == model.get("materials_hash", look)  # a Unity side from before 0.17 says nothing of materials
    if written == model.get("file_hash") and same_look:
        known.update(seq=seq, snapshot=snapshot)
        _discard(files)
        return None
    if not touched and same_look:
        _export_followed(row, objects)
        if _file_hash(files[0]) != written:  # never twice the same: what Unity has is as good as this
            _discard(files)
            return None
    _touched["own"] = True
    try:
        sent = _beside(objects, os.path.splitext(os.path.basename(files[0]))[0], True)  # now the images too
        bpy.context.view_layer.update()
    finally:
        _touched["own"] = False
    later = any(str(note).endswith(LATER) for note in sent["not_carried"])  # a bake that waits for Object Mode
    return {"row": row, "manifest": manifest, "files": files, "seq": seq, "snapshot": snapshot, "later": later}


def _follow_listed(payload):
    """Main thread: what the open Unity editors hold of this file. Lists it for the panel and, when this look sends,
    exports the models that follow and hands on the ones Unity does not have yet."""
    if not _same_path(payload["path"], bpy.data.filepath):  # another file was opened meanwhile
        _worker_over()
        return
    if payload["moved_from"] and _follow["moved_from"] == payload["moved_from"] and any(error is None for _, _, error in payload["found"]):
        _follow["moved_from"] = ""  # an open Unity knows now
    if payload["send"]:
        # What was changed just before (a script that edits and saves in one go) is told now, while it still counts:
        # a look's own export would take it for its own stirring.
        bpy.context.view_layer.update()
    rows, jobs, unreachable = [], [], []
    for entry, models, error in payload["found"]:
        if error is not None:
            unreachable.append(f"{entry.get('name')}: {_why(error)}")
        for model in models:
            row = _row(entry, model)
            if row["here"] or (row["objects"] and all(sent_as(bpy.data.objects[name]) for name in row["objects"])):
                rows.append(row)  # of this file, or of another one that this file's objects once went to (it was moved)
            if not (payload["send"] and row["here"] and row["follow"] and row["objects"]):
                continue
            key = (row["hash"], row["asset"])
            try:
                job = _compare(row, model, key not in _follow["seen"])
            except Exception as exc:  # an export that fails must not take the others along
                row["problem"] = f"내보내지 못했어요: {exc}"
                continue
            _follow["seen"].add(key)
            if job is not None:
                jobs.append(job)
    _follow["rows"] = rows
    _follow["status"] = "; ".join(unreachable)
    if jobs:
        threading.Thread(target=_send, args=(jobs, payload["reason"]), daemon=True).start()  # the look goes on
    else:
        _worker_over()
    _redraw()


def _send(jobs, reason):
    """Worker thread: hand each exported model to its Unity editor, which replaces the one it has in place."""
    results = []
    for job in jobs:
        entry = next((e for e in unity_projects() if e.get("hash") == job["row"]["hash"]), None)
        try:
            if entry is None:
                raise UnityError("UNREACHABLE", "")
            results.append((job, unity_call(entry, FOLLOW_TOOL, {"action": "update", "manifest": job["manifest"], "from_panel": True,
                                                                 "why": WHY_LOOKED.get(reason, "저장")}, timeout=120, tries=BUSY_TRIES), None))
        except UnityError as exc:
            results.append((job, {}, exc))
    _inbox.put(("follow_sent", {"results": results, "reason": reason}))


def _follow_sent(payload):
    """Main thread: how the sends went. A model Unity took is in step from now on; one it could not take is sent
    again with the next look."""
    why = WHY_LOOKED.get(payload["reason"], "저장")
    for job, data, error in payload["results"]:
        row, known = job["row"], _follow["models"].setdefault((job["row"]["hash"], job["row"]["asset"]), {"seq": -1, "snapshot": None, "took": 0.0})
        _discard(job["files"])
        state = data.get("state")
        if error is not None:
            known.update(seq=-1, snapshot=None)
            _tell(f"follow-failed:{row['hash']}:{row['asset']}",
                  f"{why} → {row['project']}: {row['label']}을(를) 보내지 못했어요({_why(error)}). 다음 저장 때 다시 보냅니다.")
        elif state == "updated":
            known.update(seq=job["seq"], snapshot=job["snapshot"])
            uses = int(data.get("instance_count") or 0)
            _tell(f"follow:{row['hash']}:{row['asset']}",
                  f"{why} → {row['project']}: {row['label']} 반영" + (f" (씬 오브젝트 {uses}개)" if uses else ""))
            check = data.get("shape_check") or {}
            if check.get("same") is False:  # Unity measured the model it imported, and it is not what was sent
                odd = [str(part.get("name")) for part in check.get("differing") or []] + [str(name) for name in check.get("missing") or []]
                _tell(f"follow-shape:{row['hash']}:{row['asset']}",
                      f"{row['project']}: {row['label']}이(가) Unity에 보낸 것과 다르게 들어갔어요({_names(odd)}). Claude에게 알려 주세요.")
            if _kept_there(data):  # else a material changed here and saved looks as if it had not arrived
                _tell(f"follow-kept:{row['hash']}:{row['asset']}", _kept_line(row["project"], _kept_there(data)))
            if job.get("later"):  # else nodes changed here and saved look as if they had not arrived
                _tell(f"follow-later:{row['hash']}:{row['asset']}",
                      f"{row['project']}: {row['label']}의 노드로 만든 재질은 오브젝트 모드에서 저장하면 구워서 갑니다(지금은 Unity에 있던 이미지 그대로).")
        elif state == "unchanged":
            known.update(seq=job["seq"], snapshot=job["snapshot"])
        elif state == "missing":
            _tell(f"follow-gone:{row['hash']}:{row['asset']}", f"{row['project']}에 {row['label']} 모델이 더 없어서 보내지 않았어요.")
    _worker_over()


def _kept_there(data):
    """The materials of a model that Unity left as they are, by its answer: they were edited there since they were made."""
    made = data.get("materials_made")
    return [str(name) for name in (made.get("kept") if isinstance(made, dict) else None) or []]


def _kept_line(project, names):
    return (f"{project}에서 고친 재질은 Unity 것 그대로 뒀어요({_names(names)}). Blender 재질대로 다시 만들려면 Unity에서 그 .mat 파일을 지우고 "
            "다시 보내세요.")


def _tell(key, text):
    """A line in the panel about something that happens again and again (a model sent because the file was saved).
    The same thing again changes the line it has instead of adding one, also when two models take turns: saving
    often must not fill the panel."""
    stamp = time.strftime("%H:%M")
    for entry in reversed(_entries):  # the lines of this kind at the end of the panel: is this one's among them?
        if entry.get("kind") != "notice" or not entry.get("key"):
            break
        if entry["key"] == key:
            entry["count"] = entry.get("count", 1) + 1
            entry["text"] = f"{text} · {entry['count']}번째 ({stamp})"
            _redraw()
            return
    _entries.append({"kind": "notice", "key": key, "text": f"{text} ({stamp})"})
    del _entries[:-KEPT_ENTRIES]
    _redraw()


def _follow_safely(handler, *args, **kwargs):
    """Run a step of a look. Blender drops a timer whose function raises, and this one is the chat panel's too: a
    look that goes wrong must cost only that look."""
    try:
        handler(*args, **kwargs)
    except Exception as exc:
        _worker_over()
        _follow["status"] = f"Unity로 보낸 모델을 살펴보다 오류가 났어요: {exc}"
        _redraw()


def _worker_over():
    """A worker thread's answer has been dealt with: the next look may start."""
    _follow["busy"] = max(0, _follow["busy"] - 1)


def _note_for_claude(note):
    """Goes along with the next message, once: what the user did here without Claude, which it would not know of."""
    if note and note not in _undo_note[0]:
        _undo_note[0] = "\n".join(filter(None, [_undo_note[0], note]))


def _from_panel(row, params, done):
    """Worker thread for a button of the panel: one call to the row's Unity editor, the outcome back through the inbox."""
    def run():
        entry = next((e for e in unity_projects() if e.get("hash") == row["hash"]), None)
        try:
            if entry is None:
                raise UnityError("UNREACHABLE", "")
            _inbox.put((done, {"row": row, "data": unity_call(entry, FOLLOW_TOOL, {**params, "from_panel": True}, timeout=120, tries=BUSY_TRIES),
                               "error": None}))
        except UnityError as exc:
            _inbox.put((done, {"row": row, "data": {}, "error": exc}))
    _follow["busy"] += 1
    threading.Thread(target=run, daemon=True).start()


def follow_set(row, on):
    """The panel's check box: this model follows the file from now on (and is sent right away), or no longer does."""
    _from_panel(row, {"action": "set", "asset_path": row["asset"], "follow": bool(on)}, "follow_set")


def follow_relink(row):
    """The panel's [이 파일로 잇기]: a model Unity has from another .blend (this file was moved, or renamed outside
    Blender) is this file's from now on."""
    _from_panel(row, {"action": "set", "asset_path": row["asset"], "source": bpy.data.filepath}, "follow_set")


def follow_adopt(key, obj):
    """The panel's [선택한 오브젝트로 잇기]: `obj` is the one Unity knows as `key` (it was renamed before it carried
    a mark, or a copy of it carries the same mark). No other object goes by that name any longer."""
    for other in bpy.data.objects:
        if other != obj and sent_as(other) == key:
            del other[SENT_AS]
    obj[SENT_AS] = key
    _follow_due("look")


def send_to_unity(roots, project):
    """The panel's [선택한 오브젝트 보내기]: send these objects (and their children) to an open Unity project
    (`project`: its hash) without Claude. Unity brings the model in, places it in the scene, sets an undo point of its
    own for it (its chat window's 되돌리기 takes it out again) and lets it follow this file. Returns "" when the send
    is under way, else why it cannot be."""
    if not bpy.data.filepath:
        return "먼저 이 .blend를 저장하세요. 저장된 파일이어야 Unity가 같은 모델인지 알고, 저장할 때 다시 보낼 수 있어요."
    entry = next((e for e in unity_projects() if e.get("hash") == project), None)
    if entry is None:
        return "그 Unity 프로젝트가 열려 있지 않아요."
    try:
        result = export_fbx(roots, roots[0])
    except Exception as exc:
        return f"내보내지 못했어요: {exc}"
    row = {"project": str(entry.get("name")), "hash": str(entry.get("hash")), "asset": "", "name": roots[0], "label": _names(roots)}
    _from_panel(row, {"action": "receive", "manifest": result["manifest"]}, "follow_received")
    return ""


def selected_roots():
    """What [선택한 오브젝트 보내기] sends: the selected objects that are not under another selected one (each goes
    with its children anyway), the active one first: it names the model."""
    layer = bpy.context.view_layer
    selected = [o for o in layer.objects if o.select_get()]
    names = {o.name for o in selected}

    def under_selected(obj):
        parent = obj.parent
        while parent is not None:
            if parent.name in names:
                return True
            parent = parent.parent
        return False
    roots = [o.name for o in selected if not under_selected(o)]
    active = layer.objects.active
    if active is not None and active.name in roots:
        roots.remove(active.name)
        roots.insert(0, active.name)
    return roots


def follow_label(row):
    """A model in the panel's list, by its objects here: "Chair → hand_test", and for one that Unity has from another
    file, which."""
    text = f"{row['label']} → {row['project']}"
    if not row["here"]:
        text += f" · {os.path.basename(row['source']) or '다른 파일'}에서 온 것으로 적혀 있음"
    return text


def _follow_done(payload, received=False):
    """Main thread: a button of the panel did its work in Unity (or did not)."""
    row, data, error = payload["row"], payload["data"], payload["error"]
    _worker_over()
    if error is not None:
        _notice(f"{row['project']}: {_why(error)}")
        return
    if received:
        asset = str(data.get("asset_path") or "")
        _notice(f"{row['project']}에 {row['label']}을(를) 보냈어요" + ("(있던 모델을 바꿈)" if data.get("updated") else "(씬에 놓음)") +
                ". 저장할 때마다 다시 갑니다. Unity 채팅창의 되돌리기로 뺄 수 있어요.")
        # No Claude reads this answer: what a subagent would report of it is said here.
        baked, lost, check = data.get("baked") or [], data.get("not_carried") or [], data.get("shape_check") or {}
        if baked:
            _notice("노드로 만든 재질 입력은 텍스처로 구워 보냈어요(그때 모습으로 굳은 이미지): " + ", ".join(str(b) for b in baked[:6]) +
                    (f" 외 {len(baked) - 6}개" if len(baked) > 6 else ""))
        for line in lost[:6]:
            _notice("못 옮긴 것 · " + str(line))
        if _kept_there(data):
            _notice(_kept_line(row["project"], _kept_there(data)))
        if check.get("same") is False:
            odd = [str(part.get("name")) for part in check.get("differing") or []] + [str(name) for name in check.get("missing") or []]
            _notice(f"Unity에 보낸 것과 다르게 들어갔어요({_names(odd)}). Claude에게 알려 주세요.")
        _note_for_claude(f"[사용자가 Blender 패널에서 '{row['label']}'을(를) Unity {row['project']}에 직접 보냈음: {asset} "
                         "(Claude를 거치지 않음, 저장할 때마다 다시 감)]")
    _follow_due("look")


def apply_event(event, payload):
    if event == "status":
        _status[0] = payload if payload is not None else (STARTING_MESSAGE if starting() else describe_missing_server())
    elif event == "missing":
        _server_missing[0] = bool(payload)
        if not payload:
            _start_requested[0] = 0.0
            if _pending[0] is not None:
                text, context = _pending[0]
                _pending[0] = None
                post_message(text, context)
    elif event == "context_sent":
        _context_sent["busy"] = False
        if not (isinstance(payload, dict) and payload.get("ok")):
            _context_sent["server_pid"] = None  # try again on the next change
    elif event == "reset":
        _entries.clear()
        _progress[0] = None
        _others.clear()
        _offer[0] = None
    elif event == "progress":
        _progress[0] = (payload, time.time()) if payload else None
    elif event == "others":
        _others[:] = [str(line) for line in payload]
    elif event == "offer":
        _offer[0] = payload if isinstance(payload, dict) and payload.get("request") else None
    elif event == "undo_sent":
        if not payload.get("ok"):
            _notice("다른 창은 되돌리지 못했습니다: " + str(payload.get("error", "Claude Hand가 받지 않았습니다")))
    elif event == "undone_sent":
        pass  # telling the channel is a nicety
    elif event == "follow_listed":
        _follow_safely(_follow_listed, payload)
    elif event == "follow_sent":
        _follow_safely(_follow_sent, payload)
    elif event == "follow_set":
        _follow_safely(_follow_done, payload)
    elif event == "follow_received":
        _follow_safely(_follow_done, payload, received=True)
    elif event == "native_text":
        if not send_text(payload, bpy.context.window_manager.claude_hand_chat_context):  # the box is empty again by now
            _notice("보내지 못했습니다. 앞 메시지가 Claude Hand가 켜지기를 기다리는 중이니 잠시 뒤 다시 보내 주세요.")
    elif event == "stop_sent":
        if not payload.get("ok"):
            _status[0] = "중지를 보내지 못했습니다: " + str(payload.get("error", "알 수 없는 오류"))
    elif event == "sent":
        if not payload.get("ok"):
            _status[0] = "보내지 못했습니다: " + str(payload.get("error", "알 수 없는 오류"))
        elif payload.get("delivered") is False:
            _status[0] = "Claude 세션이 아직 연결 중입니다. 잠시 뒤 답이 옵니다."
    elif event == "items":
        now = time.time()
        for item in payload:
            if item.get("kind") == "permission_resolved":
                for entry in _entries:
                    if entry.get("kind") == "permission" and entry.get("request_id") == item.get("request_id"):
                        entry["behavior"] = item.get("behavior")
                continue
            if item.get("kind") == "reply" and item.get("origin") != "desktop":
                _last_user[0] = 0.0
            _entries.append(dict(item))
        del _entries[:-KEPT_ENTRIES]
        if any(wants_attention(item, now) for item in payload):
            flash_taskbar()


def tick():
    """Timer on Blender's main thread: refresh identity, apply worker results, redraw the sidebar."""
    window, pin = window_identity()
    with _identity_lock:
        _identity["window"], _identity["pin"] = window, pin
    changed = False
    while True:
        try:
            event, payload = _inbox.get_nowait()
        except queue.Empty:
            break
        apply_event(event, payload)
        changed = True
    try:
        _follow_tick(time.time())
    except Exception as exc:  # as _follow_safely: this timer is the chat panel's too
        _follow["status"] = f"Unity로 보낸 모델을 살펴보다 오류가 났어요: {exc}"
    report_context()
    if _progress[0] is not None and time.time() - _progress[0][1] > PROGRESS_STALE:
        _progress[0] = None
        changed = True
    busy = _progress[0] is not None  # its seconds count up
    if changed or busy or (_last_user[0] and time.time() - _last_user[0] > NO_REPLY_HINT_SECONDS):
        _redraw()
    return 0.5


def _redraw():
    for window_ in getattr(bpy.context.window_manager, "windows", []):
        for area in window_.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _on_input_update(self, context):
    """Enter in the message field sends it."""
    text = self.claude_hand_chat_input
    if text.strip():
        send_text(text, self.claude_hand_chat_context)
        bpy.app.timers.register(_clear_input, first_interval=0.01)


def _clear_input():
    wm = bpy.context.window_manager
    if wm.claude_hand_chat_input:
        wm.claude_hand_chat_input = ""
    return None


class CLAUDEHAND_OT_send(bpy.types.Operator):
    bl_idname = "claude_hand.chat_send"
    bl_label = "보내기"
    bl_description = "Send the message to this window's claude-hand subagent"

    def execute(self, context):
        wm = context.window_manager
        if send_text(wm.claude_hand_chat_input, wm.claude_hand_chat_context):
            wm.claude_hand_chat_input = ""
        return {"FINISHED"}


class CLAUDEHAND_OT_start(bpy.types.Operator):
    bl_idname = "claude_hand.start_supervisor"
    bl_label = "Claude Hand 시작"
    bl_description = "Open the Claude Hand supervisor window (start-supervisor)"

    def execute(self, context):
        start_supervisor()
        return {"FINISHED"}


class CLAUDEHAND_OT_permission(bpy.types.Operator):
    bl_idname = "claude_hand.chat_permission"
    bl_label = "권한 응답"
    bl_description = "Allow or deny the tool Claude asked to run"

    request_id: bpy.props.StringProperty()
    behavior: bpy.props.EnumProperty(items=[("allow", "허용", ""), ("deny", "거부", "")])

    def execute(self, context):
        post_in_background("/v1/permission", {"request_id": self.request_id, "behavior": self.behavior}, "permission_sent")
        return {"FINISHED"}


class CLAUDEHAND_OT_copy(bpy.types.Operator):
    bl_idname = "claude_hand.chat_copy"
    bl_label = "전체 복사"
    bl_description = "Copy the whole message to the clipboard (the panel shows only its start)"

    seq: bpy.props.IntProperty()

    def execute(self, context):
        entry = next((e for e in _entries if e.get("seq") == self.seq), None)
        if entry is None:
            return {"CANCELLED"}
        context.window_manager.clipboard = "\n\n".join(filter(None, [entry.get("request"), entry_text(entry)]))
        self.report({"INFO"}, "메시지 전체를 클립보드에 복사했습니다")
        return {"FINISHED"}


class CLAUDEHAND_OT_open_image(bpy.types.Operator):
    bl_idname = "claude_hand.chat_open_image"
    bl_label = "크게 보기"
    bl_description = "Open the result preview full size in the system's image viewer"

    path: bpy.props.StringProperty()

    def execute(self, context):
        if not os.path.exists(self.path):
            self.report({"WARNING"}, "그림 파일이 지워졌습니다")
            return {"CANCELLED"}
        if sys.platform == "win32":
            os.startfile(self.path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", self.path])
        return {"FINISHED"}


class CLAUDEHAND_OT_native_input(bpy.types.Operator):
    bl_idname = "claude_hand.chat_native_input"
    bl_label = "한글 입력창"
    bl_description = ("Type in a Windows text box: Blender's own field loses spaces and symbols with the Korean IME and "
                      "shows a syllable late. Enter sends to this chat, Shift+Enter starts a new line")

    @classmethod
    def poll(cls, context):
        return sys.platform == "win32"

    def execute(self, context):
        return {"FINISHED"} if open_native_input() else {"CANCELLED"}


class CLAUDEHAND_OT_undo(bpy.types.Operator):
    bl_idname = "claude_hand.chat_undo"
    bl_label = "되돌리기"
    bl_description = ("Undo everything changed in this Blender since Claude's last request started (Blender's own undo "
                      "history). A request asked here that changed other windows too is undone there as well")

    def invoke(self, context, event):
        mark, offer = undo_plan()
        if mark is None and offer is None:
            return {"CANCELLED"}
        if offer is None:
            message = f"'{describe_mark(mark)}' 이후 바뀐 것을 모두 되돌립니다. 그 뒤에 직접 고친 것도 함께 되돌아갑니다."
        else:
            others = ", ".join(str(w) for w in offer.get("windows") or [])
            message = f"'{offer.get('label') or '요청'}' 요청이 바꾼 것을 되돌립니다. " + (
                "이 Blender에서는 그 뒤에 직접 고친 것도 함께 되돌아갑니다. " if mark is not None else "") + (
                f"다른 창({others})은 그 창에 더 새 요청이 있거나 작업 중이면 건너뛰고, 그렇게 알려 줍니다.")
        try:
            return context.window_manager.invoke_confirm(self, event, title="Claude Hand 되돌리기", message=message,
                                                         confirm_text="되돌리기")
        except TypeError:  # Blender before 4.1: no title or message
            return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        # Not inside the operator: undo frees the data an operator still holds.
        bpy.app.timers.register(_undo_from_button, first_interval=0.0)
        return {"FINISHED"}


class CLAUDEHAND_OT_stop(bpy.types.Operator):
    bl_idname = "claude_hand.chat_stop"
    bl_label = "중지"
    bl_description = ("Stop the work this window's request started: the next Unity/Blender action of the subagents is "
                      "refused and Claude Hand is told. Nothing is undone")

    def execute(self, context):
        window, pin = window_identity()
        post_in_background("/v1/stop", {"app": "blender", "window": window, "pin": pin}, "stop_sent")
        return {"FINISHED"}


def _follow_row(index):
    return _follow["rows"][index] if 0 <= index < len(_follow["rows"]) else None


class CLAUDEHAND_OT_follow_toggle(bpy.types.Operator):
    bl_idname = "claude_hand.follow_toggle"
    bl_label = "저장할 때 다시 보내기"
    bl_description = ("Send this model to Unity again whenever this file is saved (Unity replaces it in place, without "
                      "Claude), or stop doing so")

    index: bpy.props.IntProperty()

    def execute(self, context):
        row = _follow_row(self.index)
        if row is None:
            return {"CANCELLED"}
        follow_set(row, not row["follow"])
        return {"FINISHED"}


class CLAUDEHAND_OT_follow_relink(bpy.types.Operator):
    bl_idname = "claude_hand.follow_relink"
    bl_label = "이 파일로 잇기"
    bl_description = ("Unity has this model from another .blend (this file was moved or renamed outside Blender): "
                      "make it this file's from now on")

    index: bpy.props.IntProperty()

    def execute(self, context):
        row = _follow_row(self.index)
        if row is None:
            return {"CANCELLED"}
        follow_relink(row)
        return {"FINISHED"}


class CLAUDEHAND_OT_follow_adopt(bpy.types.Operator):
    bl_idname = "claude_hand.follow_adopt"
    bl_label = "선택한 오브젝트로 잇기"
    bl_description = "The active object is the one this Unity model is made of (it was renamed, or a copy carries its mark)"

    index: bpy.props.IntProperty()

    def execute(self, context):
        row, obj = _follow_row(self.index), context.view_layer.objects.active
        if row is None or not row["missing"]:
            return {"CANCELLED"}
        if obj is None:
            self.report({"WARNING"}, "이 모델로 갈 오브젝트를 먼저 고르세요")
            return {"CANCELLED"}
        follow_adopt(row["missing"], obj)
        return {"FINISHED"}


class CLAUDEHAND_OT_follow_send(bpy.types.Operator):
    bl_idname = "claude_hand.follow_send"
    bl_label = "선택한 오브젝트 보내기"
    bl_description = ("Send the selected objects to this Unity project now, without Claude: the model is brought in, "
                      "placed in the scene, and follows this file from then on. Unity's chat window can undo it")

    project: bpy.props.StringProperty()

    def execute(self, context):
        roots = selected_roots()
        problem = send_to_unity(roots, self.project) if roots else "보낼 오브젝트를 먼저 고르세요."
        if problem:
            self.report({"WARNING"}, problem)
            return {"CANCELLED"}
        return {"FINISHED"}


class CLAUDEHAND_OT_follow_look(bpy.types.Operator):
    bl_idname = "claude_hand.follow_look"
    bl_label = "새로 고침"
    bl_description = "Ask the open Unity projects again which models came from this file, and send the ones that follow it"

    def execute(self, context):
        _follow_due("look")
        return {"FINISHED"}


def draw_follow(layout, width):
    """The models the open Unity projects have from this file, a check box each: checked, the model is sent again
    whenever the file is saved. What stands in a model's way is said in words under it, with the button that sets it
    right."""
    if not _follow["editors"]:
        for line in wrap("열려 있는 Unity가 없습니다(Claude Hand Tools 0.16 이상이 든 프로젝트).", width):
            layout.label(text=line)
        return
    rows = _follow["rows"]
    col = layout.column(align=True)
    if not bpy.data.filepath:
        for line in wrap("저장한 적 없는 파일입니다. 저장하면 여기서 Unity로 보낼 수 있어요.", width):
            col.label(text=line)
    elif not rows:
        col.label(text="이 파일에서 Unity로 간 모델이 없습니다.")
    for index, row in enumerate(rows):
        if row["here"]:
            col.operator(CLAUDEHAND_OT_follow_toggle.bl_idname, text=follow_label(row), emboss=False,
                         icon="CHECKBOX_HLT" if row["follow"] else "CHECKBOX_DEHLT").index = index
        else:
            for i, line in enumerate(wrap(follow_label(row), width)[:3]):
                col.label(text=line, icon="DOT" if i == 0 else "BLANK1")
            col.operator(CLAUDEHAND_OT_follow_relink.bl_idname, text="이 파일로 잇기", icon="LINKED").index = index
        if row["problem"]:
            for line in wrap(row["problem"], width)[:5]:
                col.label(text=line)
            if row["missing"]:
                col.operator(CLAUDEHAND_OT_follow_adopt.bl_idname, text="선택한 오브젝트로 잇기", icon="RESTRICT_SELECT_OFF").index = index
    if any(row["here"] for row in rows):
        for line in wrap("체크한 모델은 이 파일을 저장할 때마다 Unity에서 바뀝니다.", width):
            layout.label(text=line)
    for line in wrap(_follow["status"], width)[:3] if _follow["status"] else ():
        layout.label(text=line)
    col = layout.column(align=True)
    for name, project in _follow["editors"]:
        col.operator(CLAUDEHAND_OT_follow_send.bl_idname, text=f"선택한 오브젝트 보내기 → {name}", icon="EXPORT").project = project
    col.operator(CLAUDEHAND_OT_follow_look.bl_idname, text="새로 고침", icon="FILE_REFRESH")


class CLAUDEHAND_PT_follow(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Claude Hand"
    bl_label = "Unity로 보낸 모델"
    bl_parent_id = "CLAUDEHAND_PT_chat"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_follow(self.layout, panel_metrics(context))


class CLAUDEHAND_PT_chat(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Claude Hand"
    bl_label = "Claude Hand Chat"

    def draw(self, context):
        layout = self.layout
        width = panel_metrics(context)
        for line in wrap(_status[0], width):
            layout.label(text=line)
        if _server_missing[0]:
            opening = starting()
            row = layout.row()
            row.scale_y = 1.6
            row.enabled = not opening
            row.operator(CLAUDEHAND_OT_start.bl_idname, text="여는 중... 새 창을 확인하세요" if opening else "Claude Hand 시작", icon="PLAY")
            hint = ("Claude Hand 창에서 확인을 눌러 주세요. 세션이 일을 시작하면 그 창은 저절로 최소화됩니다. 닫지는 마세요."
                    if opening else "Claude Hand(감독 세션)가 꺼져 있습니다. 버튼을 누르거나 메시지를 보내면 켜집니다.")
            for line in wrap(hint, width):
                layout.label(text=line)
        box = layout.box()
        if not _entries and _pending[0] is None:
            box.label(text="아직 대화가 없습니다.")
        shown, folded = visible_entries(width)
        if folded:
            box.label(text=f"이전 대화 {folded}개는 접어 두었습니다", icon="TRIA_UP")
        for entry, rows, hidden in shown:
            col = box.column(align=True)
            for text, icon in rows:
                col.label(text=text, icon=icon)
            if entry.get("kind") == "image":
                draw_image(col, entry, width)
            if hidden:
                row = col.row(align=True)
                row.label(text=f"…{hidden}줄 더 있음")
                row.operator(CLAUDEHAND_OT_copy.bl_idname, text="전체 복사", icon="COPYDOWN").seq = int(entry.get("seq") or 0)
            if entry.get("kind") == "permission":
                if entry.get("behavior"):
                    col.label(text="→ 허용됨" if entry["behavior"] == "allow" else "→ 거부됨")
                else:
                    row = col.row(align=True)
                    for behavior, label in (("allow", "허용"), ("deny", "거부")):
                        op = row.operator(CLAUDEHAND_OT_permission.bl_idname, text=label)
                        op.request_id = entry.get("request_id", "")
                        op.behavior = behavior
        if _pending[0] is not None:
            col = box.column(align=True)
            col.label(text="나 (Claude Hand가 켜지면 보냅니다)", icon="TIME")
            for line in wrap(_pending[0][0], width)[:ENTRY_LINES]:
                col.label(text=line)
        if _last_user[0] and time.time() - _last_user[0] > NO_REPLY_HINT_SECONDS:
            for line in wrap("1분 넘게 답이 없습니다. 감독 세션을 start-supervisor.cmd로 열었는지, Claude Code 창에 확인을 기다리는 질문이 없는지 봐 주세요.", width):
                layout.label(text=line)
        draw_others(layout, width)
        draw_progress(layout, width)
        mark, offer = undo_plan()
        if (mark is not None or offer is not None) and _progress[0] is None:
            layout.operator(CLAUDEHAND_OT_undo.bl_idname, text=f"되돌리기: {describe_plan(mark, offer)}", icon="LOOP_BACK")
        wm = context.window_manager
        row = layout.row(align=True)
        row.prop(wm, "claude_hand_chat_input", text="")
        if sys.platform == "win32":
            row.operator(CLAUDEHAND_OT_native_input.bl_idname, text="한글", icon="WINDOW")
        row = layout.row()
        row.prop(wm, "claude_hand_chat_context", text="선택 항목 함께")
        row.operator(CLAUDEHAND_OT_send.bl_idname, text="보내기", icon="PLAY")


OTHER_LINES = 2  # rows one other window may take in the panel


def others_rows(lines, width):
    """The other windows as panel rows, (text, icon) each: every window's line starts with a dot and wraps to a
    second row at most. Words only: what a window is doing is in the text, not in a colour."""
    rows = []
    for line in lines:
        wrapped = wrap(line, width)
        if len(wrapped) > OTHER_LINES:
            wrapped = wrapped[:OTHER_LINES - 1] + [wrapped[OTHER_LINES - 1].rstrip() + "…"]
        rows += [(text, "DOT" if i == 0 else "BLANK1") for i, text in enumerate(wrapped)]
    return rows


def draw_others(layout, width):
    """What the other open windows are busy with (at work and on whose request, waiting for an answer, stopped by a
    dialog; the idle ones together), as the channel tells it. Shown anew when a window's state changes."""
    if not _others:
        return
    col = layout.column(align=True)
    col.label(text=f"다른 창 ({len(_others)})", icon="WINDOW")
    for text, icon in others_rows(_others, width):
        col.label(text=text, icon=icon)


def progress_view(progress, received_at, now):
    """What the subagent is doing, in one line: "작업 중 · 12초 · execute_code bpy.ops… · 3번째 동작"."""
    seconds = int(progress.get("elapsed", 0) + now - received_at)
    parts = [f"{'중지하는 중' if progress.get('stopping') else '작업 중'} · {seconds}초",
             f"{progress.get('tool', '')} {progress.get('detail', '')}".strip()]
    if progress.get("actions"):
        parts.append(f"{progress['actions']}번째 동작")
    return " · ".join(part for part in parts if part)


def draw_progress(layout, width):
    """Just above the input while this window's subagent works, as text (no bar); the reply removes it."""
    if _progress[0] is None:
        return
    progress, received_at = _progress[0]
    now = time.time()
    if now - received_at > PROGRESS_STALE:  # no news for a long while: the supervisor never answered
        return
    col = layout.column(align=True)
    for i, line in enumerate(wrap(progress_view(progress, received_at, now), width)[:3]):
        col.label(text=line, icon="TIME" if i == 0 else "NONE")
    if not progress.get("stopping"):
        col.operator(CLAUDEHAND_OT_stop.bl_idname, text="중지", icon="CANCEL")


def image_preview(path):
    """The thumbnail of a result preview file, loaded once; None when the file is gone."""
    previews = _previews[0]
    if previews is None or not path or not os.path.exists(path):
        return None
    return previews.get(path) or previews.load(path, path, "IMAGE")


def draw_image(layout, entry, width):
    """A picture of another window that a request made here changed (Unity's Scene view, say), attached by the chat
    channel when the reply came. Thumbnails are square, so a wide picture shows letterboxed; 크게 보기 opens the file."""
    preview = image_preview(entry.get("path", ""))
    if preview is None:
        layout.label(text="(그림 파일이 지워졌습니다)")
        return
    width_px, font_px = width or (WRAP_WIDTH * CHAR_PX, 11.0)
    layout.template_icon(icon_value=preview.icon_id, scale=max(4.0, min(16.0, width_px / (20.0 * font_px / 11.0))))
    layout.operator(CLAUDEHAND_OT_open_image.bl_idname, text="크게 보기", icon="ZOOM_IN").path = entry["path"]


def panel_metrics(context):
    """(text width in pixels, label font size in pixels) for this sidebar, or None outside a UI context."""
    try:
        prefs = context.preferences
        scale = prefs.system.ui_scale or 1.0
        width = context.region.width - PANEL_MARGIN_PX * scale
        points = getattr(getattr(prefs.ui_styles[0], "widget", None), "points", 0) or 11
    except (AttributeError, IndexError):
        return None
    return (max(80.0, float(width)), float(points * scale))


def char_units(ch):
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_measure(font_px):
    """Pixel width of label text: Blender's own font metrics (blf) when it can measure, else an estimate."""
    try:
        import blf
        blf.size(0, font_px)
        if blf.dimensions(0, "가a")[0] > 0:
            return lambda text: blf.dimensions(0, text)[0] * LABEL_HEADROOM
    except Exception:  # no font in this Blender (background) or an older blf.size signature
        pass
    return lambda text: sum(char_units(ch) for ch in text) * CHAR_PX * font_px / 11.0


def wrap(text, metrics=None):
    """Label lines for `text`. With `metrics` (from panel_metrics) each line fits the sidebar instead of being
    cut with "..."; without them, WRAP_WIDTH characters."""
    return list(_wrap(str(text), metrics))


@functools.lru_cache(maxsize=1024)  # the panel redraws often; messages and the sidebar width rarely change
def _wrap(text, metrics):
    if metrics is None:
        return tuple(line for paragraph in text.splitlines() or [""] for line in textwrap.wrap(paragraph, WRAP_WIDTH) or [""])
    width, font_px = metrics
    measure = text_measure(font_px)
    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if measure(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            if measure(word) <= width:
                line = word
                continue
            for ch in word:  # wider than the sidebar (a path, a long run without spaces): break between characters
                if line and measure(line + ch) > width:
                    lines.append(line)
                    line = ""
                line += ch
        lines.append(line)
    return tuple(lines)


def entry_text(entry):
    if entry.get("kind") == "permission":
        return " ".join(filter(None, [entry.get("description"), entry.get("input_preview")]))
    return entry.get("text", "")


def entry_rows(entry, width):
    """(rows, hidden): the (text, icon) label rows of one entry, at most ENTRY_LINES lines of its text, and how
    many lines were left out. No rows for kinds the panel does not show."""
    kind = entry.get("kind")
    if kind not in SHOWN_KINDS:
        return [], 0
    if kind == "notice":  # from this panel (되돌리기) or the channel (a dialog stopped the window doing the work, what a request took)
        lines = wrap(entry_text(entry), width)[:ENTRY_LINES]
        first = "ERROR" if entry.get("alert") else "TIME" if entry.get("cost") else "FILE_REFRESH" if entry.get("key") else "LOOP_BACK"
        return [(line, first if i == 0 else "NONE") for i, line in enumerate(lines)], 0
    if kind == "image":  # the picture itself is drawn after these rows (draw_image)
        return [(entry.get("caption") or "결과 미리보기", "IMAGE_DATA")], 0
    rows = []
    if kind == "reply" and entry.get("origin") == "desktop":
        rows.append(("데스크톱 앱에서", "WINDOW"))
        rows += [(line, "NONE") for line in wrap(entry.get("request", ""), width)[:3] if line]
    if kind == "permission":
        rows.append((f"'{entry.get('tool_name')}' 실행 허락 요청", "ERROR"))
    else:
        rows.append(("나" if kind == "user" else "Claude", "USER" if kind == "user" else "INFO"))
    body = wrap(entry_text(entry), width)
    rows += [(line, "NONE") for line in body[:ENTRY_LINES]]
    return rows, max(0, len(body) - ENTRY_LINES)


def visible_entries(width):
    """([(entry, rows, hidden)], folded): the newest entries that fit PANEL_LINES (always the newest one),
    oldest first, so a long conversation never pushes the input box out of reach; folded counts the rest."""
    shown, used, total, full = [], 0, 0, False
    for entry in reversed(_entries):
        if entry.get("kind") not in SHOWN_KINDS:
            continue
        total += 1
        if full:
            continue
        rows, hidden = entry_rows(entry, width)
        cost = len(rows) + (1 if hidden else 0) + (1 if entry.get("kind") == "permission" else 0) + \
            (IMAGE_LINES if entry.get("kind") == "image" else 0)
        if shown and (used + cost > PANEL_LINES or len(shown) >= VISIBLE_ENTRIES):
            full = True  # older entries stay folded, even short ones, so the history has no gaps
            continue
        shown.append((entry, rows, hidden))
        used += cost
    shown.reverse()
    return shown, total - len(shown)


CLASSES = (CLAUDEHAND_OT_send, CLAUDEHAND_OT_start, CLAUDEHAND_OT_permission, CLAUDEHAND_OT_copy, CLAUDEHAND_OT_undo,
           CLAUDEHAND_OT_native_input, CLAUDEHAND_OT_open_image, CLAUDEHAND_OT_stop, CLAUDEHAND_OT_follow_toggle,
           CLAUDEHAND_OT_follow_relink, CLAUDEHAND_OT_follow_adopt, CLAUDEHAND_OT_follow_send, CLAUDEHAND_OT_follow_look,
           CLAUDEHAND_PT_chat, CLAUDEHAND_PT_follow)
HANDLERS = (("load_post", _forget_undo_points), ("load_post", _on_loaded), ("save_post", _on_saved),
            ("depsgraph_update_post", _on_touched))


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.claude_hand_chat_input = bpy.props.StringProperty(
        name="메시지", description="Enter로 보냅니다", update=_on_input_update)
    bpy.types.WindowManager.claude_hand_chat_context = bpy.props.BoolProperty(
        name="선택 항목 함께 보내기", default=True)
    # Defined here (not a custom property) so the Properties editor does not list it.
    setattr(bpy.types.Scene, UNDO_PROP, bpy.props.StringProperty(options={"HIDDEN"}))
    for name, handler in HANDLERS:
        if handler not in getattr(bpy.app.handlers, name):
            getattr(bpy.app.handlers, name).append(handler)
    _follow.update(path="", unity=None, looked=0.0)  # the first tick looks for open Unity editors (bpy.data is not to be read here)
    if _previews[0] is None:
        _previews[0] = bpy.utils.previews.new()
    _stop.clear()
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
    if _poller[0] is None or not _poller[0].is_alive():
        _poller[0] = threading.Thread(target=poll_loop, name="claude-hand-chat", daemon=True)
        _poller[0].start()


def unregister():
    _stop.set()
    close_native_input()
    if _previews[0] is not None:
        bpy.utils.previews.remove(_previews[0])
        _previews[0] = None
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    for name, handler in HANDLERS:
        if handler in getattr(bpy.app.handlers, name):
            getattr(bpy.app.handlers, name).remove(handler)
    delattr(bpy.types.Scene, UNDO_PROP)
    del bpy.types.WindowManager.claude_hand_chat_input
    del bpy.types.WindowManager.claude_hand_chat_context
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

