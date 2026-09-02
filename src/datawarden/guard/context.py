"""Construir el `GuardContext`: parsear y CUALIFICAR. Aquí no se decide nada.

`qualify()` con el esquema del catálogo es la decisión que ahorra más código de todo
el proyecto (`docs/RULES.md §7`, error 1): con el árbol cualificado, **los alias, las
CTE anidadas y `SELECT *` ya están resueltos, así que no hay que «buscar» el alias
porque ya no existe**. Once de las catorce reglas se apoyan en eso.

Este módulo NO rechaza. Devuelve o un contexto o un problema descrito, y quien
decide qué hacer con el problema es el validador, que es el único sitio donde puede
haber un `except Exception` (I-04).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify

from datawarden.catalog.types import CatalogSchema
from datawarden.domain.types import Principal
from datawarden.guard.rule import GuardContext
from datawarden.principal.policy import AccessPolicy

#: Presupuesto del guard por consulta. Es a la vez el máximo absoluto de
#: `G-GUARD-P95` y el timeout de fail-closed: pasado esto, la consulta se RECHAZA.
#: Un guard que tarda no es lento, es un guard caído.
DEFAULT_TIMEOUT_MS: float = 250.0


@dataclass(frozen=True, slots=True)
class ParseProblem:
    """Lo que impidió construir el contexto, con el vocabulario del guard."""

    stage: str
    message: str
    subject: str | None = None


def parse(sql: str, dialect: str) -> exp.Expression | ParseProblem:
    """Parsea UNA sentencia. Varias sentencias es un problema, no una consulta.

    `parse_one` aceptaría la primera y descartaría el resto en silencio, que es
    exactamente cómo `SELECT 1; DROP TABLE x` pasaría por un validador escrito con
    prisa: la validación miraría el `SELECT` y el motor ejecutaría las dos.
    """
    try:
        statements = sqlglot.parse(sql, dialect=dialect)
    except ParseError as exc:
        return ParseProblem(stage="parse", message=_first_line(str(exc)))
    except SqlglotError as exc:  # tokenizer, recursión, dialecto
        return ParseProblem(stage="parse", message=_first_line(str(exc)))

    real = [s for s in statements if s is not None]
    if not real:
        return ParseProblem(stage="parse", message="the input contains no statement")
    if len(real) > 1:
        return ParseProblem(
            stage="parse",
            message=f"the input contains {len(real)} statements and only one is allowed",
        )
    # `cast` y no una anotación: sqlglot tipa `parse()` con un self-type (`Expr`),
    # y el verificador estricto no lo unifica con `Expression`. La alternativa sería
    # ensanchar el tipo de retorno de esta función, que es peor: el resto del guard
    # razona sobre `Expression` y no tiene por qué saber de los genéricos de sqlglot.
    return cast("exp.Expression", real[0])


def qualify_tree(
    tree: exp.Expression, schema: CatalogSchema, dialect: str
) -> exp.Expression | ParseProblem:
    """Cualifica contra el catálogo GENERADO. Un fallo aquí es un rechazo.

    `validate_qualify_columns=True` es lo que hace que una columna que no existe se
    detecte AQUÍ y no más adelante: sin ello, `SELECT no_existe FROM t` pasaría el
    guard entero y fallaría en el motor, donde ya no hay mensaje accionable que dar.
    """
    try:
        qualified: exp.Expression = qualify(
            tree.copy(),
            schema=schema.sqlglot_schema(),
            dialect=dialect,
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
        return qualified
    except OptimizeError as exc:
        return ParseProblem(stage="qualify", message=_first_line(str(exc)))
    except SqlglotError as exc:
        return ParseProblem(stage="qualify", message=_first_line(str(exc)))


def build_context(
    *,
    raw_sql: str,
    tree: exp.Expression,
    schema: CatalogSchema,
    policy: AccessPolicy,
    principal: Principal,
    dialect: str,
    max_rows: int,
    timeout_ms: float = DEFAULT_TIMEOUT_MS,
) -> GuardContext:
    """Empaqueta el contexto y arranca el reloj."""
    return GuardContext(
        raw_sql=raw_sql,
        tree=tree,
        schema=schema,
        policy=policy,
        principal=principal,
        dialect=dialect,
        max_rows=max_rows,
        deadline=time.monotonic() + timeout_ms / 1000.0,
        lineage=lineage_index(schema),
    )


def lineage_index(schema: CatalogSchema) -> dict[str, tuple[str, ...]]:
    """`tabla.columna` -> columnas base, aplanado una vez por catálogo.

    Se construye aquí y no dentro de cada regla porque recorrer 428 columnas por
    consulta no cabe en los 25 ms de `G-GUARD-P95`. En un servidor de verdad esto se
    calcula al arrancar; el índice es inmutable mientras el catálogo lo sea.
    """
    return {
        f"{table.name.lower()}.{column.name.lower()}": column.derives_from
        for table in schema.tables
        for column in table.columns
    }


def _first_line(message: str) -> str:
    """El mensaje de sqlglot, acotado. Un rechazo no es un volcado de pila.

    Y hay un motivo de seguridad además del estético: los errores de sqlglot citan
    el fragmento de entrada, y la entrada puede llevar un literal con datos. El
    contrato de rechazo prohíbe revelar valores (I-09), así que se recorta.
    """
    first = message.strip().splitlines()[0] if message.strip() else "unparseable input"
    return first[:200]
