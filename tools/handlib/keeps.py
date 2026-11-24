"""Files in a Unity project that a request is about to change, kept so that undoing the request puts them back.

Unity's Undo knows nothing of files, and MCP for Unity's tools write most of them directly (a script, a shader, a
texture): Unity hears of it only afterwards, when what was there is gone. The guard hook sees every call of a
subagent before it runs, so it copies the files the call names, and Claude Hand Tools puts them back when the
request is undone (ChCheckpoint.cs, which itself notes what Unity does tell beforehand: new assets, saves through
Unity, deletions, moves).

Nothing here goes by the tool's name: MCP for Unity has some thirty tools that write files, and they change from
version to version. Every short text in the call's input that names a file under Assets is kept. A file kept
needlessly (the call only read it) costs one copy: at undo it is the same as what is there and nothing happens.

In <project>/Library/ClaudeHand:
  at-work.json                    {"request", "ts", "stopped"}: the request a subagent works for in this project now.
                                  Only what happens to files meanwhile is the request's doing; what the user makes
                                  by hand afterwards is not, and an undo leaves it alone.
  replaced/<request>/             the files kept for one request, each with its .meta, and
  replaced/<request>/kept.jsonl   a line per kept file: {"path": "Assets/...", "kept": where it is, "ts": when}
"""

from __future__ import annotations

import glob
import json
import os
import posixpath
import re
import shutil
import time
from pathlib import Path

MAX_FILES = 20                # files kept for one call
MAX_BYTES = 64 * 1024 * 1024  # a larger file is not copied before every call that names it
MAX_TEXT = 300                # a longer text in a tool's input is contents, not a path
MAX_TEXTS = 400               # texts looked at in one call
MAX_JSON = 20000              # a text this short that holds JSON is read as input too (MCP for Unity takes some that way)
MAX_MATCHES = 3               # files a name without an extension may stand for
KEPT_REQUESTS = 10            # requests whose files stay in replaced/ (ChCheckpoint.cs keeps as many)
WORK_SECONDS = 300.0          # with no word that the subagent stopped: how long after its last call it counts as at work
# Where MCP for Unity puts a script, a shader or a scene that is given by its name alone.
DEFAULT_FOLDERS = ("Assets/Scripts", "Assets/Shaders", "Assets/Scenes", "Assets")


def library(project: Path) -> Path:
    return Path(project) / "Library" / "ClaudeHand"


def sanitize(name: str) -> str:
    """ChPaths.Sanitize of the Unity package: both write into the request's folder, so the two must agree."""
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name or "").strip("_")[:48] or "object"


def request_folder(project: Path, key: str) -> Path:
    return library(project) / "replaced" / sanitize("req_" + key)


# --------------------------------------------------------------------------- which request is at work


def read_work(project: Path) -> dict:
    try:
        work = json.loads((library(project) / "at-work.json").read_text(encoding="utf-8"))
        return work if isinstance(work, dict) else {}
    except (OSError, ValueError):
        return {}


def write_work(project: Path, work: dict) -> None:
    folder = library(project)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"at-work.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(work), encoding="utf-8")
    try:
        os.replace(tmp, folder / "at-work.json")  # Unity may be reading it: it sees the old one or the new one, never half
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def local_key(work: dict, now: float, fresh: bool = False) -> str:
    """A name for the request when no chat channel gives one (Claude Hand is not running): the work that goes on
    counts as one request until it pauses for WORK_SECONDS, or the subagent sets an undo point of its own (fresh)."""
    request = str(work.get("request") or "")
    if not fresh and request.startswith("local-") and now - float(work.get("ts") or 0) <= WORK_SECONDS:
        return request
    return f"local-{int(now * 1000)}"


def begin_work(project: Path, key: str, now: float | None = None) -> None:
    """A subagent is about to make a call for this request."""
    write_work(project, {"request": key, "ts": time.time() if now is None else now, "stopped": 0})


def end_work(project: Path, now: float | None = None) -> None:
    """The subagent finished its turn. Unity gives what it still has to import a few seconds (a script written
    just now), then counts file changes as the user's own."""
    work = read_work(project)
    if work.get("request") and not work.get("stopped"):
        write_work(project, {**work, "stopped": time.time() if now is None else now})


# --------------------------------------------------------------------------- the files a call names


def texts(value, found: list | None = None) -> list[tuple[str, list[str]]]:
    """Every short text in a tool's input, each with the other short texts of the object it is in (a folder and a
    name are given side by side): [(text, siblings)]."""
    found = [] if found is None else found
    if len(found) >= MAX_TEXTS:
        return found
    if isinstance(value, dict):
        short = [v for v in value.values() if isinstance(v, str) and is_short(v)]
        for item in value.values():
            if isinstance(item, str):
                add_text(item, short, found)
            else:
                texts(item, found)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                add_text(item, [], found)
            else:
                texts(item, found)
    return found


def is_short(text: str) -> bool:
    return 0 < len(text.strip()) <= MAX_TEXT and "\n" not in text.strip()


def add_text(text: str, siblings: list[str], found: list) -> None:
    stripped = text.strip()
    if stripped[:1] in ("{", "[") and len(stripped) <= MAX_JSON:
        try:
            texts(json.loads(stripped), found)  # properties handed over as JSON text
            return
        except ValueError:
            pass
    if is_short(text) and len(found) < MAX_TEXTS:
        found.append((stripped, [s for s in siblings if s is not text]))


def relative(text: str, project: Path) -> str | None:
    """A text as a path inside the project ("Assets/x.mat", or "Scripts/x.cs" for a tool to complete), None when it
    cannot be one: a file elsewhere on the disk, or a path that climbs out."""
    text = text.strip().replace("\\", "/")
    text = re.sub(r"^[A-Za-z][A-Za-z0-9+.\-]*://(path/)?", "", text)  # unity://path/Assets/x.cs, file:///C:/...
    if re.match(r"^/[A-Za-z]:/", text):
        text = text[1:]
    root = Path(project).as_posix().rstrip("/")
    if text.lower().startswith(root.lower() + "/"):
        text = text[len(root) + 1:]
    elif re.match(r"^[A-Za-z]:", text) or text.startswith("//"):
        return None
    text = text.lstrip("/")
    while text.lower().startswith("assets/assets/"):
        text = text[len("assets/"):]
    if not text or any(ch in text for ch in '*?<>|":') or ".." in text.split("/"):
        return None
    return posixpath.normpath(text)


def candidates(text: str, siblings: list[str], project: Path) -> list[str]:
    """The asset paths a text may stand for, likeliest first."""
    rel = relative(text, project)
    if rel is None or rel in (".", "Assets") or rel.lower() == "assets":
        return []
    if rel.lower().startswith("assets/"):
        return [rel]
    if "/" in rel:
        return ["Assets/" + rel]
    folders = []
    for sibling in siblings:  # a name next to its folder
        there = relative(sibling, project)
        if there and there != ".":
            for folder in ([there] if there.lower() == "assets" or there.lower().startswith("assets/") else ["Assets/" + there, there]):
                if (Path(project) / folder).is_dir():
                    folders.append(folder)
    return [f"{folder}/{rel}" for folder in [*folders, *DEFAULT_FOLDERS]]


def existing(project: Path, rel: str) -> list[str]:
    """The files that are there for a candidate, as Unity names them: the file itself, or for a name without an
    extension (a script given as "Player") the files of that name."""
    full = Path(project) / rel
    found = []
    if full.is_file():
        found = [full]
    elif not full.suffix and full.parent.is_dir():
        found = sorted(Path(p) for p in glob.glob(glob.escape(str(full)) + ".*") if not p.lower().endswith(".meta")
                       and os.path.isfile(p))[:MAX_MATCHES]
    root, paths = Path(os.path.realpath(project)), []
    for path in found:
        if path.suffix.lower() == ".meta":
            continue
        try:
            real = Path(os.path.realpath(path)).relative_to(root).as_posix()  # the spelling on disk, which Unity goes by
        except ValueError:
            continue  # a link that leads out of the project
        if real.startswith("Assets/"):
            paths.append(real)
    return paths


def named_files(project: Path, tool_input) -> list[str]:
    """The files under Assets that a tool's input names, as asset paths. Never raises on what the input holds."""
    paths: list[str] = []
    for text, siblings in texts(tool_input):
        try:
            for rel in candidates(text, siblings, project):
                hits = existing(project, rel)
                paths += [p for p in hits if p not in paths]
                if hits:
                    break
        except (OSError, ValueError):
            continue
        if len(paths) >= MAX_FILES:
            break
    return paths[:MAX_FILES]


# --------------------------------------------------------------------------- keeping them


def read_kept(project: Path, key: str) -> list[dict]:
    """What is kept for a request so far, oldest first."""
    lines = []
    try:
        for line in (request_folder(project, key) / "kept.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("path") and entry.get("kept"):
                lines.append(entry)
    except OSError:
        pass
    return lines


def keep(project: Path, key: str, paths: list[str], now: float | None = None) -> list[dict]:
    """Copy these files (asset paths) and their .meta into the request's folder, once per file and request: the
    first copy is what was there before the request. Returns what it wrote down. A file that cannot be copied is
    left out: a call is never held up over this."""
    now = time.time() if now is None else now
    folder = request_folder(project, key)
    have = {entry["path"] for entry in read_kept(project, key)}
    written = []
    for rel in paths:
        source = Path(project) / rel
        try:
            if rel in have or not source.is_file() or source.stat().st_size > MAX_BYTES:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            stem = f"{source.stem}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(now))}"
            kept = folder / (stem + source.suffix)
            n = 2
            while kept.exists():  # two files of one name, from different folders
                kept, n = folder / f"{stem}_{n}{source.suffix}", n + 1
            shutil.copy2(source, kept)
            meta = Path(str(source) + ".meta")
            if meta.is_file():
                shutil.copy2(meta, str(kept) + ".meta")
            entry = {"path": rel, "kept": kept.as_posix(), "ts": now}
            with open(folder / "kept.jsonl", "a", encoding="utf-8") as journal:
                journal.write(json.dumps(entry) + "\n")
            have.add(rel)
            written.append(entry)
        except OSError:
            continue
    if written:
        prune(folder.parent, KEPT_REQUESTS)
    return written


def prune(root: Path, keep_count: int) -> None:
    """Keep the newest entries of a holding folder, by when they were made (a copy has the dates of what it was
    copied from, but is made now). A file and its .meta count as one, a request's folder as one. Best effort."""
    try:
        entries = [e for e in Path(root).iterdir() if e.suffix.lower() != ".meta"]
        made = lambda e: getattr(e.stat(), "st_birthtime", None) or e.stat().st_ctime
        for entry in sorted(entries, key=made, reverse=True)[keep_count:]:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
                Path(str(entry) + ".meta").unlink(missing_ok=True)
    except OSError:
        pass
