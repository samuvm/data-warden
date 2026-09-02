"""La cadena de auditoría: canonicalizar, encadenar y verificar. PURO.

Ni disco, ni SQLite, ni reloj. Esta separación no es aseo: es lo que permite que
la propiedad de manipulación de `G-AUDIT-TAMPER` corra mil ejemplos de Hypothesis
sobre una cadena en memoria en vez de abrir mil bases de datos, y una propiedad que
no cabe en el gate acaba con alguien bajándole el número de ejemplos.

**Por qué el hash encadenado, y hasta dónde llega.** `h_n = sha256(h_{n-1} ‖
jcs(registro_n))` hace que reescribir un registro obligue a reescribir todos los
posteriores. Eso detecta a quien manipula el almacén sin poder rehacerlo entero.
**No detecta a quien SÍ puede**, y ese límite es obligatorio en
`docs/threat-model.md` con las palabras del contrato: *quien tiene escritura sobre
el almacén puede recalcular la cadena entera*. Por eso existe `warden audit anchor`.

**El campo `hash` no está en `AuditRecord`, y es deliberado.** El contrato define el
hash como el de «el registro SIN el campo hash», así que el tipo ES el registro sin
él y `link()` lo devuelve. Guardarlo dentro obligaría a construir el objeto con un
campo vacío para luego rellenarlo, que es la clase de estado a medias donde se cuela
un registro sin hashear. De paso evita sombrear el builtin `hash`, que ruff vigila.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Final

from datawarden.domain.types import Role, RoleSource, Status

#: El `prev_hash` del primer registro. 64 ceros y no `null`: «un valor legal y
#: distinguible, para que el verificador no necesite un caso especial»
#: (`docs/spec/audit-record.schema.json`).
#:
#: **OJO: este relleno se usa DOS veces con dos significados distintos.** Aquí quiere
#: decir «primero de la cadena»; en `executor.NO_VALIDATED_TREE` quiere decir «no hubo
#: árbol validado que hashear». Hoy no colisionan porque son campos distintos y un
#: verificador los lee por NOMBRE, pero el primer registro de una cadena que además
#: sea un rechazo pre-parseo llevará los mismos 64 ceros en `prev_hash` y en
#: `sql_digest` a la vez. Lo señaló Samuel al aprobar P-007: los valores son
#: correctos y no se cambian, pero nadie debe escribir jamás un ayudante genérico
#: del tipo «¿esto es un centinela?». Se leen por campo, nunca por valor.
GENESIS: Final = "0" * 64

#: Versión de `docs/spec/audit-record.schema.json` con la que se escriben los
#: registros NUEVOS. Es el valor por defecto del campo homónimo, **no una constante
#: que se inyecte al canonicalizar**, y la diferencia es la razón de que el campo
#: exista.
#:
#: Lo destapó la propiedad de `G-AUDIT-TAMPER` recorriendo el registro byte a byte:
#: mientras la versión se inyectaba desde aquí, alterarla en un registro guardado no
#: cambiaba nada —se reconstruía con la constante— y la mutación pasaba sin
#: detectarse. Peor que el hueco era su causa: **el día que esto subiera a 2, todo
#: registro escrito bajo la 1 habría dejado de verificar**, porque se le habría
#: inyectado una versión que no era la suya. El contrato mete `schema_version` en el
#: hash para que una cadena que mezcla versiones SE PUEDA verificar, y eso solo
#: funciona si cada registro recuerda la suya.
SCHEMA_VERSION: Final = 1

#: Campos que se emiten SIEMPRE, valgan lo que valgan. El resto es opcional y solo
#: aparece cuando tiene algo que decir: el contrato tiene `additionalProperties:
#: false` y declara nullable a casi todo, y emitir `null` en trece campos por
#: registro engorda la cadena sin añadir información.
#:
#: **`tables` y `columns_masked` están aquí aunque vayan vacíos, y es una decisión.**
#: En un registro de auditoría `"columns_masked": []` es EVIDENCIA —dice que no se
#: enmascaró nada— mientras que el campo ausente es AMBIGÜEDAD: no distingue «no
#: había nada que enmascarar» de «lo escribió una versión que aún no llevaba el
#: campo». El contrato define `columns_masked` como «la evidencia de que el
#: enmascarado ocurrió, no la promesa de que ocurrió», y una evidencia que a veces
#: no está no es evidencia.
_ALWAYS: Final = (
    "seq",
    "recorded_at",
    "principal_id",
    "role",
    "role_source",
    "status",
    "question_digest",
    "sql_digest",
    "prev_hash",
    "schema_version",
    "tables",
    "columns_masked",
)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Un registro de auditoría, SIN su hash. Conforme al contrato de la fase 1.

    Los diez primeros campos son los `required` del schema. El resto es opcional y
    solo se emite cuando tiene algo que decir.
    """

    seq: int
    recorded_at: str
    principal_id: str
    role: Role
    role_source: RoleSource
    status: Status
    question_digest: str
    sql_digest: str
    prev_hash: str = GENESIS
    #: La versión del contrato con la que se escribió ESTE registro, no la que corre
    #: hoy. Por defecto la actual; se restaura tal cual al leer de disco.
    schema_version: int = SCHEMA_VERSION

    question_preview: str | None = None
    sql: str | None = None
    tables: tuple[str, ...] = ()
    columns_masked: tuple[str, ...] = ()
    rejection: dict[str, Any] | None = None
    estimated_bytes: int | None = None
    scanned_bytes: int | None = None
    budget_bytes: int | None = None
    row_count: int | None = None
    truncated: bool | None = None
    duration_ms: float | None = None
    engine: str | None = None
    model: dict[str, Any] | None = field(default=None, repr=False)
    trace_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """El registro como diccionario, listo para canonicalizar.

        **`tables` y `columns_masked` se ordenan AQUÍ, y es obligatorio.** JCS
        ordena las claves de un objeto y jamás los elementos de un array, así que
        el orden de estas dos listas lo tiene que garantizar el emisor. Sin esto,
        la misma consulta con el mismo rol produce dos hashes distintos según en
        qué orden recorriera sqlglot el árbol, y la cadena deja de ser comparable
        consigo misma.
        """
        payload: dict[str, Any] = {
            "seq": self.seq,
            "recorded_at": self.recorded_at,
            "principal_id": self.principal_id,
            "role": str(self.role),
            "role_source": str(self.role_source),
            "status": str(self.status),
            "question_digest": self.question_digest,
            "sql_digest": self.sql_digest,
            "prev_hash": self.prev_hash,
            "schema_version": self.schema_version,
            "tables": sorted(self.tables),
            "columns_masked": sorted(self.columns_masked),
            "question_preview": self.question_preview,
            "sql": self.sql,
            "rejection": self.rejection,
            "estimated_bytes": self.estimated_bytes,
            "scanned_bytes": self.scanned_bytes,
            "budget_bytes": self.budget_bytes,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "engine": self.engine,
            "model": self.model,
            "trace_id": self.trace_id,
        }
        return {
            name: value
            for name, value in payload.items()
            if name in _ALWAYS or value not in (None, [], ())
        }


def canonicalize(record: AuditRecord) -> str:
    """JCS (RFC 8785) del registro SIN su hash.

    Sin canonicalización el hash depende del orden de un diccionario y no vale
    nada; lo dice el propio contrato, y decirlo ahí es lo que impide que dos
    implementaciones produzcan dos cadenas que no se verifican entre sí.

    **El límite honesto de esta implementación, declarado en vez de descubierto.**
    JCS son tres reglas: claves ordenadas por unidades de código UTF-16, cero
    espacio en blanco, y números con el formato de ECMAScript. Las dos primeras las
    da `json.dumps` exactamente —todas las claves de este contrato son ASCII, donde
    el orden por punto de código y el orden por UTF-16 coinciden—. La tercera solo
    coincide para enteros, que es todo lo que este contrato guarda salvo
    `duration_ms`; por eso `duration_ms` se redondea a microsegundos al construir el
    registro, donde `repr` de Python y ECMAScript ya no difieren. Un flotante
    arbitrario aquí sería el único sitio donde otra implementación podría discrepar,
    y el `VECTOR DORADO` de los tests es lo que convierte esto en un acuerdo
    comprobable en vez de una promesa.
    """
    return json.dumps(
        record.to_payload(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def link(record: AuditRecord) -> str:
    """`sha256(prev_hash_bytes ‖ jcs(registro_sin_hash))`, en hex.

    `prev_hash_bytes` son los **32 bytes crudos**, no los 64 caracteres de su
    representación hexadecimal: el contrato nombra los bytes DEL hash, no los de su
    texto. Para la seguridad da igual —`prev_hash` viaja además dentro del JCS— pero
    para la interoperabilidad no, que es justo el miedo que el contrato declara. La
    decisión se fija con un vector dorado en `tests/unit/audit/test_chain.py`.
    """
    return hashlib.sha256(
        bytes.fromhex(record.prev_hash) + canonicalize(record).encode("utf-8")
    ).hexdigest()


def verify(rows: list[tuple[AuditRecord, str]]) -> tuple[bool, str | None]:
    """Recorre la cadena y dice si está intacta, y si no, DÓNDE se rompió.

    Devuelve `(True, None)` o `(False, motivo)`. El motivo nombra el `seq`, porque
    «la cadena está rota» sin decir dónde obliga a bisecar a mano un almacén de un
    millón de filas.

    Se comprueban cuatro cosas, y las cuatro son formas distintas de manipular:

    1. **El hash de cada registro cuadra con su contenido** — alguien editó un campo.
    2. **El `prev_hash` de cada uno es el `hash` del anterior** — alguien reescribió
       un registro Y su hash, pero no pudo con el siguiente. Este es el ataque real
       y el que la cadena existe para atrapar.
    3. **La cadena empieza en el génesis** — alguien borró historia por delante.
    4. **`seq` es consecutivo** — alguien borró un registro de en medio.

    Una cadena vacía se verifica: un almacén recién creado no está corrupto.
    """
    previous = GENESIS
    expected_seq = 1
    for record, stored_hash in rows:
        if record.seq != expected_seq:
            return False, (
                f"seq {record.seq} donde se esperaba {expected_seq}: hay un hueco en "
                "la numeración, o sea un registro borrado."
            )
        if record.prev_hash != previous:
            return False, (
                f"seq {record.seq}: su prev_hash no es el hash del registro anterior. "
                "Alguien reescribió un registro y su hash, pero no los posteriores."
            )
        recomputed = link(record)
        if recomputed != stored_hash:
            return False, (
                f"seq {record.seq}: el hash guardado no corresponde al contenido. "
                "El registro se editó después de escribirse."
            )
        previous = stored_hash
        expected_seq += 1
    return True, None
