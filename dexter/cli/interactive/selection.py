"""What the mouse has highlighted, in the text rather than on the screen.

A selection is held in **document** coordinates — a line index and a column — never screen
ones. That is the whole reason this is a type rather than two numbers on the navigator: output
arrives while a drag is in progress and the view scrolls under it, so a selection anchored to a
screen row would slide onto text nobody chose. Anchored to the line, it stays on the words it
was dragged across.

Nothing here imports curses, so all of it is testable without a terminal.
"""

type Point = tuple[int, int]
"""A position in the text: `(line index, column)`. The column may be one past the last
character, which is what "dragged to the end of the line" means."""


class Selection:
    """A range of text, from where the drag started to where it is now.

    The anchor stays where the button went down and the cursor follows the mouse, so dragging
    backwards is as ordinary as dragging forwards — `start` and `end` sort themselves out.
    """

    __slots__ = ("anchor", "cursor")

    def __init__(self, anchor: Point) -> None:
        """Begin a selection at `anchor`, empty until it is extended."""
        self.anchor = anchor
        self.cursor = anchor

    def extend(self, point: Point) -> None:
        """Move the loose end to `point`."""
        self.cursor = point

    @property
    def start(self) -> Point:
        """The earlier of the two ends."""
        return min(self.anchor, self.cursor)

    @property
    def end(self) -> Point:
        """The later of the two ends."""
        return max(self.anchor, self.cursor)

    @property
    def empty(self) -> bool:
        """Whether nothing is selected — a click that never became a drag."""
        return self.anchor == self.cursor

    def span(self, index: int, length: int) -> tuple[int, int] | None:
        """The columns of line `index` that are selected, or `None` if it has none.

        Used for drawing. `length` bounds the highlight so a selection dragged past the end of
        a short line does not paint over empty space beyond it.
        """
        (first, from_column), (last, to_column) = self.start, self.end
        if self.empty or index < first or index > last:
            return None

        begins = from_column if index == first else 0
        ends = to_column if index == last else length
        return (min(begins, length), min(ends, length))

    def text(self, lines: list[str]) -> str:
        """The selected text, with newlines between lines, ready to be copied."""
        (first, from_column), (last, to_column) = self.start, self.end
        if self.empty or first >= len(lines):
            return ""

        if last >= len(lines):
            # The text shrank under a drag that had already gone past its end. The last line
            # there still is was dragged across in full, so it is taken in full — the column
            # belongs to a line that is no longer there and means nothing here.
            last, to_column = len(lines) - 1, len(lines[-1])
        if first == last:
            return lines[first][from_column:to_column]

        taken = [lines[first][from_column:]]
        taken.extend(lines[first + 1 : last])
        taken.append(lines[last][:to_column])
        return "\n".join(taken)
