"""The allowlist. Whatever is not listed here is rejected.

This is the design decision most often made the wrong way round in this domain
(`docs/RULES.md` §7). A denylist of dangerous keywords is bypassed with an inter-token
comment, odd casing or an encoding; a denylist of functions falls short the day DuckDB
ships a new extension, without anyone touching this repository.

An allowlist fails the opposite way, which is survivable: it is too strict. A
legitimate query using an unlisted function is rejected, someone reads the rejection,
and the function is added as a decision, with its commit and test case. The cost of an
allowlist is friction; the cost of a denylist is a leak.

Entries are `sqlglot.expressions` class names, not SQL keywords. `exp.Count` is the node
sqlglot built after parsing, so `COUNT`, `count` and `/*x*/count` are the same node, with
nothing to normalise and no encoding to slip through.
"""

from __future__ import annotations

from typing import Final

#: Query structure. Without these, no SELECT is possible.
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

#: Literals and types. `Cast` is included on purpose: without explicit conversion, half
#: of the date queries on this warehouse cannot be written.
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
        # `Var` is how sqlglot represents an unquoted descriptor: the unit in
        # `date_trunc('month', x)` or in `INTERVAL 1 MONTH`. Added as a decision with its
        # case (R003-A2), since no query over a period can be written without it. It
        # opens nothing: a bare `Var` executes nothing, and where it would mean
        # something (`SET x = ...`) the construct is its own node, stopped by R001/R002.
        "Var",
        "Array",
        "Struct",
        "Bracket",
    }
)

#: Operators and predicates.
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
        # `Any` and `All` are deliberately excluded: sqlglot parses a call to an unknown
        # function named `all` as the quantifier `exp.All`, which would let it through.
        # Quantifiers are only needed for `x = ANY (subquery)`, and `IN` covers that.
    }
)

#: Aggregates, the reason an analytical warehouse exists.
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

#: Window functions. The benchmark requires 15 questions with a window or a correlated
#: subquery, so these are not optional.
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

#: Dates and time. Nearly every benchmark question involves a period.
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

#: Text. `Concat` is included on purpose: `concat(first_name, last_name)` is the
#: derived-expression attack described in `docs/DESIGN.md`, and it is not stopped by
#: banning the function (that would break many legitimate queries) but by R008, which
#: checks the lineage of the expression's inputs.
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

#: Numbers.
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

#: Internal nodes that sqlglot builds on its own while parsing or qualifying. Without
#: them an ordinary query would be rejected over a representation detail, the worst kind
#: of friction because nobody understands it.
_INTERNOS: Final = frozenset(
    {
        "Anonymous",  # rejected by R003, not here: it deserves its own message
        "Condition",
        "Predicate",
        "Func",
        "AggFunc",
        "Binary",
        "Unary",
        "Expression",
    }
)

#: Allowed node classes. Any other node class is rejected (R002).
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

#: Functions that sqlglot does not map to a dedicated node and leaves as `Anonymous`, but
#: that this warehouse needs and that are harmless. Deliberately short: each entry is a
#: decision, not a convenience.
#:
#: `any_value` is listed because the catalog's smoke queries use it and it reads nothing
#: from outside the query.
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

#: System schemas. Querying them reads the engine's real catalog underneath the published
#: one, and with it the columns the policy deliberately excluded.
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

#: Functions that read from outside the query: files, the network or the process itself.
#: They are already rejected by their absence from the allowlist, so this list is not
#: what stops them; it lets the rejection say what happened instead of "unknown
#: function".
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
