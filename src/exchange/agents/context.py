"""Semantic context: structured state plus additive deltas.

Context is not a transcript. It is what the agent knows, in fields, so that
a checkpoint can be materialised and a delta can be applied without keeping
every message ever exchanged.

The additive-only rule is enforced by construction rather than by validation:
`ContextDelta` has no field capable of removing a fact or a decision. The one
removable field is `unresolved`, because there removal is the meaning — a
question got answered. A delta that could drop a fact would quietly rewrite
history, and the agent would have no way to know something was missing.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ContextState:
    objective: str = ""
    constraints: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextDelta:
    objective: str | None = None
    constraints_added: tuple[str, ...] = ()
    decisions_added: tuple[str, ...] = ()
    facts_added: tuple[str, ...] = ()
    unresolved_added: tuple[str, ...] = ()
    unresolved_removed: tuple[str, ...] = ()
    artifacts_added: tuple[str, ...] = ()


def _extend(existing: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
    """Append, preserving order and skipping anything already present."""
    seen = set(existing)
    return existing + tuple(item for item in added if not (item in seen or seen.add(item)))


def apply_delta(state: ContextState, delta: ContextDelta) -> ContextState:
    removed = set(delta.unresolved_removed)
    unresolved = tuple(q for q in state.unresolved if q not in removed)
    return replace(
        state,
        objective=state.objective if delta.objective is None else delta.objective,
        constraints=_extend(state.constraints, delta.constraints_added),
        decisions=_extend(state.decisions, delta.decisions_added),
        facts=_extend(state.facts, delta.facts_added),
        unresolved=_extend(unresolved, delta.unresolved_added),
        artifacts=_extend(state.artifacts, delta.artifacts_added),
    )


_SECTIONS = (
    ("constraints", "Constraints"),
    ("decisions", "Decisions"),
    ("facts", "Known"),
    ("unresolved", "Unresolved"),
    ("artifacts", "Artifacts"),
)


def render(state: ContextState) -> str:
    """Flatten to prompt text. Empty sections are omitted, not shown as empty."""
    parts: list[str] = []
    if state.objective:
        parts.append(f"Objective: {state.objective}")
    for field_name, label in _SECTIONS:
        values = getattr(state, field_name)
        if values:
            listed = "\n".join(f"  - {v}" for v in values)
            parts.append(f"{label}:\n{listed}")
    return "\n\n".join(parts)
