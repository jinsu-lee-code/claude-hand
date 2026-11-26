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
    "version": (1, 1, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > Claude Hand",
    "description": "Chat with this Blender window's claude-hand subagent",
    "category": "Interface",
}

import array
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

# --------------------------------------------------------------------------- settings and language (any thread)
#
# What the settings window (the gear button of the panel) sets is in ~/.claude-hand/config.json, which the chat
# channel, the commands, the hooks and Unity's chat window read too; what can be set is listed once, in the
# claude-hand checkout (settings/catalog.json). The file is read anew whenever it changes, so a change made in any
# of the windows holds here at once.
#
# The language of this panel is one of the settings. The Korean text stands in the code where it is said, and T()
# gives its English from _EN (at the end of this file) when the setting says "en", as tools/handlib/lang.py does
# for the Python tools. N_() marks a text that is said elsewhere, through T: a constant, a class's label.

_config = {"stamp": None, "data": {}}
_catalog = {"stamp": None, "data": {"settings": [], "lab": []}}
SETTING_DEFAULTS = {"flash_taskbar": True}  # the settings this add-on itself goes by, should the catalog not be there


def config_file() -> Path:
    return Path(os.environ.get("CLAUDE_HAND_HOME") or str(Path.home() / ".claude-hand")) / "config.json"


def read_config() -> dict:
    """config.json as it is now: read again only when the file changed. Missing: {}. Caught half written (another
    window is saving it): what was last read."""
    path = config_file()
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        _config.update(stamp=None, data={})
        return _config["data"]
    if stamp != _config["stamp"]:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
            if isinstance(data, dict):
                _config.update(stamp=stamp, data=data)
        except (OSError, ValueError):
            pass
    return _config["data"]


def write_config(change):
    """Change config.json: change(config) alters the dict it is given. Read anew just before and put in place whole,
    so no other window reads half of it. Returns what stood in the way (a text for the user), or None."""
    path = config_file()
    try:
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        except FileNotFoundError:
            config = {}
        if not isinstance(config, dict):
            raise ValueError("not a JSON object")
        change(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"config.{os.getpid()}.{time.monotonic_ns()}.tmp")  # of this write alone
        tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        for attempt in range(8):  # Windows refuses while another window has the old file open to read it
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 7:
                    tmp.unlink(missing_ok=True)  # the file stays as it was, whole
                    raise
                time.sleep(0.03)
    except (OSError, ValueError) as exc:
        return T("설정을 적지 못했습니다: {exc}", exc=exc)
    _config.update(stamp=None, data=config)  # read anew next time, and this meanwhile
    return None


def language() -> str:
    """"ko" or "en". CLAUDE_HAND_LANG, when it is one of the two, wins over the setting (the tests)."""
    forced = os.environ.get("CLAUDE_HAND_LANG", "").strip().lower()
    if forced in ("ko", "en"):
        return forced
    value = read_config().get("language")
    return value if value in ("ko", "en") else "ko"


def N_(text):
    """Marks a Korean text that is said elsewhere, through T. Returns it as it is."""
    return text


def T(text, /, **values):
    """`text` in the language now in use, with its {names} filled in from `values`. A text that counts something
    has a second English wording for when the count is one (_EN_ONE): "1 day", not "1 days"."""
    if language() == "en":
        one = _EN_ONE.get(text) if values else None
        text = one[1] if one and type(values.get(one[0])) is int and values[one[0]] == 1 else _EN.get(text, text)
    return text.format(**values) if values else text


def pick(names):
    """Of a catalog entry's {"ko": ..., "en": ...}, the one in the language now in use."""
    names = names if isinstance(names, dict) else {}
    return str(names.get(language()) or names.get("ko") or "")


def read_catalog() -> dict:
    """{"settings": [...], "lab": [...]}: settings/catalog.json of the claude-hand checkout config.json names
    ("repo", written by setup). Empty lists when it cannot be found."""
    repo = read_config().get("repo")
    try:
        path = Path(str(repo)) / "settings" / "catalog.json"
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        if stamp != _catalog["stamp"]:
            data = json.loads(path.read_text(encoding="utf-8"))
            _catalog.update(stamp=stamp, data={
                "settings": [e for e in data.get("settings") or [] if isinstance(e, dict) and e.get("key")],
                "lab": [e for e in data.get("lab") or [] if isinstance(e, dict) and e.get("key") and e.get("stage", "lab") == "lab"]})
    except (OSError, ValueError, TypeError):
        _catalog.update(stamp=None, data={"settings": [], "lab": []})
    return _catalog["data"]


def setting_value(entry):
    """What a setting of the catalog is now: config.json's value when it is one the setting can have, else its default."""
    value, default, kind = read_config().get(entry["key"]), entry.get("default"), entry.get("kind")
    if kind == "switch":
        return value if isinstance(value, bool) else bool(default)
    if kind == "choice":
        return value if value in [c.get("value") for c in entry.get("choices") or []] else default
    if kind == "days":
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3650 else int(default or 0)
    return default


def setting_on(key) -> bool:
    """A switch as it stands now (one this add-on goes by: SETTING_DEFAULTS)."""
    value = read_config().get(key)
    return value if isinstance(value, bool) else SETTING_DEFAULTS.get(key, True)


def lab_value(entry) -> bool:
    chosen = read_config().get("lab")
    value = chosen.get(entry["key"]) if isinstance(chosen, dict) else None
    return value if isinstance(value, bool) else bool(entry.get("default"))


def set_setting(key, value):
    return write_config(lambda config: config.__setitem__(key, value))


def set_lab(key, on):
    def change(config):
        chosen = config.get("lab") if isinstance(config.get("lab"), dict) else {}
        config["lab"] = {**chosen, key: bool(on)}
    return write_config(change)

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
SHOWN_KINDS = ("user", "reply", "permission", "notice", "image", "protect_offer")
IMAGE_LINES = 8         # what a result preview counts for when the panel decides how much history fits
ALERT_FRESH_SECONDS = 30.0  # older items (history shown again after a restart) never blink the taskbar
UNDO_KEPT = 20          # undo points kept for the newest requests
UNDO_PROP = "claude_hand_undo"  # on every scene: "<this run>:<newest undo point set before this state>"
SENT_AS = "claude_hand_sent_as"  # on an object that went to Unity: the name it went under, which Unity knows its model by
FOLLOW_TOOL = "ch_model_follow"  # Claude Hand Tools' tool for models that follow their .blend (0.16 and later)
REALIZE = "ClaudeHandRealize"    # the modifier and node group a Geometry Nodes object has while it is exported (_realized)
TEXTURE_FORMATS = (".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".exr", ".hdr", ".psd")  # image files Unity reads as they are
BAKE_SIZE = 1024                 # pixels a side, of an image baked from a material's nodes
BAKED = {"base_color_texture": N_("기본 색"), "metallic_smoothness_texture": N_("금속 · 거칠기"), "normal_texture": N_("노멀"), "emission_texture": N_("발광")}
TREE_LAYOUT = {"name", "label", "location", "location_absolute", "width", "height", "dimensions", "select", "parent", "hide", "color",
               "use_custom_color", "rna_type", "type", "inputs", "outputs", "internal_links", "warning_propagation"}  # of a node: not what it does
UNITY_LOOK_SECONDS = 5.0         # how often the panel looks whether another Unity editor has opened
HEAVY_SECONDS = 1.0              # a model whose export took longer than this is exported on a save only when it was touched
BUSY_TRIES, BUSY_WAIT = 8, 2.5   # how long a send waits for a Unity that is compiling or reloading its scripts

_entries = []            # main thread only
_status = [N_("연결 중...")]  # main thread only
_status_maker = [None, None]  # main thread only: (what says the resting status line, what it last said): said anew in another language
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
        return T("채널 서버가 대기 중입니다: {reason}. 아래 버튼으로 켜거나, 메시지를 보내면 자동으로 켜집니다.", reason=status.get('reason'))
    if status.get("mode") == "stopped":
        return T("Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다.")
    return (T("Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다. 켜도 연결되지 않으면 "
            "claude-hand 폴더에서 uv run tools/claude_hand.py chat-status 를 실행하세요."))


STARTING_MESSAGE = (N_("Claude Hand 창을 여는 중입니다. 그 창에 확인 질문이 보이면 화살표 키로 고르고 Enter를 눌러 주세요"
                    "(개발용 채널 경고는 I am using this for local development). 연결되면 이 패널이 이어서 진행합니다."))


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
                # said by the main thread, and again when the language is changed: this line stays up for hours
                _inbox.put(("status", lambda agent=agent: T("담당: {agent}", agent=agent) if agent else
                            T("이 Blender 창의 하위 세션이 없습니다. uv run tools/claude_hand.py sync 를 실행하세요.")))
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
            _inbox.put(("status", T("감독 세션에 연결할 수 없습니다: {exc}", exc=exc)))
            _stop.wait(3)


def post_in_background(path, body, on_done_event):
    def run():
        server = read_server()
        if not server:
            _inbox.put((on_done_event, {"ok": False}) if on_done_event == "context_sent" else ("status", T("Claude Hand가 꺼져 있습니다.")))
            return
        try:
            _inbox.put((on_done_event, call(server, "POST", path, body)))
        except (OSError, ValueError) as exc:
            _inbox.put(("status", T("보내지 못했습니다: {exc}", exc=exc)))
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
    lines = [T("파일: {path}", path=bpy.data.filepath or T('(저장 안 됨)')), T("씬: {name}", name=bpy.context.scene.name)]
    if active is not None:
        lines.append(T("활성 오브젝트: {name} ({type}, 모드 {mode})", name=active.name, type=active.type, mode=active.mode))
    if selected:
        lines.append(T("선택한 오브젝트: ") + ", ".join(selected))
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
        "file": bpy.data.filepath or T("(저장 안 됨)"),
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
        _status[0] = T("start-supervisor를 찾지 못했습니다. 바탕화면의 Claude Hand 아이콘을 누르세요.")
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
        _status[0] = T("Claude Hand를 시작하지 못했습니다: {exc}", exc=exc)
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
$send.Text = $utf8.GetString([Convert]::FromBase64String('__SEND__'))
$send.AutoSize = $true
$hint = New-Object Windows.Forms.Label
$hint.Text = $utf8.GetString([Convert]::FromBase64String('__HINT__'))
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
    words = {"__TITLE__": title, "__SEND__": T("보내기"), "__HINT__": T("Enter 보내기 · Shift+Enter 줄바꿈 · Esc 닫기")}
    script = NATIVE_INPUT_PS.replace("__PARENT__", str(int(parent_pid)))
    for mark, text in words.items():
        script = script.replace(mark, base64.b64encode(text.encode("utf-8")).decode("ascii"))
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
        _status[0] = T("입력창을 열지 못했습니다: {exc}", exc=exc)
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
    return f"{mark['label']} ({mark['time']})" if mark["label"] else T("{time} 요청", time=mark['time'])


def mark_undo_point(label="", request=""):
    """Set an undo point for a new request. The subagent's guard hook calls this through MCP for Blender's execute_code
    just before the request's first Blender action. request: the request's name as the chat channel knows it (its
    id), so that a request which changed several windows can be undone in each of them by that name (undo_request)."""
    seq = _mark_seq[0] + 1
    for scene in bpy.data.scenes:
        setattr(scene, UNDO_PROP, f"{_undo_run}:{seq}")
    if _undo_op(bpy.ops.ed.undo_push, message=f"Claude Hand: {label or T('요청')}"[:60]) != {"FINISHED"}:
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
        return False, _notice(T("되돌릴 Claude 요청이 없습니다."))
    mark = _marks[-1]
    if state_seq() < mark["seq"]:  # Blender's own undo already went back past it
        _marks.pop()
        return False, _notice(T("'{mark}' 요청은 이미 되돌려져 있습니다.", mark=describe_mark(mark)))
    _undo_op(bpy.ops.ed.undo_push, message=T("Claude Hand: 되돌리기 전"))  # so Ctrl+Shift+Z can bring the work back
    limit = bpy.context.preferences.edit.undo_steps + 2
    steps = walk_back(mark["seq"], state_seq, lambda: _undo_op(bpy.ops.ed.undo), lambda: _undo_op(bpy.ops.ed.redo), limit)
    if steps is None:
        return False, _notice(T("'{mark}' 요청을 되돌리지 못했습니다. 그 뒤로 실행 취소 단계가 너무 많아 Blender의 실행 취소 기록에서 밀려났습니다.",
                                mark=describe_mark(mark)))
    _marks.pop()
    if note:
        _note_for_claude(T("[사용자가 채팅창에서 '{mark}' 요청 이후의 Blender 변경을 실행 취소로 되돌렸음]", mark=describe_mark(mark)))
    _follow_due("undo")  # a model that follows this file goes back in Unity too, though nothing was saved
    return True, _notice(T("되돌렸어요: '{mark}' 이후의 변경", mark=describe_mark(mark)))


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
    return f"{offer.get('label') or T('요청')} ({T('이 창 + ') if mark is not None else ''}{others})"


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
    _protect.update(base=None, read={})


# --------------------------------------------------------------------------- protection rules (main thread)
#
# What the user has said must not be changed in this window: the window as a whole ("look": only calls that look,
# "locked": none at all), objects by name, actions. A note is something Claude tries to keep; these are checked. The
# subagent's guard hook asks for them before every call (protection_state) and refuses what a call's own words show.
# Blender has no door to shut on the rest (any code can reach any object), so the hook asks again after the call
# (protection_after): the protected objects are compared with what they were just before it, and when one differs,
# what the request did in this window is taken back with its undo point. Notices from Blender's depsgraph would not
# do for this: they come only once something asks for an evaluation, say nothing of a rename or a custom property,
# and an undo from inside one would run in the middle of the code that is still changing things (tried, 2026-11-24).
#
# The rules are the user's: only the panel's buttons write them (and the [추가] under a rule Claude proposed).
#   beside the .blend: <name>.claude-hand.protect.json      lasting, goes with the file
#   ~/.claude-hand/protect/blender-<port>.json              this run of Claude Hand only ("이번만")
# The same file means the same in tools/handlib/protect.py (the guard) and in Unity (ChProtect.cs).

PROTECT_MODES = ("open", "look", "locked")  # weakest first: of two files, the stricter holds
PROTECT_MODE_WORDS = {"open": N_("열림"), "look": N_("보기만"), "locked": N_("잠금")}
PROTECT_ACTIONS = {"save_file": N_(".blend 저장")}
PROTECT_COVERED = 3000  # names told to the guard at most
PROTECT_FIELDS = {"exists": N_("없어짐"), "name": N_("이름"), "type": N_("종류"), "parent": N_("부모"), "collections": N_("컬렉션"), "world": N_("자리"),
                  "local": N_("자리"), "modifiers": N_("모디파이어"), "constraints": N_("제약"), "slots": N_("재질 칸"), "materials": N_("재질"),
                  "hide": N_("보이기"), "props": N_("사용자 속성"), "data": N_("데이터"), "mesh": N_("메시"), "shape_keys": N_("셰이프 키")}
_protect = {"base": None, "read": {}}  # base: the protected objects as they were before the call that runs now


def _protect_paths():
    """(the lasting rules file or "" for a file never saved, this run's)."""
    lasting = os.path.splitext(bpy.data.filepath)[0] + ".claude-hand.protect.json" if bpy.data.filepath else ""
    home = os.environ.get("CLAUDE_HAND_HOME") or os.path.join(os.path.expanduser("~"), ".claude-hand")
    return lasting, os.path.join(home, "protect", f"blender-{window_identity()[1].rpartition('@')[2]}.json")


def _protect_file(path):
    """One file's rules as {"window", "objects", "actions"} and what is wrong with it (None: nothing). Read anew only
    when the file changed: the panel draws often."""
    try:
        stat = os.stat(path) if path else None
    except OSError:
        stat = None
    if stat is None:
        _protect["read"].pop(path, None)
        return {"window": "open", "objects": [], "actions": []}, None
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _protect["read"].get(path)
    if cached is None or cached[0] != stamp:
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            strings = lambda key: [v.strip() for v in data.get(key) if isinstance(v, str) and v.strip()] if isinstance(data.get(key), list) else []
            window = str(data.get("window") or "open").strip().lower()
            rules = {"window": window if window in PROTECT_MODES else "open", "objects": strings("objects"),
                     "actions": [a for a in strings("actions") if a in PROTECT_ACTIONS]}
            cached = (stamp, rules, None)
        except (OSError, ValueError) as exc:  # a rule that was meant and cannot be read must not go unnoticed
            cached = (stamp, {"window": "open", "objects": [], "actions": []}, f"{path}: {exc}")
        _protect["read"][path] = cached
    return cached[1], cached[2]


def protection_rules():
    """This window's rules, the lasting ones and this run's as one: {"window", "paths": [], "objects", "actions",
    "problems", "rows"}. rows: (kind, value, lasting) for each rule as it stands in its file, for the panel."""
    out = {"window": "open", "paths": [], "objects": [], "actions": [], "problems": [], "rows": []}
    for lasting, path in zip((True, False), _protect_paths()):
        rules, problem = _protect_file(path)
        if problem:
            out["problems"].append(problem)
        if PROTECT_MODES.index(rules["window"]) > 0:
            out["rows"].append(("window", rules["window"], lasting))
            if PROTECT_MODES.index(rules["window"]) > PROTECT_MODES.index(out["window"]):
                out["window"] = rules["window"]
        for kind in ("objects", "actions"):
            for value in rules[kind]:
                out["rows"].append((kind[:-1], value, lasting))
                if value not in out[kind]:
                    out[kind].append(value)
    return out


def _protect_glob(pattern):
    parts = re.split(r"(\*\*|\*|\?)", pattern)
    return re.compile("".join(".*" if p == "**" else "[^/]*" if p == "*" else "[^/]" if p == "?" else re.escape(p) for p in parts) + r"\Z")


def protect_object_rule(rules, parts):
    """The rule that covers the object whose names from the top of its hierarchy down to itself are `parts`, or None:
    a plain name covers that object wherever it is and all below it, "A/**" covers A and all below it, "A/B" that
    one object (as object_rule in tools/handlib/protect.py; tools/tests/protect_cases.json holds both to it)."""
    for rule in rules:
        if rule.lower().startswith("collection:") or not rule.strip():
            continue
        wanted = [p for p in rule.replace("\\", "/").split("/") if p]
        if len(wanted) == 1:
            if any(_protect_glob(wanted[0]).match(part) for part in parts):
                return rule
            continue
        below = wanted[-1] == "**"
        head = wanted[:-1] if below else wanted
        for start in range(len(parts) - len(head) + 1):
            if all(_protect_glob(w).match(p) for w, p in zip(head, parts[start:])) and (below or start + len(head) == len(parts)):
                return rule
    return None


def protected_objects(rules=None):
    """{object name: the rule that covers it} for every object of this file the rules protect."""
    wanted = (rules or protection_rules())["objects"]
    if not wanted:
        return {}
    found = {}
    for rule in wanted:
        if rule.lower().startswith("collection:"):
            match = _protect_glob(rule.split(":", 1)[1].strip())
            for collection in bpy.data.collections:
                if match.match(collection.name):
                    for obj in collection.all_objects:
                        found.setdefault(obj.name, rule)
    for obj in bpy.data.objects:
        if obj.name in found:
            continue
        parts, above = [obj.name], obj.parent
        while above is not None and len(parts) < 64:
            parts.insert(0, above.name)
            above = above.parent
        rule = protect_object_rule(wanted, parts)
        if rule:
            found[obj.name] = rule
    return found


def _protect_plain(value):
    if isinstance(value, (int, str, bool, type(None))):
        return value
    if isinstance(value, float):
        return round(value, 5)
    if isinstance(value, bpy.types.ID):
        return f"<{type(value).__name__}:{value.name}>"
    try:
        return [round(float(x), 5) for x in value]  # vectors, colours, arrays
    except (TypeError, ValueError):
        return str(value)


def _protect_settings(item):
    """The plain settings of a modifier or a constraint, so that a changed bevel width shows."""
    rows = []
    for prop in item.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.type == "COLLECTION":
            continue
        try:
            value = getattr(item, prop.identifier)
            if prop.type == "POINTER" and not isinstance(value, (bpy.types.ID, type(None))):
                continue  # a struct of its own (a bevel's profile): told by where it lies in memory, which an undo changes
            rows.append((prop.identifier, _protect_plain(value)))
        except Exception:  # noqa: BLE001 - a property that cannot be read here is not one a call sets either
            continue
    return hashlib.blake2b(json.dumps(rows, sort_keys=True, default=str).encode("utf-8"), digest_size=8).hexdigest()


def _protect_material(material):
    """What a material is made of, in short: its colour and the values and links of its nodes."""
    if material is None:
        return None
    rows = [("diffuse", _protect_plain(material.diffuse_color))]
    tree = getattr(material, "node_tree", None)
    if tree is not None:
        for node in tree.nodes:
            rows.append((node.name, node.bl_idname, [(s.identifier, _protect_plain(s.default_value)) for s in node.inputs if hasattr(s, "default_value")]))
        rows.append(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in tree.links))
    return hashlib.blake2b(json.dumps(rows, sort_keys=True, default=str).encode("utf-8"), digest_size=8).hexdigest()


def protect_fingerprint(names):
    """{name: what the object is like now}, looked up by name (a pointer does not live through an undo): where it is,
    what it is made of, what hangs on it. Enough to tell that a call changed it, whatever way the call took."""
    out = {}
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            out[name] = {"exists": False}
            continue
        row = {
            "exists": True, "type": obj.type, "parent": obj.parent.name if obj.parent else None,
            "collections": sorted(c.name for c in obj.users_collection),
            "world": [round(x, 5) for line in obj.matrix_world for x in line],
            "local": [round(x, 5) for x in (*obj.location, *obj.rotation_euler, *obj.rotation_quaternion, *obj.scale)],
            "modifiers": [(m.name, m.type, _protect_settings(m)) for m in obj.modifiers],
            "constraints": [(c.name, c.type, _protect_settings(c)) for c in obj.constraints],
            "slots": [slot.material.name if slot.material else None for slot in obj.material_slots],
            "materials": [_protect_material(slot.material) for slot in obj.material_slots],
            "hide": (obj.hide_viewport, obj.hide_render, obj.hide_select),
            "props": sorted((key, str(_protect_plain(obj[key]))) for key in obj.keys() if key != SENT_AS),
            "data": obj.data.name if obj.data else None,
        }
        if obj.type == "MESH":
            mesh = obj.data
            coords = array.array("f", bytes(4 * 3 * len(mesh.vertices)))
            mesh.vertices.foreach_get("co", coords)
            row["mesh"] = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons), hashlib.blake2b(coords.tobytes(), digest_size=8).hexdigest())
            row["shape_keys"] = [(k.name, round(k.value, 5)) for k in mesh.shape_keys.key_blocks] if mesh.shape_keys else []
        out[name] = row
    return out


def _protect_changed(before, now):
    """["Hero (자리, 모디파이어)", ...]: the protected objects that are no longer as they were, and in what."""
    found = []
    for name, was in before.items():
        row = now.get(name, {"exists": False})
        if was.get("exists") and not row.get("exists"):  # by that name: it may live on under another
            found.append(T("{name} (없어지거나 이름이 바뀜)", name=name))
            continue
        fields = sorted({T(PROTECT_FIELDS[key]) if key in PROTECT_FIELDS else key for key in set(was) | set(row) if was.get(key) != row.get(key)})
        if fields:
            found.append(f"{name} ({', '.join(fields)})")
    return found


def protection_state(before=False):
    """For the subagent's guard hook, through MCP for Blender's execute_code just before a call: this window's rules,
    and the objects they protect by name ({"file", "rules", "covered": {name: rule}, "pending"}). before: the call
    may change things, so the protected objects are noted as they are now, for protection_after to compare.
    pending: what protection_after would have said of the call before, when nobody asked."""
    # Nobody asked after the last call that might have changed things (the hook that does was not run): looked at
    # now, before the next one goes ahead. What is found is told with this answer, and that call is refused.
    pending = protection_after() if before and _protect["base"] else None
    rules = protection_rules()
    covered = protected_objects(rules)
    if before:
        if covered:
            bpy.context.view_layer.update()  # where things are is worked out lazily: have it settled before the call
        _protect["base"] = protect_fingerprint(covered) if covered else None
    told = dict(list(covered.items())[:PROTECT_COVERED])
    return {"file": bpy.data.filepath, "rules": {key: rules[key] for key in ("window", "paths", "objects", "actions", "problems")},
            "covered": told, "pending": pending or None}


def protection_after():
    """For the guard hook, just after a call that may have changed things: {} when every protected object is as it
    was before the call. Else what the request did in this window is taken back with its undo point (the newest one:
    Claude Hand sets one per request, the hook one per call without it), and the answer says so: {"changed": [...],
    "undone": whether all is as before again, "detail": why not}."""
    base, _protect["base"] = _protect["base"], None
    if not base:
        return {}
    bpy.context.view_layer.update()
    changed = _protect_changed(base, protect_fingerprint(base))
    if not changed:
        return {}
    if not _marks:
        return {"changed": changed, "undone": False, "detail": T("이 요청의 되돌리기 지점이 없음")}
    mark = _marks[-1]
    done, text = _undo_newest()
    if done and mark.get("request"):  # so no other panel still offers this window along with that request
        window, pin = window_identity()
        post_in_background("/v1/undone", {"app": "blender", "window": window, "pin": pin, "request": mark["request"]}, "undone_sent")
    left = _protect_changed(base, protect_fingerprint(base)) if done else changed
    if done and not left:
        return {"changed": changed, "undone": True}
    return {"changed": changed, "undone": False,
            "detail": text if not done else T("실행 취소로 돌아가지 않은 것이 남음: ") + ", ".join(left[:3])}


def _protect_write(lasting, change):
    """Change one rules file: change(data) sets the dict that is written. Returns what stands in the way, or None."""
    path = _protect_paths()[0 if lasting else 1]
    if not path:
        return T("저장한 적 없는 파일이라 계속 두는 규칙은 적을 곳이 없습니다. '이번만'을 켜고 다시 누르거나, 파일을 저장한 뒤 누르세요.")
    try:
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except OSError:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(T("규칙 파일이 아닌 것이 그 자리에 있습니다"))
        data.setdefault("version", 1)
        change(data)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        return T("규칙을 적지 못했습니다: {exc}", exc=exc)
    # Read anew next time, whatever the file's time says: two changes within one tick of the clock that leave the
    # file as long as it was ("look" to "open") carry the same stamp.
    _protect["read"].pop(path, None)
    _redraw()
    return None


def protect_add(kind, value, lasting=True):
    """The user adds a rule (a button of the panel): kind "window" (value "look" or "locked"), "object" (a name, a
    hierarchy path or "collection:<name>") or "action" ("save_file"). Returns what stands in the way, or None."""
    value = str(value or "").strip()
    if kind == "window":
        if value not in PROTECT_MODES[1:]:
            return T("'{value}'은(는) 창 규칙이 아닙니다.", value=value)
        return _protect_write(lasting, lambda data: data.update(window=value))
    if kind == "action" and value not in PROTECT_ACTIONS:
        return T("'{value}'은(는) Blender에서 막을 수 있는 동작이 아닙니다.", value=value)
    if kind not in ("object", "action") or not value:
        return T("더할 규칙이 없습니다.")

    def add(data):
        rows = data.get(kind + "s") if isinstance(data.get(kind + "s"), list) else []
        data[kind + "s"] = rows + ([value] if value not in rows else [])
    return _protect_write(lasting, add)


def protect_remove(kind, value, lasting=True):
    """The user takes a rule away again (the panel's [풀기]): only this, never anything Claude can call."""
    if kind == "window":
        return _protect_write(lasting, lambda data: data.update(window="open"))

    def remove(data):
        rows = data.get(kind + "s") if isinstance(data.get(kind + "s"), list) else []
        data[kind + "s"] = [row for row in rows if row != value]
    return _protect_write(lasting, remove)


def protect_row_label(kind, value, lasting):
    word = lambda words: T(words[value]) if value in words else value
    what = (T("이 창: {mode}", mode=word(PROTECT_MODE_WORDS)) if kind == "window" else
            T("금지: {action}", action=word(PROTECT_ACTIONS)) if kind == "action" else
            T("컬렉션 {name}", name=value.split(':', 1)[1].strip()) if value.lower().startswith("collection:") else T("오브젝트 {value}", value=value))
    return what + ("" if lasting else T(" (이번만)"))


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
            raise ValueError(T("이 파일에 '{name}' 오브젝트가 없습니다. get_scene_info로 정확한 이름을 확인하세요.", name=name))
        for obj in (root, *root.children_recursive):
            if obj not in found:
                found.append(obj)
    if not found:
        raise ValueError(T("보낼 오브젝트 이름을 하나 이상 주세요."))
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
        raise ValueError(T("이 manifest는 '{format}' 형식이라 Blender로 받을 수 없습니다. export_blend가 만든 것만 됩니다.", format=data.get('format')))
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
        raise ValueError(T("지금 뷰 레이어에 없는 오브젝트는 내보낼 수 없습니다: ") + ", ".join(missing))
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


LATER = N_("오브젝트 모드가 아니라서 이번에는 굽지 않았습니다(Unity에 먼저 구운 이미지가 있으면 그것이 그대로 쓰입니다). 오브젝트 모드에서 저장하거나 보내면 구워집니다.")


def _bakes_now():
    """Blender bakes in Object Mode only. In another mode a baked image is named but not written: Unity keeps the one
    it has, and the image's stamp says "pending", so that it is sent once the mode allows."""
    return bpy.context.mode == "OBJECT"


def _why_not_baked(users):
    """Why an input that is fed through nodes cannot be baked for these objects at all ("" when it can)."""
    if not users:
        return T("이 재질을 쓰는 메시가 없어서")
    hidden = [o.name for o in users if o.hide_render or o.hide_viewport]
    if hidden:  # Blender bakes only what a render would show
        return T("{names}이(가) 렌더나 뷰포트에서 꺼져 있어서(아웃라이너의 카메라 · 모니터 아이콘)", names=', '.join(hidden[:3]))
    bare = [o.name for o in users if not o.data.uv_layers]
    if bare:
        return T("{names}에 UV 맵이 없어서", names=', '.join(bare[:3]))
    if not hasattr(bpy.context.scene, "cycles"):
        return T("Cycles가 꺼져 있어서(굽는 데 씁니다)")
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
        notes.append(T("{what}: 이미지가 UV가 아닌 좌표({name})에 놓여 있어서, UV 그대로 놓은 것으로 갔습니다.", what=what, name=link.from_socket.name) if link.from_node.type == "TEX_COORD"
                     else T("{what}: 이미지를 놓는 좌표가 '{node}' 노드를 거쳐서, UV 그대로 놓은 것으로 갔습니다.", what=what, node=link.from_node.bl_label))
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
        notes.append(T("Principled BSDF 하나로 된 재질이 아니라서, 뷰포트에 보이는 색 · 금속 · 거칠기 값만 갔습니다."))
        return described

    def through(what, read):
        notes.append(T("{what}: '{node}' 노드를 거쳐서 못 옮겼습니다.", what=what, node=read[1].bl_label))

    def unbakeable(what, read):
        """Notes why this node-fed input is not baked, when it is not."""
        why = T("굽기가 꺼져 있어서") if bake is None else _why_not_baked(bake["users"])
        if why:
            notes.append(T("{what}: '{node}' 노드를 거치는데 {why} 못 옮겼습니다.", what=what, node=read[1].bl_label, why=why))
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
            notes.append(f"{what}: {T(LATER)}")
        elif folder:
            try:
                _save_pixels(_bake_pixels(material, bake["users"], source, bake["size"], colour), os.path.join(folder, entry["file"]))
            except RuntimeError as exc:
                files[key] = None
                notes.append(T("{what}: 굽지 못했습니다({why}).", what=what, why=(str(exc).strip().splitlines() or ['?'])[-1][:120]))
                return None
        return dict(entry)

    def picture(read, what):
        entry = _texture(read[1].image, folder, files)
        if entry is None:
            notes.append(T("{what}: 이미지 '{name}'의 파일이 없어서 못 옮겼습니다.", what=what, name=read[1].image.name))
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
            described["base_color_texture"] = picture(base, T("기본 색"))
        else:
            feed = _link(shader.inputs["Base Color"])
            described["base_color_texture"] = cooked(T("기본 색"), "BaseColor", feed.from_socket, ("node", feed.from_node), True)
        if described["base_color_texture"] is not None:  # else the viewport colour stands in
            described["base_color"] = [1.0, 1.0, 1.0, 1.0]

    pair = []
    for inlet, key, what in (("Metallic", "metallic", T("금속")), ("Roughness", "roughness", T("거칠기"))):
        read = _reads(shader.inputs[inlet])
        if read[0] == "value":
            described[key] = read[1]
        elif skewed(read):
            read = ("baked", shader.inputs[inlet])
        elif read[0] == "node":
            read = ("value", described[key]) if unbakeable(what, read) else ("baked", shader.inputs[inlet])
        elif not read[1].image.size[0]:
            notes.append(T("{what}: 이미지 '{name}'의 파일이 없어서 못 옮겼습니다.", what=what, name=read[1].image.name))
            read = ("value", described[key])
        pair.append(read)
    if any(read[0] != "value" for read in pair):
        try:
            entry = _metallic_smoothness(material, pair[0], pair[1], folder, files, bake)
            if folder and entry.get("later"):
                notes.append(T("금속 · 거칠기: {later}", later=T(LATER)))
            described["metallic_smoothness_texture"] = {key: value for key, value in entry.items() if key in ("file", "stamp", "baked")}
            described["metallic"], described["roughness"] = (1.0 if read[0] != "value" else read[1] for read in pair)
        except RuntimeError as exc:
            files["metallic-smoothness:" + material.name] = None
            notes.append(T("금속 · 거칠기: 굽지 못했습니다({why}).", why=(str(exc).strip().splitlines() or ['?'])[-1][:120]))

    normal = _link(shader.inputs["Normal"])
    if normal is not None:
        read = _reads(normal.from_node.inputs["Color"]) if normal.from_node.type == "NORMAL_MAP" else None
        if read is not None and read[0] == "image" and not read[2] and not normal.from_node.inputs["Strength"].is_linked and not skewed(read):
            described["normal_texture"] = picture(read, T("노멀"))
            described["normal_strength"] = float(normal.from_node.inputs["Strength"].default_value)
        else:  # a bump, a mix of normal maps: the normal the surface ends up with
            described["normal_texture"] = cooked(T("노멀"), "Normal", None, ("node", normal.from_node), False)
            if described["normal_texture"] is not None:
                described["normal_strength"] = 1.0

    colour, strength = shader.inputs.get("Emission Color") or shader.inputs.get("Emission"), shader.inputs.get("Emission Strength")
    power = _reads(strength) if strength is not None else ("value", 1.0)
    glow = _reads(colour) if colour is not None else ("value", [0.0, 0.0, 0.0, 1.0])
    if power[0] != "value":
        through(T("발광 세기"), power if power[0] == "node" else ("node", _link(strength).from_node))
    elif power[1] > 0 and (glow[0] != "value" or any(c > 0 for c in glow[1][:3])):
        lit = None
        if glow[0] == "image" and not glow[2] and not skewed(glow):
            lit = picture(glow, T("발광"))
        elif glow[0] != "value":
            feed = _link(colour)
            lit = cooked(T("발광"), "Emission", feed.from_socket, ("node", feed.from_node), True)
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
        through(T("투명도"), alpha if alpha[0] == "node" else ("node", alpha[1]))
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
    baked = [f"{material['name']}: {T(what)}" for material in materials for key, what in BAKED.items()
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
        result["note"] = T("이 .blend는 저장된 적이 없어, 같은 것을 다시 보내면 Unity에 새 모델이 하나 더 생깁니다. 저장한 뒤 보내면 제자리에서 바뀝니다.")
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
            raise ValueError(T("이 manifest는 '{format}' 형식이라 모델로 받을 수 없습니다. 다른 Blender가 export_blend로 보낸 것은 import_blend로 받으세요.",
                               format=data.get('format')))
        path = os.path.join(os.path.dirname(path), data.get("model_file") or "")
    if not os.path.isfile(path):
        raise ValueError(T("모델 파일이 없습니다: {path}", path=path))
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
        raise ValueError(T("'{extension}' 파일은 받을 수 없습니다. FBX, OBJ, GLB만 됩니다.", extension=extension))
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
    return {"EDITOR_BUSY": T("Unity가 컴파일 중이에요"), "UNREACHABLE": T("Unity가 응답하지 않아요"),
            "TIMEOUT": T("Unity가 제때 답하지 않았어요(대화상자가 떠 있을 수 있어요)"),
            "CONFLICT": T("그 프로젝트에서 Claude가 일하는 중이에요. 끝난 뒤 다시 해 주세요"),
            "NOT_FOUND": T("그 프로젝트의 Claude Hand Tools가 오래된 버전이에요")}.get(exc.code) or exc.message or exc.code


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


WHY_LOOKED = {"save": N_("저장"), "undo": N_("되돌리기"), "open": N_("파일 열기"), "unity": N_("Unity 열림"), "look": N_("확인")}


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
                T("'{key}'(으)로 보낸 오브젝트가 둘이에요({names}). 이 모델로 갈 것을 고르고 [선택한 오브젝트로 잇기]를 누르세요.", key=key, names=', '.join(twins[:3]))
                if twins else T("이 파일에 '{key}' 오브젝트가 없어요. 이름을 바꿨으면 그 오브젝트를 고르고 [선택한 오브젝트로 잇기]를 누르세요.", key=key)))
            break
        row["objects"].append(obj.name)
    row["label"] = _names(row["objects"] or row["roots"])
    return row


def _names(names):
    """A model by the objects it is made of here, as the user knows it: "Chair", "Chair, Table", "Chair, Table 외 2개"."""
    return ", ".join(names[:2]) + (T(" 외 {count}개", count=len(names) - 2) if len(names) > 2 else "")


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
    later = any(str(note).endswith(T(LATER)) for note in sent["not_carried"])  # a bake that waits for Object Mode
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
                row["problem"] = T("내보내지 못했어요: {exc}", exc=exc)
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
                                                                 "why": T(WHY_LOOKED.get(reason, WHY_LOOKED["save"]))}, timeout=120, tries=BUSY_TRIES), None))
        except UnityError as exc:
            results.append((job, {}, exc))
    _inbox.put(("follow_sent", {"results": results, "reason": reason}))


def _follow_sent(payload):
    """Main thread: how the sends went. A model Unity took is in step from now on; one it could not take is sent
    again with the next look."""
    why = T(WHY_LOOKED.get(payload["reason"], WHY_LOOKED["save"]))
    for job, data, error in payload["results"]:
        row, known = job["row"], _follow["models"].setdefault((job["row"]["hash"], job["row"]["asset"]), {"seq": -1, "snapshot": None, "took": 0.0})
        _discard(job["files"])
        state = data.get("state")
        if error is not None:
            known.update(seq=-1, snapshot=None)
            _tell(f"follow-failed:{row['hash']}:{row['asset']}",
                  T("{why} → {project}: {label}을(를) 보내지 못했어요({reason}). 다음 저장 때 다시 보냅니다.",
                    why=why, project=row['project'], label=row['label'], reason=_why(error)))
        elif state == "updated":
            known.update(seq=job["seq"], snapshot=job["snapshot"])
            uses = int(data.get("instance_count") or 0)
            _tell(f"follow:{row['hash']}:{row['asset']}",
                  T("{why} → {project}: {label} 반영", why=why, project=row['project'], label=row['label']) + (T(" (씬 오브젝트 {uses}개)", uses=uses) if uses else ""))
            check = data.get("shape_check") or {}
            if check.get("same") is False:  # Unity measured the model it imported, and it is not what was sent
                odd = [str(part.get("name")) for part in check.get("differing") or []] + [str(name) for name in check.get("missing") or []]
                _tell(f"follow-shape:{row['hash']}:{row['asset']}",
                      T("{project}: {label}이(가) Unity에 보낸 것과 다르게 들어갔어요({parts}). Claude에게 알려 주세요.",
                        project=row['project'], label=row['label'], parts=_names(odd)))
            if _kept_there(data):  # else a material changed here and saved looks as if it had not arrived
                _tell(f"follow-kept:{row['hash']}:{row['asset']}", _kept_line(row["project"], _kept_there(data)))
            if job.get("later"):  # else nodes changed here and saved look as if they had not arrived
                _tell(f"follow-later:{row['hash']}:{row['asset']}",
                      T("{project}: {label}의 노드로 만든 재질은 오브젝트 모드에서 저장하면 구워서 갑니다(지금은 Unity에 있던 이미지 그대로).", project=row['project'], label=row['label']))
        elif state == "unchanged":
            known.update(seq=job["seq"], snapshot=job["snapshot"])
        elif state == "missing":
            _tell(f"follow-gone:{row['hash']}:{row['asset']}", T("{project}에 {label} 모델이 더 없어서 보내지 않았어요.", project=row['project'], label=row['label']))
    _worker_over()


def _kept_there(data):
    """The materials of a model that Unity left as they are, by its answer: they were edited there since they were made."""
    made = data.get("materials_made")
    return [str(name) for name in (made.get("kept") if isinstance(made, dict) else None) or []]


def _kept_line(project, names):
    return (T("{project}에서 고친 재질은 Unity 것 그대로 뒀어요({names}). Blender 재질대로 다시 만들려면 Unity에서 그 .mat 파일을 지우고 다시 보내세요.", project=project, names=_names(names)))


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
            entry["text"] = T("{text} · {count}번째 ({stamp})", text=text, count=entry['count'], stamp=stamp)
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
        _follow["status"] = T("Unity로 보낸 모델을 살펴보다 오류가 났어요: {exc}", exc=exc)
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
        return T("먼저 이 .blend를 저장하세요. 저장된 파일이어야 Unity가 같은 모델인지 알고, 저장할 때 다시 보낼 수 있어요.")
    entry = next((e for e in unity_projects() if e.get("hash") == project), None)
    if entry is None:
        return T("그 Unity 프로젝트가 열려 있지 않아요.")
    try:
        result = export_fbx(roots, roots[0])
    except Exception as exc:
        return T("내보내지 못했어요: {exc}", exc=exc)
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
        text += T(" · {source}에서 온 것으로 적혀 있음", source=os.path.basename(row['source']) or T('다른 파일'))
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
        _notice(T("{project}에 {label}을(를) 보냈어요", project=row['project'], label=row['label']) + (T("(있던 모델을 바꿈)") if data.get("updated") else T("(씬에 놓음)")) +
                T(". 저장할 때마다 다시 갑니다. Unity 채팅창의 되돌리기로 뺄 수 있어요."))
        # No Claude reads this answer: what a subagent would report of it is said here.
        baked, lost, check = data.get("baked") or [], data.get("not_carried") or [], data.get("shape_check") or {}
        if baked:
            _notice(T("노드로 만든 재질 입력은 텍스처로 구워 보냈어요(그때 모습으로 굳은 이미지): ") + ", ".join(str(b) for b in baked[:6]) +
                    (T(" 외 {count}개", count=len(baked) - 6) if len(baked) > 6 else ""))
        for line in lost[:6]:
            _notice(T("못 옮긴 것 · ") + str(line))
        if _kept_there(data):
            _notice(_kept_line(row["project"], _kept_there(data)))
        if check.get("same") is False:
            odd = [str(part.get("name")) for part in check.get("differing") or []] + [str(name) for name in check.get("missing") or []]
            _notice(T("Unity에 보낸 것과 다르게 들어갔어요({parts}). Claude에게 알려 주세요.", parts=_names(odd)))
        _note_for_claude(T("[사용자가 Blender 패널에서 '{label}'을(를) Unity {project}에 직접 보냈음: {asset} (Claude를 거치지 않음, 저장할 때마다 다시 감)]",
                           label=row['label'], project=row['project'], asset=asset))
    _follow_due("look")


def apply_event(event, payload):
    if event == "status":
        if callable(payload):
            _status_maker[:] = [payload, payload()]
            _status[0] = _status_maker[1]
        else:
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
            _notice(T("다른 창은 되돌리지 못했습니다: ") + str(payload.get("error", T("Claude Hand가 받지 않았습니다"))))
    elif event in ("undone_sent", "protect_sent"):
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
            _notice(T("보내지 못했습니다. 앞 메시지가 Claude Hand가 켜지기를 기다리는 중이니 잠시 뒤 다시 보내 주세요."))
    elif event == "stop_sent":
        if not payload.get("ok"):
            _status[0] = T("중지를 보내지 못했습니다: ") + str(payload.get("error", T("알 수 없는 오류")))
    elif event == "sent":
        if not payload.get("ok"):
            _status[0] = T("보내지 못했습니다: ") + str(payload.get("error", T("알 수 없는 오류")))
        elif payload.get("delivered") is False:
            _status[0] = T("Claude 세션이 아직 연결 중입니다. 잠시 뒤 답이 옵니다.")
    elif event == "items":
        now = time.time()
        for item in payload:
            if item.get("kind") == "permission_resolved":
                for entry in _entries:
                    if entry.get("kind") == "permission" and entry.get("request_id") == item.get("request_id"):
                        entry["behavior"] = item.get("behavior")
                continue
            if item.get("kind") == "protect_resolved":
                for entry in _entries:
                    if entry.get("kind") == "protect_offer" and entry.get("offer_id") == item.get("offer_id"):
                        entry["behavior"] = item.get("behavior")
                continue
            if item.get("kind") == "reply" and item.get("origin") != "desktop":
                _last_user[0] = 0.0
            _entries.append(dict(item))
        del _entries[:-KEPT_ENTRIES]
        if any(wants_attention(item, now) for item in payload) and setting_on("flash_taskbar"):
            flash_taskbar()


def restate_status():
    """The language was changed: the status line that says whose window this is stays up for hours, so it says itself
    again in the language now in use, if it is still the line shown (another line that took its place is left)."""
    if _status_maker[0] is not None and _status[0] == _status_maker[1]:
        _status_maker[1] = _status[0] = _status_maker[0]()


def tick():
    """Timer on Blender's main thread: refresh identity, apply worker results, redraw the sidebar."""
    window, pin = window_identity()
    with _identity_lock:
        _identity["window"], _identity["pin"] = window, pin
    changed = False
    if _labels_language[0] is not None and language() != _labels_language[0]:
        relabel()  # the language was changed in a settings window: the panels' and buttons' names follow
        restate_status()
        changed = True
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
        _follow["status"] = T("Unity로 보낸 모델을 살펴보다 오류가 났어요: {exc}", exc=exc)
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
    if text.strip() and not _relabeling[0]:
        send_text(text, self.claude_hand_chat_context)
        bpy.app.timers.register(_clear_input, first_interval=0.01)


def _clear_input():
    wm = bpy.context.window_manager
    if wm.claude_hand_chat_input:
        wm.claude_hand_chat_input = ""
    return None


class CLAUDEHAND_OT_send(bpy.types.Operator):
    bl_idname = "claude_hand.chat_send"
    bl_label = N_("보내기")
    bl_description = "Send the message to this window's claude-hand subagent"

    def execute(self, context):
        wm = context.window_manager
        if send_text(wm.claude_hand_chat_input, wm.claude_hand_chat_context):
            wm.claude_hand_chat_input = ""
        return {"FINISHED"}


class CLAUDEHAND_OT_start(bpy.types.Operator):
    bl_idname = "claude_hand.start_supervisor"
    bl_label = N_("Claude Hand 시작")
    bl_description = "Open the Claude Hand supervisor window (start-supervisor)"

    def execute(self, context):
        start_supervisor()
        return {"FINISHED"}


class CLAUDEHAND_OT_permission(bpy.types.Operator):
    bl_idname = "claude_hand.chat_permission"
    bl_label = N_("권한 응답")
    bl_description = "Allow or deny the tool Claude asked to run"

    request_id: bpy.props.StringProperty()
    behavior: bpy.props.EnumProperty(items=[("allow", "Allow", ""), ("deny", "Deny", "")])  # the buttons are drawn with their own words

    def execute(self, context):
        post_in_background("/v1/permission", {"request_id": self.request_id, "behavior": self.behavior}, "permission_sent")
        return {"FINISHED"}


class CLAUDEHAND_OT_copy(bpy.types.Operator):
    bl_idname = "claude_hand.chat_copy"
    bl_label = N_("전체 복사")
    bl_description = "Copy the whole message to the clipboard (the panel shows only its start)"

    seq: bpy.props.IntProperty()

    def execute(self, context):
        entry = next((e for e in _entries if e.get("seq") == self.seq), None)
        if entry is None:
            return {"CANCELLED"}
        context.window_manager.clipboard = "\n\n".join(filter(None, [entry.get("request"), entry_text(entry)]))
        self.report({"INFO"}, T("메시지 전체를 클립보드에 복사했습니다"))
        return {"FINISHED"}


class CLAUDEHAND_OT_open_image(bpy.types.Operator):
    bl_idname = "claude_hand.chat_open_image"
    bl_label = N_("크게 보기")
    bl_description = "Open the result preview full size in the system's image viewer"

    path: bpy.props.StringProperty()

    def execute(self, context):
        if not os.path.exists(self.path):
            self.report({"WARNING"}, T("그림 파일이 지워졌습니다"))
            return {"CANCELLED"}
        if sys.platform == "win32":
            os.startfile(self.path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", self.path])
        return {"FINISHED"}


class CLAUDEHAND_OT_native_input(bpy.types.Operator):
    bl_idname = "claude_hand.chat_native_input"
    bl_label = N_("한글 입력창")
    bl_description = ("Type in a Windows text box: Blender's own field loses spaces and symbols with the Korean IME and "
                      "shows a syllable late. Enter sends to this chat, Shift+Enter starts a new line")

    @classmethod
    def poll(cls, context):
        return sys.platform == "win32"

    def execute(self, context):
        return {"FINISHED"} if open_native_input() else {"CANCELLED"}


class CLAUDEHAND_OT_undo(bpy.types.Operator):
    bl_idname = "claude_hand.chat_undo"
    bl_label = N_("되돌리기")
    bl_description = ("Undo everything changed in this Blender since Claude's last request started (Blender's own undo "
                      "history). A request asked here that changed other windows too is undone there as well")

    def invoke(self, context, event):
        mark, offer = undo_plan()
        if mark is None and offer is None:
            return {"CANCELLED"}
        if offer is None:
            message = T("'{mark}' 이후 바뀐 것을 모두 되돌립니다. 그 뒤에 직접 고친 것도 함께 되돌아갑니다.", mark=describe_mark(mark))
        else:
            others = ", ".join(str(w) for w in offer.get("windows") or [])
            message = T("'{label}' 요청이 바꾼 것을 되돌립니다. ", label=offer.get('label') or T('요청')) + (
                T("이 Blender에서는 그 뒤에 직접 고친 것도 함께 되돌아갑니다. ") if mark is not None else "") + (
                T("다른 창({others})은 그 창에 더 새 요청이 있거나 작업 중이면 건너뛰고, 그렇게 알려 줍니다.", others=others))
        try:
            return context.window_manager.invoke_confirm(self, event, title=T("Claude Hand 되돌리기"), message=message,
                                                         confirm_text=T("되돌리기"))
        except TypeError:  # Blender before 4.1: no title or message
            return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        # Not inside the operator: undo frees the data an operator still holds.
        bpy.app.timers.register(_undo_from_button, first_interval=0.0)
        return {"FINISHED"}


class CLAUDEHAND_OT_stop(bpy.types.Operator):
    bl_idname = "claude_hand.chat_stop"
    bl_label = N_("중지")
    bl_description = ("Stop the work this window's request started: the next Unity/Blender action of the subagents is "
                      "refused and Claude Hand is told. Nothing is undone")

    def execute(self, context):
        window, pin = window_identity()
        post_in_background("/v1/stop", {"app": "blender", "window": window, "pin": pin}, "stop_sent")
        return {"FINISHED"}


def protect_offer_answer(offer_id, behavior):
    """[추가] or [취소] under a rule Claude proposed in this chat (the supervisor's propose_protection): the rule
    is written here, by the user's press, and the channel is told how it went. Returns what stands in the way."""
    entry = next((e for e in _entries if e.get("kind") == "protect_offer" and str(e.get("offer_id")) == str(offer_id)), None)
    if entry is None or entry.get("behavior"):
        return T("이미 답한 제안입니다.")
    if behavior == "added":
        rule = entry.get("rule") if isinstance(entry.get("rule"), dict) else {}
        problem = protect_add(str(rule.get("kind") or ""), rule.get("value"), rule.get("lasting") is not False)
        if problem:
            return problem
    entry["behavior"] = behavior
    window, pin = window_identity()
    post_in_background("/v1/protect", {"app": "blender", "window": window, "pin": pin, "offer_id": str(offer_id), "behavior": behavior},
                       "protect_sent")
    _redraw()
    return None


class CLAUDEHAND_OT_protect_offer(bpy.types.Operator):
    bl_idname = "claude_hand.protect_offer"
    bl_label = N_("보호 규칙 제안에 답하기")
    bl_description = "Add the protection rule Claude proposed to this window's rules, or leave it"

    offer_id: bpy.props.StringProperty()
    behavior: bpy.props.StringProperty()

    def execute(self, context):
        problem = protect_offer_answer(self.offer_id, self.behavior)
        if problem:
            self.report({"WARNING"}, problem)
            return {"CANCELLED"}
        return {"FINISHED"}


class CLAUDEHAND_OT_protect_add(bpy.types.Operator):
    bl_idname = "claude_hand.protect_add"
    bl_label = N_("보호 규칙 더하기")
    bl_description = ("Add a protection rule for this window: Claude's calls that would change what it covers are refused, "
                      "and a change that gets through is taken back")

    kind: bpy.props.StringProperty()   # "selected" (the selected objects), "window", "object", "action"
    value: bpy.props.StringProperty()

    def execute(self, context):
        lasting = not context.window_manager.claude_hand_protect_session
        values = selected_roots() if self.kind == "selected" else [self.value]
        if not values:
            self.report({"WARNING"}, T("보호할 오브젝트를 먼저 고르세요."))
            return {"CANCELLED"}
        for value in values:
            problem = protect_add("object" if self.kind == "selected" else self.kind, value, lasting)
            if problem:
                self.report({"WARNING"}, problem)
                return {"CANCELLED"}
        return {"FINISHED"}


class CLAUDEHAND_OT_protect_remove(bpy.types.Operator):
    bl_idname = "claude_hand.protect_remove"
    bl_label = N_("풀기")
    bl_description = "Take this protection rule away again: from then on Claude may change what it covered"

    kind: bpy.props.StringProperty()
    value: bpy.props.StringProperty()
    lasting: bpy.props.BoolProperty(default=True)

    def invoke(self, context, event):
        message = T("'{rule}' 규칙을 풉니다. Claude가 다시 바꿀 수 있게 됩니다.",
                    rule=protect_row_label(self.kind, self.value, self.lasting))
        try:
            return context.window_manager.invoke_confirm(self, event, title=T("보호 규칙 풀기"), message=message, confirm_text=T("풀기"))
        except TypeError:  # Blender before 4.1: no title or message
            return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        problem = protect_remove(self.kind, self.value, self.lasting)
        if problem:
            self.report({"WARNING"}, problem)
            return {"CANCELLED"}
        return {"FINISHED"}


PROTECT_MODE_LINES = {"open": N_("Claude가 이 창을 바꿀 수 있습니다. 아래 규칙이 가리키는 것만 빼고요."),
                      "look": N_("보기만: Claude는 이 창을 읽기만 합니다."),
                      "locked": N_("잠금: Claude는 이 창에서 아무 일도 하지 않습니다.")}


def draw_protect(layout, width, wm):
    """This window's protection rules, a line each with its [풀기], and the buttons that add one. Said in words and
    with the padlock's shape (open or shut), never by a colour."""
    rules = protection_rules()
    mode = rules["window"]
    for i, line in enumerate(wrap(T(PROTECT_MODE_LINES[mode]), width)):
        layout.label(text=line, icon=("UNLOCKED" if mode == "open" else "LOCKED") if i == 0 else "BLANK1")
    col = layout.column(align=True)
    for kind, value, lasting in rules["rows"]:
        row = col.row(align=True)
        row.label(text=protect_row_label(kind, value, lasting), icon="LOCKED")
        op = row.operator(CLAUDEHAND_OT_protect_remove.bl_idname, text=T("풀기"))
        op.kind, op.value, op.lasting = kind, value, lasting
    for problem in rules["problems"]:
        for i, line in enumerate(wrap(T("읽지 못한 규칙 파일(고칠 때까지 Claude의 바꾸는 호출을 막습니다): ") + problem, width)[:4]):
            layout.label(text=line, icon="ERROR" if i == 0 else "BLANK1")
    name = T("이번만 (Claude Hand를 끌 때까지)")
    head, bracket, rest = name.partition(" (")
    if width is None or not bracket or text_measure(width[1])(name) <= width[0] - 24:  # 24: the checkbox
        layout.prop(wm, "claude_hand_protect_session", text=name)
    else:  # the sidebar is narrower than these words (the English is longer): the bracket goes on a line below
        col = layout.column(align=True)
        col.prop(wm, "claude_hand_protect_session", text=head)
        col.label(text="(" + rest, icon="BLANK1")
    col = layout.column(align=True)
    col.operator(CLAUDEHAND_OT_protect_add.bl_idname, text=T("선택한 오브젝트 보호"), icon="LOCKED").kind = "selected"
    for value, label, icon in (("look", T("이 창 보기만"), "HIDE_OFF"), ("locked", T("이 창 잠금"), "LOCKED")):
        if mode != value:
            op = col.operator(CLAUDEHAND_OT_protect_add.bl_idname, text=label, icon=icon)
            op.kind, op.value = "window", value
    if "save_file" not in rules["actions"]:
        op = col.operator(CLAUDEHAND_OT_protect_add.bl_idname, text=T(".blend 저장 금지"), icon="FILE_BLEND")
        op.kind, op.value = "action", "save_file"


def _follow_row(index):
    return _follow["rows"][index] if 0 <= index < len(_follow["rows"]) else None


class CLAUDEHAND_OT_follow_toggle(bpy.types.Operator):
    bl_idname = "claude_hand.follow_toggle"
    bl_label = N_("저장할 때 다시 보내기")
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
    bl_label = N_("이 파일로 잇기")
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
    bl_label = N_("선택한 오브젝트로 잇기")
    bl_description = "The active object is the one this Unity model is made of (it was renamed, or a copy carries its mark)"

    index: bpy.props.IntProperty()

    def execute(self, context):
        row, obj = _follow_row(self.index), context.view_layer.objects.active
        if row is None or not row["missing"]:
            return {"CANCELLED"}
        if obj is None:
            self.report({"WARNING"}, T("이 모델로 갈 오브젝트를 먼저 고르세요"))
            return {"CANCELLED"}
        follow_adopt(row["missing"], obj)
        return {"FINISHED"}


class CLAUDEHAND_OT_follow_send(bpy.types.Operator):
    bl_idname = "claude_hand.follow_send"
    bl_label = N_("선택한 오브젝트 보내기")
    bl_description = ("Send the selected objects to this Unity project now, without Claude: the model is brought in, "
                      "placed in the scene, and follows this file from then on. Unity's chat window can undo it")

    project: bpy.props.StringProperty()

    def execute(self, context):
        roots = selected_roots()
        problem = send_to_unity(roots, self.project) if roots else T("보낼 오브젝트를 먼저 고르세요.")
        if problem:
            self.report({"WARNING"}, problem)
            return {"CANCELLED"}
        return {"FINISHED"}


class CLAUDEHAND_OT_follow_look(bpy.types.Operator):
    bl_idname = "claude_hand.follow_look"
    bl_label = N_("새로 고침")
    bl_description = "Ask the open Unity projects again which models came from this file, and send the ones that follow it"

    def execute(self, context):
        _follow_due("look")
        return {"FINISHED"}


def draw_follow(layout, width):
    """The models the open Unity projects have from this file, a check box each: checked, the model is sent again
    whenever the file is saved. What stands in a model's way is said in words under it, with the button that sets it
    right."""
    if not _follow["editors"]:
        for line in wrap(T("열려 있는 Unity가 없습니다(Claude Hand Tools 0.16 이상이 든 프로젝트)."), width):
            layout.label(text=line)
        return
    rows = _follow["rows"]
    col = layout.column(align=True)
    if not bpy.data.filepath:
        for line in wrap(T("저장한 적 없는 파일입니다. 저장하면 여기서 Unity로 보낼 수 있어요."), width):
            col.label(text=line)
    elif not rows:
        col.label(text=T("이 파일에서 Unity로 간 모델이 없습니다."))
    for index, row in enumerate(rows):
        if row["here"]:
            col.operator(CLAUDEHAND_OT_follow_toggle.bl_idname, text=follow_label(row), emboss=False,
                         icon="CHECKBOX_HLT" if row["follow"] else "CHECKBOX_DEHLT").index = index
        else:
            for i, line in enumerate(wrap(follow_label(row), width)[:3]):
                col.label(text=line, icon="DOT" if i == 0 else "BLANK1")
            col.operator(CLAUDEHAND_OT_follow_relink.bl_idname, text=T("이 파일로 잇기"), icon="LINKED").index = index
        if row["problem"]:
            for line in wrap(row["problem"], width)[:5]:
                col.label(text=line)
            if row["missing"]:
                col.operator(CLAUDEHAND_OT_follow_adopt.bl_idname, text=T("선택한 오브젝트로 잇기"), icon="RESTRICT_SELECT_OFF").index = index
    if any(row["here"] for row in rows):
        for line in wrap(T("체크한 모델은 이 파일을 저장할 때마다 Unity에서 바뀝니다."), width):
            layout.label(text=line)
    for line in wrap(_follow["status"], width)[:3] if _follow["status"] else ():
        layout.label(text=line)
    col = layout.column(align=True)
    for name, project in _follow["editors"]:
        col.operator(CLAUDEHAND_OT_follow_send.bl_idname, text=T("선택한 오브젝트 보내기 → {name}", name=name), icon="EXPORT").project = project
    col.operator(CLAUDEHAND_OT_follow_look.bl_idname, text=T("새로 고침"), icon="FILE_REFRESH")


class CLAUDEHAND_PT_follow(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Claude Hand"
    bl_label = N_("Unity로 보낸 모델")
    bl_parent_id = "CLAUDEHAND_PT_chat"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_follow(self.layout, panel_metrics(context))


class CLAUDEHAND_PT_protect(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Claude Hand"
    bl_label = N_("보호")
    bl_parent_id = "CLAUDEHAND_PT_chat"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        rules = protection_rules()
        self.layout.label(text="", icon="LOCKED" if rules["rows"] else "UNLOCKED")

    def draw(self, context):
        draw_protect(self.layout, panel_metrics(context), context.window_manager)


class CLAUDEHAND_PT_chat(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Claude Hand"
    bl_label = "Claude Hand Chat"

    def draw_header_preset(self, context):
        # The gear at the right of the panel's header: the settings window (CLAUDEHAND_PT_settings) opens under it.
        self.layout.popover(panel="CLAUDEHAND_PT_settings", text="", icon="PREFERENCES")

    def draw(self, context):
        layout = self.layout
        width = panel_metrics(context)
        for line in wrap(T(_status[0]), width):  # the line may have been set before the language was changed
            layout.label(text=line)
        if _server_missing[0]:
            opening = starting()
            row = layout.row()
            row.scale_y = 1.6
            row.enabled = not opening
            row.operator(CLAUDEHAND_OT_start.bl_idname, text=T("여는 중... 새 창을 확인하세요") if opening else T("Claude Hand 시작"), icon="PLAY")
            hint = (T("Claude Hand 창에서 확인을 눌러 주세요. 세션이 일을 시작하면 그 창은 저절로 최소화됩니다. 닫지는 마세요.")
                    if opening else T("Claude Hand(감독 세션)가 꺼져 있습니다. 버튼을 누르거나 메시지를 보내면 켜집니다."))
            for line in wrap(hint, width):
                layout.label(text=line)
        box = layout.box()
        if not _entries and _pending[0] is None:
            box.label(text=T("아직 대화가 없습니다."))
        shown, folded = visible_entries(width)
        if folded:
            box.label(text=T("이전 대화 {folded}개는 접어 두었습니다", folded=folded), icon="TRIA_UP")
        for entry, rows, hidden in shown:
            col = box.column(align=True)
            for text, icon in rows:
                col.label(text=text, icon=icon)
            if entry.get("kind") == "image":
                draw_image(col, entry, width)
            if hidden:
                row = col.row(align=True)
                row.label(text=T("…{hidden}줄 더 있음", hidden=hidden))
                row.operator(CLAUDEHAND_OT_copy.bl_idname, text=T("전체 복사"), icon="COPYDOWN").seq = int(entry.get("seq") or 0)
            if entry.get("kind") == "permission":
                if entry.get("behavior"):
                    col.label(text=T("→ 허용됨") if entry["behavior"] == "allow" else T("→ 거부됨"))
                else:
                    row = col.row(align=True)
                    for behavior, label in (("allow", T("허용")), ("deny", T("거부"))):
                        op = row.operator(CLAUDEHAND_OT_permission.bl_idname, text=label)
                        op.request_id = entry.get("request_id", "")
                        op.behavior = behavior
            if entry.get("kind") == "protect_offer":
                if entry.get("behavior"):
                    col.label(text=T("→ 규칙에 더함") if entry["behavior"] == "added" else T("→ 더하지 않음"))
                else:
                    row = col.row(align=True)
                    for behavior, label in (("added", T("추가")), ("declined", T("취소"))):
                        op = row.operator(CLAUDEHAND_OT_protect_offer.bl_idname, text=label)
                        op.offer_id = str(entry.get("offer_id", ""))
                        op.behavior = behavior
        if _pending[0] is not None:
            col = box.column(align=True)
            col.label(text=T("나 (Claude Hand가 켜지면 보냅니다)"), icon="TIME")
            for line in wrap(_pending[0][0], width)[:ENTRY_LINES]:
                col.label(text=line)
        if _last_user[0] and time.time() - _last_user[0] > NO_REPLY_HINT_SECONDS:
            for line in wrap(T("1분 넘게 답이 없습니다. 감독 세션을 start-supervisor.cmd로 열었는지, Claude Code 창에 확인을 기다리는 질문이 없는지 봐 주세요."), width):
                layout.label(text=line)
        draw_others(layout, width)
        draw_progress(layout, width)
        mark, offer = undo_plan()
        if (mark is not None or offer is not None) and _progress[0] is None:
            layout.operator(CLAUDEHAND_OT_undo.bl_idname, text=T("되돌리기: {plan}", plan=describe_plan(mark, offer)), icon="LOOP_BACK")
        wm = context.window_manager
        row = layout.row(align=True)
        row.prop(wm, "claude_hand_chat_input", text="")
        if sys.platform == "win32":
            row.operator(CLAUDEHAND_OT_native_input.bl_idname, text=T("한글"), icon="WINDOW")
        row = layout.row()
        row.prop(wm, "claude_hand_chat_context", text=T("선택 항목 함께"))
        row.operator(CLAUDEHAND_OT_send.bl_idname, text=T("보내기"), icon="PLAY")


# --------------------------------------------------------------------------- the settings window (main thread)
#
# The gear button of the chat panel opens it: two tabs, "기능 켜고 끄기" with a row per setting of the catalog and
# "실험실" with the features still in the lab. Each row is a property that reads and writes config.json itself, so
# the window shows what any window set, and a change made here is in the file at once (the chat channel, the
# commands, the hooks and Unity's chat window read it again). What a setting is, is said in words beside it.

SETTINGS_TABS = (("features", N_("기능 켜고 끄기")), ("lab", N_("실험실")))
SETTINGS_UNITS = 19  # how wide the window is at least, in Blender's interface units (20 pixels each at scale 1)
SETTINGS_WORDS = 6   # of them, the column that says in words what a setting is now


def settings_units():
    """How wide the settings window has to be for the longest name in the language now in use: an English name is
    longer than its Korean. A Hangul syllable takes about 11 pixels of the interface font, a Latin letter about 6."""
    catalog = read_catalog()
    names = [pick(entry.get("name")) for entry in catalog["settings"] + catalog["lab"]]
    longest = max((sum(0.55 if ord(ch) > 0x2E7F else 0.31 for ch in name) for name in names), default=0.0)
    return max(SETTINGS_UNITS, int(longest + 1.6 + SETTINGS_WORDS) + 1)  # 1.6: the checkbox and the margins
_setting_props = []  # the WindowManager properties made for the catalog's entries, to take away again


def setting_prop(key, lab=False):
    """The name of the WindowManager property of a setting (or of a lab feature)."""
    return ("claude_hand_lab_" if lab else "claude_hand_set_") + re.sub(r"[^a-z0-9_]", "_", str(key).lower())


def _put_setting(key, value, lab=False):
    problem = set_lab(key, value) if lab else set_setting(key, value)
    if problem:
        _status[0] = problem
    _redraw()


def _setting_property(entry, lab=False):
    """The property the settings window draws for one entry: a check box, a number of days or a choice. None for a
    kind this add-on does not know (a newer catalog)."""
    name, hint, kind = pick(entry.get("name")), pick(entry.get("hint")), "switch" if lab else entry.get("kind")
    key = entry["key"]
    # Blender counts the arguments of these functions (one for get, two for set): nothing may be bound as a default.
    if kind == "switch":
        def read(self):
            return lab_value(entry) if lab else bool(setting_value(entry))

        def write(self, value):
            _put_setting(key, bool(value), lab)
        return bpy.props.BoolProperty(name=name, description=hint, get=read, set=write)
    if kind == "days":
        def read_days(self):
            return int(setting_value(entry))

        def write_days(self, value):
            _put_setting(key, int(value))
        return bpy.props.IntProperty(name=name, description=hint, min=0, max=3650, get=read_days, set=write_days)
    if kind == "choice":
        choices = [c for c in entry.get("choices") or [] if isinstance(c, dict) and "value" in c]
        values = [c["value"] for c in choices]

        def read_choice(self):
            return values.index(setting_value(entry)) if setting_value(entry) in values else 0

        def write_choice(self, index):
            _put_setting(key, values[index])
        return bpy.props.EnumProperty(name=name, description=hint, items=[(str(c["value"]), str(c.get("label", c["value"])), "") for c in choices],
                                      get=read_choice, set=write_choice)
    return None


LANGUAGE_ENTRY = {"key": "language", "kind": "choice", "default": "ko", "applies": "now", "name": {"ko": "언어 · Language", "en": "언어 · Language"},
                  "choices": [{"value": "ko", "label": "한국어"}, {"value": "en", "label": "English"}]}  # there whatever the catalog holds


def _define_props():
    """The WindowManager properties the panels draw, named in the language now in use: the chat's own, and one for
    each entry of the catalog."""
    wm = bpy.types.WindowManager
    wm.claude_hand_chat_input = bpy.props.StringProperty(
        name=T("메시지"), description=T("Enter로 보냅니다"), update=_on_input_update)
    wm.claude_hand_chat_context = bpy.props.BoolProperty(
        name=T("선택 항목 함께 보내기"), default=True)
    wm.claude_hand_protect_session = bpy.props.BoolProperty(
        name=T("이번만"), description=T("새로 더하는 보호 규칙을 Claude Hand를 끌 때까지만 둡니다(끄면 파일 옆에 적어 계속 둡니다)"), default=False)
    wm.claude_hand_settings_tab = bpy.props.EnumProperty(name=T("설정"), items=[(key, T(label), "") for key, label in SETTINGS_TABS])
    for attr in _setting_props:
        if hasattr(wm, attr):
            delattr(wm, attr)
    _setting_props.clear()
    catalog = read_catalog()
    listed = catalog["settings"] if any(e["key"] == "language" for e in catalog["settings"]) else [LANGUAGE_ENTRY, *catalog["settings"]]
    for entry, lab in [(e, False) for e in listed] + [(e, True) for e in catalog["lab"]]:
        prop = _setting_property(entry, lab)
        if prop is not None:
            setattr(wm, setting_prop(entry["key"], lab), prop)
            _setting_props.append(setting_prop(entry["key"], lab))


def setting_words(entry, value):
    """A setting's value in words, beside its check box or its number: "켜짐", "30일", "지우지 않음"."""
    if entry.get("kind") == "switch":
        return T("켜짐") if value else T("꺼짐")
    if entry.get("kind") == "days":
        return T("{days}일", days=value) if value else T("지우지 않음")
    return ""


def settings_metrics(context):
    """(text width in pixels, label font size in pixels) of the settings window, or None outside a UI context."""
    try:
        prefs = context.preferences
        scale = prefs.system.ui_scale or 1.0
        points = getattr(getattr(prefs.ui_styles[0], "widget", None), "points", 0) or 11
    except (AttributeError, IndexError):
        return None
    return (float((CLAUDEHAND_PT_settings.bl_ui_units_x * 20 - 24) * scale), float(points * scale))


def draw_settings(layout, wm, width=None):
    """The settings window. A row per entry: the property, and what it is now in words (a state is never told by a
    colour alone); a star marks what holds from the next start of Claude Hand, said in a line at the bottom."""
    catalog = read_catalog()
    layout.row().prop(wm, "claude_hand_settings_tab", expand=True)
    lab = getattr(wm, "claude_hand_settings_tab", "features") == "lab"
    starred = False

    def row_of(entry, words):
        nonlocal starred
        later = entry.get("applies") != "now"
        starred = starred or later
        units = CLAUDEHAND_PT_settings.bl_ui_units_x
        split = layout.split(factor=(units - SETTINGS_WORDS) / units)
        if not hasattr(wm, setting_prop(entry["key"], lab)):
            # the catalog has it and this Blender was started before it did: there once Blender is opened again
            split.label(text=pick(entry.get("name")))
        elif entry.get("kind") == "choice":  # a list: Blender gives its name too little room, so the name is a label of its own
            named = split.split(factor=0.45)
            named.label(text=pick(entry.get("name")))
            named.prop(wm, setting_prop(entry["key"], lab), text="")
        else:
            split.prop(wm, setting_prop(entry["key"], lab), text=pick(entry.get("name")))
        split.label(text=words + (" *" if later else ""))

    if lab:
        for line in wrap(T("아직 다듬는 중인 기능입니다. 켜야 쓰입니다."), width):
            layout.label(text=line)
        if not catalog["lab"]:
            layout.label(text=T("지금 실험실에 있는 기능이 없습니다."))
        for entry in catalog["lab"]:
            row_of(entry, T("켜짐") if lab_value(entry) else T("꺼짐"))
            col = layout.column(align=True)
            for line in wrap(T("{since}부터", since=entry.get("since", "?")) + " · " + pick(entry.get("hint")), width)[:6]:
                col.label(text=line)
    else:
        listed = catalog["settings"] if any(e["key"] == "language" for e in catalog["settings"]) else [LANGUAGE_ENTRY, *catalog["settings"]]
        for entry in listed:
            row_of(entry, setting_words(entry, setting_value(entry)))
        if not catalog["settings"]:
            for line in wrap(T("설정 목록을 읽지 못했습니다. claude-hand 폴더에서 uv run tools/claude_hand.py chat-setup 을 한 번 실행하세요."), width):
                layout.label(text=line)
    if starred:
        for line in wrap(T("* 다음에 Claude Hand를 켤 때부터 적용됩니다."), width):
            layout.label(text=line)


class CLAUDEHAND_PT_settings(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"  # shown nowhere by itself: it is what the gear of the chat panel opens
    bl_label = N_("Claude Hand 설정")
    bl_ui_units_x = SETTINGS_UNITS

    def draw(self, context):
        draw_settings(self.layout, context.window_manager, settings_metrics(context))


_LABELS = {}                # class -> its label as it is written here (Korean)
_labels_language = [None]   # the language the classes are registered under


def _register_classes():
    """Blender reads a class's label when the class is registered: each is registered under its label in the
    language now in use."""
    CLAUDEHAND_PT_settings.bl_ui_units_x = settings_units()  # read with the class, like its label
    for cls in CLASSES:
        cls.bl_label = T(_LABELS.setdefault(cls, cls.bl_label))
        bpy.utils.register_class(cls)
    _labels_language[0] = language()


def relabel():
    """The language was changed (in a settings window, here or in Unity, or with the command): the panels' and
    buttons' names follow at once. The classes are registered anew (about a millisecond), and the properties with
    their names; what the user typed and ticked stays."""
    wm = bpy.context.window_manager
    names = ("claude_hand_chat_input", "claude_hand_chat_context", "claude_hand_protect_session", "claude_hand_settings_tab")
    kept = {name: getattr(wm, name) for name in names if hasattr(wm, name)}
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _register_classes()
    _define_props()
    _relabeling[0] = True  # putting the typed text back is not the user pressing Enter (_on_input_update)
    try:
        for name, value in kept.items():
            if getattr(wm, name, value) != value:
                setattr(wm, name, value)
    finally:
        _relabeling[0] = False
    _redraw()


_relabeling = [False]

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
    col.label(text=T("다른 창 ({count})", count=len(_others)), icon="WINDOW")
    for text, icon in others_rows(_others, width):
        col.label(text=text, icon=icon)


def progress_view(progress, received_at, now):
    """What the subagent is doing, in one line: "작업 중 · 12초 · execute_code bpy.ops… · 3번째 동작"."""
    seconds = int(progress.get("elapsed", 0) + now - received_at)
    parts = [T("{state} · {seconds}초", state=T("중지하는 중") if progress.get("stopping") else T("작업 중"), seconds=seconds),
             f"{progress.get('tool', '')} {progress.get('detail', '')}".strip()]
    if progress.get("actions"):
        parts.append(T("{actions}번째 동작", actions=progress['actions']))
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
        col.operator(CLAUDEHAND_OT_stop.bl_idname, text=T("중지"), icon="CANCEL")


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
        layout.label(text=T("(그림 파일이 지워졌습니다)"))
        return
    width_px, font_px = width or (WRAP_WIDTH * CHAR_PX, 11.0)
    layout.template_icon(icon_value=preview.icon_id, scale=max(4.0, min(16.0, width_px / (20.0 * font_px / 11.0))))
    layout.operator(CLAUDEHAND_OT_open_image.bl_idname, text=T("크게 보기"), icon="ZOOM_IN").path = entry["path"]


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
        return [(entry.get("caption") or T("결과 미리보기"), "IMAGE_DATA")], 0
    if kind == "protect_offer":  # a rule Claude proposes: it holds only once the user presses [추가] under it
        lines = wrap(entry_text(entry), width)[:ENTRY_LINES]
        return [(T("Claude가 제안한 보호 규칙"), "LOCKED")] + [(line, "NONE") for line in lines], 0
    rows = []
    if kind == "reply" and entry.get("origin") == "desktop":
        rows.append((T("데스크톱 앱에서"), "WINDOW"))
        rows += [(line, "NONE") for line in wrap(entry.get("request", ""), width)[:3] if line]
    if kind == "permission":
        rows.append((T("'{tool}' 실행 허락 요청", tool=entry.get('tool_name')), "ERROR"))
    else:
        rows.append((T("나") if kind == "user" else "Claude", "USER" if kind == "user" else "INFO"))
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
        cost = len(rows) + (1 if hidden else 0) + (1 if entry.get("kind") in ("permission", "protect_offer") else 0) + \
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
           CLAUDEHAND_OT_protect_offer, CLAUDEHAND_OT_protect_add, CLAUDEHAND_OT_protect_remove,
           CLAUDEHAND_PT_chat, CLAUDEHAND_PT_follow, CLAUDEHAND_PT_protect, CLAUDEHAND_PT_settings)
HANDLERS = (("load_post", _forget_undo_points), ("load_post", _on_loaded), ("save_post", _on_saved),
            ("depsgraph_update_post", _on_touched))


def register():
    _register_classes()
    _define_props()
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
    for name in ("claude_hand_chat_input", "claude_hand_chat_context", "claude_hand_protect_session", "claude_hand_settings_tab", *_setting_props):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    _setting_props.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


# --------------------------------------------------------------------------- what this add-on says, in English
#
# Every Korean text that goes through T() above, exactly as it stands in the code, and its English: the same
# {names} in both. tools/tests/test_lang.py holds every text of this file to an entry here, and a text that the
# Python tools (tools/handlib/english.py) or the Unity package (ChText.cs) say too to the same English.

# What a text that counts something says in English when the count is one: (the name that holds the count, the text).
_EN_ONE = {
    "{days}일": ("days", "{days} day"),
}

_EN = {
    "설정을 적지 못했습니다: {exc}": "The settings could not be written: {exc}",
    "기본 색": "base color",
    "금속 · 거칠기": "metallic · roughness",
    "노멀": "normal",
    "발광": "emission",
    "연결 중...": "Connecting...",
    "채널 서버가 대기 중입니다: {reason}. 아래 버튼으로 켜거나, 메시지를 보내면 자동으로 켜집니다.":
        "The channel server is waiting: {reason}. Turn it on with the button below, or send a message and it turns on by itself.",
    "Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다.": "Claude Hand is off. Send a message and it turns on by itself.",
    "Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다. 켜도 연결되지 않으면 claude-hand 폴더에서 uv run tools/claude_hand.py chat-status 를 실행하세요.":
        "Claude Hand is off. Send a message and it turns on by itself. If it still does not connect once on, run uv run "
        "tools/claude_hand.py chat-status in the claude-hand folder.",
    "Claude Hand 창을 여는 중입니다. 그 창에 확인 질문이 보이면 화살표 키로 고르고 Enter를 눌러 주세요(개발용 채널 경고는 I am using this for local development). 연결되면 이 패널이 "
    "이어서 진행합니다.":
        "Opening the Claude Hand window. If it shows a question, choose with the arrow keys and press Enter (for the development channel "
        "warning: I am using this for local development). Once connected, this panel carries on.",
    "담당: {agent}": "Handled by: {agent}",
    "이 Blender 창의 하위 세션이 없습니다. uv run tools/claude_hand.py sync 를 실행하세요.":
        "This Blender window has no subagent. Run uv run tools/claude_hand.py sync.",
    "감독 세션에 연결할 수 없습니다: {exc}": "Cannot connect to the supervisor session: {exc}",
    "Claude Hand가 꺼져 있습니다.": "Claude Hand is off.",
    "보내지 못했습니다: {exc}": "Could not send: {exc}",
    "파일: {path}": "file: {path}",
    "(저장 안 됨)": "(not saved)",
    "씬: {name}": "scene: {name}",
    "활성 오브젝트: {name} ({type}, 모드 {mode})": "active object: {name} ({type}, mode {mode})",
    "선택한 오브젝트: ": "selected objects: ",
    "start-supervisor를 찾지 못했습니다. 바탕화면의 Claude Hand 아이콘을 누르세요.": "start-supervisor was not found. Click the Claude Hand icon on the Desktop.",
    "Claude Hand를 시작하지 못했습니다: {exc}": "Claude Hand could not be started: {exc}",
    "보내기": "Send",
    "Enter 보내기 · Shift+Enter 줄바꿈 · Esc 닫기": "Enter sends · Shift+Enter new line · Esc closes",
    "입력창을 열지 못했습니다: {exc}": "The input box could not be opened: {exc}",
    "{time} 요청": "request at {time}",
    "요청": "request",
    "되돌릴 Claude 요청이 없습니다.": "There is no request of Claude's to undo.",
    "'{mark}' 요청은 이미 되돌려져 있습니다.": "The request '{mark}' is undone already.",
    "Claude Hand: 되돌리기 전": "Claude Hand: before the undo",
    "'{mark}' 요청을 되돌리지 못했습니다. 그 뒤로 실행 취소 단계가 너무 많아 Blender의 실행 취소 기록에서 밀려났습니다.":
        "The request '{mark}' could not be undone. Too many undo steps came after it, and it has dropped out of Blender's undo history.",
    "[사용자가 채팅창에서 '{mark}' 요청 이후의 Blender 변경을 실행 취소로 되돌렸음]":
        "[The user undid, with undo in the chat panel, the changes made in Blender since the request '{mark}']",
    "되돌렸어요: '{mark}' 이후의 변경": "Undone: the changes since '{mark}'",
    "이 창 + ": "this window + ",
    "열림": "Open",
    "보기만": "Look only",
    "잠금": "Locked",
    ".blend 저장": "Save .blend",
    "없어짐": "gone",
    "이름": "name",
    "종류": "type",
    "부모": "parent",
    "컬렉션": "collections",
    "자리": "position",
    "모디파이어": "modifiers",
    "제약": "constraints",
    "재질 칸": "material slots",
    "재질": "materials",
    "보이기": "visibility",
    "사용자 속성": "custom properties",
    "데이터": "data",
    "메시": "mesh",
    "셰이프 키": "shape keys",
    "{name} (없어지거나 이름이 바뀜)": "{name} (gone or renamed)",
    "이 요청의 되돌리기 지점이 없음": "this request has no undo point",
    "실행 취소로 돌아가지 않은 것이 남음: ": "undo did not take back all of it: ",
    "저장한 적 없는 파일이라 계속 두는 규칙은 적을 곳이 없습니다. '이번만'을 켜고 다시 누르거나, 파일을 저장한 뒤 누르세요.":
        "This file has never been saved, so there is nowhere to write a lasting rule. Turn on 'This run only' and press again, or save the "
        "file first.",
    "규칙 파일이 아닌 것이 그 자리에 있습니다": "what is in that place is not a rules file",
    "규칙을 적지 못했습니다: {exc}": "The rule could not be written: {exc}",
    "'{value}'은(는) 창 규칙이 아닙니다.": "'{value}' is not a rule for a window.",
    "'{value}'은(는) Blender에서 막을 수 있는 동작이 아닙니다.": "'{value}' is not an action that can be stopped in Blender.",
    "더할 규칙이 없습니다.": "There is no rule to add.",
    "이 창: {mode}": "This window: {mode}",
    "금지: {action}": "Forbidden: {action}",
    "컬렉션 {name}": "collection {name}",
    "오브젝트 {value}": "object {value}",
    " (이번만)": " (this run only)",
    "이 파일에 '{name}' 오브젝트가 없습니다. get_scene_info로 정확한 이름을 확인하세요.":
        "This file has no object '{name}'. Check the exact name with get_scene_info.",
    "보낼 오브젝트 이름을 하나 이상 주세요.": "Give at least one name of an object to send.",
    "이 manifest는 '{format}' 형식이라 Blender로 받을 수 없습니다. export_blend가 만든 것만 됩니다.":
        "This manifest is of the '{format}' kind and cannot be received by Blender this way. Only one made by export_blend can.",
    "지금 뷰 레이어에 없는 오브젝트는 내보낼 수 없습니다: ": "Objects that are not in the view layer now cannot be exported: ",
    "오브젝트 모드가 아니라서 이번에는 굽지 않았습니다(Unity에 먼저 구운 이미지가 있으면 그것이 그대로 쓰입니다). 오브젝트 모드에서 저장하거나 보내면 구워집니다.":
        "It was not baked this time because Blender is not in Object Mode (if Unity has an image baked earlier, that one stays in use). It "
        "is baked when you save or send in Object Mode.",
    "이 재질을 쓰는 메시가 없어서": "because no mesh uses this material",
    "{names}이(가) 렌더나 뷰포트에서 꺼져 있어서(아웃라이너의 카메라 · 모니터 아이콘)":
        "because {names} is turned off in render or in the viewport (the camera and monitor icons of the outliner)",
    "{names}에 UV 맵이 없어서": "because {names} has no UV map",
    "Cycles가 꺼져 있어서(굽는 데 씁니다)": "because Cycles is turned off (it is what bakes)",
    "{what}: 이미지가 UV가 아닌 좌표({name})에 놓여 있어서, UV 그대로 놓은 것으로 갔습니다.":
        "{what}: the image is laid on coordinates other than UV ({name}), so it went as if laid on the UVs.",
    "{what}: 이미지를 놓는 좌표가 '{node}' 노드를 거쳐서, UV 그대로 놓은 것으로 갔습니다.":
        "{what}: the coordinates the image is laid on go through a '{node}' node, so it went as if laid on the UVs.",
    "Principled BSDF 하나로 된 재질이 아니라서, 뷰포트에 보이는 색 · 금속 · 거칠기 값만 갔습니다.":
        "The material is not one made of a single Principled BSDF, so only the color, metallic and roughness values the viewport shows "
        "went.",
    "{what}: '{node}' 노드를 거쳐서 못 옮겼습니다.": "{what}: goes through a '{node}' node and could not be carried.",
    "굽기가 꺼져 있어서": "because baking is turned off",
    "{what}: '{node}' 노드를 거치는데 {why} 못 옮겼습니다.": "{what}: goes through a '{node}' node and could not be carried {why}.",
    "{what}: 굽지 못했습니다({why}).": "{what}: could not be baked ({why}).",
    "{what}: 이미지 '{name}'의 파일이 없어서 못 옮겼습니다.": "{what}: the file of the image '{name}' is missing, so it could not be carried.",
    "금속": "metallic",
    "거칠기": "roughness",
    "금속 · 거칠기: {later}": "metallic · roughness: {later}",
    "금속 · 거칠기: 굽지 못했습니다({why}).": "metallic · roughness: could not be baked ({why}).",
    "발광 세기": "emission strength",
    "투명도": "alpha",
    "이 .blend는 저장된 적이 없어, 같은 것을 다시 보내면 Unity에 새 모델이 하나 더 생깁니다. 저장한 뒤 보내면 제자리에서 바뀝니다.":
        "This .blend has never been saved, so sending the same thing again makes one more model in Unity. Saved first, it is replaced in "
        "place.",
    "이 manifest는 '{format}' 형식이라 모델로 받을 수 없습니다. 다른 Blender가 export_blend로 보낸 것은 import_blend로 받으세요.":
        "This manifest is of the '{format}' kind and cannot be received as a model. What another Blender sent with export_blend is "
        "received with import_blend.",
    "모델 파일이 없습니다: {path}": "The model file is missing: {path}",
    "'{extension}' 파일은 받을 수 없습니다. FBX, OBJ, GLB만 됩니다.": "A '{extension}' file cannot be received. Only FBX, OBJ and GLB can.",
    "Unity가 컴파일 중이에요": "Unity is compiling",
    "Unity가 응답하지 않아요": "Unity does not answer",
    "Unity가 제때 답하지 않았어요(대화상자가 떠 있을 수 있어요)": "Unity did not answer in time (a dialog may be up)",
    "그 프로젝트에서 Claude가 일하는 중이에요. 끝난 뒤 다시 해 주세요": "Claude is at work in that project. Try again once it has finished",
    "그 프로젝트의 Claude Hand Tools가 오래된 버전이에요": "That project's Claude Hand Tools is an old version",
    "저장": "Save",
    "되돌리기": "Undo",
    "파일 열기": "File opened",
    "Unity 열림": "Unity opened",
    "확인": "Check",
    "'{key}'(으)로 보낸 오브젝트가 둘이에요({names}). 이 모델로 갈 것을 고르고 [선택한 오브젝트로 잇기]를 누르세요.":
        "Two objects were sent as '{key}' ({names}). Select the one this model is to be made of and press [Link to the selected object].",
    "이 파일에 '{key}' 오브젝트가 없어요. 이름을 바꿨으면 그 오브젝트를 고르고 [선택한 오브젝트로 잇기]를 누르세요.":
        "This file has no object '{key}'. If you renamed it, select that object and press [Link to the selected object].",
    " 외 {count}개": " and {count} more",
    "내보내지 못했어요: {exc}": "Could not export: {exc}",
    "{why} → {project}: {label}을(를) 보내지 못했어요({reason}). 다음 저장 때 다시 보냅니다.":
        "{why} → {project}: {label} could not be sent ({reason}). It is sent again at the next save.",
    "{why} → {project}: {label} 반영": "{why} → {project}: {label} updated",
    " (씬 오브젝트 {uses}개)": " ({uses} scene objects)",
    "{project}: {label}이(가) Unity에 보낸 것과 다르게 들어갔어요({parts}). Claude에게 알려 주세요.":
        "{project}: {label} arrived differently from what was sent to Unity ({parts}). Tell Claude.",
    "{project}: {label}의 노드로 만든 재질은 오브젝트 모드에서 저장하면 구워서 갑니다(지금은 Unity에 있던 이미지 그대로).":
        "{project}: the node-made materials of {label} are baked and sent when you save in Object Mode (for now the images Unity had stay).",
    "{project}에 {label} 모델이 더 없어서 보내지 않았어요.": "{project} no longer has the model {label}, so it was not sent.",
    "{project}에서 고친 재질은 Unity 것 그대로 뒀어요({names}). Blender 재질대로 다시 만들려면 Unity에서 그 .mat 파일을 지우고 다시 보내세요.":
        "{project}: the materials changed there were left as Unity has them ({names}). To have them made again from Blender's, delete "
        "those .mat files in Unity and send again.",
    "{text} · {count}번째 ({stamp})": "{text} · #{count} ({stamp})",
    "Unity로 보낸 모델을 살펴보다 오류가 났어요: {exc}": "An error came up while looking at the models sent to Unity: {exc}",
    "먼저 이 .blend를 저장하세요. 저장된 파일이어야 Unity가 같은 모델인지 알고, 저장할 때 다시 보낼 수 있어요.":
        "Save this .blend first. Only with a saved file can Unity tell it is the same model, and can it be sent again on a save.",
    "그 Unity 프로젝트가 열려 있지 않아요.": "That Unity project is not open.",
    " · {source}에서 온 것으로 적혀 있음": " · recorded as coming from {source}",
    "다른 파일": "another file",
    "{project}에 {label}을(를) 보냈어요": "Sent {label} to {project}",
    "(있던 모델을 바꿈)": " (replaced the model there)",
    "(씬에 놓음)": " (placed in the scene)",
    ". 저장할 때마다 다시 갑니다. Unity 채팅창의 되돌리기로 뺄 수 있어요.": ". It goes again at every save. Undo in Unity's chat panel takes it out.",
    "노드로 만든 재질 입력은 텍스처로 구워 보냈어요(그때 모습으로 굳은 이미지): ":
        "Material inputs made with nodes were baked to textures and sent (images fixed as they looked then): ",
    "못 옮긴 것 · ": "Not carried · ",
    "Unity에 보낸 것과 다르게 들어갔어요({parts}). Claude에게 알려 주세요.": "It arrived differently from what was sent to Unity ({parts}). Tell Claude.",
    "[사용자가 Blender 패널에서 '{label}'을(를) Unity {project}에 직접 보냈음: {asset} (Claude를 거치지 않음, 저장할 때마다 다시 감)]":
        "[The user sent '{label}' to Unity {project} straight from Blender's panel: {asset} (not through Claude; it goes again at every "
        "save)]",
    "다른 창은 되돌리지 못했습니다: ": "The other windows could not be undone: ",
    "Claude Hand가 받지 않았습니다": "Claude Hand did not take it",
    "보내지 못했습니다. 앞 메시지가 Claude Hand가 켜지기를 기다리는 중이니 잠시 뒤 다시 보내 주세요.":
        "Could not send. An earlier message is waiting for Claude Hand to come on; send again in a moment.",
    "중지를 보내지 못했습니다: ": "Stop could not be sent: ",
    "알 수 없는 오류": "unknown error",
    "보내지 못했습니다: ": "Could not send: ",
    "Claude 세션이 아직 연결 중입니다. 잠시 뒤 답이 옵니다.": "The Claude session is still connecting. The answer comes in a moment.",
    "Claude Hand 시작": "Start Claude Hand",
    "권한 응답": "Answer a permission request",
    "전체 복사": "Copy all",
    "메시지 전체를 클립보드에 복사했습니다": "Copied the whole message to the clipboard",
    "크게 보기": "View larger",
    "그림 파일이 지워졌습니다": "The picture file was deleted",
    "한글 입력창": "Windows text box",
    "'{mark}' 이후 바뀐 것을 모두 되돌립니다. 그 뒤에 직접 고친 것도 함께 되돌아갑니다.":
        "Everything changed since '{mark}' is undone. What you changed by hand after it goes back too.",
    "'{label}' 요청이 바꾼 것을 되돌립니다. ": "What the request '{label}' changed is undone. ",
    "이 Blender에서는 그 뒤에 직접 고친 것도 함께 되돌아갑니다. ": "In this Blender, what you changed by hand after it goes back too. ",
    "다른 창({others})은 그 창에 더 새 요청이 있거나 작업 중이면 건너뛰고, 그렇게 알려 줍니다.":
        "The other windows ({others}) are skipped when they have a newer request or are at work, and you are told so.",
    "Claude Hand 되돌리기": "Claude Hand: Undo",
    "중지": "Stop",
    "이미 답한 제안입니다.": "That proposal is answered already.",
    "보호 규칙 제안에 답하기": "Answer a proposed protection rule",
    "보호 규칙 더하기": "Add a protection rule",
    "보호할 오브젝트를 먼저 고르세요.": "Select the objects to protect first.",
    "풀기": "Release",
    "'{rule}' 규칙을 풉니다. Claude가 다시 바꿀 수 있게 됩니다.": "The rule '{rule}' is released. Claude can change it again.",
    "보호 규칙 풀기": "Release a protection rule",
    "Claude가 이 창을 바꿀 수 있습니다. 아래 규칙이 가리키는 것만 빼고요.": "Claude can change this window, except what the rules below cover.",
    "보기만: Claude는 이 창을 읽기만 합니다.": "Look only: Claude only reads this window.",
    "잠금: Claude는 이 창에서 아무 일도 하지 않습니다.": "Locked: Claude does nothing in this window.",
    "읽지 못한 규칙 파일(고칠 때까지 Claude의 바꾸는 호출을 막습니다): ":
        "A rules file that could not be read (until it is fixed, Claude's calls that change anything are refused): ",
    "이번만 (Claude Hand를 끌 때까지)": "This run only (until Claude Hand is closed)",
    "선택한 오브젝트 보호": "Protect the selected objects",
    "이 창 보기만": "Look only",
    "이 창 잠금": "Lock this window",
    ".blend 저장 금지": "Forbid saving the .blend",
    "저장할 때 다시 보내기": "Send again on save",
    "이 파일로 잇기": "Link to this file",
    "선택한 오브젝트로 잇기": "Link to the selected object",
    "이 모델로 갈 오브젝트를 먼저 고르세요": "Select the object this model is to be made of first",
    "선택한 오브젝트 보내기": "Send the selected objects",
    "보낼 오브젝트를 먼저 고르세요.": "Select the objects to send first.",
    "새로 고침": "Refresh",
    "열려 있는 Unity가 없습니다(Claude Hand Tools 0.16 이상이 든 프로젝트).": "No Unity is open (a project with Claude Hand Tools 0.16 or later).",
    "저장한 적 없는 파일입니다. 저장하면 여기서 Unity로 보낼 수 있어요.": "This file has never been saved. Once saved, you can send to Unity from here.",
    "이 파일에서 Unity로 간 모델이 없습니다.": "No model has gone to Unity from this file.",
    "체크한 모델은 이 파일을 저장할 때마다 Unity에서 바뀝니다.": "Checked models are replaced in Unity every time this file is saved.",
    "선택한 오브젝트 보내기 → {name}": "Send the selected objects → {name}",
    "Unity로 보낸 모델": "Models sent to Unity",
    "보호": "Protect",
    "여는 중... 새 창을 확인하세요": "Opening... check the new window",
    "Claude Hand 창에서 확인을 눌러 주세요. 세션이 일을 시작하면 그 창은 저절로 최소화됩니다. 닫지는 마세요.":
        "Confirm in the Claude Hand window. Once the session starts working, that window minimizes by itself. Do not close it.",
    "Claude Hand(감독 세션)가 꺼져 있습니다. 버튼을 누르거나 메시지를 보내면 켜집니다.":
        "Claude Hand (the supervisor session) is off. Press the button or send a message to turn it on.",
    "아직 대화가 없습니다.": "No conversation yet.",
    "이전 대화 {folded}개는 접어 두었습니다": "{folded} earlier messages are folded away",
    "…{hidden}줄 더 있음": "…{hidden} more lines",
    "→ 허용됨": "→ Allowed",
    "→ 거부됨": "→ Denied",
    "허용": "Allow",
    "거부": "Deny",
    "→ 규칙에 더함": "→ Added to the rules",
    "→ 더하지 않음": "→ Not added",
    "추가": "Add",
    "취소": "Cancel",
    "나 (Claude Hand가 켜지면 보냅니다)": "Me (sent once Claude Hand is on)",
    "1분 넘게 답이 없습니다. 감독 세션을 start-supervisor.cmd로 열었는지, Claude Code 창에 확인을 기다리는 질문이 없는지 봐 주세요.":
        "No answer for over a minute. See whether the supervisor session was opened with start-supervisor.cmd, and whether the Claude Code "
        "window has a question waiting for your confirmation.",
    "되돌리기: {plan}": "Undo: {plan}",
    "한글": "Text box",
    "선택 항목 함께": "With selection",
    "기능 켜고 끄기": "Features",
    "실험실": "Lab",
    "메시지": "Message",
    "Enter로 보냅니다": "Enter sends it",
    "선택 항목 함께 보내기": "Send the selection along",
    "이번만": "This run only",
    "새로 더하는 보호 규칙을 Claude Hand를 끌 때까지만 둡니다(끄면 파일 옆에 적어 계속 둡니다)":
        "A protection rule added now is kept only until Claude Hand is closed (turned off, it is written beside the file and kept)",
    "설정": "Settings",
    "켜짐": "On",
    "꺼짐": "Off",
    "{days}일": "{days} days",
    "지우지 않음": "never removed",
    "아직 다듬는 중인 기능입니다. 켜야 쓰입니다.": "Features still being refined. In use only once turned on.",
    "지금 실험실에 있는 기능이 없습니다.": "The lab holds no feature right now.",
    "{since}부터": "since {since}",
    "설정 목록을 읽지 못했습니다. claude-hand 폴더에서 uv run tools/claude_hand.py chat-setup 을 한 번 실행하세요.":
        "The list of settings could not be read. Run uv run tools/claude_hand.py chat-setup once in the claude-hand folder.",
    "* 다음에 Claude Hand를 켤 때부터 적용됩니다.": "* Applies from the next start of Claude Hand.",
    "Claude Hand 설정": "Claude Hand settings",
    "다른 창 ({count})": "Other windows ({count})",
    "{state} · {seconds}초": "{state} · {seconds}s",
    "중지하는 중": "Stopping",
    "작업 중": "Working",
    "{actions}번째 동작": "action {actions}",
    "(그림 파일이 지워졌습니다)": "(the picture file was deleted)",
    "결과 미리보기": "Result preview",
    "Claude가 제안한 보호 규칙": "A protection rule Claude proposes",
    "데스크톱 앱에서": "From the desktop app",
    "'{tool}' 실행 허락 요청": "Permission to run '{tool}'",
    "나": "Me",
}
