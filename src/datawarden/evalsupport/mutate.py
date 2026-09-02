"""Mutación de AST sobre corpus semilla. La otra mitad de `G-WRITE-BLOCK`.

`docs/RULES.md §7`, error 8, descarta explícitamente lo que parecería la opción
obvia: *«Escribir un generador gramatical de SQL válido para Hypothesis es un
subproyecto de una o dos semanas.»* Y propone esto en su lugar, a una décima parte
del coste: **mutar el árbol de un corpus semilla**.

La idea que lo hace valer: **una mutación de un ataque sigue siendo un ataque.** Si
`DELETE FROM t` se rechaza, también tiene que rechazarse envuelto en una CTE, con un
comentario entre tokens, con las mayúsculas cambiadas o unido por `UNION` a una
consulta legítima. Cada mutación es una de las evasiones clásicas del cuaderno, y
aplicarlas todas contra los veinticinco ataques da miles de casos que nadie ha
escrito a mano y que no se parecen a los que escribió quien hizo las reglas.

Y en la otra dirección: **una mutación de una consulta legítima que el guard acepta
tiene que seguir cumpliendo los invariantes** —ni escritura, ni estrella sin
expandir—. Sin esa mitad, «todo se rechaza» satisfaría la meta.

Determinista por obligación: la semilla es un argumento, no un `random` global. Un
contraejemplo que no se puede reproducir no es un contraejemplo, es una anécdota.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator
from typing import Final

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

#: Sinónimos de dialecto. Cambiar `substr` por `substring` no cambia lo que la
#: consulta hace: si una regla dependiera del nombre en vez del nodo, esto la
#: rompería, y ese es exactamente el fallo que la mutación busca.
_SYNONYMS: Final[tuple[tuple[str, str], ...]] = (
    ("substr(", "substring("),
    ("upper(", "ucase("),
    ("count(", "COUNT("),
    ("!=", "<>"),
    (" AND ", " and "),
    (" OR ", " or "),
)

#: Comentarios que se inyectan ENTRE tokens. Es la evasión número uno contra un
#: validador que busque palabras, y contra un árbol no es nada.
_COMMENTS: Final = ("/**/", "/* x */", "/*SELECT*/", "--\n", "/*\n*/")


def _tokens(sql: str) -> list[str]:
    return re.findall(r"\w+|\W", sql)


def inject_comment(sql: str, rng: random.Random) -> str:
    """Un comentario entre dos tokens cualesquiera."""
    tokens = _tokens(sql)
    if len(tokens) < 2:
        return sql
    at = rng.randrange(1, len(tokens))
    return "".join(tokens[:at]) + rng.choice(_COMMENTS) + "".join(tokens[at:])


def flip_case(sql: str, rng: random.Random) -> str:
    """Mayúsculas y minúsculas al azar, letra a letra."""
    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in sql)


def swap_synonym(sql: str, rng: random.Random) -> str:
    """Una función por su sinónimo de dialecto."""
    frm, to = rng.choice(_SYNONYMS)
    return sql.replace(frm, to, 1)


def pad_whitespace(sql: str, rng: random.Random) -> str:
    """Saltos de línea y tabuladores donde había un espacio."""
    filler = rng.choice(["\n", "\t", "  ", "\r\n", " \n "])
    return sql.replace(" ", filler, rng.randint(1, 3))


def wrap_in_cte(sql: str, rng: random.Random) -> str:
    """Envolver en una CTE. La evasión que esconde una escritura tras un SELECT."""
    name = f"m{rng.randrange(1000)}"
    # S608 avisa de inyección por concatenación. AQUÍ ES EL TRABAJO: este módulo
    # construye SQL deliberadamente malformado para dárselo al guard. En `src/` la
    # regla sigue activa para todo lo demás, y una consulta de verdad se arma sobre
    # un AST validado y jamás por concatenación.
    return f"WITH {name} AS ({sql}) SELECT * FROM {name}"  # noqa: S608


def wrap_in_subquery(sql: str, rng: random.Random) -> str:
    name = f"s{rng.randrange(1000)}"
    return f"SELECT * FROM ({sql}) AS {name}"  # noqa: S608


def add_union(sql: str, rng: random.Random) -> str:
    """Unir con una consulta legítima: el ataque se esconde en una rama."""
    inocente = rng.choice(
        [
            "SELECT 1 AS x",
            "SELECT customer_sk AS x FROM dim_customer",
            "SELECT 2 AS x",
        ]
    )
    return (
        f"{sql} UNION ALL {inocente}" if rng.random() < 0.5 else f"{inocente} UNION ALL {sql}"
    )


def rename_aliases(sql: str, rng: random.Random) -> str:
    """Renombrar los alias del árbol. Un alias no cambia de qué columna sale un dato."""
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except SqlglotError:
        # `SqlglotError` y no `ParseError`: una comilla suelta la lanza el
        # TOKENIZADOR, que es otra excepción de la misma familia. Lo encontró un
        # test con basura, y una mutación que revienta pararía el corpus justo en la
        # clase de entrada que más interesa mutar.
        return sql
    for i, table in enumerate(tree.find_all(exp.Table)):
        if table.alias:
            table.set(
                "alias", exp.TableAlias(this=exp.to_identifier(f"z{i}{rng.randrange(99)}"))
            )
    return tree.sql(dialect="duckdb")


def push_into_having(sql: str, _rng: random.Random) -> str:
    """Colar la condición en un `HAVING`, que es otra posición del árbol."""
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except SqlglotError:
        # `SqlglotError` y no `ParseError`: una comilla suelta la lanza el
        # TOKENIZADOR, que es otra excepción de la misma familia. Lo encontró un
        # test con basura, y una mutación que revienta pararía el corpus justo en la
        # clase de entrada que más interesa mutar.
        return sql
    select = tree.find(exp.Select)
    where = select.args.get("where") if select is not None else None
    if select is None or where is None:
        return sql
    select.set("where", None)
    select.set("group", exp.Group(expressions=[exp.Literal.number(1)]))
    select.set("having", exp.Having(this=where.this))
    return tree.sql(dialect="duckdb")


#: Las ocho mutaciones. Cada una es una evasión documentada del cuaderno, no una
#: transformación al azar: por eso son ocho y no ochenta.
MUTATIONS: Final = (
    inject_comment,
    flip_case,
    swap_synonym,
    pad_whitespace,
    wrap_in_cte,
    wrap_in_subquery,
    add_union,
    rename_aliases,
    push_into_having,
)


def mutants(sql: str, *, rounds: int, seed: int) -> Iterator[tuple[str, str]]:
    """`(nombre_de_la_mutación, sql_mutado)`, de forma determinista.

    Cada ronda aplica las nueve mutaciones al resultado de la ronda anterior, así
    que la ronda dos son mutaciones COMPUESTAS —un comentario dentro de una CTE
    dentro de un UNION—, que es donde un validador escrito con prisa se rompe.
    """
    rng = random.Random(seed)  # noqa: S311 -- corpus reproducible, no criptografía
    current = [sql]
    for round_no in range(rounds):
        siguiente: list[str] = []
        for base in current:
            for mutation in MUTATIONS:
                try:
                    mutated = mutation(base, rng)
                except Exception:  # noqa: S112
                    # Una mutación que revienta no para el corpus: se salta y se
                    # sigue. Pararlo aquí dejaría sin probar justo la clase de
                    # entrada que más interesa mutar, que es la que ya venía rota.
                    continue
                if mutated and mutated != base:
                    yield (f"{mutation.__name__}@{round_no}", mutated)
                    siguiente.append(mutated)
        # Solo se propagan unas pocas por ronda: el crecimiento es exponencial y
        # dos rondas completas serían ochenta y un mil mutantes por semilla.
        current = siguiente[:3]
