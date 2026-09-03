"""`G-ROLE-SPOOF` · el rol nunca viene de datos no autenticados. **AXIOMA.**

`propuesta_admisible: false`. Cero casos en que `_meta` o `arguments` alteren el
principal efectivo, y cero no admite negociación.

**Por qué esta prueba existe justo ahora.** La spec MCP 2026-07-28 eliminó las
sesiones. Sin sesión, el rol no puede venir de la sesión — y todo lo que el cliente
diga sobre sí mismo pasa a ser dato transportado: `_meta`, `arguments`, una cabecera
que él elige. Si cualquiera de los tres pudiera fijar el rol, el anillo 4 sería
ficción: bastaría un campo en el JSON para autoconcederse `admin` y leer nombres,
correos y DNI.

**Cómo se prueba, y es lo que hace que valga.** No se comprueba que exista una
comprobación: se ejecuta la petición envenenada **contra el sistema entero** y se
exige que devuelva **exactamente los mismos bytes** que la petición limpia del mismo
principal. Una afirmación sobre el resultado, no sobre el código.

El argumento del proyecto sale REFORZADO por la spec, no debilitado: «da igual qué
cliente se conecte, no puede saltarse las reglas» es *más* cierto sin sesiones,
porque el servidor es aún más claramente el único guardián.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from datawarden.audit.executor import AuditedExecutor
from datawarden.audit.store import AuditStore
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.catalog.statistics import Statistics, TableStats
from datawarden.domain.types import Principal, Role, RoleSource
from datawarden.engines.base import RecordingEngine
from datawarden.mask.config import MaskConfig
from datawarden.mcp.principal import ROLE_ENV, SPOOF_KEYS, from_server_process, spoof_attempts
from datawarden.mcp.server import WardenTools, dispatch
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy

pytestmark = pytest.mark.adversarial

_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_BUDGETS = load_budgets(BUDGETS_PATH)
_MASK = MaskConfig(pepper="pimienta-de-pruebas-de-treinta-y-dos-o-mas")
_BARATO = Statistics(
    profile="fixture",
    source="fixture",
    tables={
        "dim_customer": TableStats(
            name="dim_customer",
            rows=10,
            bytes=1_000,
            files=1,
            column_bytes={"first_name": 500},
        )
    },
)

#: Lo que un cliente mandaría para intentar subirse el rol. Salen de la spec: `_meta`
#: es un sobre libre y `arguments` lo compone el modelo, así que los dos son dato.
_ENVENENADOS = [
    pytest.param({"role": "admin"}, id="meta-role"),
    pytest.param({"rol": "admin"}, id="meta-rol-en-espanol"),
    pytest.param({"principal": "jefa", "roles": ["admin"]}, id="meta-principal-y-roles"),
    pytest.param({"scopes": ["read:pii"]}, id="meta-scopes"),
    pytest.param({"as_role": "admin"}, id="meta-as-role"),
]


def _herramientas(principal: Principal) -> WardenTools:
    ejecutor = AuditedExecutor(
        engine=RecordingEngine(rows=3),
        store=AuditStore(pathlib.Path(tempfile.mkdtemp()) / "spoof.sqlite"),
        schema=_SCHEMA,
        policy=_POLICY,
        budgets=_BUDGETS,
        stats=_BARATO,
        mask=_MASK,
    )
    return WardenTools(executor=ejecutor, principal=principal)


_ANALYST = Principal(id="cliente", role=Role.ANALYST, source=RoleSource.SERVER_PROCESS)


# ------------------------------------ 1 · el resultado no cambia, y es lo que vale ---


_LIMPIO = {"question_sql": "SELECT first_name FROM dim_customer"}


@pytest.mark.parametrize("veneno", _ENVENENADOS)
def test_por_meta_no_cambia_ni_un_byte_de_la_respuesta(veneno: dict[str, object]) -> None:
    """**La afirmación es sobre el RESULTADO, y se entra por donde entra el cliente.**

    Se despacha de verdad por `dispatch()`, que es el `tools/call` del servidor, con
    el sobre `_meta` envenenado. Comprobar «existe una comprobación» dejaría pasar el
    día que la comprobación mire la clave equivocada; comparar los bytes de las dos
    respuestas, no.
    """
    limpio = dispatch(_herramientas(_ANALYST), "run_query", dict(_LIMPIO))
    envenenado = dispatch(_herramientas(_ANALYST), "run_query", dict(_LIMPIO), meta=veneno)

    assert envenenado == limpio


@pytest.mark.parametrize("veneno", _ENVENENADOS)
def test_por_arguments_tampoco(veneno: dict[str, object]) -> None:
    """`arguments` lo compone un MODELO, así que es el dato menos de confianza que hay."""
    limpio = dispatch(_herramientas(_ANALYST), "run_query", dict(_LIMPIO))
    envenenado = dispatch(_herramientas(_ANALYST), "run_query", {**_LIMPIO, **veneno})

    assert envenenado == limpio


@pytest.mark.parametrize("veneno", _ENVENENADOS)
def test_lo_enmascarado_sigue_enmascarado_con_el_sobre_envenenado(
    veneno: dict[str, object],
) -> None:
    """Lo que de verdad se intenta comprar con la suplantación es ver la columna."""
    salida = dispatch(_herramientas(_ANALYST), "run_query", {**_LIMPIO, **veneno}, meta=veneno)

    assert salida["outcome"] == "rows"
    assert salida["result"]["columns_masked"] == ["dim_customer.first_name"]  # type: ignore[index]


def test_una_herramienta_que_no_existe_no_revienta_y_dice_cuales_hay() -> None:
    """El cliente es un modelo: un mensaje accionable le sirve, una excepción no."""
    salida = dispatch(_herramientas(_ANALYST), "os.system", {"cmd": "rm -rf /"})

    assert salida["outcome"] == "rejected"
    assert salida["rejected"]["code"] == "unknown_tool"  # type: ignore[index]
    assert "run_query" in salida["rejected"]["suggestion"]  # type: ignore[index]


def test_una_herramienta_no_admite_un_rol_en_su_firma() -> None:
    """**Un `role=` en la firma sería un agujero con forma de comodidad.**

    Mientras el parámetro no exista, no hay ruta por la que un argumento de tool
    llegue a la decisión de política. Se comprueba sobre la firma real y no sobre la
    documentación.
    """
    import inspect

    for nombre in ("run_query", "describe_table", "sample_table", "explain_cost"):
        firma = inspect.signature(getattr(WardenTools, nombre))
        prohibidos = SPOOF_KEYS & set(firma.parameters)
        assert prohibidos == set(), f"{nombre} admite {prohibidos} y eso es autoridad por dato"


def test_el_principal_se_fija_al_construir_y_no_por_llamada() -> None:
    admin = Principal(id="jefa", role=Role.ADMIN, source=RoleSource.PRINCIPAL_TOKEN)

    como_analyst = _herramientas(_ANALYST).run_query("SELECT first_name FROM dim_customer")
    como_admin = _herramientas(admin).run_query("SELECT first_name FROM dim_customer")

    assert como_analyst["result"]["columns_masked"] == ["dim_customer.first_name"]  # type: ignore[index]
    assert como_admin["result"]["columns_masked"] == []  # type: ignore[index]


# ------------------------------------------- 2 · el intento se DETECTA y se registra ---


@pytest.mark.parametrize("veneno", _ENVENENADOS)
def test_el_intento_de_suplantacion_se_detecta_para_auditarlo(
    veneno: dict[str, object],
) -> None:
    """**Detectar no es decidir.** El rol no se lee de aquí; esto lo convierte en señal.

    Sin esto, un intento de suplantación y una petición normal serían
    indistinguibles a posteriori, y quien sondea la política sistemáticamente es
    precisamente el que más interesa ver en el registro.
    """
    assert spoof_attempts(veneno) != ()


def test_una_peticion_limpia_no_dispara_ninguna_alarma() -> None:
    """Un detector que grita siempre se acaba desactivando."""
    assert spoof_attempts({"question": "cuantos clientes hay", "limit": 10}) == ()


def test_lo_que_no_es_un_diccionario_no_revienta_el_detector() -> None:
    """`_meta` puede llegar como `None` o como cualquier cosa: es dato del cliente."""
    assert spoof_attempts(None, [], "role=admin", 42) == ()


# ---------------------------------------------- 3 · de dónde SÍ puede venir el rol ---


def test_el_rol_del_proceso_sale_del_entorno_del_servidor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lo fija quien ARRANCA el servidor, igual que `psql` hereda el usuario del sistema."""
    monkeypatch.setenv(ROLE_ENV, "finance")

    quien = from_server_process()

    assert quien.role is Role.FINANCE
    assert quien.source is RoleSource.SERVER_PROCESS


@pytest.mark.parametrize("valor", ["", "   ", "superadmin", "ADMIN; DROP", "root"])
def test_un_rol_desconocido_en_el_entorno_cae_al_mas_restringido(
    valor: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Fail-closed también aquí.** Un valor raro no escala a `admin` ni revienta.

    Reventar sería casi tan malo: un servidor que no arranca por una variable con una
    errata acaba arrancándose con la variable quitada, y entonces nadie sabe con qué
    rol corre.
    """
    monkeypatch.setenv(ROLE_ENV, valor)

    assert from_server_process().role is Role.ANALYST


def test_el_rol_nunca_se_marca_como_dicho_por_el_cliente() -> None:
    """La primera mitad del invariante vive en el TIPO y conviene comprobarlo.

    `RoleSource` no tiene ningún valor que signifique «lo dijo el cliente», así que
    la suplantación no es que esté prohibida: es que no se puede escribir.
    """
    valores = {origen.value for origen in RoleSource}

    assert valores == {"server_process", "principal_token", "cli_flag"}
    assert not any("client" in v or "request" in v or "meta" in v for v in valores)
