"""Covers `dexter.aws.models.conditions` and `dexter.aws.dynamodb._expressions`.

Assertions are on the compiled expression and its placeholder maps rather than on the tree,
because the expression is what DynamoDB reads and the tree is an implementation detail of getting
there.

**The reserved-word test is the one that would otherwise bite in production.** DynamoDB reserves
several hundred words, `status` and `name` among them, and an unescaped one is a syntax error
from the service — on the query that happens to filter by status, and no other.
"""

import pytest

from dexter.aws import (
    Attr,
    ComparisonOperator,
    Condition,
    ItemEncodingError,
    Key,
    describe_comparison_operator,
)
from dexter.aws.dynamodb._expressions import (
    compile_condition,
    compile_pair,
    compile_update,
    merge,
)


def rendered(condition: object, /, *, is_key: bool = False) -> str:
    """The expression, with placeholders substituted back so it reads as what it means."""
    compiled = compile_condition(condition, is_key=is_key)  # type: ignore[arg-type]
    text = compiled.expression
    for token, name in compiled.names.items():
        text = text.replace(token, name)
    return text


class TestComparisons:
    def test_equality(self) -> None:
        assert rendered(Attr("status").equals("PAID")) == "status = :v0"

    def test_inequality(self) -> None:
        assert rendered(Attr("status").not_equals("PAID")) == "status <> :v0"

    def test_the_four_orderings(self) -> None:
        assert rendered(Attr("n").less_than(1)) == "n < :v0"
        assert rendered(Attr("n").less_or_equal(1)) == "n <= :v0"
        assert rendered(Attr("n").greater_than(1)) == "n > :v0"
        assert rendered(Attr("n").greater_or_equal(1)) == "n >= :v0"

    def test_the_value_is_serialised_into_the_wire_form(self) -> None:
        """The builder hands back plain Python; the client API wants `{"S": ...}`."""
        compiled = compile_condition(Attr("status").equals("PAID"))
        assert compiled.values == {":v0": {"S": "PAID"}}

    def test_an_unstorable_value_is_refused(self) -> None:
        with pytest.raises(ItemEncodingError):
            compile_condition(Attr("price").equals(19.99))


class TestOtherOperators:
    def test_between(self) -> None:
        assert rendered(Attr("n").between(1, 9)) == "n BETWEEN :v0 AND :v1"

    def test_begins_with(self) -> None:
        assert rendered(Attr("sk").begins_with("order#")) == "begins_with(sk, :v0)"

    def test_contains(self) -> None:
        assert rendered(Attr("tags").contains("new")) == "contains(tags, :v0)"

    def test_exists(self) -> None:
        assert rendered(Attr("email").exists()) == "attribute_exists(email)"

    def test_not_exists(self) -> None:
        assert rendered(Attr("pk").not_exists()) == "attribute_not_exists(pk)"

    def test_is_in(self) -> None:
        assert rendered(Attr("s").is_in(("A", "B"))) == "s IN (:v0, :v1)"

    def test_attribute_type(self) -> None:
        assert rendered(Attr("x").attribute_type("S")) == "attribute_type(x, :v0)"


class TestComposition:
    def test_and(self) -> None:
        condition = Attr("a").equals(1) & Attr("b").equals(2)
        assert rendered(condition) == "(a = :v0 AND b = :v1)"

    def test_or(self) -> None:
        condition = Attr("a").equals(1) | Attr("b").equals(2)
        assert rendered(condition) == "(a = :v0 OR b = :v1)"

    def test_not(self) -> None:
        assert rendered(~Attr("a").equals(1)) == "(NOT a = :v0)"

    def test_nesting_survives(self) -> None:
        condition = (Attr("a").equals(1) | Attr("b").equals(2)) & Attr("c").exists()
        assert rendered(condition) == "((a = :v0 OR b = :v1) AND attribute_exists(c))"

    def test_each_value_gets_its_own_placeholder(self) -> None:
        condition = Attr("a").equals(1) & Attr("b").equals(2)
        assert set(compile_condition(condition).values) == {":v0", ":v1"}


class TestReservedWords:
    def test_a_reserved_attribute_name_is_escaped(self) -> None:
        """**Not cosmetic.** `status` unescaped is a syntax error from the service — on the one
        query that happens to filter by it, and no other."""
        compiled = compile_condition(Attr("status").equals("PAID"))
        assert "status" not in compiled.expression
        assert compiled.names == {"#n0": "status"}

    def test_every_name_is_escaped_not_only_the_reserved_ones(self) -> None:
        """boto3's builder does not consult the reserved list, and that is the right call: a
        caller should not have to know which of several hundred words are special, and a name
        that becomes reserved in a later release would otherwise start failing."""
        compiled = compile_condition(Attr("colour").equals("red"))
        assert compiled.expression == "#n0 = :v0"
        assert compiled.names == {"#n0": "colour"}


class TestKeyConditions:
    def test_a_key_condition_compiles_with_the_key_grammar(self) -> None:
        """**Compiled with `is_key_condition=True`**, which the builder needs to be told.

        Without it the builder produces something the service refuses, because a key condition
        permits a strictly smaller grammar than a filter.
        """
        condition = Key("pk").equals("u#1") & Key("sk").begins_with("order#")
        assert rendered(condition, is_key=True) == (
            "(pk = :v0 AND begins_with(sk, :v1))"
        )

    def test_a_key_offers_only_the_operators_a_key_condition_allows(self) -> None:
        """The improvement over one shared class: `Key("sk").contains(...)` is a type error
        rather than a service error, so the mistake is found hours earlier."""
        assert not hasattr(Key("sk"), "contains")
        assert not hasattr(Key("sk"), "exists")
        assert not hasattr(Key("sk"), "is_in")

    def test_an_attr_offers_the_full_set(self) -> None:
        for operator in ("contains", "exists", "not_exists", "is_in", "attribute_type"):
            assert hasattr(Attr("x"), operator)


class TestUpdates:
    def test_set_writes_attributes(self) -> None:
        update = compile_update(
            set_values={"status": "PAID"}, remove=(), add=None, delete=None
        )
        assert update.expression == "SET #u0 = :u0"
        assert update.names == {"#u0": "status"}
        assert update.values == {":u0": {"S": "PAID"}}

    def test_remove_deletes_attributes(self) -> None:
        update = compile_update(
            set_values=None, remove=("draft",), add=None, delete=None
        )
        assert update.expression == "REMOVE #u0"

    def test_add_increments(self) -> None:
        """The atomic counter, which a read-modify-write cannot be."""
        update = compile_update(
            set_values=None, remove=(), add={"views": 1}, delete=None
        )
        assert update.expression == "ADD #u0 :u0"

    def test_delete_removes_set_members(self) -> None:
        update = compile_update(
            set_values=None, remove=(), add=None, delete={"tags": {"old"}}
        )
        assert update.expression == "DELETE #u0 :u0"

    def test_the_clauses_combine_in_one_expression(self) -> None:
        update = compile_update(
            set_values={"a": 1}, remove=("b",), add={"c": 1}, delete={"d": {"x"}}
        )
        assert update.expression.startswith("SET ")
        assert " REMOVE " in update.expression
        assert " ADD " in update.expression
        assert " DELETE " in update.expression

    def test_several_set_values_are_comma_separated(self) -> None:
        update = compile_update(
            set_values={"a": 1, "b": 2}, remove=(), add=None, delete=None
        )
        assert update.expression == "SET #u0 = :u0, #u1 = :u1"

    def test_every_name_goes_through_a_placeholder(self) -> None:
        """Rather than only the reserved ones, because a caller should not have to know the
        list of several hundred."""
        update = compile_update(
            set_values={"colour": "red"}, remove=(), add=None, delete=None
        )
        assert update.names == {"#u0": "colour"}

    def test_an_update_that_changes_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must change something"):
            compile_update(set_values=None, remove=(), add=None, delete=None)


class TestMerging:
    def test_an_update_and_a_condition_do_not_collide(self) -> None:
        """**The reason the prefixes differ.** An update allocates `#u0` and `:u0`, the
        condition builder allocates `#n0` and `:v0`, and both travel in one request."""
        update = compile_update(
            set_values={"status": "PAID"}, remove=(), add=None, delete=None
        )
        check = compile_condition(Attr("status").equals("PENDING"))
        names, values = merge(update, check)

        assert names == {"#u0": "status", "#n0": "status"}
        assert set(values) == {":u0", ":v0"}

    def test_merging_none_is_allowed(self) -> None:
        assert merge(None, None) == ({}, {})


class TestTheOperatorEnum:
    def test_the_values_match_the_names(self) -> None:
        assert all(operator.value == operator.name for operator in ComparisonOperator)

    def test_it_renders_as_the_symbol_a_caller_would_type(self) -> None:
        assert (
            describe_comparison_operator(ComparisonOperator.EQUALS)
            == "ComparisonOperator.EQUALS"
        )


class TestSharedPlaceholders:
    def test_a_key_condition_and_a_filter_do_not_collide(self) -> None:
        """**A silent wrong-answer bug without the shared builder.**

        Each `ConditionExpressionBuilder` counts from zero, so compiling the two separately
        gives both `#n0` and `:v0`. Merging then discards half of each, and the query runs the
        right expressions against the other clause's values — returning the wrong rows, with
        nothing failing anywhere.
        """
        keys, rest = compile_pair(
            Key("pk").equals("u#1"), Attr("status").not_equals("CANCELLED")
        )

        assert rest is not None
        assert set(keys.names) & set(rest.names) == set()
        assert set(keys.values) & set(rest.values) == set()
        assert keys.expression == "#n0 = :v0"
        assert rest.expression == "#n1 <> :v1"

    def test_a_query_with_no_filter_compiles_only_the_key(self) -> None:
        keys, rest = compile_pair(Key("pk").equals("u#1"), None)
        assert rest is None
        assert keys.expression == "#n0 = :v0"


class TestKeyRanges:
    """The sort-key comparisons, which are most of why `Key` exists at all."""

    def test_less_than(self) -> None:
        assert rendered(Key("sk").less_than(5), is_key=True) == "sk < :v0"

    def test_less_or_equal(self) -> None:
        assert rendered(Key("sk").less_or_equal(5), is_key=True) == "sk <= :v0"

    def test_greater_than(self) -> None:
        assert rendered(Key("sk").greater_than(5), is_key=True) == "sk > :v0"

    def test_greater_or_equal(self) -> None:
        assert rendered(Key("sk").greater_or_equal(5), is_key=True) == "sk >= :v0"

    def test_between(self) -> None:
        assert (
            rendered(Key("sk").between(1, 9), is_key=True) == "sk BETWEEN :v0 AND :v1"
        )

    def test_a_range_composes_with_the_partition_key(self) -> None:
        """The shape every "the page of orders after this one" query has."""
        condition = Key("pk").equals("u#1") & Key("sk").greater_than("order#5")
        assert rendered(condition, is_key=True) == "(pk = :v0 AND sk > :v1)"


class TestAnUnknownNode:
    def test_a_condition_this_module_cannot_compile_says_so(self) -> None:
        """Reachable, because `Condition` is subclassable and nothing stops a consumer.

        The alternative to this clause is `match` falling through and returning `None`, which
        reaches boto3's builder as a non-condition and fails there with something unrelated.
        """

        class Homemade(Condition):
            __slots__ = ()

        with pytest.raises(ItemEncodingError, match="Homemade"):
            compile_condition(Homemade())
