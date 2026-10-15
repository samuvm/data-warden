"""LangGraph used strictly as a state machine. Thin adapter.

Decides nothing. Wraps `run_loop` in a graph so callers can get tracing,
checkpoints or a human `interrupt`. The functions doing the work are the ones the
evaluation uses; deleting this file would not change `G-RECOVERY`.

The retry loop stays outside the framework: a framework-managed retry policy would
confound exactly what `G-RECOVERY` measures.

Per `docs/STACK.md`, state machine only: retrieval is plain SQL, model access is the
`Provider`, and no `langchain-*` package is a dependency, so no domain module imports
a provider SDK.

It does not execute. The graph ends with a `ValidatedQuery` or a rejection; the
engine is reachable only through `AuditedExecutor` (I-06), enforced by
`lint-imports`.

Measured, not unit-tested (`docs/RULES.md`): a unit test of the graph would test
LangGraph. A contract test checks that graph and loop produce the same result.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from datawarden.domain.types import RejectionReason, ValidatedQuery
from datawarden.nl2sql.loop import MAX_RETRIES, Attempt, run_loop
from datawarden.nl2sql.providers import Provider

Validate = Callable[[str], ValidatedQuery | RejectionReason]


def _keep_last(_current: Any, incoming: Any) -> Any:
    """Explicit reducer: the writing node wins, with no merge surprises."""
    return incoming


class GraphState(TypedDict, total=False):
    """Graph state. Data, never authority.

    The `principal` is not carried here: the role never comes from unauthenticated
    data (I-09), and graph state is whatever a previous node wrote. The caller binds
    the role inside `validate` before building the graph.
    """

    question: Annotated[str, _keep_last]
    attempts: Annotated[tuple[Attempt, ...], _keep_last]
    query: Annotated[ValidatedQuery | None, _keep_last]
    rejection: Annotated[RejectionReason | None, _keep_last]
    provider_name: Annotated[str, _keep_last]


def build(
    *,
    provider: Provider,
    validate: Validate,
    max_retries: int = MAX_RETRIES,
    seed: Attempt | None = None,
) -> Any:
    """Compile the graph: a single node delegating to `run_loop`.

    Splitting it into generate / validate / decide nodes would move the retry
    decision, which `G-RECOVERY` measures, into the graph. The graph provides tracing
    and checkpoints; the retry policy stays where it is tested.
    """
    from langgraph.graph import END, START, StateGraph

    def resolve(state: GraphState) -> GraphState:
        result = run_loop(
            state["question"],
            provider=provider,
            validate=validate,
            max_retries=max_retries,
            seed=seed,
        )
        return {
            "question": state["question"],
            "attempts": result.attempts,
            "query": result.query,
            "rejection": result.rejection,
            "provider_name": result.provider,
        }

    graph = StateGraph(GraphState)
    graph.add_node("resolve", resolve)
    graph.add_edge(START, "resolve")
    graph.add_edge("resolve", END)
    return graph.compile()


def ask(
    question: str,
    *,
    provider: Provider,
    validate: Validate,
    max_retries: int = MAX_RETRIES,
) -> GraphState:
    """Shortcut: compile and invoke the graph, returning the final state."""
    compiled = build(provider=provider, validate=validate, max_retries=max_retries)
    result: GraphState = compiled.invoke({"question": question})
    return result
