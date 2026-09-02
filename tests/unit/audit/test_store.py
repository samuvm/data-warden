"""El almacén de auditoría: SQLite en WAL y APPEND-ONLY. Zona TDD, 95 %.

FASE ROJA.

**Todo esto corre contra `:memory:`, y no es una comodidad.** `pyproject.toml` fija
`pytest_add_cli_args_test_selection = ["tests/unit", "tests/contract"]`, así que
mutmut NO ejecuta `tests/integration`: cualquier mutante de este módulo que solo
cubriera un test de integración saldría marcado «sin tests» y contaría como VIVO.
Con `G-MUTATION` recién cruzado por cuatro décimas, escribir la lógica del almacén
y probarla solo en integración habría vuelto a tumbar la meta.

A `tests/integration/` va únicamente lo que EXIGE un fichero real: que el modo WAL
deje sus sidecars, y que la cadena sobreviva a cerrar y reabrir.
"""

from __future__ import annotations

import sqlite3

import pytest

from datawarden.audit.chain import GENESIS, link
from datawarden.audit.store import AuditStore
from datawarden.domain.types import Role, RoleSource, Status


def _campos(**cambios: object) -> dict[str, object]:
    base: dict[str, object] = {
        "principal_id": "corpus-analyst",
        "role": Role.ANALYST,
        "role_source": RoleSource.CLI_FLAG,
        "status": Status.EXECUTED,
        "question_digest": "a" * 64,
        "sql_digest": "b" * 64,
    }
    base.update(cambios)
    return base


@pytest.fixture
def store() -> AuditStore:
    return AuditStore(":memory:")


# ------------------------------------------------------------------ escritura ---


def test_el_primer_registro_arranca_en_el_genesis(store: AuditStore) -> None:
    """Sin caso especial: el primero encadena con 64 ceros como cualquier otro."""
    fila = store.append(**_campos())

    assert fila.seq == 1
    assert fila.record.prev_hash == GENESIS
    assert fila.chain_hash == link(fila.record)


def test_el_seq_lo_asigna_el_almacen_y_es_consecutivo(store: AuditStore) -> None:
    """No lo elige quien llama. Un `seq` de fuera se puede repetir o saltar, y el
    hueco en la numeración es indistinguible de un registro borrado."""
    seqs = [store.append(**_campos()).seq for _ in range(5)]

    assert seqs == [1, 2, 3, 4, 5]


def test_cada_registro_encadena_con_el_hash_del_anterior(store: AuditStore) -> None:
    primero = store.append(**_campos())
    segundo = store.append(**_campos(sql_digest="c" * 64))

    assert segundo.record.prev_hash == primero.chain_hash


def test_el_almacen_verifica_su_propia_cadena(store: AuditStore) -> None:
    for i in range(6):
        store.append(**_campos(sql_digest=f"{i:064x}"))

    assert store.verify() == (True, None)


def test_un_almacen_vacio_se_verifica(store: AuditStore) -> None:
    """Recién creado no está corrupto: está vacío."""
    assert store.verify() == (True, None)


def test_el_registro_vuelve_del_disco_igual_que_entro(store: AuditStore) -> None:
    """La vuelta completa importa porque `verify()` RECALCULA el hash desde los
    campos leídos: si un campo se pierde o cambia de tipo al volver, el hash no
    cuadra y la cadena se declararía rota sin que nadie la hubiera tocado."""
    store.append(
        **_campos(
            tables=("fact_payment_attempt", "dim_customer"),
            columns_masked=("dim_customer.birth_date",),
            estimated_bytes=1234,
            row_count=7,
            truncated=False,
            duration_ms=12.5,
            engine="duckdb",
            trace_id="0af7651916cd43dd8448eb211c80319c",
        )
    )

    ((recuperado, _),) = store.rows()

    assert recuperado.tables == ("dim_customer", "fact_payment_attempt")
    assert recuperado.columns_masked == ("dim_customer.birth_date",)
    assert recuperado.estimated_bytes == 1234
    assert recuperado.row_count == 7
    assert recuperado.truncated is False
    assert recuperado.duration_ms == 12.5
    assert recuperado.engine == "duckdb"
    assert recuperado.trace_id == "0af7651916cd43dd8448eb211c80319c"
    assert store.verify() == (True, None)


def test_se_auditan_los_cuatro_estados_y_no_solo_los_ejecutados(
    store: AuditStore,
) -> None:
    """`G-AUDIT-COV` es un AXIOMA y dice esto: se audita toda invocación.

    Un rechazo del guard es un evento de seguridad. No registrarlo sería el peor
    fallo posible de este sistema, porque el atacante que sondea la política
    sistemáticamente no dejaría ni una línea.
    """
    for estado in Status:
        store.append(**_campos(status=estado))

    assert [f.record.status for f in store.rows_as_entries()] == list(Status)


def test_la_marca_de_tiempo_lleva_la_z_final_que_el_contrato_exige(store: AuditStore) -> None:
    """`isoformat()` produce `+00:00` y NO casa el `pattern: "Z$"` del schema.

    Es la trampa que más fácil se cuela: la suite tiene `filterwarnings = error`,
    así que `utcnow()` —que sí da la Z— revienta por DeprecationWarning, y el
    sustituto obvio devuelve el formato equivocado.
    """
    marca = store.append(**_campos()).record.recorded_at

    assert marca.endswith("Z")
    assert "+00:00" not in marca


# ----------------------------------------------------------------- append-only ---


def test_no_se_puede_actualizar_una_fila_ya_escrita(store: AuditStore) -> None:
    """Append-only impuesto por el MOTOR, no por la disciplina de quien llama.

    Si el append-only viviera solo en el código de este módulo, el primer script
    que abriera el fichero con `sqlite3` se lo saltaría. El trigger viaja con los
    datos.
    """
    store.append(**_campos())

    with pytest.raises(sqlite3.DatabaseError):
        store.connection.execute("UPDATE audit_log SET principal_id = 'otro'")


def test_no_se_puede_borrar_una_fila_ya_escrita(store: AuditStore) -> None:
    store.append(**_campos())

    with pytest.raises(sqlite3.DatabaseError):
        store.connection.execute("DELETE FROM audit_log")


def test_el_trigger_nombra_el_motivo_y_no_solo_falla(store: AuditStore) -> None:
    """Quien se lo encuentre tiene que entender que es deliberado."""
    store.append(**_campos())

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.connection.execute("DELETE FROM audit_log")


# -------------------------------------------------------------- manipulación ---


def test_un_campo_alterado_a_mano_rompe_la_verificacion(store: AuditStore) -> None:
    """El escenario que la cadena existe para atrapar, ejecutado de verdad.

    Se salta el trigger a propósito —se desactiva y se reescribe— porque el
    atacante que importa es justo el que puede hacerlo. Lo que se comprueba es que
    aun así la cadena lo delata.
    """
    for i in range(4):
        store.append(**_campos(sql_digest=f"{i:064x}"))

    store.connection.executescript(
        "PRAGMA writable_schema = OFF;"
        "DROP TRIGGER audit_log_no_update;"
        "UPDATE audit_log SET principal_id = 'intruso' WHERE seq = 2;"
    )

    ok, problema = store.verify()

    assert ok is False
    assert problema is not None
    assert "2" in problema


def test_una_fila_borrada_de_en_medio_rompe_la_verificacion(store: AuditStore) -> None:
    for i in range(4):
        store.append(**_campos(sql_digest=f"{i:064x}"))

    store.connection.executescript(
        "DROP TRIGGER audit_log_no_delete; DELETE FROM audit_log WHERE seq = 2;"
    )

    ok, _ = store.verify()

    assert ok is False


# ------------------------------------------------------------------ contadores ---


def test_el_almacen_cuenta_por_estado(store: AuditStore) -> None:
    """Es lo que `warden audit reconcile` compara contra el contador del motor."""
    store.append(**_campos(status=Status.EXECUTED))
    store.append(**_campos(status=Status.EXECUTED))
    store.append(**_campos(status=Status.REJECTED_BY_GUARD))

    conteo = store.count_by_status()

    assert conteo[Status.EXECUTED] == 2
    assert conteo[Status.REJECTED_BY_GUARD] == 1
    assert conteo[Status.ERROR] == 0


def test_el_almacen_dice_cuantos_registros_tiene(store: AuditStore) -> None:
    assert store.count() == 0
    store.append(**_campos())
    assert store.count() == 1


def test_cerrar_el_almacen_suelta_la_conexion(tmp_path) -> None:
    """`close()` existe para que un proceso largo no se quede con el fichero abierto.

    Se prueba sobre un fichero real y no sobre `:memory:` porque cerrar una base en
    memoria la DESTRUYE, y entonces el test comprobaría otra cosa: aquí lo que se
    fija es que la conexión queda inutilizable, no que los datos desaparezcan.
    """
    destino = tmp_path / "audit.sqlite3"
    store = AuditStore(str(destino))
    store.append(**_campos())

    store.close()

    with pytest.raises(sqlite3.ProgrammingError):
        store.connection.execute("SELECT 1")
    # Y el fichero sigue ahí con su registro: cerrar no es borrar.
    assert AuditStore(str(destino)).count() == 1
