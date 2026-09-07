"""Los dos transportes dan LO MISMO. Nivel 3, contrato.

**Esta es la prueba de que `http/` sirve para lo que dice servir.** `docs/PLAN.md` lo
pone antes que `mcp/` en la fase 7 con un argumento: si las mismas cuatro operaciones se
sirven por dos transportes sin tocar `guard/`, `cost/`, `mask/` ni `audit/`, entonces
«el dominio no depende del transporte» deja de ser una frase del README.

Pero la frase solo vale si se comprueba, y se comprueba de la única forma que significa
algo: **misma pregunta por los dos caminos, misma respuesta byte a byte.** Mirar que
los dos importan `service/` sería mirar el código; esto mira el resultado.

Y hay una segunda mitad, que es la que se rompería primero: **ninguno de los dos
transportes importa al otro.** El día que `http` necesite algo de `mcp`, la
demostración se cae aunque las respuestas sigan coincidiendo — porque entonces habría
un transporte y un envoltorio, no dos transportes.
"""

from __future__ import annotations

import pathlib

import pytest

from datawarden.domain.types import Principal, Role, RoleSource
from datawarden.evalsupport.resultset_equality import Table, compare
from datawarden.mcp.server import dispatch
from datawarden.service.tools import WardenTools

pytestmark = pytest.mark.integration

_DATABASE = pathlib.Path("datagen/out/cierzo-dev.duckdb")
_PEPPER = "pimienta-de-pruebas-de-treinta-y-dos-o-mas"
_ANALYST = Principal(id="paridad", role=Role.ANALYST, source=RoleSource.SERVER_PROCESS)

#: Las cuatro preguntas que cubren los cuatro desenlaces: filas, descubrimiento,
#: rechazo por política y rechazo no reintentable.
_CASOS = [
    pytest.param(
        "run_query",
        {
            "question_sql": "SELECT country_code, count(*) AS n FROM dim_customer"
            " GROUP BY country_code"
        },
        id="filas",
    ),
    pytest.param("describe_table", {}, id="descubrimiento"),
    pytest.param(
        "run_query",
        {
            "question_sql": "SELECT customer_sk FROM dim_customer"
            " WHERE birth_date < '1990-01-01'"
        },
        id="rechazo-de-politica",
    ),
    pytest.param("run_query", {"question_sql": "DELETE FROM dim_customer"}, id="escritura"),
]


def _como_resultset(payload: object) -> Table:
    """La respuesta de un transporte, en la forma que compara el contrato.

    Es la `Table` de `resultset_equality` y no la `ResultSet` del dominio: el
    comparador necesita los NOMBRES de columna para poder alinear, y la decisión 8 de
    la especificación exige comparar el número de columnas incluso entre dos vacíos.
    """
    cuerpo = payload["result"]  # type: ignore[index]
    return Table(
        columns=tuple(cuerpo["columns"]),
        rows=[tuple(fila) for fila in cuerpo["rows"]],
    )


def _por_mcp(nombre: str, argumentos: dict[str, object], audit: pathlib.Path) -> object:
    from datawarden.audit.factory import build_executor
    from datawarden.mask.config import MaskConfig

    tools = WardenTools(
        executor=build_executor(
            database=_DATABASE, audit_db=audit, mask=MaskConfig(pepper=_PEPPER)
        ),
        principal=_ANALYST,
    )
    return dispatch(tools, nombre, dict(argumentos))


def _por_http(nombre: str, argumentos: dict[str, object], audit: pathlib.Path) -> object:
    from fastapi.testclient import TestClient

    from datawarden.http.app import build_app
    from datawarden.mask.config import MaskConfig

    cliente = TestClient(
        build_app(
            database=_DATABASE,
            audit_db=audit,
            principal=_ANALYST,
            mask=MaskConfig(pepper=_PEPPER),
        )
    )
    if nombre == "describe_table" and not argumentos:
        return cliente.get("/catalog").json()
    if nombre == "describe_table":
        return cliente.get(f"/tables/{argumentos['table']}").json()
    return cliente.post("/query", json=dict(argumentos)).json()


@pytest.mark.parametrize(("herramienta", "argumentos"), _CASOS)
def test_los_dos_transportes_responden_lo_mismo(
    herramienta: str, argumentos: dict[str, object], tmp_path: pathlib.Path
) -> None:
    """**Se compara con `resultset_equality`, no con `==`.**

    La primera versión de este test comparaba los diccionarios crudos y falló en el
    caso de las filas — no porque los transportes difirieran, sino porque la consulta
    no lleva `ORDER BY` y DuckDB no garantiza el orden sin él. El test estaba mal, no
    el código.

    Es exactamente lo que `docs/spec/resultset-equality.md` existe para evitar, y por
    lo que I-11 prohíbe asertar sobre la cadena de SQL: **la verdad se establece sobre
    el resultset normalizado.** Usar aquí el mismo contrato que usará la fase 8 es
    además la forma de que ese contrato se ejercite antes de que dependa de él una
    métrica insignia.
    """
    por_mcp = _por_mcp(herramienta, argumentos, tmp_path / "mcp.sqlite3")
    por_http = _por_http(herramienta, argumentos, tmp_path / "http.sqlite3")

    assert por_mcp["outcome"] == por_http["outcome"]  # type: ignore[index]
    if por_mcp["outcome"] == "rejected":  # type: ignore[index]
        # Un rechazo no tiene filas: se compara entero, que ahí sí es determinista.
        assert por_mcp == por_http
        return

    veredicto = compare(_como_resultset(por_mcp), _como_resultset(por_http))
    assert veredicto.equal, veredicto.reason


def test_un_rechazo_no_es_un_error_de_http(tmp_path: pathlib.Path) -> None:
    """200 con `outcome: rejected`, no un 4xx.

    Un rechazo del guard es la respuesta más valiosa que da este sistema: trae la
    regla, el motivo y la alternativa. Devolverlo como error del protocolo haría que
    un cliente lo reintentara a ciegas o lo tratara como una caída.
    """
    from fastapi.testclient import TestClient

    from datawarden.http.app import build_app
    from datawarden.mask.config import MaskConfig

    cliente = TestClient(
        build_app(
            database=_DATABASE,
            audit_db=tmp_path / "a.sqlite3",
            principal=_ANALYST,
            mask=MaskConfig(pepper=_PEPPER),
        )
    )

    respuesta = cliente.post("/query", json={"question_sql": "DELETE FROM dim_customer"})

    assert respuesta.status_code == 200
    assert respuesta.json()["outcome"] == "rejected"
    assert respuesta.json()["rejected"]["rule_id"] == "R010"


def test_ninguno_de_los_dos_transportes_importa_al_otro() -> None:
    """**La demostración se cae si uno depende del otro**, aunque coincidan.

    Entonces habría un transporte y un envoltorio, no dos transportes. Se mira el
    código fuente y no los módulos cargados: un import dentro de una función también
    cuenta, y esos no aparecen en `sys.modules` hasta que se llaman.
    """
    raiz = pathlib.Path(__file__).resolve().parents[2] / "src" / "datawarden"

    http_src = (raiz / "http" / "app.py").read_text(encoding="utf-8")
    mcp_src = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((raiz / "mcp").glob("*.py"))
    )

    assert "datawarden.mcp" not in http_src
    assert "datawarden.http" not in mcp_src
