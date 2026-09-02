"""`G-FAILCLOSED` · el guard NUNCA propaga una excepción. Es un AXIOMA.

`docs/GOALS.yaml` lo marca `propuesta_admisible: false` y exige **>= 5.000 entradas
arbitrarias**. La nota de la meta dice lo que hace que el número signifique algo:
*«Las entradas son bytes arbitrarios, no solo SQL bien formado.»* Un fuzzer que solo
genera SQL válido prueba el parser, no el guardián.

**Por qué esto es un axioma y no un umbral.** Un guard que revienta con una entrada
rara no está «fallando»: está devolviendo el control a quien llamó, y quien llamó no
sabe si la consulta era segura. La única respuesta correcta a «no sé» es «no».

Y hay una segunda propiedad, que es la que de verdad vale: **de todo lo que el guard
ACEPTA, el árbol resultante no contiene ni un nodo de escritura ni una estrella sin
expandir.** Sin ella, «no lanza excepciones» se cumpliría con un guard que aceptara
todo, que es exactamente el guard que no queremos.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlglot import expressions as exp

from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import Principal, RejectionReason, Role, RoleSource, ValidatedQuery
from datawarden.guard.rules.r010_no_write_node import WRITE_NODES
from datawarden.guard.validator import validate
from datawarden.principal import POLICY_PATH
from datawarden.principal.policy import load_policy

_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_PRINCIPAL = Principal(id="fuzz", role=Role.ANALYST, source=RoleSource.CLI_FLAG)

#: 5.000 ejemplos por propiedad, que es lo que `G-FAILCLOSED` exige. Se fija AQUÍ y
#: no en el perfil de Hypothesis porque el número es de la meta, no del entorno:
#: `--hypothesis-profile=dev` no puede bajar un axioma sin que nadie se entere.
_AXIOM_EXAMPLES = 5_000

_SETTINGS = settings(
    max_examples=_AXIOM_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

#: Trozos de SQL, de sintaxis peligrosa y de basura pura. Mezclarlos es lo que
#: produce entradas que ni un generador gramatical ni un fuzzer ciego alcanzan:
#: casi-SQL, que es donde viven los fallos de un parser.
_FRAGMENTS = st.sampled_from(
    [
        "SELECT",
        "FROM",
        "WHERE",
        "dim_customer",
        "birth_date",
        ";",
        "--",
        "/*",
        "*/",
        "'",
        '"',
        "(",
        ")",
        "UNION",
        "DROP TABLE",
        "DELETE",
        "\\x00",
        "\\u0000",
        "0x41",
        "\n",
        "\t",
        "\r\n",
        "�",
        "ñ",
        "0",
        "-1",
        "1e400",
        "NULL",
        "*",
        "read_csv",
        "information_schema",
        "%",
        "\\",
    ]
)


def _guard(sql: str) -> ValidatedQuery | RejectionReason:
    return validate(sql, principal=_PRINCIPAL, schema=_SCHEMA, policy=_POLICY, max_rows=1_000)


@_SETTINGS
@given(st.text(max_size=200))
def test_texto_arbitrario_nunca_hace_estallar_el_guard(entrada: str) -> None:
    """Texto Unicode cualquiera. Ni una excepción sale de `validate()`."""
    verdict = _guard(entrada)
    assert isinstance(verdict, ValidatedQuery | RejectionReason)


@_SETTINGS
@given(st.binary(max_size=200))
def test_bytes_arbitrarios_nunca_hacen_estallar_el_guard(entrada: bytes) -> None:
    """Bytes, decodificados con reemplazo. Es lo que llega por un transporte real."""
    verdict = _guard(entrada.decode("utf-8", errors="replace"))
    assert isinstance(verdict, ValidatedQuery | RejectionReason)


@_SETTINGS
@given(st.lists(_FRAGMENTS, min_size=1, max_size=12).map(" ".join))
def test_casi_sql_nunca_hace_estallar_el_guard(entrada: str) -> None:
    """Fragmentos de SQL barajados: el casi-SQL donde viven los fallos de parser."""
    verdict = _guard(entrada)
    assert isinstance(verdict, ValidatedQuery | RejectionReason)


@_SETTINGS
@given(st.lists(_FRAGMENTS, min_size=1, max_size=12).map(" ".join))
def test_lo_que_el_guard_acepta_no_escribe_ni_lleva_estrella(entrada: str) -> None:
    """La propiedad que impide que «no lanza» se cumpla aceptándolo todo.

    Sin esto, `G-FAILCLOSED` lo satisfaría un guard que devolviera siempre
    `ValidatedQuery`, que es literalmente el peor guard posible.
    """
    verdict = _guard(entrada)
    if not isinstance(verdict, ValidatedQuery):
        return
    assert verdict.ast.find(*WRITE_NODES) is None
    assert not [
        star for star in verdict.ast.find_all(exp.Star) if not isinstance(star.parent, exp.Func)
    ]
    # Y lo que se ejecutaría sale del ÁRBOL, no de la entrada (I-02).
    assert verdict.sql()
