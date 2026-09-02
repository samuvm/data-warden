"""De qué columna base sale cada columna de cada vista. Se resuelve UNA vez.

**El agujero que este módulo cierra.** La política firmada protege
`dim_customer.birth_date`. El almacén tiene además una vista `v_customer` que
reexpone exactamente esa columna con otro nombre de tabla, y una columna
`full_name` que es `concat_ws(' ', first_name, last_name_1, last_name_2)`: es
literalmente el ataque que `PROJECT.md` describe —«expresiones derivadas
(`CONCAT(nombre, apellido)`) sobre columnas marcadas»— servido por el propio
catálogo. Una política que casa por `tabla.columna` no ve nada de eso.

**Por qué se resuelve en tiempo de catálogo y no en tiempo de consulta.** Porque
el linaje de una vista no cambia entre consultas: cambia cuando cambia la vista, y
eso ya obliga a regenerar el catálogo (`G-CATALOG-FRESH`). Hacerlo por consulta
sería parsear 24 definiciones de vista dentro de un presupuesto de 25 ms.

**Fail-closed.** Si el linaje de una columna no se puede resolver —una vista que
sqlglot no cualifica, una función que no sabe seguir— se marca como DESCONOCIDO, y
una columna de linaje desconocido se trata como la más restrictiva que exista, no
como una columna cualquiera. Un linaje que falla en silencio es una fuga.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import expressions as exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope

#: Marca de linaje irresoluble. No es una cadena decorativa: el guard la trata
#: como «puede venir de cualquier cosa» y aplica el nivel más restrictivo.
UNKNOWN = "?"

#: Tope de anidamiento al bajar por subconsultas y CTE. Un guardián que se cuelga
#: es un guardián caído.
_MAX_DEPTH = 12


def dependencies(sql: str, relations: frozenset[str], dialect: str) -> frozenset[str]:
    """Qué relaciones del catálogo usa esta definición de vista.

    Solo mira nombres de tabla del árbol: una función de lectura de ficheros
    (`read_parquet`) no es una relación del catálogo y por eso no aparece, que es
    justo lo que distingue una tabla base de una vista derivada sin depender de
    ninguna convención de nombres.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except sqlglot.ParseError:
        return frozenset()
    # La propia vista aparece como tabla en su `CREATE VIEW`. Excluirla aquí y no
    # en quien llama evita que una vista se declare dependiente de sí misma y
    # acabe al final del orden topológico con linaje desconocido sin motivo.
    created = {
        node.this.name.lower()
        for node in tree.find_all(exp.Create)
        if isinstance(node.this, exp.Table)
    }
    created |= {
        node.this.this.name.lower()
        for node in tree.find_all(exp.Create)
        if isinstance(node.this, exp.Schema) and isinstance(node.this.this, exp.Table)
    }
    return frozenset(
        t.name.lower()
        for t in tree.find_all(exp.Table)
        if t.name.lower() in relations and t.name.lower() not in created
    )


def topological_order(graph: Mapping[str, frozenset[str]]) -> list[str]:
    """Relaciones ordenadas de base a derivada.

    Un ciclo —que en un catálogo sano no existe— no revienta: las relaciones que no
    se pueden ordenar salen al final, y su linaje quedará DESCONOCIDO, que es la
    respuesta segura.
    """
    ordered: list[str] = []
    placed: set[str] = set()
    pending = dict(graph)
    while pending:
        ready = sorted(name for name, deps in pending.items() if all(d in placed for d in deps))
        if not ready:
            ordered.extend(sorted(pending))
            break
        for name in ready:
            ordered.append(name)
            placed.add(name)
            del pending[name]
    return ordered


def resolve(
    columns_by_relation: Mapping[str, tuple[str, ...]],
    view_sql: Mapping[str, str],
    *,
    dialect: str,
) -> dict[str, tuple[str, ...]]:
    """`relacion.columna` -> las `tabla_base.columna` de las que sale.

    Una columna de una tabla base sale de sí misma. Una columna de una vista sale
    de la unión del linaje de todas las columnas que su expresión referencia: por
    eso `v_customer.full_name` sale de `first_name`, `last_name_1` y `last_name_2`
    a la vez, y basta con que UNA de las tres esté restringida para que la columna
    derivada lo esté.
    """
    relations = frozenset(columns_by_relation)
    graph = {
        name: (
            dependencies(view_sql[name], relations - {name}, dialect)
            if name in view_sql
            else frozenset()
        )
        for name in relations
    }
    schema: dict[str, object] = {
        name: dict.fromkeys(cols, "UNKNOWN") for name, cols in columns_by_relation.items()
    }

    lineage: dict[str, tuple[str, ...]] = {}
    for relation in topological_order(graph):
        if not graph[relation] or relation not in view_sql:
            # Tabla base: cada columna sale de sí misma.
            for column in columns_by_relation[relation]:
                lineage[f"{relation}.{column}"] = (f"{relation}.{column}",)
            continue
        resolved = _resolve_view(relation, view_sql[relation], schema, lineage, dialect)
        fallback = _closure_columns(relation, graph, columns_by_relation)
        for column in columns_by_relation[relation]:
            sources = resolved.get(column.lower(), (UNKNOWN,))
            if UNKNOWN in sources:
                # FAIL-CLOSED, PERO CON PUNTERÍA. Una columna cuyo linaje sqlglot no
                # sabe seguir —el caso real es una CTE RECURSIVA— podría venir de
                # cualquier columna de las relaciones que la vista usa, así que se le
                # atribuyen TODAS. Es más restrictivo que la verdad y menos que un
                # `deny` a ciegas: la vista sigue siendo consultable por quien tenga
                # acceso a todo lo que hay debajo, y nadie más.
                sources = tuple(sorted(set(sources) - {UNKNOWN} | set(fallback)))
            lineage[f"{relation}.{column}"] = sources
    return lineage


def unresolved(
    columns_by_relation: Mapping[str, tuple[str, ...]],
    view_sql: Mapping[str, str],
    *,
    dialect: str,
) -> tuple[str, ...]:
    """Las columnas cuyo linaje NO se pudo seguir, para poder publicarlas.

    Se publican en `JOURNAL.md` y en el modelo de amenaza: un límite contado vale
    más que un límite escondido, y este se puede contar exactamente.
    """
    relations = frozenset(columns_by_relation)
    graph = {
        name: (
            dependencies(view_sql[name], relations - {name}, dialect)
            if name in view_sql
            else frozenset()
        )
        for name in relations
    }
    schema: dict[str, object] = {
        name: dict.fromkeys(cols, "UNKNOWN") for name, cols in columns_by_relation.items()
    }
    lineage: dict[str, tuple[str, ...]] = {}
    out: list[str] = []
    for relation in topological_order(graph):
        if not graph[relation] or relation not in view_sql:
            for column in columns_by_relation[relation]:
                lineage[f"{relation}.{column}"] = (f"{relation}.{column}",)
            continue
        resolved = _resolve_view(relation, view_sql[relation], schema, lineage, dialect)
        for column in columns_by_relation[relation]:
            sources = resolved.get(column.lower(), (UNKNOWN,))
            if UNKNOWN in sources:
                out.append(f"{relation}.{column}")
            lineage[f"{relation}.{column}"] = sources
    return tuple(out)


def _closure_columns(
    relation: str,
    graph: Mapping[str, frozenset[str]],
    columns_by_relation: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Todas las columnas de las relaciones de las que esta depende, transitivamente."""
    seen: set[str] = set()
    stack = list(graph.get(relation, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return tuple(
        sorted(f"{rel}.{col}" for rel in seen for col in columns_by_relation.get(rel, ()))
    )


def _resolve_view(
    relation: str,
    sql: str,
    schema: Mapping[str, object],
    lineage: Mapping[str, tuple[str, ...]],
    dialect: str,
) -> dict[str, tuple[str, ...]]:
    """El linaje de las columnas de UNA vista. Devuelve `{}` si no se puede.

    Se apoya en el `Scope` de sqlglot y no en un mapa de alias propio, porque una
    vista real no es «tabla con alias»: `v_attempt_dedup` proyecta desde una
    SUBCONSULTA con `row_number()`, y ahí el alias no apunta a ninguna tabla. Con
    el árbol de scopes, cada fuente es o una tabla o un scope hijo, y el linaje se
    resuelve bajando hasta llegar a tablas.
    """
    try:
        tree = qualify(
            sqlglot.parse_one(sql, dialect=dialect),
            schema=dict(schema),
            dialect=dialect,
        )
        root = build_scope(tree)
    except Exception:
        return {}
    if root is None:
        return {}

    select = root.expression
    if not isinstance(select, exp.Select):
        return {}

    out: dict[str, tuple[str, ...]] = {}
    for projection in select.expressions:
        name = projection.alias_or_name.lower()
        if not name or name == "*":
            continue
        sources = _sources_of(projection, root, lineage, depth=0)
        out[name] = tuple(sorted(sources)) if sources else (f"{relation}.{name}",)
    return out


def _sources_of(
    node: exp.Expression,
    scope: Scope,
    lineage: Mapping[str, tuple[str, ...]],
    *,
    depth: int,
) -> set[str]:
    """Las columnas base de las que depende una expresión, bajando por los scopes.

    `depth` es un tope duro y no una precaución teórica: una vista mal formada o un
    ciclo entre CTEs haría esto infinito, y un guardián que se cuelga es un
    guardián caído. Al agotarse, DESCONOCIDO, que es la respuesta segura.
    """
    if depth > _MAX_DEPTH:
        return {UNKNOWN}
    sources: set[str] = set()
    for column in node.find_all(exp.Column):
        sources |= _column_sources(column, scope, lineage, depth=depth)
    return sources


def _column_sources(
    column: exp.Column,
    scope: Scope,
    lineage: Mapping[str, tuple[str, ...]],
    *,
    depth: int,
) -> set[str]:
    source = scope.sources.get(column.table) or scope.sources.get(column.table.lower())
    if source is None:
        return {UNKNOWN}
    if isinstance(source, exp.Table):
        key = f"{source.name.lower()}.{column.name.lower()}"
        return set(lineage.get(key, (UNKNOWN,)))
    inner = source
    if not isinstance(inner.expression, exp.Select):
        return {UNKNOWN}
    for projection in inner.expression.expressions:
        if projection.alias_or_name.lower() == column.name.lower():
            return _sources_of(projection, inner, lineage, depth=depth + 1)
    return {UNKNOWN}
