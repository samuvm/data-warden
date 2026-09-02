"""Anillo 4 · el enmascarado, **reescribiendo el AST antes de ejecutar**.

`docs/PLAN.md` lo exige con esas palabras, y la alternativa —post-procesar el
DataFrame por nombre de columna— falla de tres maneras distintas: en cuanto hay un
alias, en cuanto una vista renombra, y en cuanto dos tablas tienen una columna con el
mismo nombre. Pero el motivo de fondo es otro y es más grave: **post-procesar deja el
valor real viajar desde el motor hasta el proceso**, donde ya se le ha escapado a
quien mira los logs del motor. Reescribiendo el árbol, el valor real no sale nunca de
la base de datos.

**Dónde encaja, y dónde NO.** Corre DESPUÉS de `validate()` y ANTES de ejecutar. No
relaja ni un rechazo del guard: R008 sigue rechazando toda columna `deny` en cualquier
posición, y toda `mask` fuera de la proyección directa —`where`, `join_on`,
`group_by`, `order_by`, `having`, `qualify`, argumento de función, partición de
ventana—. Lo único que pasa de rechazo a reescritura es exactamente un caso: **nivel
`mask` en proyección directa.** Esa asimetría es la tesis del anillo: una columna
enmascarada es legal en la salida y prohibida en el predicado, porque en la salida se
ve un sustituto y en el predicado se ADIVINA el original.

**El problema es más pequeño de lo que parece, y lo es gracias al guard.** Toda
proyección que sobrevive llevando una columna enmascarada es un `exp.Column` desnudo,
con o sin alias: `concat(...)`, `upper(substring(...))` y `CASE WHEN ... THEN
first_name` son argumento de función y R008 los rechaza, y `a || b` lo rechaza R002
porque `DPipe` no está en la allowlist. Así que reescribir es «sustituye la expresión
de la proyección y conserva el alias», y encontrarse cualquier otra forma **es un
fallo del anillo anterior**: se rechaza, no se improvisa.

**LÍMITE DECLARADO: la pimienta viaja dentro del SQL.** `hash_estable` se calcula en
el motor, así que la pimienta aparece como literal en el árbol que se ejecuta, y por
tanto en los logs del motor y en el campo `sql` del registro de auditoría. Es la
consecuencia directa de que el enmascarado sea una reescritura de AST y no un
post-proceso; no hay forma de hashear en el motor sin darle la clave. Afecta a dos
columnas (`dim_device.device_fingerprint` y `fact_payment_attempt.ip_address_int`) y
está abierto como **P-008** en `docs/PARA-SAMUEL.md`. Se declara aquí en vez de
descubrirse leyendo un registro de auditoría.
"""

from __future__ import annotations

import dataclasses
from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import (
    Position,
    RejectionReason,
    Severity,
    ValidatedQuery,
)
from datawarden.guard.allowlist import ALLOWED_NODES
from datawarden.guard.context import lineage_index
from datawarden.guard.query_lineage import UNKNOWN, resolve
from datawarden.mask.config import MaskConfig
from datawarden.principal.policy import AccessPolicy, Level

#: Los nodos que puede contener un árbol ENMASCARADO: los del guard más el hash.
#:
#: `SHA2` no se añade a `ALLOWED_NODES` y es deliberado. El usuario no debe poder
#: escribir `sha256()` a mano —es una función de motor y R003 la para—, pero el
#: enmascarador corre después de validar y necesita producirla. Declarar la
#: diferencia en un conjunto propio mantiene acotada la superficie del anillo 3 y
#: deja comprobable que el anillo 4 no la agranda por su cuenta.
MASK_NODES: Final[frozenset[str]] = frozenset(ALLOWED_NODES) | {"SHA2"}

#: Lo que sustituye a un valor tachado. Sin conservar longitud: la longitud de un
#: apellido o de una dirección también informa.
REDACTED: Final = "***"

#: Caracteres del hash que se publican. Doce hex son 48 bits: suficientes para que
#: dos valores distintos casi nunca colisionen —medido sobre las 104.164 IPs
#: distintas del perfil dev: cero colisiones— y pocos para que el hash no sea una
#: copia del valor.
HASH_HEX: Final = 12


def mask_query(
    query: ValidatedQuery, *, policy: AccessPolicy, config: MaskConfig
) -> ValidatedQuery | RejectionReason:
    """Reescribe el árbol validado para que las columnas `mask` salgan enmascaradas.

    Devuelve una `ValidatedQuery` nueva —con `masked_columns` relleno, que es la
    evidencia que va al registro de auditoría— o un rechazo si se encuentra una forma
    que el guard no debería haber dejado pasar.
    """
    role = query.principal.role
    tree = query.ast.copy()
    select = tree.find(exp.Select)
    if select is None:
        return query

    lineage = resolve(tree, lineage_index_for(query))
    masked: set[str] = set()

    for projection in list(select.expressions):
        target = projection.this if isinstance(projection, exp.Alias) else projection
        bases = _masked_bases(target, lineage, policy, role)
        if not bases:
            continue
        if not isinstance(target, exp.Column):
            return _not_a_bare_column(target)
        base = bases[0]
        column_policy = policy.column(base)
        replacement = _mask_expression(target, column_policy, config)
        if replacement is None:
            return _unknown_transformation(base, column_policy.transformation)
        if isinstance(projection, exp.Alias):
            projection.set("this", replacement)
        else:
            select.set(
                "expressions",
                [
                    exp.alias_(replacement, target.name) if p is projection else p
                    for p in select.expressions
                ],
            )
        masked.update(bases)

    if not masked:
        return query
    return dataclasses.replace(query, ast=tree, masked_columns=tuple(sorted(masked)))


def lineage_index_for(query: ValidatedQuery) -> dict[str, tuple[str, ...]]:
    """El índice de linaje del catálogo, para volver a resolver sobre el árbol nuevo.

    Se recalcula en vez de arrastrarse desde el guard porque `mask_query` trabaja
    sobre una COPIA del árbol, y el índice del guard está referenciado por `id()` de
    los nodos del original: reutilizarlo daría respuestas de otro árbol, que es la
    peor clase de acierto.
    """
    from datawarden.catalog import SCHEMA_PATH, load_generated

    return lineage_index(load_generated(SCHEMA_PATH))


def _masked_bases(
    target: exp.Expression,
    lineage: dict[int, tuple[str, ...]],
    policy: AccessPolicy,
    role: object,
) -> list[str]:
    """Las columnas base de esta proyección que están enmascaradas para el rol."""
    found: list[str] = []
    for column in target.find_all(exp.Column):
        for base in lineage.get(id(column), ()):
            if base == UNKNOWN:
                continue
            if policy.level_for(base, role) is Level.MASK and base not in found:  # type: ignore[arg-type]
                found.append(base)
    return found


def _mask_expression(
    column: exp.Column, column_policy: object, config: MaskConfig
) -> exp.Expression | None:
    """La máscara de una columna, **preservando NULL**.

    La forma canónica es `CASE WHEN <col> IS NULL THEN NULL ELSE <máscara> END`, y no
    es un detalle: `policy.yaml` dice que el NULL de `last_name_2` significa «este
    sistema de nombres no tiene segundo apellido» —o sea, es un DATO— y
    `resultset-equality.md` decide que NULL y cadena vacía son DISTINTOS y tienen
    motivo propio en el informe. Una máscara que convirtiera NULL en `'***'`
    INVENTARÍA un valor donde no lo había, y las respuestas de referencia del banco
    de 60 saldrían falsas sin que nadie lo notara.
    """
    body = _mask_body(column, column_policy, config)
    if body is None:
        return None
    # La envoltura es la misma para las cuatro, incluida `generalizar`, donde el
    # sustituto es OTRA columna: el NULL que se preserva sigue siendo el de la
    # ORIGINAL, porque saber que no había fecha de nacimiento es información y la
    # franja de edad de una fila sin fecha sería una invención.
    return exp.Case(
        ifs=[exp.If(this=exp.Is(this=column.copy(), expression=exp.Null()), true=exp.Null())],
        default=body,
    )


def _mask_body(
    column: exp.Column, column_policy: object, config: MaskConfig
) -> exp.Expression | None:
    """El sustituto, sin la envoltura que preserva NULL."""
    transformation = getattr(column_policy, "transformation", None)

    if transformation == "tachar":
        return exp.Literal.string(REDACTED)

    if transformation == "generalizar":
        generalized = getattr(column_policy, "generalized", None)
        if not generalized:
            return None
        # Se conserva el calificador de tabla del nodo original: la consulta puede
        # haber puesto un alias a la tabla, y escribir el nombre del contrato a pelo
        # produciría una referencia que no resuelve.
        return exp.column(str(generalized).split(".")[-1], table=column.table or None)

    if transformation == "ultimos_n":
        keep = getattr(column_policy, "keep_last_n", None)
        if not keep:
            return None
        # Nodos por CLASE CONCRETA y no por `exp.func("CONCAT", ...)`: en sqlglot 30
        # `func()` devuelve un `Func` generico que ni siquiera es un `Expression`, y
        # sobre todo la allowlist del guard casa por NOMBRE DE CLASE. Construir el
        # nodo que la allowlist espera es lo que hace comprobable `MASK_NODES`.
        return exp.Concat(
            expressions=[
                exp.Literal.string(REDACTED),
                exp.Right(this=_as_text(column), expression=exp.Literal.number(int(keep))),
            ]
        )

    if transformation == "hash_estable":
        # La pimienta entra como literal porque el hash se calcula en el MOTOR. Ver
        # el límite declarado en el docstring del módulo y P-008.
        salted = exp.Concat(expressions=[_as_text(column), exp.Literal.string(config.pepper)])
        return exp.Substring(
            this=exp.SHA2(this=salted, length=exp.Literal.number(256)),
            start=exp.Literal.number(1),
            length=exp.Literal.number(HASH_HEX),
        )

    return None


def _as_text(column: exp.Column) -> exp.Expression:
    """`CAST(col AS TEXT)`. Las columnas enmascarables no son todas de texto:
    `ip_address_int` es un entero y `birth_date` una fecha."""
    return exp.cast(column.copy(), "TEXT")


def _not_a_bare_column(target: exp.Expression) -> RejectionReason:
    return RejectionReason(
        rule_id="INTERNAL",
        code="mask_shape_unexpected",
        message=(
            f"the projection carrying a masked column is a "
            f"{target.__class__.__name__} and not a bare column reference, which the "
            "guard should have rejected before this point"
        ),
        suggestion=(
            "project the masked column on its own instead of inside an expression. "
            "If this rejection appears for a query the guard accepted, it is a guard "
            "defect and not a query defect: report it"
        ),
        severity=Severity.INTERNAL,
        position=Position.PROJECTION,
        subject=target.__class__.__name__,
        retryable=False,
    )


def _unknown_transformation(base: str, transformation: object) -> RejectionReason:
    return RejectionReason(
        rule_id="INTERNAL",
        code="mask_transformation_missing",
        message=(
            f"column {base} is masked for this role but its policy row declares "
            f"transformation {transformation!r}, which this masker cannot build"
        ),
        suggestion=(
            "declare one of the four transformations of docs/spec/policy.yaml for "
            "that row: generalizar, tachar, ultimos_n or hash_estable. A masked "
            "column with no way to mask it is refused, never shown"
        ),
        severity=Severity.INTERNAL,
        position=Position.PROJECTION,
        subject=base,
        retryable=False,
    )
