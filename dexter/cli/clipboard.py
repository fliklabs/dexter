"""Putting text on the system clipboard.

No standard library does this, so it is one of the platform's own tools or nothing. Every
option is tried in turn and the first that works wins; a caller gets back whether anything did,
because a menu that says "copied" when nothing was is worse than one that admits it could not.

The last resort is OSC 52 — an escape sequence the *terminal* acts on rather than the machine
running this. It is what makes copying work over SSH, where `pbcopy` would put the text on a
clipboard nobody is sitting at. Not every terminal honours it, which is why it is last and why
its result cannot be checked: writing the sequence always "succeeds".

Nothing here imports curses, so it is testable without a terminal.
"""

import base64
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

TOOLS: tuple[tuple[str, Sequence[str]], ...] = (
    ("pbcopy", ("pbcopy",)),
    ("wl-copy", ("wl-copy",)),
    ("xclip", ("xclip", "-selection", "clipboard")),
    ("xsel", ("xsel", "--clipboard", "--input")),
    ("clip", ("clip",)),
)
"""The commands to try, in order. The first whose executable exists is used."""


def copy(text: str) -> bool:
    """Put `text` on the clipboard. Returns whether it went anywhere.

    Never raises. A failure to copy is a disappointment, not an error, and it must not unwind a
    menu that has the terminal in raw mode.
    """
    if not text:
        return False
    return _by_tool(text) or _by_escape(text)


def _by_tool(text: str) -> bool:
    """Hand the text to whichever platform tool is installed."""
    for name, command in TOOLS:
        if shutil.which(name) is None:
            continue
        try:
            subprocess.run(command, input=text.encode(), check=True)  # noqa: S603
        except OSError, subprocess.SubprocessError:
            continue
        return True
    return False


def _by_escape(text: str) -> bool:
    """Ask the terminal itself to copy, with OSC 52.

    Reported as success without being able to know: the sequence is written to the terminal and
    there is no reply to read. That is the honest limit of the mechanism, and it is better than
    the alternative of claiming nothing happened when usually something did.
    """
    payload = base64.b64encode(text.encode()).decode("ascii")
    try:
        with Path("/dev/tty").open("w", encoding="utf-8") as terminal:
            terminal.write(f"\033]52;c;{payload}\a")
            terminal.flush()
    except OSError:
        try:
            sys.stdout.write(f"\033]52;c;{payload}\a")
            sys.stdout.flush()
        except OSError:
            return False
    return True
