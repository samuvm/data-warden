"""`G-AUDIT-TAMPER` · **la cadena detecta cualquier manipulación posterior.**

Separada de `G-AUDIT-COV` a propósito: cobertura e integridad son dos propiedades
distintas. Una cadena completa que se puede reescribir en silencio no vale nada, y
una cadena infalsificable a la que le faltan registros tampoco.

**El umbral adicional pide >= 1.000 mutaciones de byte inyectadas**, y aquí se
inyectan de verdad: se recorre el JSON canónico de un registro **byte a byte**, se
altera cada posición, y se exige que la cadena lo detecte en todas. No es una
muestra: es la superficie entera del registro.

Detectar significa una de dos cosas, y las dos son detección:

1. **El registro alterado ya no se puede reconstruir** — el JSON deja de parsear, o
   un enum deja de tener ese valor. Un registro corrupto es un registro rechazado.
2. **El hash deja de cuadrar** — se reconstruye bien pero `verify()` lo delata.

Lo que NO se prueba aquí, porque no es cierto y `docs/threat-model.md` lo dice: que
la cadena resista a quien puede reescribirla ENTERA. El hash encadenado detecta a
quien no puede rehacer todo lo posterior. Ese límite es del diseño, no del test.
"""

from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from datawarden.audit.chain import GENESIS, AuditRecord, canonicalize, link, verify
from datawarden.domain.types import Role, RoleSource, Status

_SETTINGS = settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _record(seq: int, prev: str, **cambios: object) -> AuditRecord:
    base: dict[str, object] = {
        "seq": seq,
        "recorded_at": f"2026-09-02T10:00:{seq:02d}.000000Z",
        "principal_id": "prop-analyst",
        "role": Role.ANALYST,
        "role_source": RoleSource.CLI_FLAG,
        "status": Status.EXECUTED,
        "question_digest": f"{seq:064x}",
        "sql_digest": f"{seq + 100:064x}",
        "prev_hash": prev,
        "tables": ("dim_customer",),
        "columns_masked": ("dim_customer.birth_date",),
    }
    base.update(cambios)
    return AuditRecord(**base)  # type: ignore[arg-type]


def _cadena(n: int) -> list[tuple[AuditRecord, str]]:
    filas: list[tuple[AuditRecord, str]] = []
    previo = GENESIS
    for i in range(1, n + 1):
        r = _record(i, previo)
        h = link(r)
        filas.append((r, h))
        previo = h
    return filas


def _desde_payload(payload: dict[str, object]) -> AuditRecord | None:
    """Reconstruye el registro desde un payload alterado, o `None` si ya no es uno."""
    campos = dict(payload)
    try:
        campos["role"] = Role(campos["role"])
        campos["role_source"] = RoleSource(campos["role_source"])
        campos["status"] = Status(campos["status"])
        campos["tables"] = tuple(campos.get("tables") or ())
        campos["columns_masked"] = tuple(campos.get("columns_masked") or ())
        return AuditRecord(**campos)  # type: ignore[arg-type]
    except (ValueError, TypeError, KeyError):
        return None


#: Los bytes que se prueban en cada posición. Tres y no 256 porque el objetivo del
#: umbral es cubrir toda la SUPERFICIE del registro, no todo el alfabeto: una
#: posición que se detecta con un byte se detecta con los 256, y multiplicar por 256
#: convierte una propiedad de segundos en una de minutos que alguien acabará
#: bajando de ejemplos, que es justo lo que el umbral existe para impedir.
_SUSTITUTOS = ("0", "z", "~")


def _mutaciones_de_byte(record: AuditRecord) -> list[str]:
    """Toda posición del JSON canónico, alterada. Devuelve los payloads mutados."""
    canonico = canonicalize(record)
    mutados: list[str] = []
    for i, original in enumerate(canonico):
        for sustituto in _SUSTITUTOS:
            if sustituto == original:
                continue
            mutados.append(canonico[:i] + sustituto + canonico[i + 1 :])
    return mutados


def test_mil_mutaciones_de_byte_y_la_cadena_las_detecta_todas() -> None:
    """**El número que el gate lee.** Se inyectan >= 1.000 y se detectan todas.

    Se muta el registro del medio de la cadena, que es el caso que importa: al
    primero le falla el génesis y al último no le sigue nadie, mientras que uno de
    en medio tiene que ser cazado por su propio hash Y por el `prev_hash` del
    siguiente. Si solo funcionara una de las dos comprobaciones, este test seguiría
    pasando; por eso hay además la propiedad de abajo, que quita el hash de la
    ecuación.
    """
    filas = _cadena(5)
    objetivo = 2
    original, hash_original = filas[objetivo]

    inyectadas = 0
    for payload_mutado in _mutaciones_de_byte(original):
        inyectadas += 1
        try:
            payload = json.loads(payload_mutado)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue  # JSON roto: detectado, el registro no se reconstruye
        if not isinstance(payload, dict):
            continue
        alterado = _desde_payload(payload)
        if alterado is None:
            continue  # el registro ya no es válido: detectado
        candidata = list(filas)
        candidata[objetivo] = (alterado, hash_original)
        ok, _ = verify(candidata)
        assert ok is False, f"una mutación de byte pasó sin detectarse: {payload_mutado[:160]}"

    assert inyectadas >= 1000, (
        f"solo se inyectaron {inyectadas} mutaciones y el umbral adicional de "
        "G-AUDIT-TAMPER exige >= 1.000. Un número que no llega al umbral no lo "
        "cumple aunque todas se detecten."
    )


@_SETTINGS
@given(
    posicion=st.integers(min_value=0, max_value=4),
    nuevo_id=st.text(min_size=1, max_size=40).filter(lambda s: s.strip()),
)
def test_reescribir_un_registro_y_su_hash_lo_delata_el_siguiente(
    posicion: int, nuevo_id: str
) -> None:
    """El ataque REAL, y el que justifica que exista un encadenado.

    Un atacante que edita un registro no deja el hash viejo: lo recalcula. Contra
    eso el hash propio no dice nada —cuadra— y quien lo delata es el `prev_hash`
    del registro siguiente, que ya no coincide.

    El último de la cadena es la excepción y **es el límite honesto**: no le sigue
    nadie, así que reescribir la punta no lo detecta nada. Por eso existe
    `warden audit anchor`, y por eso está escrito en `docs/threat-model.md`.
    """
    filas = _cadena(5)
    original, _ = filas[posicion]
    if nuevo_id == original.principal_id:
        return

    falso = _record(original.seq, original.prev_hash, principal_id=nuevo_id)
    filas[posicion] = (falso, link(falso))

    ok, problema = verify(filas)

    if posicion == len(filas) - 1:
        # La punta sin anclar: la cadena no puede delatarla, y lo declaramos.
        assert ok is True
    else:
        assert ok is False
        assert problema is not None


@_SETTINGS
@given(
    campo=st.sampled_from(
        [
            "seq",
            "recorded_at",
            "principal_id",
            "question_digest",
            "sql_digest",
            "prev_hash",
            "schema_version",
        ]
    )
)
def test_ningun_campo_del_contrato_se_queda_fuera_del_hash(campo: str) -> None:
    """Un campo fuera del hash es un campo que se puede reescribir impunemente.

    No es una comprobación de fontanería: `role` fuera del hash permitiría cambiar
    a posteriori quién hizo la consulta, que es exactamente lo que la auditoría
    existe para impedir.
    """
    base = _record(1, GENESIS)
    valores: dict[str, object] = {
        "seq": 99,
        "recorded_at": "2099-01-01T00:00:00.000000Z",
        "principal_id": "otro",
        "question_digest": "f" * 64,
        "sql_digest": "e" * 64,
        "prev_hash": "1" * 64,
        "schema_version": 2,
    }
    # `seq` y `prev_hash` son POSICIONALES en `_record`, así que se alteran por su
    # sitio y el resto por `kwargs`. Construirlo al revés pasaría el argumento dos
    # veces, que es como se descubrió: con un `TypeError`.
    seq = int(valores["seq"]) if campo == "seq" else base.seq  # type: ignore[arg-type]
    prev = str(valores["prev_hash"]) if campo == "prev_hash" else base.prev_hash
    extra = {} if campo in ("seq", "prev_hash") else {campo: valores[campo]}
    alterado = _record(seq, prev, **extra)

    assert link(alterado) != link(base)


@_SETTINGS
@given(rol=st.sampled_from(list(Role)), fuente=st.sampled_from(list(RoleSource)))
def test_el_rol_y_su_fuente_entran_en_el_hash(rol: Role, fuente: RoleSource) -> None:
    """I-05 llevado a la auditoría: quién preguntó y con qué autoridad no se edita."""
    base = _record(1, GENESIS)
    otro = _record(1, GENESIS, role=rol, role_source=fuente)

    if (rol, fuente) == (base.role, base.role_source):
        assert link(otro) == link(base)
    else:
        assert link(otro) != link(base)
