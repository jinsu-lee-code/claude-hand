"""Protection rules: what the user has said must not be changed in a window, and whether a tool call would.

A note (ClaudeHand/NOTES.md) is something a subagent reads and tries to keep; a rule here is checked by the guard
hook before every call, and by the window itself where the guard cannot tell (docs/DESIGN.md, "창마다 보호 규칙").
The file is the user's: nothing Claude can call writes it. The window's own panel does, on the user's click.

  {"version": 1, "window": "open" | "look" | "locked",
   "paths": ["Assets/Art/**"], "objects": ["Player", "Environment/**", "collection:Rig"],
   "actions": ["save_scene", "delete_asset", "play_mode", "save_file"]}

Three readers keep to the same meaning: this module (the guard, the chat channel), Claude Hand Tools in Unity
(ChProtect.cs) and the Blender add-on. tools/tests/protect_cases.json holds what each of them must answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from handlib.lang import N_, T, both

MODES = ("open", "look", "locked")  # weakest first: merging takes the strictest
MODE_WORDS = {"open": N_("열림"), "look": N_("보기만"), "locked": N_("잠금")}
ACTIONS = {"unity": ("save_scene", "delete_asset", "play_mode"), "blender": ("save_file",)}
ACTION_WORDS = {"save_scene": N_("씬 저장"), "delete_asset": N_("에셋 삭제"), "play_mode": N_("플레이 모드"), "save_file": N_(".blend 저장")}
RULES_FILES = ("claudehand/protect.json", ".claude-hand.protect.json")  # as they end, lowercase: nobody's call may name them
# ... nor what the windows' own panels write them with (the add-on's functions and operators, the package's class).
# Not a lock against code that hides what it does; the plain way in is shut.
RULES_WRITERS = ("protect_add", "protect_remove", "protect_offer", "_protect_write", "chprotect", ".claude-hand/protect", ".claude-hand\\protect")
REFUSED = N_("보호 규칙이 이 호출을 막았습니다: {rule} ({what}). 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요. 규칙을 푸는 것은 사용자만 할 수 있습니다.")
# A call that changes something and names what is protected: it may only have wanted to read it, or to use it.
NAMED = (N_("보호 규칙이 이 호출을 막았습니다: {rule} ({what}). 이 호출은 무언가를 바꾸면서 보호된 것을 이름으로 가리킵니다. 보호된 것을 "
         "바꾸려던 것이면 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요. 읽거나 가져다 쓰기만 하려던 것이면 읽는 호출과 바꾸는 "
         "호출을 나눠 다시 부르세요(바꾸는 호출에는 그 이름을 적지 않습니다). 규칙을 푸는 것은 사용자만 할 수 있습니다."))


@dataclass
class Rules:
    window: str = "open"
    paths: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # files that were there and could not be read as rules

    def empty(self) -> bool:
        return self.window == "open" and not (self.paths or self.objects or self.actions)

    def watched(self) -> bool:
        """There is something the window has to look at after a call: the guard cannot tell from a call's words
        everything that happens to an object or a file."""
        return bool(self.paths or self.objects or self.actions)


def unity_rules_path(project_path: str | Path) -> Path:
    """Next to the project's notes, outside Assets (Unity does not import it)."""
    return Path(project_path) / "ClaudeHand" / "protect.json"


def blender_rules_path(filepath: str | Path) -> Path:
    """Beside the .blend, like its notes: <name>.claude-hand.protect.json."""
    return Path(filepath).with_suffix(".claude-hand.protect.json")


def session_key(app: str, pin: str) -> str:
    """The name of a window's rules for this run of Claude Hand only: what the window itself can tell without asking
    anybody (a Unity project's hash, a Blender's MCP port), which is the part of its pin after the @."""
    return f"{app}-{pin.rpartition('@')[2]}"


def session_rules_path(home: str | Path, window: str) -> Path:
    """Rules for this run of Claude Hand only ("이번만"): emptied when it starts. window: session_key()."""
    return Path(home) / "protect" / f"{re.sub(r'[^A-Za-z0-9_.-]+', '-', window) or 'window'}.json"


def clear_session(home: str | Path) -> int:
    """A new run of Claude Hand: the rules that were for the last one only are over. Returns how many files went."""
    gone = 0
    for path in (Path(home) / "protect").glob("*.json"):
        try:
            path.unlink()
            gone += 1
        except OSError:
            pass
    return gone


def _strings(value) -> list[str]:
    return [str(v).strip() for v in value if isinstance(v, str) and str(v).strip()] if isinstance(value, list) else []


def from_dict(data) -> Rules:
    """Rules as they are written in a file, or as a window says them. What is not understood is not a rule."""
    if not isinstance(data, dict):
        return Rules()
    window = str(data.get("window") or "open").strip().lower()
    return Rules(window=window if window in MODES else "open", paths=_strings(data.get("paths")), objects=_strings(data.get("objects")),
                 actions=[a for a in _strings(data.get("actions")) if a in ACTION_WORDS], problems=_strings(data.get("problems")))


def read(path: str | Path | None) -> Rules:
    """The rules in one file. No file: no rules. A file that is not rules: none either, and it says so (a rule that
    was meant and is not kept must not go unnoticed)."""
    if not path or not Path(path).is_file():
        return Rules()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        return Rules(problems=[f"{path}: {exc}"])
    data.pop("problems", None)
    return from_dict(data)


def merged(*many: Rules) -> Rules:
    """Several files' rules as one: the strictest window, every path, object and action."""
    out = Rules()
    for rules in many:
        if MODES.index(rules.window) > MODES.index(out.window):
            out.window = rules.window
        for name in ("paths", "objects", "actions", "problems"):
            getattr(out, name).extend(v for v in getattr(rules, name) if v not in getattr(out, name))
    return out


def window_rules(app: str, where: str | Path | None, home: str | Path | None = None, window: str = "") -> Rules:
    """A window's rules: its lasting file (the project's, the .blend's) and this run's."""
    lasting = read((unity_rules_path(where) if app == "unity" else blender_rules_path(where)) if where else None)
    return merged(lasting, read(session_rules_path(home, window)) if home and window else Rules())


def in_words(rules: Rules) -> str:
    """One line for a status or a chat panel: "보기만 · 경로 2 · 오브젝트 1 · 금지 동작: 씬 저장"; "" for no rules."""
    parts = [T(MODE_WORDS[rules.window])] if rules.window != "open" else []
    parts += [T("경로 {count}", count=len(rules.paths))] if rules.paths else []
    parts += [T("오브젝트 {count}", count=len(rules.objects))] if rules.objects else []
    parts += [T("금지 동작: {actions}", actions=", ".join(T(ACTION_WORDS[a]) for a in rules.actions))] if rules.actions else []
    return " · ".join(parts)


# --------------------------------------------------------------------------- what a rule covers


def _clean(path: str) -> str:
    path = path.replace("\\", "/").strip().strip("/")
    path = path[2:] if path.startswith("./") else path
    return (path[:-5] if path.lower().endswith(".meta") else path).lower()  # a file's .meta is the file's


def _glob(pattern: str) -> re.Pattern:
    parts = re.split(r"(\*\*|\*|\?)", pattern)
    return re.compile("".join(".*" if p == "**" else "[^/]*" if p == "*" else "[^/]" if p == "?" else re.escape(p) for p in parts) + r"\Z")


def path_rule(rules: Rules, asset_path: str) -> str | None:
    """The rule that covers this asset path (project relative), or None. No case; `*` stays within a name, `**`
    goes any depth; a folder rule covers the folder itself, and a rule with no wildcard everything below it."""
    path = _clean(asset_path)
    for rule in rules.paths:
        pattern = _clean(rule)
        if not pattern:
            continue
        if not re.search(r"[*?]", pattern):
            if path == pattern or path.startswith(pattern + "/"):
                return rule
            continue
        folder = pattern[:-3] if pattern.endswith("/**") else ""
        if _glob(pattern).match(path) or (folder and "*" not in folder and "?" not in folder and path == folder):
            return rule
    return None


def object_rule(rules: Rules, name: str) -> str | None:
    """The rule that covers an object named so. `name` may be a hierarchy path ("Environment/Trees/Oak"): a rule
    that is a plain name covers that object wherever it is, and all below it; "A/**" covers A and what is below it
    (moving or deleting A takes them along); "A/B" that one object. Rules for
    a Blender collection ("collection:Rig") are the add-on's to tell: who is in it does not show in a name."""
    parts = [p for p in name.replace("\\", "/").split("/") if p]
    for rule in rules.objects:
        if rule.lower().startswith("collection:") or not rule.strip():
            continue
        wanted = [p for p in rule.replace("\\", "/").split("/") if p]
        if len(wanted) == 1:  # the object itself, or one above it
            if any(_glob(wanted[0]).match(part) for part in parts):
                return rule
            continue
        below = wanted[-1] == "**"
        head = wanted[:-1] if below else wanted
        for start in range(len(parts) - len(head) + 1):
            if all(_glob(w).match(p) for w, p in zip(head, parts[start:])) and (below or start + len(head) == len(parts)):
                return rule
    return None


# --------------------------------------------------------------------------- a call against the rules

NAME_KEYS = ("name", "target", "parent", "new_name", "object", "object_name", "gameobject", "search_term")
ASSET_IN_TEXT = re.compile(r"Assets/[^\s\"'`<>|*?:;,)]+", re.IGNORECASE)
QUOTED = re.compile(r"\"((?:[^\"\\\n]|\\.){1,200})\"|'((?:[^'\\\n]|\\.){1,200})'")
SAVES_BLEND = re.compile(r"\bsave_(?:as_)?mainfile\b")
# MCP for Unity's tools that write the asset they name (a script, a material, a prefab), as opposed to those that
# put an asset to use in the scene (manage_gameobject with a prefab_path): using what is protected is no change to it.
ASSET_TOOLS = {"manage_asset", "manage_script", "create_script", "delete_script", "apply_text_edits", "script_apply_edits",
               "manage_shader", "manage_prefabs", "manage_material", "manage_texture", "manage_scriptable_object",
               "manage_animation", "manage_scene"}
ASSET_USES = {("manage_scene", "load"), ("manage_scene", "open"), ("manage_prefabs", "open_stage"), ("manage_prefabs", "close_stage"),
              ("manage_material", "assign_material_to_renderer")}
LOOKING_ACTIONS = {"find", "search", "read", "list", "info", "exists", "screenshot", "ping", "status", "validate", "telemetry_status",
                   "telemetry_ping"}
# In code, what the forbidden actions look like. Only a first line of defence: the window refuses the action itself.
CODE_ACTIONS = {
    "save_scene": re.compile(r"\b(?:SaveScene|SaveOpenScenes|SaveCurrentModifiedScenesIfUserWantsTo|SaveModifiedScenesIfUserWantsTo)\b"),
    "delete_asset": re.compile(r"\b(?:DeleteAssets?|MoveAssets?ToTrash|DeleteFileOrDirectory)\b"),
    "play_mode": re.compile(r"\b(?:EnterPlaymode|isPlaying\s*=\s*true)\b"),
}
MENU_ACTIONS = {"save_scene": ("file/save",), "play_mode": ("edit/play",)}


def _texts(value, depth: int = 0):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict) and depth < 6:
        for v in value.values():
            yield from _texts(v, depth + 1)
    elif isinstance(value, (list, tuple)) and depth < 6:
        for v in value:
            yield from _texts(v, depth + 1)


def _act(tool_input: dict) -> str:
    return str(tool_input.get("action") or "").strip().lower()


def action_looks(tool: str, tool_input: dict) -> bool:
    """A Unity tool that does many things, called for one that only looks: manage_asset's search, manage_scene's
    get_hierarchy. (handlib.looks goes by the tool's name alone, which is enough for setting undo points: a point
    too many costs nothing. A window that may only be looked at would be of little use without these.)"""
    act = _act(tool_input)
    return tool.startswith("manage_") and bool(act) and (act.startswith("get") or act in LOOKING_ACTIONS)


def _action(app: str, tool: str, tool_input: dict) -> str | None:
    """The forbidden-action name this call is, if any."""
    act, code = _act(tool_input), str(tool_input.get("code") or "")
    if app == "blender":
        return "save_file" if SAVES_BLEND.search(code) else None
    if tool == "manage_scene" and act in ("save", "save_as"):
        return "save_scene"
    if tool == "manage_editor" and act in ("play", "enter_play_mode"):
        return "play_mode"
    if tool in ("delete_script",) or (tool in ASSET_TOOLS and act == "delete"):
        return "delete_asset"
    menu = str(tool_input.get("menu_path") or tool_input.get("menu") or "").strip().lower()
    for name, pattern in CODE_ACTIONS.items():
        if (code and pattern.search(code)) or (menu and any(menu.startswith(m) for m in MENU_ACTIONS.get(name, ()))):
            return name
    return None


def refusal(app: str, rules: Rules, tool: str, tool_input: dict | None, only_looks: bool, files: list[str] | None = None,
            covered: dict[str, str] | None = None) -> str | None:
    """Why the rules do not let this call through, or None. `tool`: its short name ("manage_asset", "execute_code",
    "execute_blender_code"); `only_looks`: the call cannot change the window (handlib.looks); `files`: the asset paths
    its input names, as handlib.keeps finds them (paths that are written out in its texts are found here too);
    `covered`: the objects the window itself says are protected, name -> rule (it knows who is in a collection and
    who is whose child, which a name does not show).
    What the guard cannot tell from a call's words is the window's own to catch (Unity refuses, both undo)."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if rules.window == "locked":
        return T(REFUSED, rule=T("이 창은 잠겨 있습니다(잠금)"), what=T("이 창에서는 아무 일도 하지 않습니다"))
    only_looks = only_looks or (app == "unity" and action_looks(tool, tool_input))
    texts = list(_texts(tool_input))
    if not only_looks and any(any(end in t.replace("\\", "/").lower() for end in RULES_FILES) or
                              any(word in t.lower() for word in RULES_WRITERS) for t in texts):
        return T(REFUSED, rule=T("보호 규칙 파일"), what=T("규칙은 사용자만 고칩니다"))
    if only_looks or rules.empty():
        return None
    if rules.window == "look":
        return T(REFUSED, rule=T("이 창은 보기만 합니다(보기만)"), what=T("{tool}: 바꾸는 호출", tool=tool))
    action = _action(app, tool, tool_input)
    if action in rules.actions:
        return T(REFUSED, rule=T("금지 동작 '{action}'", action=T(ACTION_WORDS[action])), what=tool)
    code = str(tool_input.get("code") or "")
    if app == "unity" and (code or (tool in ASSET_TOOLS and (tool, _act(tool_input)) not in ASSET_USES)):
        named = list(files or []) + [m.group(0).rstrip(".") for t in texts for m in ASSET_IN_TEXT.finditer(t)]
        for path in named:
            rule = path_rule(rules, path)
            if rule:
                return T(NAMED if code else REFUSED, rule=T("보호 경로 '{rule}'", rule=rule), what=f"{tool}: {path}")
    names = [(str(tool_input[key]), REFUSED) for key in NAME_KEYS if isinstance(tool_input.get(key), str)] if not code else []
    names += [(m.group(1) or m.group(2), NAMED) for m in QUOTED.finditer(code)]  # an object the code names
    for name, words in names:
        rule = object_rule(rules, name) or (covered or {}).get(name)
        if rule:
            return T(words, rule=T("보호 오브젝트 '{rule}'", rule=rule), what=f"{tool}: {name}")
    return None


OPENING = N_("보호 규칙이 이 호출을 막았습니다: ")  # how REFUSED and NAMED begin ...
PANEL_OPENING = N_("보호 규칙이 막았어요: ")       # ... and how the chat panel says the same


def panel_line(refused: str) -> str:
    """A refusal as the chat panel says it: what was stopped, without what the subagent is to do about it. In the
    language the refusal itself is in."""
    first = refused.split(". ")[0]
    for said, shown in zip(both(OPENING), both(PANEL_OPENING)):
        if said in first:
            return first.replace(said, shown, 1)
    return first


# --------------------------------------------------------------------------- a rule the supervisor proposes


def proposal_problem(app: str, kind: str, value: str) -> str | None:
    """What is wrong with a rule proposed for a window of this app (for the supervisor, in its own words), or None."""
    if kind == "window":
        return None if value in MODES[1:] else "For kind window, value is 'look' or 'locked'."
    if kind == "action":
        return None if value in ACTIONS.get(app, ()) else \
            f"For a {'Unity' if app == 'unity' else 'Blender'} window, an action is one of: {', '.join(ACTIONS.get(app, ()))}."
    if kind == "path":
        if app != "unity":
            return "Paths are for Unity projects. In Blender, protect objects by name ('Hero', 'collection:Rig')."
        return None if _clean(value).startswith("assets/") or _clean(value) == "assets" else "A path is project relative and starts with Assets/."
    if kind == "object":
        if value.lower().startswith("collection:") and app != "blender":
            return "'collection:' is for Blender windows."
        return None if value.strip("/ ") else "Name the object."
    return "kind is one of window, path, object, action."


def proposal_words(kind: str, value: str, lasting: bool) -> str:
    """A proposed rule as the panel shows it above [추가] and [취소]."""
    what = ((T("이 창을 보기만 하기(읽기만 하고 바꾸지 않음)") if value == "look" else T("이 창을 잠그기(이 창에는 일을 맡기지 않음)")) if kind == "window" else
            T("'{action}' 금지", action=T(ACTION_WORDS[value]) if value in ACTION_WORDS else value) if kind == "action" else
            T("경로 '{value}' 보호(고치기 · 지우기 · 옮기기 금지)", value=value) if kind == "path" else
            T("컬렉션 '{name}'의 오브젝트 보호", name=value.split(':', 1)[1].strip()) if value.lower().startswith("collection:") else
            T("오브젝트 '{value}' 보호(바꾸기 · 지우기 금지)", value=value))
    return what + (T(" — 계속") if lasting else T(" — 이번만(Claude Hand를 끌 때까지)"))


# --------------------------------------------------------------------------- what a window found after a call

BLOCKED = N_("보호 규칙이 막았어요: {what}")
UNDONE = N_("보호된 {what}이(가) 바뀌어 이 요청이 이 창에 한 일을 되돌렸어요")
NOT_UNDONE = N_("보호된 {what}이(가) 바뀌었는데 되돌리지 못했어요({why})")


def found_after(result) -> tuple[str | None, str | None]:
    """What a window says it found when asked after a call ({"blocked": [...], "changed": [...], "undone": bool,
    "detail": "..."}): (a line for the chat panel and the event log, what the subagent is told), both None when
    nothing was found. blocked: what the window itself refused while the call ran (a save, a deletion, play mode);
    changed: protected things that are no longer as they were before the call, which the window then took back."""
    if not isinstance(result, dict):
        return None, None
    blocked = [str(b) for b in result.get("blocked") or [] if b][:5]
    changed = [str(c) for c in result.get("changed") or [] if c][:5]
    if not blocked and not changed:
        return None, None
    lines = []
    if blocked:
        lines.append(T(BLOCKED, what=", ".join(blocked)))
    if changed:
        what = ", ".join(changed)
        lines.append(T(UNDONE, what=what) if result.get("undone") else
                     T(NOT_UNDONE, what=what, why=str(result.get("detail") or T("되돌릴 지점이 없음"))[:200]))
    line = ". ".join(lines)
    # Looking is left open on purpose: told only "call nothing more", a real subagent reported the values it
    # expected after the undo and said it had not been able to check them (2026-11-24).
    told = T("{line}. 바꾸는 호출은 더 하지 말고 이 내용을 그대로 감독에게 보고하세요(무엇이 막혔거나 되돌려졌는지, 그래서 이 "
             "요청에서 남은 것이 무엇인지). 지금 상태를 읽어 확인하는 것은 괜찮습니다. 같은 일을 다른 방법으로 다시 하지 마세요. "
             "규칙을 푸는 것은 사용자만 할 수 있습니다.", line=line)
    return line, told
