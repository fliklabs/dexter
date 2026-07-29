"""What a drag has highlighted: which columns are painted, and what gets copied."""

from dexter.cli.interactive.selection import Selection

LINES = ["first line", "second line", "third line"]


class TestEnds:
    def test_a_new_selection_is_empty(self) -> None:
        assert Selection((1, 4)).empty

    def test_extending_it_is_not(self) -> None:
        selection = Selection((1, 4))
        selection.extend((1, 7))

        assert not selection.empty

    def test_dragging_forwards_sorts_the_ends(self) -> None:
        selection = Selection((1, 2))
        selection.extend((3, 5))

        assert (selection.start, selection.end) == ((1, 2), (3, 5))

    def test_dragging_backwards_sorts_them_too(self) -> None:
        """The anchor stays where the button went down whichever way the mouse goes."""
        selection = Selection((3, 5))
        selection.extend((1, 2))

        assert (selection.start, selection.end) == ((1, 2), (3, 5))
        assert selection.anchor == (3, 5)


class TestSpan:
    def test_a_line_outside_the_range_has_none(self) -> None:
        selection = Selection((1, 0))
        selection.extend((1, 4))

        assert selection.span(0, 10) is None
        assert selection.span(2, 10) is None

    def test_an_empty_selection_paints_nothing(self) -> None:
        assert Selection((1, 4)).span(1, 10) is None

    def test_a_single_line_uses_both_columns(self) -> None:
        selection = Selection((1, 2))
        selection.extend((1, 6))

        assert selection.span(1, 20) == (2, 6)

    def test_a_middle_line_is_selected_whole(self) -> None:
        selection = Selection((0, 3))
        selection.extend((2, 4))

        assert selection.span(1, 11) == (0, 11)

    def test_the_first_line_runs_to_its_end(self) -> None:
        selection = Selection((0, 3))
        selection.extend((2, 4))

        assert selection.span(0, 10) == (3, 10)

    def test_the_last_line_stops_at_the_cursor(self) -> None:
        selection = Selection((0, 3))
        selection.extend((2, 4))

        assert selection.span(2, 10) == (0, 4)

    def test_it_never_paints_past_the_end_of_a_short_line(self) -> None:
        """Dragged across a long line and down onto a short one."""
        selection = Selection((0, 0))
        selection.extend((1, 40))

        assert selection.span(1, 5) == (0, 5)


class TestText:
    def test_an_empty_selection_copies_nothing(self) -> None:
        assert Selection((0, 0)).text(LINES) == ""

    def test_part_of_one_line(self) -> None:
        selection = Selection((1, 0))
        selection.extend((1, 6))

        assert selection.text(LINES) == "second"

    def test_across_lines_keeps_the_newlines(self) -> None:
        selection = Selection((0, 6))
        selection.extend((2, 5))

        assert selection.text(LINES) == "line\nsecond line\nthird"

    def test_backwards_copies_the_same_thing(self) -> None:
        selection = Selection((2, 5))
        selection.extend((0, 6))

        assert selection.text(LINES) == "line\nsecond line\nthird"

    def test_a_range_past_the_end_stops_at_the_last_line(self) -> None:
        """Output can shrink between a drag and its release; that must not raise."""
        selection = Selection((1, 0))
        selection.extend((9, 4))

        assert selection.text(LINES) == "second line\nthird line"

    def test_a_range_entirely_past_the_end_copies_nothing(self) -> None:
        selection = Selection((7, 0))
        selection.extend((9, 4))

        assert selection.text(LINES) == ""
