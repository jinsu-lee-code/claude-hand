"""The language of what claude-hand says: Korean as it is written in the code, or English from handlib/english.py.

  T("교환 폴더의 파일을 {days}일 동안 둡니다.", days=30)

The Korean text is the source and stays where it is used; with the language set to English (prefs.language) T looks
it up in english.EN and fills the same {names} in. Korean therefore comes out exactly as written, and a text with no
English yet comes out in Korean rather than not at all (tools/tests/test_lang.py fails on one).

A text that is not said where it is written (a module's constant, a table of words) is marked with N_, which only
marks it for that test, and goes through T where it is said: T(STATES[state]). A constant wrapped in T itself would
keep the language of the moment the module was loaded, and the chat channel runs for days.

The Unity package (ChText.cs) and the Blender add-on have tables of their own, built the same way; the test holds a
text that two of them say to the same English.

Standard library only, and small: eventlog and the hooks load it.
"""

from __future__ import annotations

from handlib import prefs


def language() -> str:
    return prefs.language()


def N_(text: str) -> str:
    """Marks a Korean text that is said elsewhere, through T. Returns it as it is."""
    return text


def T(text: str, /, **values) -> str:
    """`text` in the language now in use, with its {names} filled in from `values`. A text that counts something
    has a second English wording for when the count is one (english.EN_ONE): "1 action", not "1 actions"."""
    if prefs.language() == "en":
        from handlib import english
        one = english.EN_ONE.get(text) if values else None
        text = one[1] if one and type(values.get(one[0])) is int and values[one[0]] == 1 else english.EN.get(text, text)
    return text.format(**values) if values else text


def both(text: str) -> tuple[str, str]:
    """A text as each language has it, (Korean, English): for reading back what was said in either."""
    from handlib import english
    return text, english.EN.get(text, text)


def pick(names: dict | None) -> str:
    """Of a catalog entry's {"ko": ..., "en": ...}, the one in the language now in use (Korean when the other is missing)."""
    names = names if isinstance(names, dict) else {}
    return str(names.get(prefs.language()) or names.get("ko") or "")
