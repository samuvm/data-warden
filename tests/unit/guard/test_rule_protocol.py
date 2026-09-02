"""El protocolo de regla y la resolución de POSICIÓN. Zona TDD, cobertura 95 %.

El corpus de casos prueba las catorce reglas de punta a punta. Aquí se prueban las
dos piezas que el corpus ejercita sin nombrar: el contrato que todas las reglas
cumplen, y `position_of`, que es lo que convierte «no puedes usar esa columna» en
«no puedes usarla AHÍ» — la mitad de la tesis del proyecto.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.position import position_of, table_and_column
from datawarden.guard.registry import BY_ID, POST_RULES, PRE_RULES, RULES
from datawarden.guard.rule import PASS, POST_QUALIFY, PRE_QUALIFY, RuleResult


def _column(sql: str, name: str, *, occurrence: int = 0) -> exp.Column:
    """La columna con ese nombre. `occurrence` elige cuál cuando hay varias.

    Hace falta porque `SELECT a FROM t GROUP BY a` tiene DOS nodos `a`: el de la
    proyección y el del `GROUP BY`. Coger siempre el primero probaba la proyección
    creyendo que probaba el `GROUP BY`, que es exactamente la clase de test que pasa
    sin comprobar nada.
    """
    tree = sqlglot.parse_one(sql, dialect="duckdb")
    found = [c for c in tree.find_all(exp.Column) if c.name.lower() == name]
    if len(found) <= occurrence:
        message = f"no hay una columna {name!r} número {occurrence} en {sql!r}"
        raise AssertionError(message)
    return found[occurrence]


# ------------------------------------------------------------ el protocolo


def test_las_catorce_reglas_cumplen_el_contrato() -> None:
    """Una regla sin `code` no agrupa métricas; sin `families`, no defiende nada."""
    for rule in RULES:
        assert rule.rule_id.startswith("R")
        assert rule.code and rule.code.islower()
        assert isinstance(rule.severity, Severity)
        assert rule.summary
        assert rule.families
        assert rule.phase in {PRE_QUALIFY, POST_QUALIFY}


def test_no_hay_dos_reglas_con_el_mismo_id() -> None:
    """Un `rule_id` no se reutiliza jamás (I-01), y menos a la vez."""
    assert len(BY_ID) == len(RULES) == 14


def test_las_dos_fases_suman_las_catorce() -> None:
    assert len(PRE_RULES) + len(POST_RULES) == len(RULES)


def test_el_resultado_limpio_no_rechaza_ni_reescribe() -> None:
    assert PASS.rejected is False
    assert PASS.tree is None


def test_un_resultado_con_rechazo_lo_dice() -> None:
    from datawarden.domain.types import RejectionReason

    resultado = RuleResult(
        rejection=RejectionReason(
            rule_id="R001",
            code="x_test_code",
            message="a message long enough",
            suggestion="a suggestion long enough",
            severity=Severity.SECURITY,
        )
    )
    assert resultado.rejected is True


# ------------------------------------------------------------- la posición


@pytest.mark.parametrize(
    ("sql", "column", "occurrence", "position"),
    [
        ("SELECT a FROM t", "a", 0, Position.PROJECTION),
        ("SELECT b FROM t WHERE a = 1", "a", 0, Position.WHERE),
        ("SELECT a FROM t GROUP BY a", "a", 1, Position.GROUP_BY),
        ("SELECT a FROM t ORDER BY a", "a", 1, Position.ORDER_BY),
        ("SELECT count(*) FROM t GROUP BY b HAVING a > 1", "a", 0, Position.HAVING),
        ("SELECT b FROM t JOIN u ON t.a = u.a", "a", 0, Position.JOIN_ON),
        ("SELECT upper(a) FROM t", "a", 0, Position.FUNCTION_ARGUMENT),
        ("SELECT row_number() OVER (PARTITION BY a) FROM t", "a", 0, Position.WINDOW_PARTITION),
        ("SELECT b FROM t QUALIFY a > 1", "a", 0, Position.QUALIFY),
    ],
)
def test_cada_posicion_del_arbol_se_reconoce(
    sql: str, column: str, occurrence: int, position: Position
) -> None:
    assert position_of(_column(sql, column, occurrence=occurrence)) is position


def test_una_columna_dentro_de_una_funcion_dentro_de_un_where_esta_en_el_where() -> None:
    """El orden de las comprobaciones ES la semántica: manda la cláusula.

    Las dos posiciones están prohibidas para una columna enmascarada, pero el
    mensaje tiene que nombrar la cláusula, que es lo que quien preguntó puede cambiar.
    """
    assert position_of(_column("SELECT b FROM t WHERE upper(a) = 'X'", "a")) is Position.WHERE


def test_una_columna_dentro_de_una_funcion_en_la_proyeccion_no_esta_en_la_proyeccion() -> None:
    """`SELECT substr(national_id, 1, 3)` devuelve tres dígitos sin nombrar la columna."""
    posicion = position_of(_column("SELECT substr(a, 1, 3) FROM t", "a"))
    assert posicion is Position.FUNCTION_ARGUMENT


def test_ordenar_dentro_de_una_ventana_no_es_particionar() -> None:
    sql = "SELECT row_number() OVER (PARTITION BY b ORDER BY a) FROM t"
    assert position_of(_column(sql, "a")) is Position.ORDER_BY


def test_la_tabla_y_la_columna_salen_en_minusculas() -> None:
    assert table_and_column(_column("SELECT T.A FROM t AS T", "a")) == ("t", "a")


def test_una_columna_sin_tabla_devuelve_cadena_vacia() -> None:
    """Tras `qualify()` es raro, y lo raro en un guardián se trata como lo peor."""
    assert table_and_column(_column("SELECT a FROM t", "a")) == ("", "a")


def test_una_columna_de_la_particion_de_ventana_entre_varias_se_reconoce() -> None:
    """`PARTITION BY a, b`: las dos son clave de partición, no solo la primera."""
    sql = "SELECT row_number() OVER (PARTITION BY b, a ORDER BY c) FROM t"
    assert position_of(_column(sql, "a")) is Position.WINDOW_PARTITION
    assert position_of(_column(sql, "b")) is Position.WINDOW_PARTITION


def test_una_columna_dentro_de_una_expresion_de_particion_tambien_cuenta() -> None:
    """Particionar por `upper(a)` es particionar por `a` con un disfraz."""
    sql = "SELECT row_number() OVER (PARTITION BY upper(a)) FROM t"
    assert position_of(_column(sql, "a")) is Position.WINDOW_PARTITION


# ------------------------------------------------ el único `except` del guard


def test_si_una_regla_revienta_el_guard_rechaza_en_vez_de_propagar() -> None:
    """I-04 · fail-closed, probado y no razonado.

    La propiedad de `tests/property` comprueba que con 5.000 entradas reales no sale
    ninguna excepción. Esto comprueba la otra mitad: **qué pasa cuando sí la hay.**
    Se fuerza una regla a estallar y se exige un rechazo, no una excepción.

    Un guard que revienta no está «fallando»: está devolviendo el control a quien
    llamó, y quien llamó no sabe si la consulta era segura. La única respuesta
    correcta a «no sé» es «no».
    """
    from datawarden.catalog.types import CatalogSchema
    from datawarden.domain.types import Principal, RejectionReason, Role, RoleSource
    from datawarden.guard import registry
    from datawarden.guard.validator import validate
    from datawarden.principal.policy import policy_from_dict

    class _ReglaQueRevienta:
        rule_id = "R001"
        code = "boom"
        severity = Severity.INTERNAL
        summary = "revienta a propósito"
        families: tuple[str, ...] = ("prueba",)
        phase = PRE_QUALIFY

        def check(self, ctx: object) -> object:
            message = "esto es un fallo interno a propósito"
            raise RuntimeError(message)

    original = registry.PRE_RULES
    registry_module_pre = registry.PRE_RULES
    try:
        # Se sustituye la tupla que el validador importó, no el registro entero: lo
        # que se prueba es el camino del `except`, no el registro.
        from datawarden.guard import validator as validator_module

        validator_module.PRE_RULES = (_ReglaQueRevienta(),)  # type: ignore[assignment]
        verdict = validate(
            "SELECT 1 AS x",
            principal=Principal(id="p", role=Role.ANALYST, source=RoleSource.CLI_FLAG),
            schema=CatalogSchema(dialect="duckdb", tables=()),
            policy=policy_from_dict({"columns": {}}),
            max_rows=10,
        )
    finally:
        validator_module.PRE_RULES = registry_module_pre  # type: ignore[assignment]
        assert registry.PRE_RULES is original

    assert isinstance(verdict, RejectionReason)
    assert verdict.rule_id == "INTERNAL"
    assert verdict.code == "guard_internal_error"
    assert verdict.severity is Severity.INTERNAL
    # Y el mensaje NO cita ni el texto de la excepción ni el fragmento de entrada:
    # los errores citan la entrada, y la entrada puede llevar un literal con datos.
    assert "a propósito" not in verdict.message
    assert "RuntimeError" in verdict.message
    assert verdict.suggestion.strip()


def test_un_guard_sin_tiempo_rechaza_en_vez_de_seguir_pensando() -> None:
    """Un timeout es un rechazo, no un paso. 250 ms es el máximo de `G-GUARD-P95`."""
    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.domain.types import Principal, RejectionReason, Role, RoleSource
    from datawarden.guard.validator import validate
    from datawarden.principal import POLICY_PATH
    from datawarden.principal.policy import load_policy

    verdict = validate(
        "SELECT customer_sk FROM dim_customer",
        principal=Principal(id="p", role=Role.ANALYST, source=RoleSource.CLI_FLAG),
        schema=load_generated(SCHEMA_PATH),
        policy=load_policy(POLICY_PATH),
        max_rows=10,
        timeout_ms=-1.0,
    )
    assert isinstance(verdict, RejectionReason)
    assert verdict.code == "guard_timeout"
    assert verdict.rule_id == "INTERNAL"
