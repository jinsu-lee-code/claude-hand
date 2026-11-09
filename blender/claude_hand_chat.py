"""Claude Hand Chat: talk to this Blender window's claude-hand subagent from the 3D Viewport sidebar.

Messages go to the claude-hand-chat channel (tools/chat_channel.py in the claude-hand repo), which
the supervisor Claude Code session runs; the supervisor hands them to blender-<file> and answers here.
Networking runs on a background thread; everything that touches bpy runs on the main thread
through a bpy.app.timers callback.

Install: `uv run tools/claude_hand.py blender-install-chat` (or Preferences > Add-ons > Install).
"""

bl_info = {
    "name": "Claude Hand Chat",
    "author": "claude-hand",
    "version": (0, 2, 1),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > Claude Hand",
    "description": "Chat with this Blender window's claude-hand subagent",
    "category": "Interface",
}

import functools
import json
import os
import queue
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
PANEL_MARGIN_PX = 50.0  # category tabs, box and label padding at UI scale 1
LABEL_HEADROOM = 1.2    # blf's width at the label size can come out a little narrower than the drawn label
CHAR_PX = 6.0           # without blf: width of a narrow character at 11 px (wide ones such as Korean count twice)

_entries = []            # main thread only
_status = ["연결 중..."]  # main thread only
_last_user = [0.0]       # main thread only
_server_missing = [False]  # main thread only
_start_requested = [0.0]   # main thread only
_pending = [None]          # main thread only: (text, context) typed while Claude Hand was off
_context_sent = {"time": 0.0, "signature": None, "server_pid": None, "busy": False}  # main thread only
_inbox = queue.Queue()   # (event, payload) from worker threads
_identity = {"window": None, "pin": None}  # written on the main thread, read by the poller
_identity_lock = threading.Lock()
_stop = threading.Event()
_poller = [None]


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
    chat_id, agent, after, server_pid = None, None, 0, None
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
            server_pid, chat_id, agent, after = server.get("pid"), None, None, 0
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
            query = urllib.parse.urlencode({"chat_id": chat_id, "after": after, "wait": POLL_WAIT_SECONDS})
            _inbox.put(("missing", False))
            updates = call(server, "GET", f"/v1/updates?{query}", timeout=POLL_WAIT_SECONDS + 15)
            if updates.get("ok") is False:
                server_pid = None
                _stop.wait(1)
                continue
            if updates.get("items"):
                _inbox.put(("items", updates["items"]))
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
    elif event == "sent":
        if not payload.get("ok"):
            _status[0] = "보내지 못했습니다: " + str(payload.get("error", "알 수 없는 오류"))
        elif payload.get("delivered") is False:
            _status[0] = "Claude 세션이 아직 연결 중입니다. 잠시 뒤 답이 옵니다."
    elif event == "items":
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
    if changed or (_last_user[0] and time.time() - _last_user[0] > NO_REPLY_HINT_SECONDS):
        for window_ in getattr(bpy.context.window_manager, "windows", []):
            for area in window_.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    return 0.5


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
        wm = context.window_manager
        layout.prop(wm, "claude_hand_chat_input", text="")
        row = layout.row()
        row.prop(wm, "claude_hand_chat_context", text="선택 항목 함께")
        row.operator(CLAUDEHAND_OT_send.bl_idname, text="보내기", icon="PLAY")


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
    if kind not in ("user", "reply", "permission"):
        return [], 0
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
        if entry.get("kind") not in ("user", "reply", "permission"):
            continue
        total += 1
        if full:
            continue
        rows, hidden = entry_rows(entry, width)
        cost = len(rows) + (1 if hidden else 0) + (1 if entry.get("kind") == "permission" else 0)
        if shown and (used + cost > PANEL_LINES or len(shown) >= VISIBLE_ENTRIES):
            full = True  # older entries stay folded, even short ones, so the history has no gaps
            continue
        shown.append((entry, rows, hidden))
        used += cost
    shown.reverse()
    return shown, total - len(shown)


CLASSES = (CLAUDEHAND_OT_send, CLAUDEHAND_OT_start, CLAUDEHAND_OT_permission, CLAUDEHAND_OT_copy, CLAUDEHAND_PT_chat)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.claude_hand_chat_input = bpy.props.StringProperty(
        name="메시지", description="Enter로 보냅니다", update=_on_input_update)
    bpy.types.WindowManager.claude_hand_chat_context = bpy.props.BoolProperty(
        name="선택 항목 함께 보내기", default=True)
    _stop.clear()
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
    if _poller[0] is None or not _poller[0].is_alive():
        _poller[0] = threading.Thread(target=poll_loop, name="claude-hand-chat", daemon=True)
        _poller[0].start()


def unregister():
    _stop.set()
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    del bpy.types.WindowManager.claude_hand_chat_input
    del bpy.types.WindowManager.claude_hand_chat_context
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

