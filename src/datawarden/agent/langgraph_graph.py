"""LangGraph como MÁQUINA DE ESTADOS, y nada más. Adaptador delgado.

**Qué hace este módulo y qué no.** No decide nada. Envuelve `run_loop` —que ya es
un bucle propio, puro y de sesenta líneas— en un grafo de tres nodos para que quien
quiera trazas, checkpoints o un `interrupt` humano los tenga. Las funciones que
hacen el trabajo son EXACTAMENTE las mismas que usa la evaluación; si un día se
borra este fichero, `G-RECOVERY` sigue midiendo el mismo número.

**Por qué el bucle no está dentro del framework.** `docs/PLAN.md` pide un bucle
propio y el motivo es medible: este bucle es donde se demuestra la tesis del
proyecto, y un framework que hiciera los reintentos por dentro dejaría el número de
`G-RECOVERY` a merced de su política de reintentos, que es justo lo que se está
midiendo. El grafo es el envoltorio; el bucle es la medida.

**Regla dura de `docs/STACK.md`:** solo máquina de estados. El retrieval es SQL
propio, el acceso al modelo es el `Provider`, y `langchain-*` NO aparece en
`pyproject.toml`. El riesgo nunca fue LangGraph: es arrastrar LangChain entero y
romper la regla de que ningún módulo de dominio importa el SDK de un proveedor.

**Y no ejecuta.** El grafo termina con un `ValidatedQuery` o con un rechazo. Al
motor solo se llega por `AuditedExecutor` (I-06), y el contrato de imports lo
prohíbe desde aquí: no es disciplina, es `lint-imports`.

**Este módulo se MIDE, no se testea** (`CLAUDE.md`): es un adaptador sobre
funciones puras que ya están probadas, y un test unitario sobre el grafo probaría
LangGraph. Lo que sí se comprueba es que el grafo y el bucle dan lo mismo, y eso es
un test de CONTRATO.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from datawarden.domain.types import RejectionReason, ValidatedQuery
from datawarden.nl2sql.loop import MAX_RETRIES, Attempt, run_loop
from datawarden.nl2sql.providers import Provider

Validate = Callable[[str], ValidatedQuery | RejectionReason]


def _keep_last(_current: Any, incoming: Any) -> Any:
    """Reductor explícito: el nodo que escribe gana. Sin sorpresas de fusión."""
    return incoming


class GraphState(TypedDict, total=False):
    """El estado del grafo. **Es un DATO, nunca una autoridad.**

    El `principal` NO viaja aquí. El rol no viene nunca de datos no autenticados
    (I-09) y el estado de un grafo es exactamente eso: algo que un nodo anterior
    escribió. Quien decide con qué rol se valida es el llamante, que lo cierra
    dentro de `validate` antes de construir el grafo.
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
    """Compila el grafo. Un solo nodo que delega en `run_loop`, y es a propósito.

    Podría partirse en generar / validar / decidir, y sería más bonito de dibujar.
    No se hace porque partirlo movería la decisión de cuántas veces se reintenta al
    grafo, y esa decisión es la que `G-RECOVERY` mide. El grafo aporta traza y
    checkpoint; la política de reintento se queda donde está probada.
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
    """Atajo: compila el grafo y lo invoca. Devuelve el estado final."""
    compiled = build(provider=provider, validate=validate, max_retries=max_retries)
    result: GraphState = compiled.invoke({"question": question})
    return result
