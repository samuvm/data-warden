"""El `AuditedExecutor`: **el único camino a `Engine.execute()`** (I-06).

`G-AUDIT-COV` es un axioma y dice una sola cosa: *ninguna invocación sin su
registro*. No «ninguna ejecución»: ninguna INVOCACIÓN. Se auditan los cuatro
estados, y el que más importa es el que no ejecuta nada: un rechazo del guard es un
evento de seguridad, y quien sondea la política sistemáticamente para averiguar qué
columnas existen es precisamente el que no dejaría ni una línea si solo se
registraran los éxitos.

**Este módulo no reimplementa el orden de los anillos.** Envuelve `cost.screen()`,
que ya encadena validar → estimar → presupuestar sin ejecutar. Reescribir aquí ese
orden crearía un segundo sitio donde puede equivocarse, y dos caminos que hacen «lo
mismo» acaban haciendo dos cosas.

**Cómo se distinguen los dos rechazos, y por qué así.** El del guard llega con
`cost=None`; el de presupuesto llega con su coste calculado. Es una diferencia
ESTRUCTURAL, no un parseo del `rule_id` ni del texto: el día que un mensaje cambie
de redacción —y van a cambiar, `G-RECOVERY` los va a reescribir en la fase 6— esta
clasificación sigue en pie.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from datawarden.audit.store import AuditStore, Entry
from datawarden.catalog.statistics import Statistics
from datawarden.catalog.types import CatalogSchema
from datawarden.cost.screen import screen
from datawarden.domain.types import (
    Principal,
    RejectionReason,
    ResultSet,
    Status,
    ValidatedQuery,
)
from datawarden.engines.base import Engine
from datawarden.principal.budgets import BudgetBook
from datawarden.principal.policy import AccessPolicy

#: `sql_digest` cuando NO existe árbol validado, o sea en todo `rejected_by_guard`:
#: R001 rechaza cosas que ni parsean. El contrato define el campo como el sha256 del
#: SQL **re-serializado desde el AST validado** y «nunca de la cadena de entrada»,
#: así que hashear la entrada sería justo lo prohibido: la auditoría certificaría
#: algo distinto de lo que corrió. Los 64 ceros son un valor legal y distinguible,
#: el mismo vocabulario que el contrato usa para el génesis de `prev_hash`.
#:
#: **OJO: el mismo relleno que `chain.GENESIS`, y significa otra cosa.** Allí quiere
#: decir «primero de la cadena»; aquí, «no hubo árbol validado». No colisionan porque
#: son campos distintos y se leen por NOMBRE, pero un registro que sea a la vez el
#: primero de la cadena y un rechazo pre-parseo llevará los dos. Se leen por campo,
#: nunca por valor. Lo señaló Samuel al aprobar P-007, y está escrito también en la
#: `description` de los dos campos del contrato.
NO_VALIDATED_TREE = "0" * 64


@dataclass(frozen=True, slots=True)
class RunResult:
    """Lo que devuelve una invocación, con su registro de auditoría al lado."""

    rows: ResultSet | None
    rejection: RejectionReason | None
    query: ValidatedQuery | None
    entry: Entry

    @property
    def executed(self) -> bool:
        return self.rows is not None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditedExecutor:
    """Valida, presupuesta, ejecuta si procede — y registra pase lo que pase."""

    def __init__(
        self,
        *,
        engine: Engine,
        store: AuditStore,
        schema: CatalogSchema,
        policy: AccessPolicy,
        budgets: BudgetBook,
        stats: Statistics,
        dialect: str = "duckdb",
    ) -> None:
        self.engine = engine
        self.store = store
        self.schema = schema
        self.policy = policy
        self.budgets = budgets
        self.stats = stats
        self.dialect = dialect

    def run(
        self,
        sql: str,
        *,
        principal: Principal,
        question: str | None = None,
        keep_question: bool = False,
        trace_id: str | None = None,
    ) -> RunResult:
        """Una invocación completa. Deja SIEMPRE exactamente un registro.

        `question` se guarda **hasheada**: una pregunta en lenguaje natural puede
        llevar un dato personal dentro, y el digest permite agrupar y contar sin
        conservar el texto. Conservarlo es `keep_question=True`, una decisión
        explícita y nunca el valor por defecto.
        """
        question_digest = _digest(question if question is not None else sql)
        preview = question if (keep_question and question is not None) else None
        common: dict[str, Any] = {
            "principal_id": principal.id,
            "role": principal.role,
            "role_source": principal.source,
            "question_digest": question_digest,
            "question_preview": preview,
            "trace_id": trace_id,
        }

        result = screen(
            sql,
            principal=principal,
            schema=self.schema,
            policy=self.policy,
            budgets=self.budgets,
            stats=self.stats,
            dialect=self.dialect,
        )

        if result.rejection is not None:
            # ESTRUCTURAL, no textual: el guard rechaza antes de estimar, así que
            # no hay coste; el presupuesto rechaza después, así que sí lo hay.
            by_guard = result.cost is None
            entry = self.store.append(
                status=Status.REJECTED_BY_GUARD if by_guard else Status.REJECTED_BY_BUDGET,
                sql_digest=NO_VALIDATED_TREE,
                rejection=_rejection_payload(result.rejection),
                estimated_bytes=None if result.cost is None else result.cost.estimated_bytes,
                budget_bytes=self.budgets.for_role(principal.role).hard_bytes,
                **common,
            )
            return RunResult(rows=None, rejection=result.rejection, query=None, entry=entry)

        query = result.query
        assert query is not None
        started = time.perf_counter()
        try:
            rows = self.engine.execute(query)
        except Exception:
            # I-04 en espíritu: se captura, SE ESCRIBE EL REGISTRO y se RELANZA.
            # Tragarse la excepción convertiría un fallo del motor en un éxito
            # silencioso —el llamante recibiría un resultset vacío indistinguible
            # de «no hay filas»—, y no escribir el registro dejaría sin rastro
            # justo el evento que más falta hace investigar. El `raise` desnudo
            # conserva la traza original.
            self.store.append(
                status=Status.ERROR,
                sql_digest=query.sql_digest(),
                sql=query.sql(),
                tables=query.tables,
                columns_masked=query.masked_columns,
                estimated_bytes=None if result.cost is None else result.cost.estimated_bytes,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                engine=self.engine.name,
                **common,
            )
            raise

        entry = self.store.append(
            status=Status.EXECUTED,
            sql_digest=query.sql_digest(),
            sql=query.sql(),
            tables=query.tables,
            columns_masked=query.masked_columns,
            estimated_bytes=None if result.cost is None else result.cost.estimated_bytes,
            budget_bytes=self.budgets.for_role(principal.role).hard_bytes,
            row_count=len(rows.rows),
            truncated=rows.truncated,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            engine=self.engine.name,
            **common,
        )
        return RunResult(rows=rows, rejection=None, query=query, entry=entry)


def _rejection_payload(rejection: RejectionReason) -> dict[str, Any]:
    """El rechazo, en la forma que guarda el registro.

    Se guarda la ESTRUCTURA —quién rechazó, de qué habla, dónde— y no solo la
    frase. El texto va a cambiar cuando `G-RECOVERY` lo mida en la fase 6; los
    campos con los que se agrupa por causa no pueden cambiar con él.
    """
    return {
        "rule_id": rejection.rule_id,
        "code": rejection.code,
        "severity": str(rejection.severity),
        "position": str(rejection.position),
        "subject": rejection.subject,
        "retryable": rejection.retryable,
    }
