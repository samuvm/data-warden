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

import json

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


# =============================================================================
# Añadido el 2026-09-02. `principal.policy` tenía 48 mutantes vivos de 146
# (67,12 %) y 46 de ellos vivían en `policy_from_dict`. Los tests de arriba
# prueban la IDEA —`mask` es una escala de posición en el árbol— y son los que
# importan; lo que nadie asertaba era el PARSEO. Y en una política de acceso el
# parseo no es fontanería: **cada valor por defecto es una decisión de seguridad
# tomada para el caso en que el contrato venga incompleto**, y la dirección de
# esos defectos es lo único que separa «fallar cerrado» de «fallar abierto».
# =============================================================================


def _payload(**cambios: object) -> dict[str, object]:
    """Copia honda del fixture, para que un test no contamine al siguiente."""
    p = json.loads(json.dumps(_FIXTURE))
    p.update(cambios)
    return p


# --------------------------------------------- los defectos, uno por uno ---


def test_sin_default_level_declarado_la_politica_es_allow() -> None:
    """Y es correcto, aunque suene al revés.

    `default_level` gobierna las columnas SIN fila en la política, y esas son las
    ~40.000 columnas anodinas del almacén. Si el defecto fuera `deny`, el sistema
    bloquearía todo lo que nadie clasificó y se desactivaría en tres semanas —que
    es el fallo que `docs/spec/policy.yaml` nombra con todas sus letras—. Lo que
    protege no es este defecto: es que las columnas sensibles SÍ tienen fila.
    """
    p = _payload()
    del p["default_level"]

    assert policy_from_dict(p).default_level is Level.ALLOW


def test_una_columna_se_publica_en_el_catalogo_salvo_que_se_diga_lo_contrario() -> None:
    """`published_in_catalog` por defecto `True`; `admin_exception` por defecto
    `False`. Las dos direcciones son deliberadas y son opuestas.

    Publicar de más solo revela un NOMBRE de columna, y el nombre ya está en el
    esquema. Conceder una excepción de admin de más revela DATOS. Ante la duda se
    publica el nombre y no se concede el privilegio.
    """
    p = _payload()
    for spec in p["columns"].values():
        spec.pop("published_in_catalog", None)
        spec.pop("admin_exception", None)

    politica = policy_from_dict(p)
    columna = politica.column("dim_customer.birth_date")

    assert columna.published_in_catalog is True
    assert columna.admin_exception is False


@pytest.mark.parametrize("campo", ["data_type", "generalized", "transformation", "keep_last_n"])
def test_los_campos_descriptivos_ausentes_quedan_en_none_y_no_en_cadena_vacia(
    campo: str,
) -> None:
    """`None` significa «no declarado» y `''` significaría «declarado vacío».

    La fase 4 va a ramificar sobre `transformation`: con `None` no hay
    transformación y hay que rechazar o tachar; con `''` habría una transformación
    llamada «» y el reescritor la buscaría en su tabla.
    """
    p = _payload()
    del p["columns"]["dim_customer.birth_date"][campo]

    assert getattr(policy_from_dict(p).column("dim_customer.birth_date"), campo) is None


def test_una_columna_sin_derived_from_no_deriva_de_nada() -> None:
    """Tupla vacía, no `None`: `derivation_violations()` la recorre sin comprobar."""
    p = _payload()
    del p["columns"]["dim_customer.birth_date"]["derived_from"]

    assert policy_from_dict(p).column("dim_customer.birth_date").derived_from == ()


def test_sin_declararlo_el_enmascarado_no_se_da_por_determinista() -> None:
    """Defecto `False`, y esta dirección sí es la conservadora.

    `deterministic_masking: True` es una PROMESA: que el mismo valor produce el
    mismo hash entre ejecuciones, que es lo que permite escribir las respuestas de
    referencia del banco de 60 sobre columnas enmascaradas. Darla por hecha porque
    falta una línea del contrato es prometer lo que nadie firmó.
    """
    p = _payload()
    del p["deterministic_masking"]
    del p["pepper_from"]

    politica = policy_from_dict(p)

    assert politica.deterministic_masking is False
    assert politica.pepper_from == ""


def test_una_politica_sin_firma_se_carga_pero_lo_declara() -> None:
    """No revienta —el compilador puede producirla— pero no finge estar firmada."""
    p = _payload()
    del p["signed_by"]
    del p["source_sha256"]

    politica = policy_from_dict(p)

    assert politica.signed_by is None
    assert politica.source_sha256 == ""


# ------------------------------------------------------- normalizaciones ---


def test_el_nombre_de_la_columna_se_normaliza_al_cargar_y_no_al_consultar() -> None:
    """El contrato lo escribe un humano; el índice vive en minúsculas.

    Si la normalización solo estuviera en la consulta, una fila escrita
    `DIM_CUSTOMER.Birth_Date` crearía una entrada que ninguna consulta encuentra:
    la columna quedaría con la política POR DEFECTO, o sea `allow`, y una columna
    sensible pasaría a ser visible por un error de mayúsculas.
    """
    p = _payload()
    spec = p["columns"].pop("dim_customer.birth_date")
    p["columns"]["DIM_Customer.Birth_Date"] = spec

    politica = policy_from_dict(p)

    assert politica.level_for("dim_customer.birth_date", Role.ANALYST) is Level.MASK
    assert "dim_customer.birth_date" in politica.columns


def test_las_fuentes_de_una_derivada_tambien_se_normalizan() -> None:
    """Sin esto, `derivation_violations()` no encontraría su propia fuente y una
    columna derivada de una protegida saldría limpia."""
    p = _payload()
    p["columns"]["dim_customer.age_band"]["derived_from"] = ["DIM_CUSTOMER.BIRTH_DATE"]

    columna = policy_from_dict(p).column("dim_customer.age_band")

    assert columna.derived_from == ("dim_customer.birth_date",)


def test_las_columnas_excluidas_del_catalogo_se_normalizan() -> None:
    p = _payload(excluded_from_catalog=["DIM_MERCHANT.Traffic_Weight"])

    assert "dim_merchant.traffic_weight" in policy_from_dict(p).excluded_from_catalog


# ------------------------------------------------- posiciones prohibidas ---


def test_una_posicion_prohibida_que_no_se_reconoce_se_ignora_en_vez_de_reventar() -> None:
    """El filtro `if p in _POSITION_ALIASES` es deliberado y conviene verlo.

    Una posición inventada en el contrato NO tumba la carga: se ignora. Es la
    decisión correcta —el contrato puede nombrar una posición de una versión
    futura— pero deja una arista: **una posición mal escrita deja de estar
    prohibida en silencio.** Lo que la tapa es el test de las OCHO posiciones de
    arriba, que falla si la lista se queda corta.
    """
    p = _payload(forbidden_positions_in_mask=["where", "no_existe_esta_posicion"])

    politica = policy_from_dict(p)

    assert len(politica.forbidden_positions_in_mask) == 1


def test_sin_lista_de_posiciones_no_hay_ninguna_prohibida() -> None:
    """Y por eso la lista de ocho es parte del contrato firmado, no un defecto
    del código: aquí el defecto es el conjunto vacío, que es permisivo."""
    p = _payload()
    del p["forbidden_positions_in_mask"]

    assert policy_from_dict(p).forbidden_positions_in_mask == frozenset()


# ------------------------------------------------- el índice de restringidas ---


def test_el_indice_de_restringidas_incluye_mask_y_deny_pero_no_allow() -> None:
    """`is not Level.ALLOW`, no `is Level.DENY`.

    Es la comparación que decide qué columnas vigila el guard para un rol. Si
    mirara solo `deny`, las columnas `mask` saldrían del índice y el canal lateral
    por predicado —`WHERE birth_date = ...`, que no muestra el dato pero lo
    adivina— quedaría abierto para el rol que más consulta.
    """
    politica = policy_from_dict(_FIXTURE)

    restringidas = politica.restricted_columns(Role.ANALYST)

    # `birth_date` es `mask` para analyst: tiene que estar.
    assert "dim_customer.birth_date" in restringidas
    # `age_band` es su alternativa publicada y es `allow`: no puede estar.
    assert "dim_customer.age_band" not in restringidas


def test_cada_rol_tiene_su_propio_indice_de_restringidas() -> None:
    """La política no es una jerarquía: `finance` no ve lo que `analyst` no ve."""
    politica = policy_from_dict(_FIXTURE)

    por_rol = {rol: politica.restricted_columns(rol) for rol in Role}

    assert set(por_rol) == set(Role)
    # `birth_date` es `deny` para finance y `mask` para analyst: restringida en
    # ambos, pero por motivos distintos y con permisos distintos.
    assert "dim_customer.birth_date" in por_rol[Role.FINANCE]
    assert "dim_customer.birth_date" in por_rol[Role.ANALYST]


@pytest.mark.parametrize("rol", ["admin", "analyst", "finance", "ops"])
def test_falta_el_nivel_de_un_rol_en_una_columna_y_la_carga_falla(rol: str) -> None:
    """Los cuatro niveles son obligatorios en cada fila.

    Un `spec["levels"].get(rol, "allow")` sería el peor mutante de este módulo:
    una fila a la que le faltara un rol quedaría permitida para él, en silencio, y
    la columna protegida se vería. Se recorre `Role` y se indexa, no se pregunta.
    """
    p = _payload()
    del p["columns"]["dim_customer.birth_date"]["levels"][rol]

    with pytest.raises(KeyError):
        policy_from_dict(p)


def test_una_politica_sin_columnas_no_se_puede_cargar() -> None:
    """`payload["columns"]` se indexa, no se pide con defecto.

    Una política vacía es sintácticamente válida y semánticamente catastrófica:
    ninguna columna restringida, todo `allow`, y el guard en verde.
    """
    p = _payload()
    del p["columns"]

    with pytest.raises(KeyError):
        policy_from_dict(p)
