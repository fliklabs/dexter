"""Output helpers, kept apart so the wiring and handlers stay free of presentation code."""

import json


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


def request(method: str, path: str, detail: str = "") -> None:
    """Print the request about to be made."""
    print(f"  {method} {path}{f'  {detail}' if detail else ''}")


def reply(status: int, body: object, detail: str = "") -> None:
    """Print a response: its status, its body, and anything else worth showing."""
    rendered = json.dumps(body, separators=(", ", ": ")) if body is not None else "-"
    if len(rendered) > _WIDTH:
        rendered = f"{rendered[: _WIDTH - 1]}…"
    print(f"     {status}  {rendered}{f'  {detail}' if detail else ''}")


_WIDTH = 72
"""Where a rendered body is truncated, so one long line cannot wreck the transcript."""
