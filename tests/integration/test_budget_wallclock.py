"""`G-BUDGET-ESCAPE` **por reloj**: la consulta cara muere antes de tocar el motor.

La meta pide, además del invariante por contador, un número: **el rechazo de una
consulta cara sobre 3 GB en <= 200 ms**. Y el número es la mitad del argumento: una
consulta que se rechaza en 200 ms es una que NO se ha ejecutado, porque escanear 3 GB
no cabe en 200 ms ni en el mejor disco. El reloj es lo que convierte «el código dice
que no ejecuta» en evidencia.

Nivel 2: corre en `make done`, nunca en el gate rápido.
"""

from __future__ import annotations

import pathlib
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATABASE = ROOT / "datagen" / "out" / "cierzo-full.duckdb"

#: La consulta cara, y es LA PREGUNTA INGENUA de siempre: «dame todo». Sobre la
#: tabla de 66,6 M de filas son 4,1 GB de Parquet sin una sola poda. El rol `ops`
#: tiene 0,05 GB, es decir, ochenta veces menos.
#:
#: Con rol `ops` a propósito: la estrella se EXPANDE contra el catálogo (R009) y
#: todas las columnas de esta tabla son `allow` para ops, así que la consulta pasa
#: el anillo 3 entera y muere en el 4. Si muriera antes, el número mediría el guard
#: y no el presupuesto.
COSTOSA = "SELECT * FROM fact_payment_attempt"


@pytest.fixture(scope="module")
def piezas():
    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.catalog.statistics import load as load_stats
    from datawarden.cost import STATISTICS_PATH
    from datawarden.principal import BUDGETS_PATH, POLICY_PATH
    from datawarden.principal.budgets import load_budgets
    from datawarden.principal.policy import load_policy

    if not STATISTICS_PATH.exists():
        pytest.fail(
            "no hay estadísticas generadas. Se construyen desde los manifiestos de "
            "Iceberg con `make statistics`. Sin ellas el estimador no puede podar y "
            "el número de esta meta no significaría nada."
        )
    return (
        load_generated(SCHEMA_PATH),
        load_policy(POLICY_PATH),
        load_budgets(BUDGETS_PATH),
        load_stats(STATISTICS_PATH),
    )


def test_una_consulta_de_gigabytes_se_rechaza_en_menos_de_200_ms(piezas) -> None:
    """El número de la meta, medido con el reloj y no razonado."""
    from datawarden.cost.screen import screen
    from datawarden.domain.types import Principal, Role, RoleSource
    from datawarden.engines.base import executions

    schema, policy, budgets, stats = piezas
    principal = Principal(id="reloj", role=Role.OPS, source=RoleSource.CLI_FLAG)

    antes = executions()
    inicio = time.perf_counter()
    result = screen(
        COSTOSA,
        principal=principal,
        schema=schema,
        policy=policy,
        budgets=budgets,
        stats=stats,
    )
    transcurrido_ms = (time.perf_counter() - inicio) * 1000.0

    assert not result.accepted
    assert result.rejection is not None
    assert result.rejection.rule_id == "BUDGET"
    assert result.cost is not None
    assert result.cost.estimated_bytes > 3_000_000_000, (
        f"la consulta de prueba tiene que superar los 3 GB para que el número "
        f"signifique algo, y el estimador dice {result.cost.estimated_bytes}"
    )
    assert executions() == antes, "el motor se movió, y esta consulta no debía llegar"
    assert transcurrido_ms <= 200.0, f"el rechazo tardó {transcurrido_ms:.1f} ms"


def test_el_motor_ejecuta_de_verdad_lo_que_si_cabe(piezas) -> None:
    """La otra mitad: si nada se ejecutara nunca, el invariante sería trivial.

    Y comprueba de paso el invariante I-02 sobre un motor REAL: lo que corre es
    `ast.sql(dialect)`, y el resultado sale de DuckDB, no de un doble de test.
    """
    from datawarden.cost.screen import screen
    from datawarden.domain.types import Principal, Role, RoleSource
    from datawarden.engines.duckdb_engine import DuckDBEngine

    if not DATABASE.exists():
        pytest.fail(f"no existe {DATABASE}: `make dataset PROFILE=full`")

    schema, policy, budgets, stats = piezas
    result = screen(
        "SELECT currency_code, minor_units FROM ref_currency",
        principal=Principal(id="reloj", role=Role.OPS, source=RoleSource.CLI_FLAG),
        schema=schema,
        policy=policy,
        budgets=budgets,
        stats=stats,
    )
    assert result.query is not None

    engine = DuckDBEngine(DATABASE)
    try:
        rows = engine.execute(result.query)
    finally:
        engine.close()
    assert rows.row_count > 0
    assert rows.column_count == 2
