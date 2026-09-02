"""Cuánto costaría la consulta, calculado **antes de ejecutarla**.

`docs/RULES.md §7`, error 11, descarta las dos vías obvias con su motivo: `EXPLAIN
ANALYZE` **ejecuta la consulta**, así que es inútil para un guardián preventivo; y
el `EXPLAIN` de DuckDB da cardinalidad pero no bytes escaneados, mientras que Athena
solo reporta `DataScannedInBytes` **después**. El estimador se construye sobre los
metadatos de Iceberg, que llevan `column_sizes` por fichero y el valor de partición
de cada uno **sin leer una sola fila** — y por eso mismo sirve para los dos motores.

Dos podas, que son las dos razones de ser de un lakehouse columnar:

- **Por proyección.** Solo se pagan las columnas que la consulta toca. No solo las
  del `SELECT`: también las del `WHERE`, las del `JOIN` y las del `GROUP BY`, porque
  el motor tiene que leerlas igual aunque no las devuelva.
- **Por partición.** Un predicado sobre la columna de partición reduce los ficheros,
  y eso se lee del ÁRBOL VALIDADO —no del texto—, así que ni un comentario ni unas
  mayúsculas raras cambian la cuenta.

**LA REGLA QUE GOBIERNA TODO EL MÓDULO: ante la duda, se cobra de más.**
`G-COST-CALIB` mide `p95(real/estimado) <= 1,5` y exige cero casos con ratio > 3. Ese
cociente dice hacia dónde tiene que equivocarse el estimador: si subestima, el ratio
se dispara y una consulta cara se cuela; si sobreestima, alguien tiene que acotar más
su pregunta y no ha pasado nada. Por eso un predicado que el estimador no sabe leer
NO poda, y una tabla que no conoce NO vale cero.
"""

from __future__ import annotations

from typing import Any, Final

from sqlglot import expressions as exp

from datawarden.catalog.statistics import Statistics, TableStats
from datawarden.domain.types import CostEstimate, ValidatedQuery

#: Lo que se supone que ocupa una tabla que las estadísticas no conocen. No es una
#: estimación: es un CASTIGO deliberado. Cobrar cero por lo desconocido es la forma
#: exacta de dejar escapar lo caro, y `G-BUDGET-ESCAPE` es un axioma.
UNKNOWN_TABLE_BYTES: Final = 1_000_000_000


def estimate(query: ValidatedQuery, stats: Statistics) -> CostEstimate:
    """Bytes, filas y ficheros que la consulta tocaría. Nunca ejecuta nada."""
    tree = query.ast
    per_table: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    total_bytes = 0
    total_rows = 0
    total_files = 0

    aliases = _alias_map(tree)
    for name in _tables_of(tree):
        table_stats = stats.table(name)
        columns = _columns_of(tree, name, aliases)
        if table_stats is None:
            unknown.append(name)
            total_bytes += UNKNOWN_TABLE_BYTES
            per_table[name] = {
                "known": False,
                "bytes": UNKNOWN_TABLE_BYTES,
                "columns": sorted(columns),
            }
            continue

        kept, kept_bytes, kept_rows, kept_files = _surviving_partitions(tree, table_stats)
        column_bytes = table_stats.bytes_of(tuple(sorted(columns)))
        share = kept_bytes / table_stats.bytes if table_stats.bytes else 1.0
        table_bytes = round(column_bytes * share)

        total_bytes += table_bytes
        total_rows += kept_rows
        total_files += kept_files
        per_table[name] = {
            "known": True,
            "bytes": table_bytes,
            "columns": sorted(columns),
            "column_bytes": column_bytes,
            "partitions_kept": kept,
            "partitions_total": len(table_stats.partitions) or 1,
            "rows": kept_rows,
            "files": kept_files,
        }

    return CostEstimate(
        estimated_bytes=total_bytes,
        estimated_rows=total_rows,
        files_scanned=total_files,
        method="iceberg",
        detail={"per_table": per_table, "unknown_tables": sorted(unknown)},
    )


def _tables_of(tree: exp.Expression) -> list[str]:
    """Las tablas reales del árbol, sin las CTE, que no ocupan bytes en disco."""
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE) if c.alias_or_name}
    seen: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name and name not in cte_names and name not in seen:
            seen.append(name)
    return seen


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    """`alias -> tabla real`, en minúsculas.

    `qualify()` cualifica cada columna con el ALIAS —`f.auth_status`—, no con el
    nombre de la tabla. Sin este mapa, un `FROM fact_payment_attempt AS f` no
    atribuía ni una columna y se cobraba la tabla entera: la poda por proyección
    desaparecía en cuanto alguien escribía un alias, que es siempre.
    """
    out: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        real = table.name.lower()
        out[real] = real
        if table.alias:
            out[table.alias.lower()] = real
    return out


def _columns_of(tree: exp.Expression, table: str, aliases: dict[str, str]) -> set[str]:
    """Toda columna de esa tabla que aparezca en CUALQUIER posición del árbol.

    Incluye las del `WHERE`, el `JOIN` y el `GROUP BY`: el motor las lee aunque no
    las devuelva, y contar solo la proyección subestimaría justo en las consultas
    con predicado, que son las que más se escriben.

    El alias ya está resuelto porque el árbol viene de `qualify()`; sin eso, esta
    función tendría que resolver alias y sería la decimoquinta implementación de lo
    mismo.
    """
    return {
        column.name.lower()
        for column in tree.find_all(exp.Column)
        if aliases.get((column.table or "").lower()) == table
    }


def _surviving_partitions(tree: exp.Expression, stats: TableStats) -> tuple[int, int, int, int]:
    """`(particiones, bytes, filas, ficheros)` que sobreviven a la poda."""
    if stats.partition_column is None or not stats.partitions:
        return (1, stats.bytes, stats.rows, stats.files)

    keep = _partition_filter(tree, stats.partition_column, set(stats.partitions))
    if keep is None:
        return (len(stats.partitions), stats.bytes, stats.rows, stats.files)

    kept_bytes = sum(stats.partitions[v]["bytes"] for v in keep)
    kept_rows = sum(stats.partitions[v]["rows"] for v in keep)
    kept_files = sum(stats.partitions[v]["files"] for v in keep)
    return (len(keep), kept_bytes, kept_rows, kept_files)


def _partition_filter(tree: exp.Expression, column: str, values: set[str]) -> set[str] | None:
    """Los valores de partición que sobreviven, o `None` si no se puede saber.

    `None` NO significa «ninguno»: significa «el estimador no sabe acotar», y
    entonces se cobran todas. La diferencia es exactamente la que separa un
    estimador prudente de uno que deja escapar consultas caras.

    Solo se miran los predicados unidos por `AND` en el `WHERE`. Un `OR` con una
    rama que no habla de la partición no acota nada, y tratarlo como si acotara sería
    subestimar por el camino más fácil de recorrer sin querer.
    """
    where = tree.find(exp.Where)
    if where is None:
        return None

    kept: set[str] | None = None
    for predicate in _conjuncts(where.this):
        narrowed = _values_from(predicate, column, values)
        if narrowed is None:
            continue
        kept = narrowed if kept is None else kept & narrowed

    # UNA INTERSECCIÓN VACÍA NO SE TOMA POR BUENA, y lo enseñó un fallo real: las
    # claves de partición se estaban generando como `Record[19967]` en vez de como
    # fechas ISO, así que ningún literal casaba, la poda salía vacía y el estimador
    # cobraba CERO bytes por una tabla de 4,1 GB. `G-BUDGET-ESCAPE` habría dejado
    # pasar cualquier consulta con predicado de fecha.
    #
    # Un rango legítimamente vacío existe —preguntar por un día sin datos— y con
    # esto se sobreestima. Pero sobreestimar cuesta que alguien acote su pregunta, y
    # subestimar a cero cuesta el axioma entero.
    if kept is not None and not kept:
        return None
    return kept


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    """Los predicados unidos por `AND`, aplanados. Un `OR` se devuelve entero."""
    if isinstance(node, exp.And):
        return [*_conjuncts(node.this), *_conjuncts(node.expression)]
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    return [node]


def _values_from(predicate: exp.Expression, column: str, values: set[str]) -> set[str] | None:
    """Qué valores de partición deja pasar ESTE predicado, si se puede saber."""
    if isinstance(predicate, exp.EQ):
        for left, right in (
            (predicate.this, predicate.expression),
            (predicate.expression, predicate.this),
        ):
            if _is_partition_column(left, column):
                literal = _literal_text(right)
                return None if literal is None else values & {literal}
        return None

    if isinstance(predicate, exp.In) and _is_partition_column(predicate.this, column):
        wanted = {
            text for item in predicate.expressions if (text := _literal_text(item)) is not None
        }
        return values & wanted if wanted else None

    if isinstance(predicate, exp.Between) and _is_partition_column(predicate.this, column):
        low = _literal_text(predicate.args.get("low"))
        high = _literal_text(predicate.args.get("high"))
        if low is None or high is None:
            return None
        return {v for v in values if low <= v <= high}

    if isinstance(predicate, exp.GT | exp.GTE | exp.LT | exp.LTE):
        return _values_from_range(predicate, column, values)

    return None


def _values_from_range(
    predicate: exp.Expression, column: str, values: set[str]
) -> set[str] | None:
    """`>=`, `>`, `<=`, `<` sobre la columna de partición."""
    left, right = predicate.this, predicate.expression
    if _is_partition_column(left, column):
        bound, inclusive, lower = (
            _literal_text(right),
            isinstance(predicate, exp.GTE | exp.LTE),
            isinstance(predicate, exp.GT | exp.GTE),
        )
    elif _is_partition_column(right, column):
        # `DATE '2026-08-03' <= event_date` es lo mismo con los lados cambiados.
        bound, inclusive, lower = (
            _literal_text(left),
            isinstance(predicate, exp.GTE | exp.LTE),
            isinstance(predicate, exp.LT | exp.LTE),
        )
    else:
        return None
    if bound is None:
        return None
    if lower:
        return {v for v in values if (v >= bound if inclusive else v > bound)}
    return {v for v in values if (v <= bound if inclusive else v < bound)}


def _is_partition_column(node: exp.Expression | None, column: str) -> bool:
    """La columna DESNUDA. `date_trunc('month', event_date)` no lo es, y no poda."""
    return isinstance(node, exp.Column) and node.name.lower() == column.lower()


def _literal_text(node: exp.Expression | None) -> str | None:
    """El literal como texto, o `None` si no es un literal que se pueda comparar.

    Las particiones de este almacén son fechas y sus claves son cadenas ISO, así que
    comparar como texto es comparar como fecha. Se dice aquí porque el día que
    aparezca una partición numérica esta suposición dejará de valer, y es mejor que
    esté escrita a que se descubra.
    """
    if isinstance(node, exp.Cast):
        return _literal_text(node.this)
    if isinstance(node, exp.Literal):
        return str(node.this)
    if isinstance(node, exp.Paren):
        return _literal_text(node.this)
    if isinstance(node, exp.DateStrToDate | exp.TsOrDsToDate | exp.StrToDate):
        return _literal_text(node.this)
    return None
