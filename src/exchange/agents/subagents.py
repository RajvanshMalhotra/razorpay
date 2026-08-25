"""The three acting sub-agents, each in its own isolated context.

A single agent holding every concern gets slow, gets confused, and says the
wrong thing in the wrong conversation — quoting what was paid to supplier A
while negotiating with supplier B. Each sub-agent therefore branches from the
orchestrator's node and never reads a sibling's branch.

Results travel upward as structured summaries that become facts in the
parent's delta. That is narrowing, not merging: the parent chooses what to
promote rather than reconciling two versions of the same thing.
"""
from __future__ import annotations

from exchange.agents.context import ContextDelta, apply_delta, render
from exchange.agents.tree import ContextTree
from exchange.agents.mandate import compose
from exchange.llm.base import LLMMessage, LLMProvider

TRADER_PROMPT = """You are the Trader for a merchant on a business-to-business exchange.
You buy what the merchant needs and sell what it has. You care about price, quantity,
delivery terms and whether an offer is actually feasible.
Answer in at most three sentences. State numbers plainly. Do not speculate about
counterparties' motives — that is the Diplomat's job."""

SCOUT_PROMPT = """You are the Scout for a merchant on a business-to-business exchange.
You watch demand signals and market trends and judge what an insight is worth.
Answer in at most three sentences. Say what is rising, how confident you are, and what
you would pay for more detail. Do not negotiate — that is the Trader's job."""

DIPLOMAT_PROMPT = """You are the Diplomat for a merchant on a business-to-business exchange.
You judge counterparties: who has dealt well with us, who pushes hard, who is unknown.
Answer in at most three sentences. You advise; you never veto. An unknown counterparty
is an opportunity to be tried at small size, not a risk to be avoided."""


class SubAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        provider: LLMProvider,
        tree: ContextTree,
        parent_id: str,
        state_version: int,
        reasoning_effort: str | None = None,
    ) -> None:
        self.name = name
        self._system = system_prompt
        self._provider = provider
        self._tree = tree
        self._state_version = state_version
        self._effort = reasoning_effort
        # Branch immediately, so this agent's work never lands on the parent's chain.
        self.node_id = tree.add(parent_id, ContextDelta(), state_version)

    def act(self, instruction: str, facts: tuple[str, ...] = ()) -> str:
        inherited = self._tree.materialise(self.node_id)
        if facts:
            # Per-call facts inform this one action without joining the agent's
            # durable context — applied to a copy, never written to the tree.
            inherited = apply_delta(inherited, ContextDelta(facts_added=facts))

        prompt = render(inherited)
        body = f"{prompt}\n\nTask: {instruction}" if prompt else f"Task: {instruction}"

        response = self._provider.complete(
            [LLMMessage("user", body)],
            system=self._system,
            # MEASURED against a real run, not chosen. At the provider's
            # default of 1024 this produced 46 empty replies in the first two
            # minutes of a live round — each one a paid call returning "",
            # then a retry at triple the budget. A sub-agent's prompt carries
            # its materialised context, so it is the longest input in the
            # system and needs the most room to think before answering.
            max_tokens=2400,
            reasoning_effort=self._effort,
        )

        self.node_id = self._tree.add(
            self.node_id,
            ContextDelta(facts_added=(response.text,)),
            self._state_version,
        )
        return response.text

    def reparent(self, parent_id: str) -> None:
        """Re-root this agent's next action under a new shared ancestor.

        Called when the orchestrator promotes a fact, so the agent's later
        actions see it. Its own prior branch is left intact and unreachable —
        history is never rewritten, only re-anchored.
        """
        self.node_id = self._tree.add(parent_id, ContextDelta(), self._state_version)


# Each role takes the merchant's mandate, because a merchant's priorities
# apply to all three: the Trader's haggling, what the Scout thinks an insight
# is worth, and how readily the Diplomat trusts a stranger. `compose` returns
# the role prompt unchanged when there is no mandate, so a merchant that says
# nothing gets exactly the behaviour it got before this existed.


def make_trader(provider, tree, parent_id, state_version, mandate=None) -> SubAgent:
    return SubAgent("trader", compose(TRADER_PROMPT, mandate), provider, tree,
                    parent_id, state_version, reasoning_effort="low")


def make_scout(provider, tree, parent_id, state_version, mandate=None) -> SubAgent:
    return SubAgent("scout", compose(SCOUT_PROMPT, mandate), provider, tree,
                    parent_id, state_version, reasoning_effort="low")


def make_diplomat(provider, tree, parent_id, state_version, mandate=None) -> SubAgent:
    return SubAgent("diplomat", compose(DIPLOMAT_PROMPT, mandate), provider, tree,
                    parent_id, state_version, reasoning_effort="low")
