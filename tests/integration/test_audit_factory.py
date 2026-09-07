"""La factoría del ejecutor auditado. Nivel 2 · DuckDB de verdad.

**Está aquí y no en `tests/unit` porque construye un motor**, y I-13 prohíbe el
motor en los unitarios. No es una formalidad: un test que se llame «integración» sin
tocar el motor mide una imitación, y lo que esta factoría tiene que garantizar es
justo lo que solo se ve con el motor delante.

**Por qué existe la factoría.** `datawarden.engines` solo puede importarse desde
`audit/` —lo impone el contrato «Al motor solo se llega por la auditoría (I-06)»—, y
`mcp/`, `http/` y el CLI necesitan un ejecutor. Si cada uno construyera el suyo
habría tres sitios desde los que llegar al motor sin pasar por la auditoría, que es
el atajo que I-06 existe para impedir.
"""

from __future__ import annotations

import pathlib

import pytest

from datawarden.audit.executor import AuditedExecutor
from datawarden.audit.factory import DEFAULT_AUDIT_DB, MissingDatasetError, build_executor
from datawarden.domain.types import Principal, Role, RoleSource
from datawarden.mask.config import MaskConfig

pytestmark = pytest.mark.integration

_DATABASE = pathlib.Path("datagen/out/cierzo-dev.duckdb")
_MASK = MaskConfig(pepper="pimienta-de-pruebas-de-treinta-y-dos-o-mas")


@pytest.fixture
def audit_db(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "audit.sqlite3"


def test_un_almacen_que_no_existe_no_se_inventa_vacio(tmp_path: pathlib.Path) -> None:
    """**Arrancar sin datos sería peor que no arrancar.**

    Un servidor que se levanta sin almacén convierte «no encuentro los datos» en «no
    hay filas», y quien pregunta no puede distinguirlas: recibiría un cero por
    respuesta a una pregunta cuya respuesta no es cero.
    """
    with pytest.raises(MissingDatasetError, match="make dataset"):
        build_executor(
            database=tmp_path / "no-existe.duckdb",
            audit_db=tmp_path / "audit.sqlite3",
            mask=_MASK,
        )


def test_construye_un_ejecutor_con_la_mascara_puesta(audit_db: pathlib.Path) -> None:
    """La máscara que recibe la factoría es la que acaba en el ejecutor.

    Si difirieran, el árbol reescrito llamaría a una macro que hashea con otra clave
    y los valores dejarían de ser estables entre ejecuciones — justo lo que el
    contrato firmado prohíbe al declarar `determinista: true`.
    """
    executor = build_executor(database=_DATABASE, audit_db=audit_db, mask=_MASK)

    assert isinstance(executor, AuditedExecutor)
    assert executor.mask is _MASK


def test_el_ejecutor_construido_enmascara_de_verdad(audit_db: pathlib.Path) -> None:
    """De punta a punta y contra datos: la macro que instala la factoría funciona.

    Es lo único que prueba que `setup_sql` y la máscara comparten pimienta. Con dos
    claves distintas, esto devolvería valores hasheados que nadie podría reproducir,
    o reventaría — y las dos cosas se descubrirían en producción.
    """
    executor = build_executor(database=_DATABASE, audit_db=audit_db, mask=_MASK)
    analyst = Principal(id="factoria", role=Role.ANALYST, source=RoleSource.SERVER_PROCESS)

    resultado = executor.run("SELECT first_name FROM dim_customer LIMIT 5", principal=analyst)

    assert resultado.rows is not None
    valores = [v for fila in resultado.rows.rows for v in fila if v is not None]
    assert valores and all(v == "***" for v in valores)
    assert resultado.query is not None
    assert resultado.query.masked_columns == ("dim_customer.first_name",)


def test_deja_su_registro_en_la_cadena_que_se_le_dijo(audit_db: pathlib.Path) -> None:
    """El almacén de auditoría se crea donde se pide, no donde le apetezca."""
    executor = build_executor(database=_DATABASE, audit_db=audit_db, mask=_MASK)
    analyst = Principal(id="factoria", role=Role.ANALYST, source=RoleSource.SERVER_PROCESS)

    executor.run("SELECT country_code FROM dim_customer LIMIT 3", principal=analyst)

    assert audit_db.exists()
    assert executor.store.count() == 1


def test_el_directorio_de_la_cadena_se_crea_solo(tmp_path: pathlib.Path) -> None:
    """`var/` no existe en un clon recién hecho, y el servidor tiene que arrancar igual."""
    anidado = tmp_path / "sin" / "crear" / "audit.sqlite3"

    build_executor(database=_DATABASE, audit_db=anidado, mask=_MASK)

    assert anidado.parent.is_dir()


def test_la_ruta_por_defecto_de_la_cadena_no_depende_del_directorio_de_trabajo() -> None:
    """**Una cadena que cambia de sitio no es una cadena.**

    Cuando el servidor lo lanza una aplicación de escritorio, el directorio de
    trabajo lo elige ella —Claude Desktop ignora el `cwd` de la configuración, y eso
    se descubrió en Q-009—. Con una ruta relativa, la auditoría aparecería en un
    sitio distinto según quién arrancara el proceso.
    """
    assert DEFAULT_AUDIT_DB.is_absolute()
    assert DEFAULT_AUDIT_DB.name == "audit.sqlite3"
    assert DEFAULT_AUDIT_DB.parent.name == "var"
