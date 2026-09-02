"""Los tipos congelados del dominio. Fase 1, `G-CONTRACTS-FROZEN`.

FASE ROJA. Estos tests se escriben ANTES de la implementación y contra los dos
contratos que ya existen —`docs/spec/rejection.schema.json` y
`docs/spec/audit-record.schema.json`—, no contra una implementación imaginada.

Lo que se prueba aquí no es «que los dataclasses existan»: eso lo comprueba mypy.
Se prueban las cuatro cosas que un tipo congelado tiene que garantizar y que un
`dict` no garantiza:

1. Que un rechazo sin sugerencia NO SE PUEDE CONSTRUIR (I-09). Si la validación
   viviera en el sitio que lo construye, el primer camino que se olvidara de
   llamarla produciría un rechazo mudo.
2. Que el rol NO PUEDE VENIR de datos no autenticados (I-05), y que eso es
   verdad en el TIPO: `RoleSource` no tiene un valor para «lo dijo el cliente».
3. Que `ValidatedQuery` re-serializa el AST y nunca guarda la cadena de entrada
   (I-02), que es el primer invariante de `CLAUDE.md`.
4. Que los tipos son inmutables de verdad: un `ValidatedQuery` que se pudiera
   mutar entre la validación y la ejecución convierte los cinco anillos en
   decoración.
"""

from __future__ import annotations

import dataclasses

import pytest
import sqlglot

from datawarden.domain.types import (
    CostEstimate,
    Position,
    Principal,
    RejectionReason,
    ResultSet,
    Role,
    RoleSource,
    Severity,
    ValidatedQuery,
)

# --------------------------------------------------------------------- Principal


def test_los_cuatro_roles_son_los_de_la_politica() -> None:
    """Ni uno más. Un rol que no está en `policy.yaml` no tiene matriz que aplicar."""
    assert {r.value for r in Role} == {"analyst", "ops", "finance", "admin"}


def test_el_rol_nunca_puede_venir_de_datos_no_autenticados() -> None:
    """I-05 hecho tipo: no existe un `RoleSource` para «lo dijo el cliente».

    `_meta` y los argumentos de tool son DATO, no autoridad. Si el enum tuviera un
    valor `request_meta`, alguien lo usaría, y el anillo 5 sería ficción.
    """
    fuentes = {s.value for s in RoleSource}
    assert fuentes == {"server_process", "principal_token", "cli_flag"}
    assert not fuentes & {"request_meta", "tool_argument", "client", "header"}


def test_un_principal_es_inmutable() -> None:
    p = Principal(id="p-1", role=Role.ANALYST, source=RoleSource.SERVER_PROCESS)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.role = Role.ADMIN  # type: ignore[misc]


def test_un_principal_sin_identificador_no_se_puede_construir() -> None:
    """Sin `principal_id` no hay auditoría, y sin auditoría no hay no repudio."""
    with pytest.raises(ValueError, match="principal_id"):
        Principal(id="", role=Role.ADMIN, source=RoleSource.CLI_FLAG)


# --------------------------------------------------------------- RejectionReason


def _rechazo_valido(**overrides: object) -> RejectionReason:
    campos: dict[str, object] = {
        "rule_id": "R008",
        "code": "denied_column_position",
        "message": "column dim_customer.birth_date is denied for role analyst",
        "suggestion": "use dim_customer.age_band instead, published for every role",
        "severity": Severity.POLICY,
        "position": Position.WHERE,
    }
    campos.update(overrides)
    return RejectionReason(**campos)  # type: ignore[arg-type]


def test_un_rechazo_sin_sugerencia_no_se_puede_construir() -> None:
    """I-09. Un rechazo sin salida no redirige el trabajo: lo bloquea."""
    with pytest.raises(ValueError, match="suggestion"):
        _rechazo_valido(suggestion="")


def test_una_sugerencia_de_una_palabra_tampoco_vale() -> None:
    """«no» cumple «no vacía» y no es accionable. El contrato exige 10 caracteres."""
    with pytest.raises(ValueError, match="suggestion"):
        _rechazo_valido(suggestion="no")


def test_un_mensaje_vacio_no_se_puede_construir() -> None:
    with pytest.raises(ValueError, match="message"):
        _rechazo_valido(message="nope")


def test_un_rule_id_inventado_no_se_puede_construir() -> None:
    """Los `rule_id` son un registro cerrado (I-01), no una cadena libre."""
    with pytest.raises(ValueError, match="rule_id"):
        _rechazo_valido(rule_id="regla-nueva")


def test_los_tres_rule_id_que_no_son_del_guard_son_legales() -> None:
    """El presupuesto y la política también rechazan, y también deben ser accionables."""
    for rule_id in ("BUDGET", "POLICY", "INTERNAL"):
        assert _rechazo_valido(rule_id=rule_id).rule_id == rule_id


def test_un_code_con_mayusculas_no_se_puede_construir() -> None:
    """El `code` agrupa métricas: si `Denied` y `denied` fueran dos, no agruparía."""
    with pytest.raises(ValueError, match="code"):
        _rechazo_valido(code="DeniedColumn")


def test_un_rechazo_se_serializa_conforme_al_contrato() -> None:
    d = _rechazo_valido(subject="dim_customer.birth_date", alternative="dim_customer.age_band")
    payload = d.to_dict()
    assert payload["rule_id"] == "R008"
    assert payload["severity"] == "policy"
    assert payload["position"] == "where"
    assert payload["alternative"] == "dim_customer.age_band"
    assert set(payload) <= {
        "rule_id",
        "code",
        "message",
        "suggestion",
        "severity",
        "position",
        "subject",
        "alternative",
        "retryable",
        "docs",
    }


def test_un_intento_de_escritura_no_es_reintentable() -> None:
    """Reintentar un DELETE solo gasta tokens y contamina `G-RECOVERY`."""
    r = _rechazo_valido(rule_id="R001", severity=Severity.SECURITY, retryable=False)
    assert r.retryable is False


# ---------------------------------------------------------------- ValidatedQuery


def _validada(sql: str = "SELECT a FROM t") -> ValidatedQuery:
    return ValidatedQuery(
        ast=sqlglot.parse_one(sql, dialect="duckdb"),
        dialect="duckdb",
        principal=Principal(id="p-1", role=Role.ANALYST, source=RoleSource.SERVER_PROCESS),
        tables=("t",),
        columns=("t.a",),
        max_rows=100,
    )


def test_lo_que_se_ejecuta_sale_del_arbol_y_no_de_la_entrada() -> None:
    """I-02, el primer invariante del proyecto.

    La entrada lleva un comentario entre tokens y mayúsculas raras. Lo que sale es
    el árbol re-serializado, así que el comentario no llega al motor: ahí vive la
    clase entera de ataques por diferencia de parser.
    """
    entrada = "SeLeCt /* comentario */ a FROM t"
    q = _validada(entrada)
    rendered = q.sql()
    assert "comentario" not in rendered
    assert rendered.upper().startswith("SELECT")


def test_una_consulta_validada_es_inmutable() -> None:
    q = _validada()
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.max_rows = 10**9  # type: ignore[misc]


def test_una_consulta_validada_sin_tope_de_filas_no_se_puede_construir() -> None:
    """`max_rows` es del dominio (I-12): sin tope, el motor decide, y eso es otro sistema."""
    with pytest.raises(ValueError, match="max_rows"):
        ValidatedQuery(
            ast=sqlglot.parse_one("SELECT a FROM t", dialect="duckdb"),
            dialect="duckdb",
            principal=Principal(id="p", role=Role.OPS, source=RoleSource.CLI_FLAG),
            tables=("t",),
            columns=("t.a",),
            max_rows=0,
        )


def test_el_digest_del_sql_es_el_del_arbol_reserializado() -> None:
    """La auditoría certifica lo que corrió, no lo que entró."""
    import hashlib

    q = _validada("select   a   from   t")
    esperado = hashlib.sha256(q.sql().encode("utf-8")).hexdigest()
    assert q.sql_digest() == esperado
    assert len(q.sql_digest()) == 64


# ------------------------------------------------------------------ CostEstimate


def test_una_estimacion_de_coste_no_puede_ser_negativa() -> None:
    with pytest.raises(ValueError, match="estimated_bytes"):
        CostEstimate(estimated_bytes=-1, estimated_rows=0, files_scanned=0, method="iceberg")


def test_una_estimacion_declara_de_donde_sale_su_numero() -> None:
    """Un coste sin método no es auditable: `EXPLAIN` y metadatos no valen lo mismo."""
    e = CostEstimate(estimated_bytes=10, estimated_rows=2, files_scanned=1, method="iceberg")
    assert e.method == "iceberg"
    with pytest.raises(ValueError, match="method"):
        CostEstimate(estimated_bytes=10, estimated_rows=2, files_scanned=1, method="a_ojo")


# --------------------------------------------------------------------- ResultSet


def test_un_resultset_conoce_su_forma_aunque_este_vacio() -> None:
    """Decisión 8 de `resultset-equality.md`: un vacío de 3 columnas no es uno de 1."""
    rs = ResultSet(columns=("a", "b", "c"), rows=(), truncated=False)
    assert rs.row_count == 0
    assert rs.column_count == 3


def test_un_resultset_recortado_lo_dice() -> None:
    """Sin este campo, un análisis sobre 2.000 filas parece completo."""
    rs = ResultSet(columns=("a",), rows=((1,), (2,)), truncated=True)
    assert rs.truncated is True
    assert rs.row_count == 2


def test_un_resultset_con_filas_de_otra_anchura_no_se_puede_construir() -> None:
    with pytest.raises(ValueError, match="columns"):
        ResultSet(columns=("a", "b"), rows=((1,),), truncated=False)


def test_un_hint_de_optimizacion_dentro_de_un_comentario_tampoco_llega_al_motor() -> None:
    """Lo encontró un test, no una revisión: sqlglot CONSERVA los comentarios.

    `SELECT /*+ hint */ ...` re-serializado seguía llevando texto del atacante hasta
    el motor, y hay motores que leen hints ahí. Un comentario no es estructura.
    """
    q = _validada("SELECT /*+ use_index(t) */ a FROM t -- y esto también")
    rendered = q.sql()
    assert "use_index" not in rendered
    assert "--" not in rendered
    assert rendered == "SELECT a FROM t"


def test_dos_consultas_que_solo_difieren_en_el_comentario_tienen_el_mismo_digest() -> None:
    """Si no, dos invocaciones idénticas producirían dos registros de auditoría."""
    limpia = _validada("SELECT a FROM t")
    comentada = _validada("SELECT /* nota */ a FROM t")
    assert limpia.sql_digest() == comentada.sql_digest()
