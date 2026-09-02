"""De qué columna base sale cada columna DE ESTA CONSULTA. El agujero del alias.

**El caso que obligó a escribir este módulo**, encontrado con una prueba de humo y
no con una revisión:

    WITH c AS (SELECT birth_date AS b, customer_sk FROM dim_customer)
    SELECT customer_sk FROM c WHERE b > '1990-01-01'

`birth_date` está enmascarada para `analyst`, y una columna enmascarada está
prohibida en un `WHERE`. Pero en el `WHERE` no pone `birth_date`: pone `b`. El
linaje del CATÁLOGO no puede ayudar aquí, porque `c` no es una relación del
catálogo: **es una relación que la propia consulta acaba de inventar.** Sin este
módulo, R008 miraba `c.b`, no encontraba política, aplicaba el `allow` por defecto y
aceptaba. El canal lateral por predicado quedaba abierto con dos líneas de SQL.

La solución es la misma que en `catalog/lineage.py` y por el mismo motivo: se baja
por el árbol de ámbitos de sqlglot hasta llegar a tablas de verdad, y ahí se
engancha con el linaje del catálogo. **Un alias no cambia de qué columna sale un
dato**, ni cuando lo pone una vista ni cuando lo pone la consulta.

**Fail-closed.** Una columna cuyo origen no se puede seguir se marca `UNKNOWN`, y
R008 trata lo desconocido como lo más restrictivo. Un linaje que falla en silencio
es una fuga.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, build_scope

#: Origen irresoluble. R008 lo trata como la posición más restrictiva posible.
UNKNOWN: Final = "?"

_MAX_DEPTH: Final = 12


def resolve(
    tree: exp.Expression, catalog_lineage: Mapping[str, tuple[str, ...]]
) -> dict[int, tuple[str, ...]]:
    """`id(nodo Column)` -> columnas base del catálogo de las que sale.

    Se indexa por identidad del nodo y no por `tabla.columna` porque dos columnas
    con el mismo nombre en dos ámbitos distintos son dos cosas distintas, y
    confundirlas es exactamente el error que este módulo existe para no cometer.
    """
    try:
        root = build_scope(tree)
    except Exception:
        return {}
    if root is None:
        return {}

    out: dict[int, tuple[str, ...]] = {}
    for scope in root.traverse():
        for column in scope.columns:
            out[id(column)] = tuple(sorted(_sources(column, scope, catalog_lineage, 0)))
    _index_alias_references(root, out)
    return out


def _index_alias_references(root: Scope, out: dict[int, tuple[str, ...]]) -> None:
    """Las columnas que `Scope.columns` NO devuelve. **Era una fuga real.**

    `SELECT first_name AS n FROM dim_customer ORDER BY n LIMIT 5` se ACEPTABA para
    `analyst`, y `first_name` está enmascarada para ese rol. La cadena del fallo:

    1. `qualify()` expande el alias en `GROUP BY` y en `HAVING` —por eso esos dos
       casos sí se rechazaban— pero **NO en `ORDER BY`**: deja el nodo como una
       columna sin tabla. De paso convierte el ordinal, así que `ORDER BY 1` acaba
       exactamente en el mismo sitio.
    2. `Scope.columns` de sqlglot excluye deliberadamente las referencias a alias de
       salida, así que el bucle de arriba nunca indexa ese nodo.
    3. R008 sí lo ve —recorre `exp.Column`—, pregunta por `.n`, no encuentra fila en
       la matriz y aplica el `allow` por defecto.

    Y no era un rechazo perdido cualquiera: **`ORDER BY n LIMIT 1` con predicados
    sobre columnas permitidas es una búsqueda binaria sobre un valor enmascarado.**
    Se ordena, se mira el extremo, se acota. La política firmada lo prohíbe con esas
    palabras —una columna «alcanzada a través de un alias»— y el guard no lo estaba
    cumpliendo.

    **Se cierra la CLASE, no el caso.** Toda columna que el bucle principal no haya
    indexado se resuelve aquí o se marca `UNKNOWN`: si su nombre casa con un alias de
    salida de su `SELECT`, hereda el linaje de esa proyección —un alias no cambia de
    dónde sale un dato—; y si no casa con nada, es una columna cuyo origen el guard no
    sabe seguir, que es la definición de fail-closed. Arreglar solo el `ORDER BY`
    habría dejado la puerta abierta para la siguiente cláusula que sqlglot decida no
    expandir.
    """
    for scope in root.traverse():
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        by_alias: dict[str, tuple[str, ...]] = {}
        for projection in select.expressions:
            sources: set[str] = set()
            for inner in projection.find_all(exp.Column):
                sources |= set(out.get(id(inner), (UNKNOWN,)))
            by_alias[projection.alias_or_name.lower()] = tuple(sorted(sources))
        for column in select.find_all(exp.Column):
            if id(column) in out or column.table:
                continue
            out[id(column)] = by_alias.get(column.name.lower(), (UNKNOWN,))


def _sources(
    column: exp.Column,
    scope: Scope,
    catalog_lineage: Mapping[str, tuple[str, ...]],
    depth: int,
) -> set[str]:
    if depth > _MAX_DEPTH:
        return {UNKNOWN}
    table = (column.table or "").lower()
    source = scope.sources.get(column.table) or scope.sources.get(table)
    if source is None:
        # Una columna sin fuente conocida en su ámbito. Tras `qualify()` esto es
        # raro, y lo raro en un guardián se trata como lo peor, no como lo normal.
        return {UNKNOWN}
    if isinstance(source, exp.Table):
        key = f"{source.name.lower()}.{column.name.lower()}"
        return set(catalog_lineage.get(key, (key,)))
    inner = source
    if not isinstance(inner.expression, exp.Select):
        return {UNKNOWN}
    for projection in inner.expression.expressions:
        if projection.alias_or_name.lower() != column.name.lower():
            continue
        found: set[str] = set()
        for inner_column in projection.find_all(exp.Column):
            found |= _sources(inner_column, inner, catalog_lineage, depth + 1)
        # CONJUNTO VACÍO NO ES DESCONOCIDO, y la diferencia la encontró el corpus.
        # `count(*)` o un literal no salen de NINGUNA columna: no hay nada de lo que
        # informen, así que no hay política que aplicarles. Devolver `UNKNOWN` aquí
        # rechazaba una CTE perfectamente normal que proyecta un conteo — es decir,
        # rompía el trabajo legítimo por exceso de celo, que es la otra forma de que
        # un guardián se acabe desactivando.
        return found
    return {UNKNOWN}
