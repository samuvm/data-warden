"""I-03 · el guard es una ALLOWLIST. `exp.Anonymous` con nombre aleatorio, rechazo.

`docs/RULES.md` lo fija así: *«`exp.Anonymous` con nombre aleatorio ⇒ rechazo, 5.000
ejemplos»*. Es la propiedad que distingue una allowlist de una denylist con mejor
prensa: una denylist se prueba con las funciones que alguien se acordó de prohibir,
y esta se prueba con **funciones que nadie ha visto nunca**, que es precisamente el
caso que la denylist no cubre —la extensión de DuckDB que se publica mañana—.

Se generan nombres al azar y se comprueba que TODOS se rechazan, en las tres
posiciones donde una función puede aparecer: la proyección, el predicado y el FROM.
"""

from __future__ import annotations

import sqlglot
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlglot import expressions as exp

from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import Principal, RejectionReason, Role, RoleSource
from datawarden.guard.allowlist import ALLOWED_ANONYMOUS
from datawarden.guard.validator import validate
from datawarden.principal import POLICY_PATH
from datawarden.principal.policy import load_policy

_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_PRINCIPAL = Principal(id="fuzz", role=Role.ADMIN, source=RoleSource.CLI_FLAG)

_AXIOM_EXAMPLES = 5_000

_SETTINGS = settings(
    max_examples=_AXIOM_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

#: Las cuatro reglas que pueden parar una función desconocida, y por qué son cuatro.
#: La propiedad genera nombres al azar y algunos caen en PALABRAS RESERVADAS —`not`,
#: `all`, `case`—, con las que la consulta ni siquiera parsea: eso lo para R001 y
#: sigue siendo un rechazo. Aceptar las cuatro es más honesto que filtrar los
#: nombres incómodos de la estrategia, que sería estrechar el fuzzer para que el
#: número salga bonito. Lo que la propiedad afirma es que **NINGUNA función
#: desconocida se acepta**, no por qué puerta se la echa.
_STRUCTURAL = frozenset({"R001", "R002", "R003", "R004"})

#: Nombres de función plausibles: minúsculas, dígitos y guion bajo, como los de
#: cualquier extensión. Generar `!!!` probaría el tokenizador, no la allowlist.
_FUNCTION_NAMES = st.from_regex(r"\A[a-z][a-z0-9_]{2,20}\Z", fullmatch=True)


def _es_desconocida(sql: str, name: str) -> bool:
    """¿sqlglot ve ahí una función DESCONOCIDA, o resulta que conoce ese nombre?

    Existe porque el generador aleatorio acierta de vez en cuando con un nombre que
    sqlglot SÍ conoce —`exp`, `left`, `trim`, `all`—, y entonces no hay ninguna
    función desconocida en la consulta y la propiedad no tiene nada que afirmar.
    Filtrar aquí no es ablandar el fuzzer: es hacer que la propiedad diga lo que
    quiere decir. La allowlist de nodos, que es lo que cubre a los nombres
    conocidos, la prueba el corpus de R002.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except sqlglot.ParseError:
        return True  # lo que no parsea también tiene que rechazarse
    return any(str(node.this).lower() == name for node in tree.find_all(exp.Anonymous))


def _guard(sql: str) -> object:
    """Con rol ADMIN a propósito: **el rol más alto tampoco ejecuta lo desconocido.**

    Si esto se probara con `analyst`, un rechazo podría venir de la política de
    columnas y la propiedad no probaría nada sobre la allowlist de funciones.
    """
    return validate(sql, principal=_PRINCIPAL, schema=_SCHEMA, policy=_POLICY, max_rows=1_000)


@_SETTINGS
@given(_FUNCTION_NAMES)
def test_una_funcion_desconocida_en_la_proyeccion_se_rechaza(name: str) -> None:
    assume(name not in ALLOWED_ANONYMOUS)
    sql = f"SELECT {name}(customer_sk) AS x FROM dim_customer"
    assume(_es_desconocida(sql, name))
    verdict = _guard(sql)
    assert isinstance(verdict, RejectionReason)
    assert verdict.rule_id in _STRUCTURAL


@_SETTINGS
@given(_FUNCTION_NAMES)
def test_una_funcion_desconocida_en_el_predicado_se_rechaza(name: str) -> None:
    assume(name not in ALLOWED_ANONYMOUS)
    sql = f"SELECT customer_sk FROM dim_customer WHERE {name}(customer_sk) > 1"
    assume(_es_desconocida(sql, name))
    verdict = _guard(sql)
    assert isinstance(verdict, RejectionReason)
    assert verdict.rule_id in _STRUCTURAL


@_SETTINGS
@given(_FUNCTION_NAMES)
def test_una_funcion_de_tabla_desconocida_en_el_from_se_rechaza(name: str) -> None:
    assume(name not in ALLOWED_ANONYMOUS)
    sql = f"SELECT customer_sk FROM {name}('x')"
    assume(_es_desconocida(sql, name))
    verdict = _guard(sql)
    assert isinstance(verdict, RejectionReason)
    assert verdict.rule_id in _STRUCTURAL
