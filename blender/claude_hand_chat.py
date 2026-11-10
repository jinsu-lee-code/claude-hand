# SPDX-License-Identifier: MIT
# Copyright (c) 2026 jinsu-lee-code. This file is installed on its own, so it says so itself; the full text is in
# LICENSE at the root of https://github.com/jinsu-lee-code/claude-hand
"""Claude Hand Chat: talk to this Blender window's claude-hand subagent from the 3D Viewport sidebar.

It is also what that subagent calls, through MCP for Blender's execute_code, for the things claude-hand does in
Blender: undo points (mark_undo_point, undo_last_request) and moving objects to another Blender or to and from Unity
(export_blend, import_blend, export_fbx, import_model).

Messages go to the claude-hand-chat channel (tools/chat_channel.py in the claude-hand repo), which
the supervisor Claude Code session runs; the supervisor hands them to blender-<file> and answers here.
Networking runs on a background thread; everything that touches bpy runs on the main thread
through a bpy.app.timers callback.

Install: `uv run tools/claude_hand.py blender-install-chat` (or Preferences > Add-ons > Install).
"""

bl_info = {
    "name": "Claude Hand Chat",
    "author": "claude-hand",
    "version": (0, 9, 1),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > Claude Hand",
    "description": "Chat with this Blender window's claude-hand subagent",
    "category": "Interface",
}

import base64
import datetime
import functools
import json
import os
import queue
import re
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

_entries = []            # main thread only
_status = ["연결 중..."]  # main thread only
_last_user = [0.0]       # main thread only
_server_missing = [False]  # main thread only
_start_requested = [0.0]   # main thread only
_pending = [None]          # main thread only: (text, context) typed while Claude Hand was off
_progress = [None]         # main thread only: (what the subagent is doing, when it arrived) while it works
_context_sent = {"time": 0.0, "signature": None, "server_pid": None, "busy": False}  # main thread only
_inbox = queue.Queue()   # (event, payload) from worker threads
_identity = {"window": None, "pin": None}  # written on the main thread, read by the poller
_identity_lock = threading.Lock()
_stop = threading.Event()
_poller = [None]
_marks = []               # main thread only: undo points of Claude's requests, newest last ({"seq", "label", "time"})
_mark_seq = [0]           # main thread only
_undo_note = [""]         # main thread only: goes along with the next message after "되돌리기"
_undo_run = f"{os.getpid()}-{int(time.time())}"  # undo points of another Blender run never match
_native_input = [None]    # main thread only: the Windows input box (a PowerShell process) while it is open
_previews = [None]        # main thread only: thumbnails of result previews (bpy.utils.previews), by file path


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
    chat_id, agent, after, server_pid, progress_rev = None, None, 0, None, -1
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
            server_pid, chat_id, agent, after, progress_rev = server.get("pid"), None, None, 0, -1
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
            query = urllib.parse.urlencode({"chat_id": chat_id, "after": after, "prev": progress_rev, "wait": POLL_WAIT_SECONDS})
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


def mark_undo_point(label=""):
    """Set an undo point for a new request. The subagent's guard hook calls this through MCP for Blender's execute_code
    just before the request's first Blender action."""
    seq = _mark_seq[0] + 1
    for scene in bpy.data.scenes:
        setattr(scene, UNDO_PROP, f"{_undo_run}:{seq}")
    if _undo_op(bpy.ops.ed.undo_push, message=f"Claude Hand: {label or '요청'}"[:60]) != {"FINISHED"}:
        return "undo point not set: no undo history here"
    _mark_seq[0] = seq
    _marks.append({"seq": seq, "label": " ".join(str(label).split())[:40], "time": time.strftime("%H:%M")})
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
    if not _marks:
        return _notice("되돌릴 Claude 요청이 없습니다.")
    mark = _marks[-1]
    if state_seq() < mark["seq"]:  # Blender's own undo already went back past it
        _marks.pop()
        return _notice(f"'{describe_mark(mark)}' 요청은 이미 되돌려져 있습니다.")
    _undo_op(bpy.ops.ed.undo_push, message="Claude Hand: 되돌리기 전")  # so Ctrl+Shift+Z can bring the work back
    limit = bpy.context.preferences.edit.undo_steps + 2
    steps = walk_back(mark["seq"], state_seq, lambda: _undo_op(bpy.ops.ed.undo), lambda: _undo_op(bpy.ops.ed.redo), limit)
    if steps is None:
        return _notice(f"'{describe_mark(mark)}' 요청을 되돌리지 못했습니다. 그 뒤로 실행 취소 단계가 너무 많아 "
                       "Blender의 실행 취소 기록에서 밀려났습니다.")
    _marks.pop()
    if note:
        _undo_note[0] = f"[사용자가 채팅창에서 '{describe_mark(mark)}' 요청 이후의 Blender 변경을 실행 취소로 되돌렸음]"
    return _notice(f"되돌렸어요: '{describe_mark(mark)}' 이후의 변경")


def _notice(text):
    _entries.append({"kind": "notice", "text": text})
    del _entries[:-KEPT_ENTRIES]
    _redraw()
    return text


def _undo_from_button():
    undo_last_request(note=True)
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


def export_fbx(roots, name="model"):
    """Export the named objects and their children as an FBX in the exchange folder, set up for Unity (Y up, -Z
    forward, textures embedded). Returns {"manifest": path for Unity's ch_import_model, "objects": names exported}.
    The selection is put back as it was. Sending the same objects of the same saved .blend again makes Unity replace
    the model it already has in place; from a file that was never saved it cannot tell, and adds a new one."""
    roots, objects = _with_children(roots)
    name, transfer_id = _transfer_id(name)
    layer = bpy.context.view_layer
    missing = [o.name for o in objects if o.name not in layer.objects]
    if missing:
        raise ValueError("지금 뷰 레이어에 없는 오브젝트는 내보낼 수 없습니다: " + ", ".join(missing))
    selected, active = {o.name for o in layer.objects if o.select_get()}, layer.objects.active
    for obj in layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    layer.objects.active = objects[0]
    try:
        bpy.ops.export_scene.fbx(filepath=os.path.join(exchange_dir(), transfer_id + ".fbx"), use_selection=True,
                                 apply_scale_options="FBX_SCALE_ALL", axis_forward="-Z", axis_up="Y", path_mode="COPY",
                                 embed_textures=True)
    finally:
        for obj in layer.objects:
            obj.select_set(obj.name in selected)
        layer.objects.active = active
    manifest = _write_manifest(name, transfer_id, {"format": "claude-hand/model@1", "model_file": transfer_id + ".fbx", "roots": roots,
                                                   "objects": sorted(o.name for o in objects)})
    result = {"manifest": manifest, "objects": sorted(o.name for o in objects)}
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
    elif event == "progress":
        _progress[0] = (payload, time.time()) if payload else None
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
    bl_description = "Undo everything changed in this Blender since Claude's last request started (Blender's own undo history)"

    def invoke(self, context, event):
        if not _marks:
            return {"CANCELLED"}
        message = f"'{describe_mark(_marks[-1])}' 이후 바뀐 것을 모두 되돌립니다. 그 뒤에 직접 고친 것도 함께 되돌아갑니다."
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
        draw_progress(layout, width)
        if _marks and _progress[0] is None:
            layout.operator(CLAUDEHAND_OT_undo.bl_idname, text=f"되돌리기: {describe_mark(_marks[-1])}", icon="LOOP_BACK")
        wm = context.window_manager
        row = layout.row(align=True)
        row.prop(wm, "claude_hand_chat_input", text="")
        if sys.platform == "win32":
            row.operator(CLAUDEHAND_OT_native_input.bl_idname, text="한글", icon="WINDOW")
        row = layout.row()
        row.prop(wm, "claude_hand_chat_context", text="선택 항목 함께")
        row.operator(CLAUDEHAND_OT_send.bl_idname, text="보내기", icon="PLAY")


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
    if kind == "notice":  # from this panel (되돌리기) or the channel (a dialog stopped the window doing the work)
        lines = wrap(entry_text(entry), width)[:ENTRY_LINES]
        first = "ERROR" if entry.get("alert") else "LOOP_BACK"
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
           CLAUDEHAND_OT_native_input, CLAUDEHAND_OT_open_image, CLAUDEHAND_OT_stop, CLAUDEHAND_PT_chat)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.claude_hand_chat_input = bpy.props.StringProperty(
        name="메시지", description="Enter로 보냅니다", update=_on_input_update)
    bpy.types.WindowManager.claude_hand_chat_context = bpy.props.BoolProperty(
        name="선택 항목 함께 보내기", default=True)
    # Defined here (not a custom property) so the Properties editor does not list it.
    setattr(bpy.types.Scene, UNDO_PROP, bpy.props.StringProperty(options={"HIDDEN"}))
    if _forget_undo_points not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_forget_undo_points)
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
    if _forget_undo_points in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_forget_undo_points)
    delattr(bpy.types.Scene, UNDO_PROP)
    del bpy.types.WindowManager.claude_hand_chat_input
    del bpy.types.WindowManager.claude_hand_chat_context
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

