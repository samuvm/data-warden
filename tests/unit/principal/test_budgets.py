"""Los presupuestos por rol. `docs/spec/budgets.yaml`, zona TDD obligatorio.

FASE ROJA. En memoria: la carga del fichero real vive en `tests/contract/` (I-13).

Lo que se prueba es que los TRES números son tres decisiones distintas y no «el
presupuesto» dicho tres veces: el blando pide confirmación, el duro no ejecuta, y
`max_rows` recorta en el dominio y nunca en el motor (I-12).
"""

from __future__ import annotations

import json

import pytest

from datawarden.domain.types import Role
from datawarden.principal.budgets import BudgetBook, Decision, budgets_from_dict

_FIXTURE: dict[str, object] = {
    "version": 1,
    "source": "fixture",
    "source_sha256": "0" * 64,
    "unit_base": 1_000_000_000,
    "measures": "bytes_escaneados_estimados",
    "roles": {
        "analyst": {
            "soft_bytes": 300_000_000,
            "hard_bytes": 600_000_000,
            "soft_gb": 0.30,
            "hard_gb": 0.60,
            "max_rows": 50_000,
            "soft_is_calibrated": False,
        },
        "ops": {
            "soft_bytes": 20_000_000,
            "hard_bytes": 50_000_000,
            "soft_gb": 0.02,
            "hard_gb": 0.05,
            "max_rows": 2_000,
            "soft_is_calibrated": False,
        },
        "finance": {
            "soft_bytes": 400_000_000,
            "hard_bytes": 800_000_000,
            "soft_gb": 0.40,
            "hard_gb": 0.80,
            "max_rows": 100_000,
            "soft_is_calibrated": False,
        },
        "admin": {
            "soft_bytes": 750_000_000,
            "hard_bytes": 1_500_000_000,
            "soft_gb": 0.75,
            "hard_gb": 1.50,
            "max_rows": 250_000,
            "soft_is_calibrated": False,
        },
    },
}


@pytest.fixture
def book() -> BudgetBook:
    return budgets_from_dict(_FIXTURE)


def test_los_cuatro_roles_tienen_presupuesto(book: BudgetBook) -> None:
    """Un rol sin presupuesto ejecutaría sin límite, que es peor que no tener rol."""
    for role in Role:
        assert book.for_role(role).hard_bytes > 0


def test_por_debajo_del_blando_se_ejecuta_sin_preguntar(book: BudgetBook) -> None:
    assert book.decide(Role.ANALYST, estimated_bytes=1_000) is Decision.EXECUTE


def test_entre_el_blando_y_el_duro_se_pide_confirmacion(book: BudgetBook) -> None:
    """Un guardián que solo sabe decir «no» se desactiva en tres semanas."""
    assert book.decide(Role.ANALYST, estimated_bytes=450_000_000) is Decision.CONFIRM


def test_por_encima_del_duro_no_se_ejecuta(book: BudgetBook) -> None:
    """`G-BUDGET-ESCAPE` es un axioma: cero consultas caras llegan al motor."""
    assert book.decide(Role.ANALYST, estimated_bytes=600_000_001) is Decision.REJECT


def test_el_limite_duro_es_inclusivo(book: BudgetBook) -> None:
    """Exactamente el presupuesto SÍ se ejecuta; el axioma es «por encima»."""
    assert book.decide(Role.ANALYST, estimated_bytes=600_000_000) is Decision.CONFIRM


def test_ops_es_el_mas_estrecho_y_es_deliberado(book: BudgetBook) -> None:
    """Firmado así: ops mira un pago concreto; si necesita más, no es su rol."""
    assert book.for_role(Role.OPS).hard_bytes < book.for_role(Role.ANALYST).hard_bytes
    assert book.for_role(Role.OPS).max_rows == 2_000


def test_el_tope_de_filas_lo_pone_el_dominio(book: BudgetBook) -> None:
    """I-12: ningún engine aplica límites propios."""
    assert book.max_rows(Role.FINANCE) == 100_000


def test_un_presupuesto_sin_calibrar_lo_declara(book: BudgetBook) -> None:
    """Publicar un `soft` inventado como si estuviera medido es una mentira silenciosa."""
    assert book.for_role(Role.ADMIN).soft_is_calibrated is False


def test_una_estimacion_negativa_no_se_acepta(book: BudgetBook) -> None:
    with pytest.raises(ValueError, match="estimated_bytes"):
        book.decide(Role.OPS, estimated_bytes=-1)


def test_un_blando_por_encima_del_duro_no_se_puede_cargar() -> None:
    """Un aviso que salta después del rechazo no avisa de nada."""
    roto = {**_FIXTURE, "roles": {**_FIXTURE["roles"]}}  # type: ignore[dict-item]
    roto["roles"] = dict(_FIXTURE["roles"])  # type: ignore[arg-type]
    roto["roles"]["ops"] = {**roto["roles"]["ops"], "soft_bytes": 10**12}  # type: ignore[index]
    with pytest.raises(ValueError, match="soft"):
        budgets_from_dict(roto)


def test_falta_un_rol_y_se_dice_cual() -> None:
    incompleto = dict(_FIXTURE)
    incompleto["roles"] = {k: v for k, v in _FIXTURE["roles"].items() if k != "finance"}  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="finance"):
        budgets_from_dict(incompleto)


# =============================================================================
# Añadido el 2026-09-02. `principal.budgets` tenía 30 mutantes vivos de 67
# (55,22 %) y 25 de ellos estaban en `budgets_from_dict`. Los tests de arriba
# prueban las DECISIONES —blando, duro, `max_rows`— y son los correctos; lo que
# nadie asertaba era el PARSEO, que es donde un presupuesto se afloja sin que
# ninguna decisión cambie de forma: un `int()` que se cae, un valor por defecto
# que se invierte, un campo obligatorio que pasa a opcional.
# =============================================================================


def test_los_tres_numeros_del_presupuesto_se_convierten_a_entero() -> None:
    """JSON no distingue `1` de `1.0`, y un `float` en los bytes es un límite
    que compara mal en los bordes.

    `budgets.yaml` lo firma un humano y lo compila un script. Los `int()` de
    `budgets_from_dict` son la frontera donde eso se normaliza, y son la razón de
    que `test_el_limite_duro_es_inclusivo` signifique algo.
    """
    payload = json.loads(json.dumps(_FIXTURE))
    payload["roles"]["analyst"].update(
        {"soft_bytes": 300_000_000.0, "hard_bytes": "600000000", "max_rows": 50_000.0}
    )

    presupuesto = budgets_from_dict(payload).budgets[Role.ANALYST]

    assert presupuesto.soft_bytes == 300_000_000
    assert presupuesto.hard_bytes == 600_000_000
    assert presupuesto.max_rows == 50_000
    assert isinstance(presupuesto.soft_bytes, int)
    assert isinstance(presupuesto.hard_bytes, int)
    assert isinstance(presupuesto.max_rows, int)


@pytest.mark.parametrize("campo", ["soft_bytes", "hard_bytes", "max_rows"])
def test_los_tres_numeros_son_obligatorios_y_no_tienen_defecto(campo: str) -> None:
    """Ninguno puede degradar a un valor por defecto, y menos a cero o a infinito.

    Un `row.get("hard_bytes", 0)` rechazaría todo —molesto pero seguro— y un
    `row.get("hard_bytes", sys.maxsize)` ejecutaría todo, que es exactamente el
    agujero que `G-BUDGET-ESCAPE` existe para cerrar. La única respuesta correcta
    a un presupuesto incompleto es no cargarlo.
    """
    payload = json.loads(json.dumps(_FIXTURE))
    del payload["roles"]["analyst"][campo]

    with pytest.raises(KeyError):
        budgets_from_dict(payload)


def test_un_presupuesto_se_declara_sin_calibrar_mientras_no_diga_lo_contrario() -> None:
    """El defecto es `False`, y esa dirección es la honesta.

    `soft_gb` sigue sin calibrar en los cuatro roles —`budgets.yaml` lo dice con
    `origen_soft: agente_por_calibrar`—. Si el defecto fuera `True`, un rol al que
    se le olvidara el campo se publicaría como calibrado sin haberlo estado, y eso
    es un número que miente en el README.
    """
    payload = json.loads(json.dumps(_FIXTURE))
    del payload["roles"]["analyst"]["soft_is_calibrated"]

    assert budgets_from_dict(payload).budgets[Role.ANALYST].soft_is_calibrated is False


def test_el_libro_arrastra_el_sha_del_fichero_firmado() -> None:
    """Sin procedencia no se puede auditar de qué `budgets.yaml` salió el número."""
    assert budgets_from_dict(_FIXTURE).source_sha256 == "0" * 64


def test_un_libro_sin_sha_se_carga_con_la_procedencia_vacia_y_no_revienta() -> None:
    """Vacío, nunca `None`: el campo se publica en el registro de auditoría."""
    payload = json.loads(json.dumps(_FIXTURE))
    del payload["source_sha256"]

    assert budgets_from_dict(payload).source_sha256 == ""


def test_un_blando_igual_al_duro_es_legal() -> None:
    """El borde exacto de la comprobación: se prohíbe `soft > hard`, no `soft == hard`.

    Un rol cuyo aviso coincida con su rechazo es un rol sin aviso, pero es una
    configuración coherente y legítima. Un mutante que cambie `>` por `>=` la
    prohibiría, y aquí es donde se nota.
    """
    payload = json.loads(json.dumps(_FIXTURE))
    payload["roles"]["ops"]["soft_bytes"] = payload["roles"]["ops"]["hard_bytes"]

    libro = budgets_from_dict(payload)

    assert libro.budgets[Role.OPS].soft_bytes == libro.budgets[Role.OPS].hard_bytes


@pytest.mark.parametrize("rol", ["admin", "analyst", "finance", "ops"])
def test_falta_cualquiera_de_los_cuatro_roles_y_se_dice_cual(rol: str) -> None:
    """El mensaje nombra al rol: un fallo que no dice cuál obliga a adivinar."""
    payload = json.loads(json.dumps(_FIXTURE))
    del payload["roles"][rol]

    with pytest.raises(ValueError, match=rol):
        budgets_from_dict(payload)


def test_un_rol_de_mas_en_el_fichero_no_entra_en_el_libro() -> None:
    """Se recorre `Role`, no las claves del fichero.

    Al revés —recorrer el fichero— un rol inventado en `budgets.yaml` crearía un
    presupuesto para un principal que el dominio no reconoce, y el rol es lo único
    que este sistema no acepta de datos no autenticados.
    """
    payload = json.loads(json.dumps(_FIXTURE))
    payload["roles"]["intruso"] = {
        "soft_bytes": 10**15,
        "hard_bytes": 10**15,
        "max_rows": 10**9,
    }

    libro = budgets_from_dict(payload)

    assert set(libro.budgets) == set(Role)
