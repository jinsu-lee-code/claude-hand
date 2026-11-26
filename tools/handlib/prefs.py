"""The user's settings and the lab: what config.json says, read anew whenever the file changes, and the one list of
what can be set (settings/catalog.json, which the settings windows in Unity and Blender read too).

  config.json   {"language": "en", "show_cost": false, "history_keep_days": 30, "lab": {"english_prompts": true}, ...}

A setting is read where it is used, every time, so that a change made in a settings window holds without Claude Hand
being started again (the chat channel runs for days). What cannot change while a session runs says so in the
catalog ("applies": "start").

Standard library only, and small: the guard hook reads the language through this on every refused call.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path

from handlib import link

CATALOG_FILE = Path(__file__).resolve().parents[2] / "settings" / "catalog.json"
LANGUAGES = ("ko", "en")
LANGUAGE_NAMES = {"ko": "한국어", "en": "English"}  # each in its own words, whatever the interface's language
DAYS_MAX = 3650

_READ: dict[str, tuple[tuple[int, int] | None, dict]] = {}  # path -> ((mtime, size) when read, what it held)
_CATALOG: list[dict] = []  # [the catalog], once read
_CONFIGURED_ONLY = [0]  # how many as_configured() are open


def config_path(home: Path | None = None) -> Path:
    return (home or link.claude_hand_home()) / "config.json"


def live(home: Path | None = None) -> dict:
    """config.json as it is now. Read again only when the file changed; a file that is missing is {}, and one caught
    half written (a settings window is saving it) or broken leaves what was last read. Never an error: the chat
    channel and the hooks ask on every use."""
    path = config_path(home)
    key = str(path)
    seen = _READ.get(key)
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        _READ[key] = (None, {})
        return {}
    if seen is not None and seen[0] == stamp:
        return seen[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        return seen[1] if seen is not None else {}
    _READ[key] = (stamp, data)
    return data


def catalog() -> dict:
    """settings/catalog.json: {"settings": [...], "lab": [...]}. Empty lists when it cannot be read."""
    if not _CATALOG:
        try:
            data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _CATALOG.append({"settings": [e for e in data.get("settings") or [] if isinstance(e, dict) and e.get("key")],
                         "lab": [e for e in data.get("lab") or [] if isinstance(e, dict) and e.get("key")]})
    return _CATALOG[0]


def entry(key: str) -> dict | None:
    """The catalog's entry of a setting, or None."""
    return next((e for e in catalog()["settings"] if e["key"] == key), None)


def lab_entry(key: str) -> dict | None:
    return next((e for e in catalog()["lab"] if e["key"] == key), None)


def clean(found: dict, value):
    """`value` as this setting may hold it, or its default: a switch is true or false, a choice one of its choices,
    days a whole number from 0 to DAYS_MAX. What config.json holds was typed by hand as often as not."""
    kind, default = found.get("kind"), found.get("default")
    if kind == "switch":
        return value if isinstance(value, bool) else default
    if kind == "choice":
        return value if value in [c.get("value") for c in found.get("choices") or []] else default
    if kind == "days":
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= DAYS_MAX else default
    return default


def get(key: str, config: dict | None = None, home: Path | None = None):
    """What a setting is now (its default when config.json does not say, or says something it cannot be)."""
    found = entry(key)
    config = live(home) if config is None else config
    if found is None:
        return config.get(key)
    return clean(found, config.get(key, found.get("default")))


def language(config: dict | None = None, home: Path | None = None, env=os.environ) -> str:
    """"ko" or "en": the language of everything claude-hand itself says. CLAUDE_HAND_LANG, when set to one of the
    two, wins over config.json (the tests, and a single command: `CLAUDE_HAND_LANG=en ... doctor`)."""
    forced = str(env.get("CLAUDE_HAND_LANG") or "").strip().lower()
    if forced in LANGUAGES and not _CONFIGURED_ONLY[0]:
        return forced
    value = (live(home) if config is None else config).get("language")
    return value if value in LANGUAGES else "ko"


@contextlib.contextmanager
def as_configured():
    """Inside it language() is what config.json says, also while CLAUDE_HAND_LANG has one command speak the other
    language. For looking at the files claude-hand wrote: they are in the configured language, and a `doctor` asked
    to speak English once would otherwise call every one of them made by an earlier version."""
    _CONFIGURED_ONLY[0] += 1
    try:
        yield
    finally:
        _CONFIGURED_ONLY[0] -= 1


def lab_on(key: str, config: dict | None = None, home: Path | None = None) -> bool:
    """Whether a lab feature is in use: one that has left the lab ("stage": "stable") always, one in the lab when
    the user turned it on, one the catalog does not know never."""
    found = lab_entry(key)
    if found is None:
        return False
    if found.get("stage") == "stable":
        return True
    chosen = (live(home) if config is None else config).get("lab")
    value = chosen.get(key) if isinstance(chosen, dict) else None
    return value if isinstance(value, bool) else bool(found.get("default"))


def in_lab() -> list[dict]:
    """The features the lab tab lists: those still in the lab, newest first."""
    listed = [e for e in catalog()["lab"] if e.get("stage", "lab") == "lab"]
    return sorted(listed, key=lambda e: [int(p) if p.isdigit() else 0 for p in str(e.get("since", "")).split(".")], reverse=True)


def parse(found: dict, word: str):
    """A value typed on the command line ("on", "off", "en", "30") as this setting holds it. ValueError with the
    values it takes when the word is none of them."""
    word = str(word).strip().lower()
    kind = found.get("kind")
    if kind == "switch":
        if word in ("on", "true", "1", "yes", "켜기", "켬"):
            return True
        if word in ("off", "false", "0", "no", "끄기", "끔"):
            return False
        raise ValueError("on | off")
    if kind == "choice":
        values = [str(c.get("value")) for c in found.get("choices") or []]
        if word in values:
            return word
        raise ValueError(" | ".join(values))
    if kind == "days":
        if word.isdigit() and int(word) <= DAYS_MAX:
            return int(word)
        raise ValueError(f"0 - {DAYS_MAX}")
    raise ValueError("?")


def write(change, home: Path | None = None) -> dict:
    """Change config.json: change(config) alters the dict it is given. The file is read anew just before (another
    window may have written it meanwhile) and replaced as a whole, so nobody reads half of it. Returns what it holds
    now. A file that is not JSON is the user's to fix: SystemExit, as handlib.settings does."""
    from handlib import settings
    path = config_path(home)
    config = settings.read_json_file(path)
    change(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"config.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")  # of this write alone
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    settings.put_in_place(tmp, path)
    try:  # what this process reads next is what it just wrote, even within the same tick of the file's clock
        stat = path.stat()
        _READ[str(path)] = ((stat.st_mtime_ns, stat.st_size), dict(config))
    except OSError:
        _READ.pop(str(path), None)
    return config


def put(key: str, value, home: Path | None = None) -> dict:
    """Set one setting (a value `clean` accepts)."""
    return write(lambda config: config.__setitem__(key, value), home)


def put_lab(key: str, on: bool, home: Path | None = None) -> dict:
    """Turn one lab feature on or off."""
    def change(config: dict) -> None:
        chosen = config.get("lab") if isinstance(config.get("lab"), dict) else {}
        config["lab"] = {**chosen, key: bool(on)}
    return write(change, home)
