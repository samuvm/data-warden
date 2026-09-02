"""La política de acceso, en memoria. Zona TDD obligatorio, cobertura 95 %.

FASE ROJA. Escritos contra `docs/spec/policy.yaml` firmado, no contra una
implementación imaginada.

**Ni un solo test de este fichero toca disco** (I-13): la política real se carga en
`tests/contract/`, que es donde tiene sentido comprobar que el contrato firmado y
el catálogo generado dicen lo mismo. Aquí se prueba la LÓGICA, y la lógica no
necesita 40 columnas para equivocarse.

Lo que se prueba es la idea entera del proyecto: **`mask` no es una escala de
confianza, es una escala de POSICIÓN EN EL ÁRBOL.** Una columna enmascarada es
legal en la proyección y prohibida en el `WHERE`, y esa asimetría es lo que cierra
el canal lateral por predicado.
"""

from __future__ import annotations

import pytest

from datawarden.domain.types import Position, Role
from datawarden.principal.policy import AccessPolicy, ColumnPolicy, Level, policy_from_dict

# Una política mínima con las cuatro formas que importan: una columna con máscara y
# alternativa publicada, una denegada del todo, una derivada de otras dos, y una
# excepción explícita para admin.
_FIXTURE: dict[str, object] = {
    "version": 1,
    "source": "fixture",
    "source_sha256": "0" * 64,
    "signed_by": "fixture",
    "signed_on": "2026-09-02",
    "roles": ["admin", "analyst", "finance", "ops"],
    "levels": ["allow", "mask", "deny"],
    "default_level": "allow",
    "deterministic_masking": True,
    "pepper_from": "config",
    "forbidden_positions_in_mask": [
        "where",
        "join_on",
        "group_by",
        "order_by",
        "having",
        "qualify",
        "argumento_de_funcion",
        "clave_de_particion_de_ventana",
    ],
    "excluded_from_catalog": ["dim_merchant.traffic_weight"],
    "columns": {
        "dim_customer.birth_date": {
            "levels": {"admin": "allow", "analyst": "mask", "finance": "deny", "ops": "deny"},
            "data_type": "cuasi_identificador",
            "generalized": "dim_customer.age_band",
            "transformation": "generalizar",
            "keep_last_n": None,
            "derived_from": [],
            "admin_exception": False,
            "published_in_catalog": True,
        },
        "dim_customer.age_band": {
            "levels": {
                "admin": "allow",
                "analyst": "allow",
                "finance": "allow",
                "ops": "allow",
            },
            "data_type": "derivada_generalizada",
            "generalized": None,
            "transformation": None,
            "keep_last_n": None,
            "derived_from": [],
            "admin_exception": False,
            "published_in_catalog": True,
        },
        "dim_customer.segment_code": {
            "levels": {
                "admin": "allow",
                "analyst": "allow",
                "finance": "allow",
                "ops": "allow",
            },
            "data_type": "derivada_no_identificativa",
            "generalized": None,
            "transformation": None,
            "keep_last_n": None,
            "derived_from": ["dim_customer.age_band", "dim_customer.birth_date"],
            "admin_exception": False,
            "published_in_catalog": True,
        },
        "dim_employee.salary_band": {
            "levels": {"admin": "deny", "analyst": "deny", "finance": "mask", "ops": "deny"},
            "data_type": "retribucion",
            "generalized": "dim_employee.org_level",
            "transformation": "tachar",
            "keep_last_n": None,
            "derived_from": [],
            "admin_exception": True,
            "published_in_catalog": True,
        },
        "dim_merchant.traffic_weight": {
            "levels": {"admin": "deny", "analyst": "deny", "finance": "deny", "ops": "deny"},
            "data_type": "artefacto_del_generador",
            "generalized": None,
            "transformation": None,
            "keep_last_n": None,
            "derived_from": [],
            "admin_exception": True,
            "published_in_catalog": False,
        },
    },
}


@pytest.fixture
def policy() -> AccessPolicy:
    return policy_from_dict(_FIXTURE)


# ------------------------------------------------------------------ niveles


def test_una_columna_sin_fila_es_allow_por_defecto(policy: AccessPolicy) -> None:
    """La matriz inventaría las columnas SENSIBLES, no las 300 del almacén.

    Denegar por defecto dejaría el sistema sin nada que responder. El límite —una
    columna sensible nueva que nadie clasifique se queda en `allow`— está declarado
    en `docs/spec/catalog-overlay.yaml` y va al modelo de amenaza.
    """
    assert policy.level_for("fact_payment_attempt.amount_minor", Role.ANALYST) is Level.ALLOW


def test_el_nivel_depende_del_rol_y_no_de_una_jerarquia(policy: AccessPolicy) -> None:
    """La asimetría que demuestra que la matriz no es una escala de confianza."""
    assert policy.level_for("dim_customer.birth_date", Role.ANALYST) is Level.MASK
    assert policy.level_for("dim_customer.birth_date", Role.OPS) is Level.DENY
    assert policy.level_for("dim_customer.birth_date", Role.ADMIN) is Level.ALLOW


def test_el_nombre_de_la_columna_no_distingue_mayusculas(policy: AccessPolicy) -> None:
    """SQL no las distingue, y el guard resuelve contra el catálogo cualificado."""
    assert policy.level_for("DIM_CUSTOMER.BIRTH_DATE", Role.OPS) is Level.DENY


# --------------------------------------------------- posición, no confianza


def test_una_columna_enmascarada_es_legal_en_la_proyeccion(policy: AccessPolicy) -> None:
    assert policy.is_position_allowed(
        "dim_customer.birth_date", Role.ANALYST, Position.PROJECTION
    )


@pytest.mark.parametrize(
    "position",
    [
        Position.WHERE,
        Position.JOIN_ON,
        Position.GROUP_BY,
        Position.ORDER_BY,
        Position.HAVING,
        Position.QUALIFY,
        Position.FUNCTION_ARGUMENT,
        Position.WINDOW_PARTITION,
    ],
)
def test_una_columna_enmascarada_esta_prohibida_en_las_ocho_posiciones(
    policy: AccessPolicy, position: Position
) -> None:
    """El canal lateral por predicado, cerrado posición a posición.

    `WHERE birth_date LIKE '1985%'` no devuelve la fecha y filtra por ella: quince
    consultas con distinto literal la fijan. Una máscara esconde el valor, no la
    respuesta.
    """
    assert not policy.is_position_allowed("dim_customer.birth_date", Role.ANALYST, position)


def test_una_columna_denegada_esta_prohibida_incluso_en_la_proyeccion(
    policy: AccessPolicy,
) -> None:
    assert not policy.is_position_allowed(
        "dim_customer.birth_date", Role.OPS, Position.PROJECTION
    )


def test_una_columna_permitida_es_legal_en_cualquier_posicion(policy: AccessPolicy) -> None:
    for position in Position:
        assert policy.is_position_allowed("dim_customer.age_band", Role.ANALYST, position)


# ------------------------------------------------------- salidas publicadas


def test_una_columna_enmascarada_publica_su_alternativa(policy: AccessPolicy) -> None:
    """Un rechazo sin salida no redirige el trabajo: lo bloquea (I-09)."""
    assert policy.generalized_for("dim_customer.birth_date") == "dim_customer.age_band"


def test_una_columna_sin_alternativa_lo_dice_con_none(policy: AccessPolicy) -> None:
    assert policy.generalized_for("dim_merchant.traffic_weight") is None


def test_la_transformacion_de_una_columna_enmascarada_esta_declarada(
    policy: AccessPolicy,
) -> None:
    assert policy.column("dim_customer.birth_date").transformation == "generalizar"


# ------------------------------------------- C-5 · la regla de composición


def test_una_columna_allow_no_puede_derivar_de_una_que_no_lo_es(policy: AccessPolicy) -> None:
    """C-5 de la firma de Q-003, y es la clase entera de bugs, no un caso.

    `segment_code` es `allow` para analyst y contiene `birth_date`, que para analyst
    es `mask`. Eso es un puente que rodea la política, y el test lo encuentra sin
    que nadie tenga que acordarse.
    """
    violaciones = policy.derivation_violations()
    assert any("segment_code" in v.derived and "birth_date" in v.source for v in violaciones)
    assert any(v.role is Role.ANALYST for v in violaciones)


def test_una_derivada_de_columnas_permitidas_no_es_violacion() -> None:
    limpia = dict(_FIXTURE)
    columnas = dict(_FIXTURE["columns"])  # type: ignore[arg-type]
    segment = dict(columnas["dim_customer.segment_code"])  # type: ignore[index]
    segment["derived_from"] = ["dim_customer.age_band"]
    columnas["dim_customer.segment_code"] = segment
    limpia["columns"] = columnas
    assert policy_from_dict(limpia).derivation_violations() == ()


def test_una_derivada_de_una_columna_inexistente_es_allow_y_no_revienta() -> None:
    """Una columna sin fila es `allow`: derivar de ella no es una violación."""
    suelta = dict(_FIXTURE)
    columnas = dict(_FIXTURE["columns"])  # type: ignore[arg-type]
    segment = dict(columnas["dim_customer.segment_code"])  # type: ignore[index]
    segment["derived_from"] = ["dim_customer.no_existe"]
    columnas["dim_customer.segment_code"] = segment
    suelta["columns"] = columnas
    assert policy_from_dict(suelta).derivation_violations() == ()


# ---------------------------------------------- C-2 · la excepción de admin


def test_la_excepcion_de_admin_es_un_dato_y_no_prosa(policy: AccessPolicy) -> None:
    """C-2. Si viviera en la justificación, un test de coherencia la «arreglaría»."""
    assert policy.column("dim_employee.salary_band").admin_exception is True
    assert policy.level_for("dim_employee.salary_band", Role.ADMIN) is Level.DENY


def test_las_columnas_que_admin_no_ve_estan_todas_declaradas(policy: AccessPolicy) -> None:
    """El invariante «admin lo ve todo» con sus excepciones EXPLÍCITAS.

    Sin este test, la primera comprobación de coherencia de la matriz marcaría
    `salary_band` como error y alguien lo «arreglaría» concediéndoselo a admin.
    """
    assert policy.undeclared_admin_denials() == ()


def test_una_denegacion_a_admin_sin_declarar_es_un_fallo() -> None:
    sucia = dict(_FIXTURE)
    columnas = dict(_FIXTURE["columns"])  # type: ignore[arg-type]
    fila = dict(columnas["dim_customer.age_band"])  # type: ignore[index]
    fila["levels"] = {"admin": "deny", "analyst": "allow", "finance": "allow", "ops": "allow"}
    columnas["dim_customer.age_band"] = fila
    sucia["columns"] = columnas
    assert policy_from_dict(sucia).undeclared_admin_denials() == ("dim_customer.age_band",)


# --------------------------------------------------------------- inventario


def test_la_politica_sabe_que_columnas_protege(policy: AccessPolicy) -> None:
    """Lo necesita el guard: recorrer 300 columnas por consulta no cabe en 25 ms."""
    protegidas = policy.restricted_columns(Role.ANALYST)
    assert "dim_customer.birth_date" in protegidas
    assert "dim_customer.age_band" not in protegidas


def test_la_politica_declara_de_que_fichero_firmado_sale(policy: AccessPolicy) -> None:
    """Un contrato compilado que no dice de dónde viene no se puede verificar."""
    assert policy.source_sha256 == "0" * 64
    assert policy.signed_by == "fixture"


def test_el_enmascarado_es_determinista_y_la_pimienta_no_es_por_sesion(
    policy: AccessPolicy,
) -> None:
    """Si la sal fuera por sesión, las preguntas del banco no tendrían referencia."""
    assert policy.deterministic_masking is True
    assert policy.pepper_from == "config"


def test_una_columna_desconocida_devuelve_una_politica_por_defecto(
    policy: AccessPolicy,
) -> None:
    por_defecto: ColumnPolicy = policy.column("fact_payment_attempt.amount_minor")
    assert por_defecto.levels[Role.ADMIN] is Level.ALLOW
    assert por_defecto.generalized is None
