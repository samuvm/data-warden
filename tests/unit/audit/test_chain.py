"""La cadena de auditoría: JCS, hash encadenado y verificación. Zona TDD, 95 %.

FASE ROJA. Escritos contra `docs/spec/audit-record.schema.json`, que está congelado
desde la fase 1, y no contra una implementación imaginada.

**Ni una línea de este fichero toca SQLite.** Lo de aquí es PURO: canonicalizar,
hashear y verificar. El almacén vive en `test_store.py` y se prueba contra
`:memory:`, que no es una comodidad sino una obligación: mutmut solo ejecuta
`tests/unit` y `tests/contract`, así que todo lo que solo cubra un test de
integración sale de la pasada marcado «sin tests» y cuenta como mutante VIVO.

Lo que se fija aquí es lo único que hace verificable una cadena: que dos
implementaciones distintas produzcan el MISMO hash para el mismo registro. Por eso
hay un VECTOR DORADO con su hash escrito a mano: sin él, «canonicalización» es una
palabra y no un acuerdo.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from datawarden.audit.chain import (
    GENESIS,
    SCHEMA_VERSION,
    AuditRecord,
    canonicalize,
    link,
    verify,
)
from datawarden.domain.types import Role, RoleSource, Status

_SCHEMA = json.loads(
    (pathlib.Path("docs/spec/audit-record.schema.json")).read_text(encoding="utf-8")
)


def _record(**cambios: object) -> AuditRecord:
    """Un registro mínimo y VÁLIDO, con los diez campos obligatorios."""
    base: dict[str, object] = {
        "seq": 1,
        "recorded_at": "2026-09-02T10:00:00.000000Z",
        "principal_id": "corpus-analyst",
        "role": Role.ANALYST,
        "role_source": RoleSource.CLI_FLAG,
        "status": Status.EXECUTED,
        "question_digest": "a" * 64,
        "sql_digest": "b" * 64,
        "prev_hash": GENESIS,
    }
    base.update(cambios)
    return AuditRecord(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------ canonicalización ---


def test_el_registro_canonico_no_lleva_el_hash_dentro() -> None:
    """El hash se calcula SOBRE el registro sin él: incluirlo sería circular."""
    assert "hash" not in json.loads(canonicalize(_record()))


def test_el_registro_canonico_lleva_prev_hash_y_la_version_del_esquema() -> None:
    """Las dos ENTRAN en el hash, y las dos por el mismo motivo.

    `prev_hash` es lo que encadena. `schema_version` está porque una cadena que
    mezcla versiones del contrato sin decirlo no se puede verificar: el
    verificador de mañana no sabría con qué reglas se escribió el registro de hoy.
    """
    payload = json.loads(canonicalize(_record()))
    assert payload["prev_hash"] == GENESIS
    assert payload["schema_version"] == SCHEMA_VERSION


def test_las_claves_salen_ordenadas_y_sin_un_solo_espacio() -> None:
    """JCS, RFC 8785. Sin esto el hash depende del orden de un diccionario."""
    canonico = canonicalize(_record())
    assert " " not in canonico
    assert "\n" not in canonico
    claves = list(json.loads(canonico))
    assert claves == sorted(claves)


def test_el_mismo_registro_canonicaliza_igual_aunque_se_construya_al_reves() -> None:
    """La propiedad entera de la canonicalización, en un test."""
    uno = _record(seq=7, principal_id="x")
    otro = _record(principal_id="x", seq=7)
    assert canonicalize(uno) == canonicalize(otro)


def test_las_tablas_y_las_columnas_enmascaradas_salen_ordenadas() -> None:
    """JCS ordena claves de OBJETO; jamás elementos de ARRAY.

    Así que el orden de estas dos listas lo tiene que garantizar el emisor. Si no,
    dos registros idénticos —mismo rol, misma consulta, mismas tablas— producen
    dos hashes distintos según en qué orden los recorrió sqlglot, y la cadena deja
    de ser comparable entre dos ejecuciones de la misma consulta.
    """
    r = _record(
        tables=("z_tabla", "a_tabla", "m_tabla"),
        columns_masked=("dim_customer.national_id", "dim_customer.birth_date"),
    )
    payload = json.loads(canonicalize(r))
    assert payload["tables"] == ["a_tabla", "m_tabla", "z_tabla"]
    assert payload["columns_masked"] == [
        "dim_customer.birth_date",
        "dim_customer.national_id",
    ]


def test_el_registro_canonico_valida_contra_el_contrato_congelado() -> None:
    """El contrato tiene `additionalProperties: false`: un campo de más lo rompe."""
    jsonschema = pytest.importorskip("jsonschema")
    payload = json.loads(canonicalize(_record()))
    payload["hash"] = "c" * 64  # el schema lo exige; la canonicalización no lo lleva
    jsonschema.validate(payload, _SCHEMA)


# --------------------------------------------------------------------- link ---


def test_el_hash_es_sha256_de_los_bytes_del_previo_mas_el_canonico() -> None:
    """La fórmula del contrato, comprobada contra `hashlib` a pelo.

    `prev_hash_bytes` son los 32 BYTES CRUDOS, no los 64 caracteres de su hex: el
    campo nombra los bytes DEL hash, no los de su representación. Da igual para la
    seguridad —`prev_hash` viaja además dentro del JCS— pero no da igual para la
    interoperabilidad, que es justo el miedo que el contrato declara.
    """
    r = _record()
    esperado = hashlib.sha256(
        bytes.fromhex(GENESIS) + canonicalize(r).encode("utf-8")
    ).hexdigest()
    assert link(r) == esperado


def test_el_hash_tiene_la_forma_que_el_contrato_exige() -> None:
    assert len(link(_record())) == 64
    assert all(c in "0123456789abcdef" for c in link(_record()))


def test_el_genesis_son_64_ceros_y_no_none() -> None:
    """«Un valor legal y distinguible en vez de `null`, para que el verificador no
    necesite un caso especial», dice el propio contrato."""
    assert GENESIS == "0" * 64


def test_cambiar_un_solo_byte_de_cualquier_campo_cambia_el_hash() -> None:
    """Lo que hace que la cadena valga algo. Un campo que no entrara en el hash
    sería un campo que se puede reescribir sin que nadie se entere."""
    base = link(_record())
    for campo, valor in (
        ("seq", 2),
        ("recorded_at", "2026-09-02T10:00:00.000001Z"),
        ("principal_id", "corpus-analystt"),
        ("role", Role.OPS),
        ("role_source", RoleSource.SERVER_PROCESS),
        ("status", Status.ERROR),
        ("question_digest", "a" * 63 + "b"),
        ("sql_digest", "b" * 63 + "c"),
        ("prev_hash", "0" * 63 + "1"),
    ):
        assert link(_record(**{campo: valor})) != base, (
            f"{campo} no entra en el hash: se puede cambiar sin romper la cadena"
        )


def test_dos_registros_iguales_producen_el_mismo_hash() -> None:
    assert link(_record()) == link(_record())


def test_vector_dorado_el_hash_de_un_registro_conocido() -> None:
    """**El test que convierte «canonicalización» en un acuerdo.**

    Sin un vector con su hash escrito, dos implementaciones del contrato pueden
    ser las dos «correctas» y producir cadenas que no se verifican entre sí, que
    es exactamente lo que la `description` del schema dice temer. Si este test
    falla, NO se actualiza el número: se investiga qué cambió, porque cambiarlo
    invalida toda cadena escrita hasta hoy.
    """
    r = _record()
    canonico = canonicalize(r)
    assert canonico == (
        '{"columns_masked":[],"prev_hash":"'
        + GENESIS
        + '","principal_id":"corpus-analyst","question_digest":"'
        + "a" * 64
        + '","recorded_at":"2026-09-02T10:00:00.000000Z","role":"analyst",'
        '"role_source":"cli_flag","schema_version":1,"seq":1,"sql_digest":"'
        + "b" * 64
        + '","status":"executed","tables":[]}'
    )


# -------------------------------------------------------------------- verify ---


def _cadena(n: int) -> list[tuple[AuditRecord, str]]:
    filas: list[tuple[AuditRecord, str]] = []
    previo = GENESIS
    for i in range(1, n + 1):
        r = _record(seq=i, prev_hash=previo, sql_digest=f"{i:064x}")
        h = link(r)
        filas.append((r, h))
        previo = h
    return filas


def test_una_cadena_intacta_se_verifica() -> None:
    ok, problema = verify(_cadena(5))
    assert ok is True
    assert problema is None


def test_una_cadena_vacia_se_verifica_y_no_revienta() -> None:
    """Un almacén recién creado no está corrupto: está vacío."""
    assert verify([]) == (True, None)


def test_un_registro_alterado_rompe_la_verificacion_y_dice_cual() -> None:
    """Decir el `seq` no es cortesía: es lo que hace útil a `warden audit verify`."""
    filas = _cadena(5)
    alterado = _record(seq=3, prev_hash=filas[2][0].prev_hash, principal_id="otro")
    filas[2] = (alterado, filas[2][1])

    ok, problema = verify(filas)

    assert ok is False
    assert problema is not None
    assert "3" in problema


def test_un_eslabon_roto_se_detecta_aunque_su_hash_sea_correcto() -> None:
    """El ataque real: reescribir un registro Y su hash. Lo delata el SIGUIENTE.

    Es el límite exacto de esta defensa y el que `docs/threat-model.md` tiene que
    escribir: detecta a quien no puede reescribir TODO lo posterior.
    """
    filas = _cadena(5)
    falso = _record(seq=3, prev_hash=filas[2][0].prev_hash, principal_id="intruso")
    filas[2] = (falso, link(falso))

    ok, problema = verify(filas)

    assert ok is False
    assert problema is not None


def test_una_cadena_que_no_empieza_en_el_genesis_no_se_verifica() -> None:
    """Truncar la cadena por delante es borrar historia, y tiene que notarse."""
    filas = _cadena(5)[2:]

    ok, problema = verify(filas)

    assert ok is False
    assert problema is not None


def test_un_seq_que_salta_no_se_verifica() -> None:
    """Un hueco en la numeración es un registro borrado."""
    filas = _cadena(5)
    del filas[2]

    ok, _ = verify(filas)

    assert ok is False
