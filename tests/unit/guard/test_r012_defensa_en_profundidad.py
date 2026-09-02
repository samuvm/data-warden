"""R012 vigila lo que R008 hoy ya para. **Y eso no es código muerto.**

Medido el 2026-09-02: en `docs/spec/policy.yaml` **cero columnas** son a la vez
identificadoras por `data_type` y `allow` para un rol. Como R008 corre antes y rechaza
toda columna `mask` o `deny` fuera de la proyección, la rama de R012 que mira
`GROUP BY` y `PARTITION BY` **no se alcanza hoy a través del guard completo**.

Es exactamente para eso que existe. La política la escribe un humano y puede cambiar:
el día que negocio marque una columna identificadora como `allow` —porque «no tiene
datos personales», que es la frase que precede a la mitad de las fugas— R008 dejará de
pararla y R012 seguirá ahí. Esa es la definición de defensa en profundidad, y **un
anillo que solo se prueba cuando el anterior falla no se prueba nunca** si se espera a
que falle.

Así que se prueba con una política FIXTURE que declara ese estado futuro. Es la
excepción deliberada a I-13 —el corpus se prueba contra la política real, porque probar
el guard contra una política de juguete probaría el juguete— y aquí la política de
juguete **es el sujeto del test**: lo que se comprueba no es la política de hoy, es que
la regla aguanta la de mañana.
"""

from __future__ import annotations

import pytest
import sqlglot

from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import Position, Principal, Role, RoleSource
from datawarden.guard.context import build_context, qualify_tree
from datawarden.guard.registry import BY_ID
from datawarden.principal.policy import policy_from_dict

_SCHEMA = load_generated(SCHEMA_PATH)
_ANALYST = Principal(id="r012", role=Role.ANALYST, source=RoleSource.CLI_FLAG)

#: El estado futuro que se quiere cubrir: una columna que la política clasifica como
#: IDENTIFICADOR y que sin embargo deja ver a todo el mundo. Hoy no existe ninguna así.
_POLICY_PERMISIVA = policy_from_dict(
    {
        "default_level": "allow",
        "columns": {
            "dim_customer.national_id": {
                "levels": {
                    "admin": "allow",
                    "analyst": "allow",
                    "finance": "allow",
                    "ops": "allow",
                },
                "data_type": "identificador_directo",
                "generalized": "dim_customer.country_code",
                "transformation": None,
                "keep_last_n": None,
                "derived_from": [],
            }
        },
    }
)


def _check(sql: str):
    # R012 es POST_QUALIFY: sin cualificar, `national_id` no lleva tabla y el linaje
    # no puede resolverla. Se cualifica igual que hace el validador, porque probar la
    # regla sobre un árbol que ella nunca ve no probaría nada.
    qualified = qualify_tree(sqlglot.parse_one(sql, dialect="duckdb"), _SCHEMA, "duckdb")
    ctx = build_context(
        raw_sql=sql,
        tree=qualified,
        schema=_SCHEMA,
        policy=_POLICY_PERMISIVA,
        principal=_ANALYST,
        dialect="duckdb",
        max_rows=50_000,
    )
    return BY_ID["R012"].check(ctx)


def test_agrupar_por_un_identificador_permitido_se_rechaza_igual() -> None:
    """El caso que R008 ya no para porque la política dice `allow`.

    Agrupar por un identificador hace que cada grupo sea una persona, y eso es cierto
    aunque la política haya decidido que la columna se puede ver: son dos cosas
    distintas —ver el valor y aislarlo por cardinalidad— y por eso hay dos anillos.
    """
    resultado = _check("SELECT count(*) AS n FROM dim_customer GROUP BY national_id")

    assert resultado.rejection is not None
    assert resultado.rejection.rule_id == "R012"
    assert resultado.rejection.position is Position.GROUP_BY
    assert "national_id" in str(resultado.rejection.subject)


def test_particionar_una_ventana_por_un_identificador_permitido_se_rechaza() -> None:
    """`PARTITION BY` es un `GROUP BY` con otro nombre, y el mismo problema.

    Cada partición acaba siendo una persona, y el número de fila revela su posición
    en el orden. Que la ventana no proyecte la columna no cambia nada.
    """
    resultado = _check(
        "SELECT row_number() OVER (PARTITION BY national_id) AS r FROM dim_customer"
    )

    assert resultado.rejection is not None
    assert resultado.rejection.rule_id == "R012"
    assert resultado.rejection.position is Position.WINDOW_PARTITION
    assert "national_id" in str(resultado.rejection.subject)


def test_el_rechazo_publica_la_alternativa_que_declara_la_politica() -> None:
    """Un rechazo sin salida bloquea el trabajo en vez de redirigirlo (I-09)."""
    resultado = _check("SELECT count(*) AS n FROM dim_customer GROUP BY national_id")

    assert resultado.rejection is not None
    assert resultado.rejection.alternative == "dim_customer.country_code"
    assert "country_code" in resultado.rejection.suggestion


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) AS n FROM dim_customer GROUP BY country_code",
        "SELECT row_number() OVER (PARTITION BY country_code) AS r FROM dim_customer",
    ],
    ids=["group_by", "partition_by"],
)
def test_una_columna_que_no_identifica_no_se_toca(sql: str) -> None:
    """La otra mitad: R012 no puede convertirse en «prohibido agrupar».

    Agrupar por país es análisis normal y tiene que seguir siéndolo. Sin este test,
    endurecer la regla podría degenerar en rechazar toda agregación, que es un guard
    que se desactiva en tres semanas.
    """
    assert _check(sql).rejection is None
