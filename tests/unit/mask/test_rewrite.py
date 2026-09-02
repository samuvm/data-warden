"""El enmascarado por REESCRITURA DEL AST. Zona TDD obligatorio, cobertura 95 %.

FASE ROJA.

`docs/PLAN.md` lo exige con esas palabras: **reescribiendo el AST antes de ejecutar,
nunca post-procesando el DataFrame por nombre de columna.** La diferencia no es de
estilo. Post-procesar por nombre falla en cuanto hay un alias, una vista que renombra
o dos columnas con el mismo nombre en dos tablas; y sobre todo, deja el valor real
viajando desde el motor hasta el proceso, donde ya se le ha escapado a quien mira los
logs. Reescribir el árbol hace que **el valor real no salga nunca de la base de datos**.

**Ni un solo test de este fichero asierta sobre la cadena de SQL generada.** Lo
prohíbe `docs/RULES.md §2` y con razón: comparar SQL como texto produce una suite que
se rompe con cada cambio de formateo de sqlglot y que no dice nada sobre lo que el
árbol significa. La verdad se establece sobre el AST.
"""

from __future__ import annotations

import dataclasses

import pytest
import sqlglot
from sqlglot import expressions as exp

from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import Principal, RejectionReason, Role, RoleSource, ValidatedQuery
from datawarden.guard.validator import validate
from datawarden.mask.config import MaskConfig
from datawarden.mask.rewrite import MASK_NODES, mask_query
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy, policy_from_dict

_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_BUDGETS = load_budgets(BUDGETS_PATH)
_CONFIG = MaskConfig(pepper="pimienta-de-pruebas-suficientemente-larga")


def _validada(sql: str, rol: Role = Role.ANALYST) -> ValidatedQuery:
    """La consulta ya validada. Enmascarar SIEMPRE ocurre después del guard."""
    quien = Principal(id=f"mask-{rol.value}", role=rol, source=RoleSource.CLI_FLAG)
    verdict = validate(
        sql,
        principal=quien,
        schema=_SCHEMA,
        policy=_POLICY,
        max_rows=_BUDGETS.max_rows(rol),
        dialect="duckdb",
    )
    assert isinstance(verdict, ValidatedQuery), f"el guard rechazó el fixture: {verdict}"
    return verdict


def _enmascarada(sql: str, rol: Role = Role.ANALYST) -> ValidatedQuery:
    resultado = mask_query(_validada(sql, rol), policy=_POLICY, config=_CONFIG)
    assert isinstance(resultado, ValidatedQuery), f"el enmascarado rechazó: {resultado}"
    return resultado


def _proyecciones(query: ValidatedQuery) -> dict[str, exp.Expression]:
    """`nombre de salida` -> la expresión que lo produce, ya enmascarada o no."""
    select = query.ast.find(exp.Select)
    assert select is not None
    return {
        p.alias_or_name: (p.this if isinstance(p, exp.Alias) else p) for p in select.expressions
    }


# ------------------------------------------------ lo que NO se toca ---


def test_una_columna_permitida_sale_intacta() -> None:
    """El enmascarador no es un filtro: lo que la política permite no se toca."""
    salida = _proyecciones(_enmascarada("SELECT country_code FROM dim_customer"))
    assert isinstance(salida["country_code"], exp.Column)


def test_admin_no_lleva_mascara_ninguna() -> None:
    """La política no es una jerarquía, pero `admin` sí ve todo lo que existe.

    Y por eso `G-AUDIT-COV` es un axioma: la auditoría es el ÚNICO control que este
    sistema tiene sobre el rol admin.
    """
    salida = _proyecciones(_enmascarada("SELECT first_name FROM dim_customer", Role.ADMIN))
    assert isinstance(salida["first_name"], exp.Column)


def test_una_consulta_sin_columnas_enmascaradas_no_declara_ninguna() -> None:
    assert _enmascarada("SELECT country_code FROM dim_customer").masked_columns == ()


# --------------------------------------------- las cuatro transformaciones ---


def test_tachar_sustituye_por_un_literal_fijo() -> None:
    """`first_name` para analyst. Nueve de las diecisiete filas usan esto."""
    expresion = _proyecciones(_enmascarada("SELECT first_name FROM dim_customer"))["first_name"]

    assert isinstance(expresion, exp.Case)
    literales = [n.this for n in expresion.find_all(exp.Literal) if n.is_string]
    assert "***" in literales


def test_generalizar_sustituye_por_la_columna_declarada_en_la_politica() -> None:
    """`birth_date` -> `age_band`. La alternativa la publica el contrato, no el código.

    Es lo que convierte el enmascarado en algo usable: el analista sigue pudiendo
    hacer cohortes por edad sin ver una fecha de nacimiento.
    """
    expresion = _proyecciones(_enmascarada("SELECT birth_date FROM dim_customer"))["birth_date"]

    columnas = {c.name.lower() for c in expresion.find_all(exp.Column)}
    assert "age_band" in columnas


def test_ultimos_n_conserva_exactamente_los_n_que_declara_la_politica() -> None:
    """`payout_iban` para finance: `keep_last_n = 4`, ni 3 ni 5.

    El número sale de la fila de la política. Codificarlo aquí haría que cambiar la
    política no cambiara el comportamiento, que es la definición de contrato muerto.
    """
    query = _enmascarada("SELECT payout_iban FROM dim_corporate_group", Role.FINANCE)
    expresion = _proyecciones(query)["payout_iban"]

    numeros = [int(n.this) for n in expresion.find_all(exp.Literal) if not n.is_string]
    assert 4 in numeros


def test_hash_estable_produce_un_hash_truncado_y_no_el_valor() -> None:
    """`ip_address_int` para analyst. Permite contar sesiones distintas sin verlas."""
    query = _enmascarada("SELECT ip_address_int FROM fact_payment_attempt")
    expresion = _proyecciones(query)["ip_address_int"]

    assert expresion.find(exp.SHA2) is not None, "el hash estable no hashea nada"
    numeros = [int(n.this) for n in expresion.find_all(exp.Literal) if not n.is_string]
    assert 12 in numeros, "el hash tiene que salir truncado a 12 hex"


# ------------------------------------------------------------ NULL ---


@pytest.mark.parametrize(
    ("sql", "rol", "columna"),
    [
        ("SELECT first_name FROM dim_customer", Role.ANALYST, "first_name"),
        ("SELECT birth_date FROM dim_customer", Role.ANALYST, "birth_date"),
        ("SELECT payout_iban FROM dim_corporate_group", Role.FINANCE, "payout_iban"),
        ("SELECT ip_address_int FROM fact_payment_attempt", Role.ANALYST, "ip_address_int"),
    ],
    ids=["tachar", "generalizar", "ultimos_n", "hash_estable"],
)
def test_las_cuatro_transformaciones_preservan_null(sql: str, rol: Role, columna: str) -> None:
    """**Obligatorio, y lo exigen dos contratos a la vez.**

    `policy.yaml` dice que el NULL de `last_name_2` significa «este sistema de
    nombres no tiene segundo apellido» —o sea, es un DATO— y
    `resultset-equality.md` decide que NULL y cadena vacía son DISTINTOS y tienen
    motivo propio en el informe. Una máscara que convirtiera NULL en `'***'`
    INVENTARÍA un valor donde no lo había, y el banco de 60 preguntas de la fase 8
    tendría respuestas de referencia falsas sin que nadie lo notara.
    """
    expresion = _proyecciones(_enmascarada(sql, rol))[columna]

    assert isinstance(expresion, exp.Case), "sin CASE no hay forma de preservar NULL"
    assert expresion.find(exp.Is) is not None, "la máscara no comprueba IS NULL"
    assert expresion.find(exp.Null) is not None, "la máscara no devuelve NULL"


# --------------------------------------------------------- el alias y el linaje ---


def test_el_alias_de_salida_se_conserva() -> None:
    """Reescribir la expresión no puede cambiar el nombre de la columna de salida.

    Si cambiara, el resultset dejaría de ser comparable con su respuesta de
    referencia por un motivo que no tiene nada que ver con el enmascarado.
    """
    salida = _proyecciones(_enmascarada("SELECT first_name AS nombre FROM dim_customer"))
    assert "nombre" in salida
    assert isinstance(salida["nombre"], exp.Case)


def test_se_declara_la_columna_base_enmascarada_y_no_el_alias() -> None:
    """`masked_columns` es la evidencia que va al registro de auditoría.

    El contrato dice `tabla.columna`, ordenadas. El alias es del cliente; la columna
    base es lo que de verdad se protegió, y es lo único con lo que se puede auditar.
    """
    query = _enmascarada("SELECT first_name AS n FROM dim_customer")
    assert query.masked_columns == ("dim_customer.first_name",)


def test_las_columnas_enmascaradas_salen_ordenadas_y_sin_repetir() -> None:
    """Entran en el hash de la cadena de auditoría: el orden no puede depender del
    orden en que sqlglot recorriera el árbol."""
    query = _enmascarada("SELECT last_name_1, first_name, first_name AS otra FROM dim_customer")
    assert query.masked_columns == (
        "dim_customer.first_name",
        "dim_customer.last_name_1",
    )


def test_varias_columnas_se_enmascaran_a_la_vez_y_cada_una_con_lo_suyo() -> None:
    salida = _proyecciones(
        _enmascarada("SELECT first_name, birth_date, country_code FROM dim_customer")
    )

    assert isinstance(salida["first_name"], exp.Case)
    assert isinstance(salida["birth_date"], exp.Case)
    assert isinstance(salida["country_code"], exp.Column)


# ------------------------------------------------------- lo que se ejecuta ---


def test_lo_enmascarado_sigue_siendo_ejecutable_por_el_dialecto() -> None:
    """El árbol reescrito tiene que re-serializar sin reventar. Es lo que corre."""
    query = _enmascarada("SELECT first_name FROM dim_customer")
    assert sqlglot.parse_one(query.sql(), dialect="duckdb") is not None


def test_los_nodos_de_la_mascara_son_los_del_guard_mas_el_hash() -> None:
    """`SHA2` no está en la allowlist del guard, y NO se añade allí.

    El usuario no debe poder escribir `sha256()` a mano —es una función de motor y
    R003 la para—, pero el enmascarador corre DESPUÉS de validar y necesita
    producirla. La diferencia se declara aquí, en un conjunto explícito, en vez de
    debilitar la allowlist del anillo 3.
    """
    from datawarden.guard.allowlist import ALLOWED_NODES

    assert "SHA2" in MASK_NODES
    assert "SHA2" not in ALLOWED_NODES
    assert ALLOWED_NODES <= MASK_NODES


def test_el_arbol_enmascarado_solo_usa_nodos_del_conjunto_declarado() -> None:
    """La autocomprobación: enmascarar no puede introducir un nodo desconocido.

    Si lo hiciera, lo que se ejecuta dejaría de ser «el árbol validado más
    exactamente lo que el enmascarador añade», que es la única forma de que el
    anillo 4 no agrande la superficie que el anillo 3 acotó.
    """
    for sql, rol in (
        ("SELECT first_name FROM dim_customer", Role.ANALYST),
        ("SELECT birth_date FROM dim_customer", Role.ANALYST),
        ("SELECT payout_iban FROM dim_corporate_group", Role.FINANCE),
        ("SELECT ip_address_int FROM fact_payment_attempt", Role.ANALYST),
    ):
        query = _enmascarada(sql, rol)
        usados = {n.__class__.__name__ for n in query.ast.walk()}
        assert usados <= MASK_NODES, f"nodos fuera de MASK_NODES: {usados - MASK_NODES}"


# --------------------------------------------------------------- fail-closed ---


def test_una_proyeccion_que_no_es_una_columna_desnuda_se_rechaza() -> None:
    """La rama defensiva, y termina en rechazo y no en un intento de adivinar.

    Toda proyección que sobrevive al guard llevando una columna enmascarada es un
    `exp.Column` desnudo: cualquier función, `CASE` o concatenación ya la rechaza
    R008 como `argumento_de_funcion`. Encontrarse otra cosa aquí significa que el
    guard tiene un agujero, y la respuesta correcta a «el anillo anterior falló» no
    es enmascarar a ojo: es parar.
    """
    query = _validada("SELECT first_name FROM dim_customer")
    # Se fabrica a mano el estado que el guard no debería dejar pasar.
    select = query.ast.find(exp.Select)
    assert select is not None
    columna = select.expressions[0]
    select.set("expressions", [exp.func("upper", columna.copy())])

    resultado = mask_query(query, policy=_POLICY, config=_CONFIG)

    assert isinstance(resultado, RejectionReason)
    assert resultado.rule_id == "INTERNAL"


# =============================================================================
# Las ramas FAIL-CLOSED. Son las que deciden qué pasa cuando el contrato o el
# anillo anterior no dicen lo que deberían, y por eso son justo las que no pueden
# quedarse sin test: el camino feliz se ve enseguida, el de la excepción no.
# =============================================================================


def test_un_arbol_sin_select_se_devuelve_intacto() -> None:
    """No hay nada que enmascarar donde no hay proyección.

    Devolver la consulta tal cual es correcto y NO es una fuga: si algún día llegara
    aquí un árbol sin `SELECT`, no habría columnas de salida que proteger. Lo que
    sería un fallo es reventar, porque convertiría una consulta legal en un error.
    """
    query = _validada("SELECT country_code FROM dim_customer")
    sin_select = dataclasses.replace(query, ast=exp.Literal.number(1))

    assert mask_query(sin_select, policy=_POLICY, config=_CONFIG) is sin_select


def _politica_con(transformation: object, **extra: object):
    """Una política de un solo dato, para forzar las ramas del contrato roto."""
    fila: dict[str, object] = {
        "levels": {"admin": "allow", "analyst": "mask", "finance": "allow", "ops": "allow"},
        "transformation": transformation,
        "generalized": None,
        "keep_last_n": None,
        "derived_from": [],
    }
    fila.update(extra)
    return policy_from_dict(
        {
            "default_level": "allow",
            "deterministic_masking": True,
            "pepper_from": "config",
            "columns": {"dim_customer.first_name": fila},
        }
    )


@pytest.mark.parametrize(
    ("transformation", "extra"),
    [
        ("generalizar", {}),
        ("ultimos_n", {}),
        ("una_que_no_existe", {}),
        (None, {}),
    ],
    ids=["generalizar_sin_alternativa", "ultimos_n_sin_n", "desconocida", "sin_declarar"],
)
def test_una_fila_que_no_se_puede_enmascarar_se_rechaza_en_vez_de_mostrarse(
    transformation: object, extra: dict[str, object]
) -> None:
    """**La dirección de este fallo es la que importa.**

    Una columna declarada `mask` cuya fila no dice CÓMO enmascararla —porque falta
    la alternativa generalizada, porque falta el `keep_last_n`, o porque la
    transformación no existe— no se puede proteger. Ante eso solo hay dos salidas y
    una es inaceptable: mostrarla en claro. Se rechaza.

    Es la misma regla que gobierna el estimador de coste: ante la duda, la respuesta
    conservadora, nunca la cómoda.
    """
    query = _validada("SELECT first_name FROM dim_customer")

    resultado = mask_query(query, policy=_politica_con(transformation, **extra), config=_CONFIG)

    assert isinstance(resultado, RejectionReason)
    assert resultado.rule_id == "INTERNAL"
    assert resultado.code == "mask_transformation_missing"
    assert "dim_customer.first_name" in str(resultado.subject)
    assert resultado.retryable is False


def test_una_proyeccion_sin_alias_se_enmascara_y_conserva_su_nombre() -> None:
    """`SELECT first_name FROM ...` sin alias: la salida se sigue llamando igual.

    Reescribir la expresión obliga a ponerle nombre, porque `CASE WHEN ... END`
    no tiene uno. Si el nombre saliera generado por sqlglot, el resultset dejaría de
    ser comparable con su respuesta de referencia.
    """
    query = _validada("SELECT first_name FROM dim_customer")
    select = query.ast.find(exp.Select)
    assert select is not None
    # Se quita el alias que `qualify` pudiera haber puesto, para forzar la rama.
    select.set(
        "expressions",
        [p.this if isinstance(p, exp.Alias) else p for p in select.expressions],
    )

    resultado = mask_query(query, policy=_POLICY, config=_CONFIG)

    assert isinstance(resultado, ValidatedQuery)
    salida = _proyecciones(resultado)
    assert "first_name" in salida
    assert isinstance(salida["first_name"], exp.Case)
