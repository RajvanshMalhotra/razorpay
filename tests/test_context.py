import dataclasses

from exchange.agents.context import ContextDelta, ContextState, apply_delta, render


def test_applying_a_delta_adds_facts():
    state = ContextState(objective="buy mailers", facts=("stock is low",))
    delta = ContextDelta(facts_added=("merchant_41 quotes 1940",))

    result = apply_delta(state, delta)

    assert result.facts == ("stock is low", "merchant_41 quotes 1940")


def test_applying_a_delta_leaves_the_original_untouched():
    state = ContextState(facts=("a",))

    apply_delta(state, ContextDelta(facts_added=("b",)))

    assert state.facts == ("a",)


def test_a_resolved_question_can_be_removed():
    state = ContextState(unresolved=("what is their lead time?", "do they ship north?"))
    delta = ContextDelta(unresolved_removed=("what is their lead time?",))

    result = apply_delta(state, delta)

    assert result.unresolved == ("do they ship north?",)


def test_removing_an_unresolved_question_that_is_absent_is_harmless():
    state = ContextState(unresolved=("a",))

    result = apply_delta(state, ContextDelta(unresolved_removed=("b",)))

    assert result.unresolved == ("a",)


def test_there_is_no_way_to_remove_a_fact():
    """The additive-only rule is structural: the field does not exist."""
    fields = {f.name for f in dataclasses.fields(ContextDelta)}

    assert "facts_removed" not in fields
    assert "decisions_removed" not in fields


def test_objective_is_replaced_not_appended():
    state = ContextState(objective="buy mailers")

    result = apply_delta(state, ContextDelta(objective="buy boxes instead"))

    assert result.objective == "buy boxes instead"


def test_objective_is_kept_when_the_delta_does_not_set_it():
    state = ContextState(objective="buy mailers")

    result = apply_delta(state, ContextDelta(facts_added=("x",)))

    assert result.objective == "buy mailers"


def test_duplicate_facts_are_not_added_twice():
    state = ContextState(facts=("a",))

    result = apply_delta(state, ContextDelta(facts_added=("a", "b")))

    assert result.facts == ("a", "b")


def test_render_produces_labelled_sections_for_populated_fields_only():
    state = ContextState(objective="buy mailers", facts=("stock low",))

    text = render(state)

    assert "buy mailers" in text
    assert "stock low" in text
    assert "unresolved" not in text.lower()


def test_render_of_an_empty_state_is_empty():
    assert render(ContextState()) == ""
