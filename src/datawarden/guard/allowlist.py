"""La allowlist. **Lo que no está aquí, se rechaza.**

Es la decisión de diseño que más veces se toma al revés en este dominio, y por eso
`docs/RULES.md §7` la nombra dos veces: una denylist de palabras peligrosas se salta
con un comentario entre tokens, con mayúsculas raras o con una codificación; y una
denylist de funciones se queda corta **el día que DuckDB añade una extensión**, sin
que nadie toque una línea de este repositorio.

Una allowlist tiene el fallo contrario, y es el que se puede vivir: se queda corta
de más. Una consulta legítima que usa una función que no está en la lista se
rechaza, alguien lo lee, y la función se añade **como una decisión, con su commit y
su caso de test**. El coste es fricción; el de la denylist es una fuga.

**Cómo se lee esta lista.** Son nombres de clase de `sqlglot.expressions`, no
palabras de SQL. La diferencia es la tesis del proyecto: `exp.Count` es el nodo que
sqlglot construyó tras parsear, así que `COUNT`, `count` y `/*x*/count` son el mismo
nodo, y no hay nada que normalizar ni ninguna codificación que esquivar.
"""

from __future__ import annotations

from typing import Final

#: Estructura de la consulta. Sin esto no hay SELECT posible.
_ESTRUCTURA: Final = frozenset(
    {
        "Select",
        "Subquery",
        "From",
        "Join",
        "Where",
        "Group",
        "Having",
        "Order",
        "Ordered",
        "Limit",
        "Offset",
        "Qualify",
        "Distinct",
        "With",
        "CTE",
        "Union",
        "Except",
        "Intersect",
        "Table",
        "TableAlias",
        "Column",
        "Alias",
        "Identifier",
        "Star",
        "Paren",
        "Tuple",
        "Values",
    }
)

#: Literales y tipos. `Cast` está dentro a propósito: sin conversión explícita, la
#: mitad de las consultas de fecha de este almacén no se pueden escribir.
_LITERALES: Final = frozenset(
    {
        "Literal",
        "Boolean",
        "Null",
        "Cast",
        "TryCast",
        "DataType",
        "DataTypeParam",
        "Interval",
        # `Var` es el nodo con el que sqlglot representa un descriptor sin comillas:
        # la unidad de `date_trunc('month', x)`, la de un `INTERVAL 1 MONTH`. Se
        # añade como DECISIÓN, con su caso (R003-A2), porque sin él no se puede
        # escribir ni una consulta con periodo — y todas las del banco lo llevan.
        # No abre nada: un `Var` suelto no ejecuta nada, y los sitios donde sí
        # significaría algo (`SET x = ...`) son nodos propios que R001 y R002 paran.
        "Var",
        "Array",
        "Struct",
        "Bracket",
    }
)

#: Operadores y predicados.
_OPERADORES: Final = frozenset(
    {
        "And",
        "Or",
        "Not",
        "EQ",
        "NEQ",
        "GT",
        "GTE",
        "LT",
        "LTE",
        "NullSafeEQ",
        "NullSafeNEQ",
        "Is",
        "In",
        "Between",
        "Like",
        "ILike",
        "Add",
        "Sub",
        "Mul",
        "Div",
        "IntDiv",
        "Mod",
        "Neg",
        "Pow",
        "BitwiseAnd",
        "BitwiseOr",
        "BitwiseXor",
        "BitwiseNot",
        "BitwiseLeftShift",
        "BitwiseRightShift",
        "Case",
        "If",
        "Coalesce",
        "Nullif",
        "Exists",
        # `Any` y `All` SALEN de la allowlist, y lo decidió la propiedad: un nombre
        # de función generado al azar cayó en `all`, sqlglot lo parseó como el
        # cuantificador `exp.All` —que estaba dentro— y el guard aceptó algo que ni
        # siquiera es una consulta válida para DuckDB. Los cuantificadores solo
        # hacen falta para `x = ANY (subconsulta)`, y para eso está `IN`, que sí
        # está. Quitar dos entradas que nadie necesita a cambio de cerrar una
        # coincidencia de nombres es exactamente cómo se afina una allowlist.
    }
)

#: Agregados. Son la razón de ser de un almacén analítico.
_AGREGADOS: Final = frozenset(
    {
        "Count",
        "Sum",
        "Avg",
        "Min",
        "Max",
        "Stddev",
        "StddevPop",
        "StddevSamp",
        "Variance",
        "VariancePop",
        "Median",
        "Quantile",
        "PercentileCont",
        "PercentileDisc",
        "ApproxDistinct",
        "ApproxQuantile",
        "Corr",
        "CovarPop",
        "CovarSamp",
        "GroupConcat",
        "ArrayAgg",
        "Filter",
    }
)

#: Ventanas. `docs/PLAN.md` exige 15 preguntas del banco con ventana o subconsulta
#: correlacionada, así que esto no es opcional.
_VENTANAS: Final = frozenset(
    {
        "Window",
        "WindowSpec",
        "RowNumber",
        "Rank",
        "DenseRank",
        "PercentRank",
        "CumeDist",
        "NthValue",
        "FirstValue",
        "LastValue",
        "Lag",
        "Lead",
        "NTile",
    }
)

#: Fechas y tiempo. Casi todas las preguntas del banco llevan un periodo.
_TEMPORALES: Final = frozenset(
    {
        "DateTrunc",
        "TimestampTrunc",
        "DateAdd",
        "DateSub",
        "DateDiff",
        "DatetimeDiff",
        "TimestampDiff",
        "DateStrToDate",
        "StrToDate",
        "StrToTime",
        "TimeToStr",
        "TsOrDsToDate",
        "TsOrDsToDateStr",
        "Extract",
        "Year",
        "Month",
        "Day",
        "Week",
        "Quarter",
        "DayOfWeek",
        "DayOfMonth",
        "DayOfYear",
        "WeekOfYear",
        "LastDay",
        "UnixToTime",
        "TimeToUnix",
    }
)

#: Texto. `Concat` está DENTRO, y es deliberado: `concat(first_name, last_name)` es
#: exactamente el ataque por expresión derivada que describe `PROJECT.md`, y no se
#: para prohibiendo la función —eso rompería la mitad de las consultas legítimas—,
#: se para en **R008**, que mira el LINAJE de lo que entra en la expresión.
_TEXTO: Final = frozenset(
    {
        "Concat",
        "ConcatWs",
        "Lower",
        "Upper",
        "Initcap",
        "Length",
        "Substring",
        "Trim",
        "Left",
        "Right",
        "Repeat",
        "Split",
        "StrPosition",
        "RegexpLike",
        "RegexpExtract",
        "RegexpReplace",
        "Lpad",
        "Rpad",
    }
)

#: Números.
_NUMEROS: Final = frozenset(
    {
        "Round",
        "Floor",
        "Ceil",
        "Abs",
        "Sign",
        "Sqrt",
        "Exp",
        "Ln",
        "Log",
        "Least",
        "Greatest",
        "Cbrt",
    }
)

#: Nodos internos que `sqlglot` construye por su cuenta al parsear o al cualificar.
#: Sin ellos, una consulta perfectamente normal se rechazaría por un detalle de la
#: representación, que es la peor forma de fricción: la que nadie entiende.
_INTERNOS: Final = frozenset(
    {
        "Anonymous",  # se rechaza en R003, no aquí: merece su propio mensaje
        "Condition",
        "Predicate",
        "Func",
        "AggFunc",
        "Binary",
        "Unary",
        "Expression",
    }
)

#: El nodo permitido. Cualquier otro tipo de nodo se rechaza (R002).
ALLOWED_NODES: Final = (
    _ESTRUCTURA
    | _LITERALES
    | _OPERADORES
    | _AGREGADOS
    | _VENTANAS
    | _TEMPORALES
    | _TEXTO
    | _NUMEROS
) - {"Anonymous"}

#: Funciones que sqlglot NO reconoce como nodo propio y deja como `Anonymous`, pero
#: que este almacén necesita y son inofensivas. **Es una lista corta a propósito**:
#: cada entrada es una decisión, no una comodidad.
#:
#: `any_value` está aquí porque el catálogo de este proyecto la usa en una de las
#: tres consultas de la fase 0 y porque no lee nada de fuera de la consulta.
ALLOWED_ANONYMOUS: Final = frozenset(
    {
        "any_value",
        "date_diff",
        "date_trunc",
        "date_part",
        "epoch",
        "list_value",
        "nullifzero",
        "regexp_matches",
        "strftime",
        "strptime",
        "time_bucket",
        "trunc",
    }
)

#: Esquemas de sistema. Consultarlos es leer el catálogo REAL por debajo del
#: catálogo publicado, y por tanto ver las columnas que C-3 excluyó a propósito.
SYSTEM_SCHEMAS: Final = frozenset(
    {
        "information_schema",
        "pg_catalog",
        "sqlite_master",
        "sqlite_temp_master",
        "duckdb_tables",
        "duckdb_views",
        "duckdb_columns",
        "duckdb_databases",
        "duckdb_schemas",
        "duckdb_settings",
        "duckdb_functions",
        "duckdb_extensions",
        "duckdb_secrets",
        "system",
        "temp",
    }
)

#: Funciones que leen de fuera de la consulta: ficheros, red o el propio proceso.
#: **Están en la allowlist por ausencia**, así que esta lista NO es lo que las para:
#: es lo que permite dar un mensaje que dice qué ha pasado en vez de «función
#: desconocida». La diferencia entre un rechazo útil y uno mudo.
KNOWN_DANGEROUS: Final = frozenset(
    {
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "read_text",
        "read_blob",
        "glob",
        "parquet_scan",
        "csv_scan",
        "iceberg_scan",
        "delta_scan",
        "postgres_scan",
        "mysql_scan",
        "sqlite_scan",
        "httpfs",
        "load_extension",
        "install_extension",
        "pg_read_file",
        "lo_import",
        "lo_export",
        "shell",
        "system",
        "getenv",
    }
)
