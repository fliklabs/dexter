"""Output helpers, kept apart so the wiring and handlers stay free of presentation code."""


def short(message_id: str) -> str:
    """Shorten a message id to a leading block and a distinguishing tail.

    Both halves are needed. A UUIDv7 begins with a millisecond timestamp, which is what makes
    ids sort chronologically — but a walkthrough sends everything inside the same millisecond,
    so the leading block alone is identical for every message and would make distinct
    dispatches look like one. The tail is what tells them apart.
    """
    return f"{message_id[:8]}…{message_id[-4:]}"


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
