"""El ciclo de corrección: generar → validar → mensaje accionable → reintentar.

**Un bucle propio de sesenta líneas, y no un framework.** `docs/PLAN.md` lo pide así
y el motivo es que este bucle es el sitio donde se demuestra la tesis del proyecto:
*el valor no está en la tasa de acierto, está en la garantía sobre el fallo.* Un
framework que hiciera esto por dentro dejaría el número de `G-RECOVERY` a merced de su
política de reintentos, que es exactamente lo que aquí se está midiendo.

**Qué mide `G-RECOVERY`, dicho con precisión.** No si el modelo acierta a la primera:
si **se corrige con el mensaje de rechazo**. Un guard que rechaza sin explicar produce
un modelo que reintenta al azar; uno que explica produce uno que corrige. La
diferencia entre las dos cosas es este bucle y el `suggestion` que lo alimenta.

**El bucle es CÓDIGO y se prueba; el generador se MIDE.** Cuántas veces se reintenta,
qué se le pasa al intento siguiente y cuándo se para son decisiones deterministas y
tienen sus tests con un provider grabado. Lo que el modelo escribe no se testea: un
test unitario sobre la salida de un modelo mide el modelo, no el código.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from datawarden.domain.types import (
    Position,
    RejectionReason,
    Severity,
    ValidatedQuery,
)
from datawarden.nl2sql.providers import Provider, Request

#: Dos reintentos, tres intentos en total. No es una comodidad: cada reintento cuesta
#: una llamada al modelo y un guard entero, y un bucle sin tope convierte un rechazo
#: persistente en una factura y en una latencia sin fin. Si el mensaje no ha servido
#: en dos intentos, el problema es el mensaje y eso es lo que hay que arreglar.
MAX_RETRIES: Final = 2

Validate = Callable[[str], ValidatedQuery | RejectionReason]


@dataclass(frozen=True, slots=True)
class Attempt:
    """Un intento, con lo que se generó y lo que el guard dijo."""

    sql: str
    rejection: RejectionReason | None


@dataclass(frozen=True, slots=True)
class LoopResult:
    """El resultado del ciclo, con TODA la historia.

    La historia completa y no solo el último intento: guardar solo el final haría
    imposible responder «¿de qué se corrigió?», que es la única pregunta interesante
    sobre una recuperación y la que agrupa el informe de `G-RECOVERY`.
    """

    query: ValidatedQuery | None
    rejection: RejectionReason | None
    attempts: tuple[Attempt, ...]
    provider: str

    @property
    def accepted(self) -> bool:
        return self.query is not None

    @property
    def recovered(self) -> bool:
        """Aceptada DESPUÉS de al menos un rechazo. Es lo que cuenta `G-RECOVERY`.

        Acertar a la primera es bueno y no es una recuperación: no hubo mensaje que
        seguir. Contarlo como tal inflaría la métrica con los casos fáciles.
        """
        return self.accepted and len(self.attempts) > 1

    @property
    def first_rejection(self) -> RejectionReason | None:
        """El PRIMER rechazo, que es el que agrupa la métrica.

        Si se agrupara por el último, una consulta que se corrige de R008 y cae en
        R006 contaría como fallo de R006 — y el mensaje que se estaba evaluando era
        el de R008.
        """
        for attempt in self.attempts:
            if attempt.rejection is not None:
                return attempt.rejection
        return None


def run_loop(
    question: str,
    *,
    provider: Provider,
    validate: Validate,
    max_retries: int = MAX_RETRIES,
    prompt_id: str = "nl2sql",
    prompt_version: str = "1",
) -> LoopResult:
    """Genera, valida y reintenta con el mensaje. Nunca lanza, nunca ejecuta."""
    name = getattr(provider, "name", "unknown")
    if not question.strip():
        return LoopResult(
            query=None,
            rejection=_internal(
                "the question is empty, so there is nothing to translate into SQL",
                "ask something about the data. The catalog resource lists what there is",
            ),
            attempts=(),
            provider=name,
        )

    attempts: list[Attempt] = []
    rejection: RejectionReason | None = None

    for attempt_number in range(1, max_retries + 2):
        request = Request(
            question=question,
            attempt=attempt_number,
            rejection=rejection,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
        )
        try:
            sql = provider.generate(request)
        except Exception as exc:
            # FAIL-CLOSED, el mismo principio que gobierna el guard: lo que no se
            # puede completar se convierte en un veredicto, nunca en un fallo del
            # proceso. Un modelo caído no puede tumbar al llamante.
            rejection = _internal(
                f"the model did not answer: {type(exc).__name__}",
                "retry later, or run with the recorded provider, which needs no model",
            )
            attempts.append(Attempt(sql="", rejection=rejection))
            break

        if not sql.strip():
            rejection = _internal(
                "the model returned nothing",
                "rephrase the question. An empty answer is not a query",
            )
            attempts.append(Attempt(sql=sql, rejection=rejection))
            break

        verdict = validate(sql)
        if isinstance(verdict, ValidatedQuery):
            attempts.append(Attempt(sql=sql, rejection=None))
            return LoopResult(
                query=verdict, rejection=None, attempts=tuple(attempts), provider=name
            )

        rejection = verdict
        attempts.append(Attempt(sql=sql, rejection=verdict))
        if not verdict.retryable:
            # NO SE REINTENTA lo que no se puede reformular. Un `DELETE` no se
            # convierte en pregunta reescribiéndolo, y darle la oportunidad de
            # fallar contaminaría `G-RECOVERY` con casos que nadie puede arreglar.
            break

    return LoopResult(query=None, rejection=rejection, attempts=tuple(attempts), provider=name)


def _internal(message: str, suggestion: str) -> RejectionReason:
    """Un rechazo que no viene de una regla: viene de que el ciclo no pudo seguir."""
    return RejectionReason(
        rule_id="INTERNAL",
        code="generation_failed",
        message=message,
        suggestion=suggestion,
        severity=Severity.INTERNAL,
        position=Position.STATEMENT,
        subject="the model",
        retryable=False,
    )
