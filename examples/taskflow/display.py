"""Output helpers, kept apart so the wiring and services stay free of presentation code.

The important one is `tag`, which labels an object `ClassName#N` with a number assigned in
first-seen order. Raw `id()` hex would be unreadable and would change every run; a stable short
tag makes it obvious at a glance when two lines refer to the same object — which is the entire
point of the transcript.
"""

import itertools

_counter = itertools.count(1)
_tags: dict[int, str] = {}
_retained: list[object] = []


def tag(instance: object) -> str:
    """Return a short stable label for `instance`, such as `ConnectionPool#1`.

    The same object always gets the same label, and two different objects never share one, so
    identity is visible without printing memory addresses.

    Every tagged object is retained deliberately. The label is keyed on `id()`, and CPython
    reuses an address once an object is collected — so without holding a reference, a
    transient that has just been garbage-collected would hand its label to whatever is
    allocated next, and the transcript would claim two unrelated objects are the same one. A
    demonstration that has to be trusted cannot afford that; a real program should not keep
    this list.
    """
    key = id(instance)
    existing = _tags.get(key)
    if existing is not None:
        return existing
    label = f"{type(instance).__name__}#{next(_counter)}"
    _tags[key] = label
    _retained.append(instance)
    return label


def reset_tags() -> None:
    """Forget every label, so a second walkthrough numbers from one again."""
    _tags.clear()
    _retained.clear()


def heading(title: str) -> None:
    """Print a section heading."""
    print()
    print(title)


def line(text: str = "") -> None:
    """Print one indented line of detail."""
    print(f"  {text}" if text else "")


def note(text: str) -> None:
    """Print an indented aside, marked so it is not mistaken for output."""
    print(f"    · {text}")
