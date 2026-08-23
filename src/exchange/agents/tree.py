"""The execution tree: how an agent got where it is.

Nodes hold a delta, not a copy of the parent's context. Copying whole
contexts down a chain is quadratic in storage; deltas are linear. A
checkpoint materialises the full state at a node so that reconstruction
walks back only as far as the nearest one instead of to the root.

Retrieval is leaf-first: the current node carries `state_version`, so the
world state is immediate, and ancestors are consulted only when the question
needs history. The leaf says what just happened; ancestors say why.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from exchange.agents.context import ContextDelta, ContextState, apply_delta
from exchange.ids import new_id


@dataclass(frozen=True)
class ContextNode:
    node_id: str
    parent_id: str | None
    delta: ContextDelta
    state_version: int
    checkpoint: ContextState | None = None


class ContextTree:
    def __init__(self) -> None:
        self._nodes: dict[str, ContextNode] = {}

    def add(
        self,
        parent_id: str | None,
        delta: ContextDelta,
        state_version: int,
    ) -> str:
        if parent_id is not None and parent_id not in self._nodes:
            raise KeyError(f"unknown parent node {parent_id!r}")
        node_id = new_id("ctx")
        self._nodes[node_id] = ContextNode(
            node_id=node_id,
            parent_id=parent_id,
            delta=delta,
            state_version=state_version,
        )
        return node_id

    def node(self, node_id: str) -> ContextNode:
        return self._nodes[node_id]

    def ancestors(self, node_id: str) -> list[ContextNode]:
        """Nearest first, excluding the node itself."""
        out: list[ContextNode] = []
        current = self._nodes[node_id].parent_id
        while current is not None:
            node = self._nodes[current]
            out.append(node)
            current = node.parent_id
        return out

    def _chain(self, node_id: str) -> list[ContextNode]:
        """Nodes from the nearest checkpoint (inclusive) down to node_id."""
        chain: list[ContextNode] = []
        current: str | None = node_id
        while current is not None:
            node = self._nodes[current]
            chain.append(node)
            if node.checkpoint is not None:
                break
            current = node.parent_id
        chain.reverse()
        return chain

    def walk_length(self, node_id: str) -> int:
        """How many nodes materialising this one has to touch. Used by tests."""
        return len(self._chain(node_id))

    def materialise(self, node_id: str) -> ContextState:
        chain = self._chain(node_id)
        head = chain[0]
        if head.checkpoint is not None:
            state = head.checkpoint
            rest = chain[1:]
        else:
            state = ContextState()
            rest = chain
        for node in rest:
            state = apply_delta(state, node.delta)
        return state

    def checkpoint(self, node_id: str) -> None:
        """Freeze the full state at this node so later walks stop here."""
        state = self.materialise(node_id)
        self._nodes[node_id] = replace(self._nodes[node_id], checkpoint=state)
