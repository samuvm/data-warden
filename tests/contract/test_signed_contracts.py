"""Los contratos FIRMADOS y el catálogo GENERADO dicen lo mismo. Nivel 3.

Estos tests sí tocan disco, y por eso están aquí y no en `tests/unit` (I-13). Lo
que comprueban no es lógica: es que los artefactos reales del repositorio son
coherentes entre sí. Es la clase de fallo que ninguna prueba unitaria puede ver
porque no depende del código, depende de que alguien se haya acordado.

Corre en el gate B, cuesta milisegundos y es donde más barato sale detectar que la
política protege una columna que el catálogo ya no tiene.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import Role
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import Level, load_policy

SPEC = SCHEMA_PATH.parents[4] / "docs" / "spec"


@pytest.fixture(scope="module")
def policy():
    return load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def catalog():
    return load_generated(SCHEMA_PATH)


# --------------------------------------------------- la política y el catálogo


def test_toda_columna_de_la_politica_existe_en_el_catalogo(policy, catalog) -> None:
    """I-07. Proteger una columna que ya no existe es cobertura imaginaria."""
    faltan = [
        ref
        for ref in policy.columns
        if (t := catalog.table(ref.split(".", 1)[0])) is None
        or t.column(ref.split(".", 1)[1]) is None
    ]
    assert faltan == []


def test_toda_columna_generalizada_publicada_existe(policy, catalog) -> None:
    """Una alternativa que no existe convierte el mensaje accionable en una mentira."""
    rotas = []
    for ref, spec in policy.columns.items():
        if spec.generalized is None:
            continue
        table_name, _, column = spec.generalized.partition(".")
        table = catalog.table(table_name)
        if table is None or table.column(column) is None:
            rotas.append(f"{ref} -> {spec.generalized}")
    assert rotas == []


def test_toda_columna_generalizada_es_visible_para_quien_la_necesita(policy) -> None:
    """Sugerir `age_band` a un rol que tampoco puede verla no redirige el trabajo."""
    inutiles = []
    for ref, spec in policy.columns.items():
        if spec.generalized is None:
            continue
        for role in Role:
            if spec.levels[role] is Level.ALLOW:
                continue
            if policy.level_for(spec.generalized, role) is not Level.ALLOW:
                inutiles.append(f"{ref} · {role.value} -> {spec.generalized}")
    assert inutiles == []


def test_la_regla_de_composicion_se_cumple_hoy(policy) -> None:
    """C-5 de la firma de Q-003, sobre la matriz REAL y no sobre un fixture."""
    assert policy.derivation_violations() == ()


def test_toda_excepcion_al_invariante_de_admin_esta_declarada(policy) -> None:
    """C-2. Las dos que hay están firmadas; una tercera sin declarar es un fallo."""
    assert policy.undeclared_admin_denials() == ()


def test_la_politica_esta_firmada_por_samuel(policy) -> None:
    assert policy.signed_by == "Samuel"
    assert len(policy.source_sha256) == 64


# ------------------------------------------------------------- el catálogo


def test_el_catalogo_valida_contra_su_contrato() -> None:
    schema = json.loads((SPEC / "catalog.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")), schema)


def test_el_catalogo_publicado_no_lleva_las_columnas_excluidas(policy, catalog) -> None:
    """C-3: si `traffic_weight` se publicara, el generador la propondría."""
    publicado = catalog.published()
    for ref in policy.excluded_from_catalog:
        table_name, _, column = ref.partition(".")
        table = publicado.table(table_name)
        assert table is not None
        assert table.column(column) is None


def test_toda_columna_del_catalogo_declara_de_donde_sale(catalog) -> None:
    """Sin linaje, una vista es un rodeo a la política."""
    sin_linaje = [
        f"{t.name}.{c.name}" for t in catalog.tables for c in t.columns if not c.derives_from
    ]
    assert sin_linaje == []


def test_las_vistas_que_reexponen_pii_lo_declaran_en_su_linaje(catalog) -> None:
    """El caso concreto que motivó el módulo, comprobado sobre el catálogo real."""
    v_customer = catalog.table("v_customer")
    assert v_customer is not None
    assert v_customer.column("birth_date").derives_from == ("dim_customer.birth_date",)
    assert set(v_customer.column("full_name").derives_from) == {
        "dim_customer.first_name",
        "dim_customer.last_name_1",
        "dim_customer.last_name_2",
    }


def test_el_linaje_sin_resolver_esta_acotado_y_declarado(catalog) -> None:
    """Un límite contado vale más que un límite escondido, y este se puede contar.

    Si crece, es que alguien añadió una vista que sqlglot no sabe seguir, y eso
    hay que verlo cuando pasa y no en la fase 8.
    """
    sin_resolver = [
        f"{t.name}.{c.name}"
        for t in catalog.tables
        for c in t.columns
        if not c.lineage_resolved
    ]
    assert sorted(sin_resolver) == [
        "v_group_ultimate_parent.group_sk",
        "v_group_ultimate_parent.hops_to_ultimate",
        "v_group_ultimate_parent.ultimate_group_sk",
        "v_money_flow.hops_to_ultimate",
    ]


def test_una_columna_de_linaje_desconocido_arrastra_todo_lo_que_hay_debajo(catalog) -> None:
    """Fail-closed con puntería: el cierre de dependencias, no un `deny` a ciegas."""
    columna = catalog.table("v_group_ultimate_parent").column("group_sk")
    assert "dim_corporate_group.payout_iban" in columna.derives_from


# ------------------------------------------------------------ presupuestos


def test_los_cuatro_presupuestos_cargan_y_ops_es_el_mas_estrecho() -> None:
    book = load_budgets(BUDGETS_PATH)
    assert book.for_role(Role.OPS).hard_bytes == 50_000_000
    assert book.for_role(Role.OPS).max_rows == 2_000
    assert all(book.for_role(r).soft_bytes <= book.for_role(r).hard_bytes for r in Role)


def test_los_presupuestos_blandos_se_declaran_sin_calibrar() -> None:
    """Publicar un `soft` inventado como si estuviera medido es una mentira silenciosa."""
    book = load_budgets(BUDGETS_PATH)
    assert all(not book.for_role(r).soft_is_calibrated for r in Role)
