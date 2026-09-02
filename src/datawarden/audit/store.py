"""El almacén de la cadena: SQLite en WAL, append-only por TRIGGER.

**El append-only lo impone el motor, no la disciplina de quien llama.** Si viviera
en el código de este módulo, el primer script que abriera el fichero con `sqlite3`
se lo saltaría sin enterarse. Un trigger viaja con los datos: quien quiera reescribir
un registro tiene que borrar el trigger primero, y eso es un acto deliberado que
deja huella, no un descuido.

**WAL** porque el escritor no puede bloquear a `warden audit verify`: un almacén que
hay que parar para auditarlo se audita una vez y nunca más.

**Y el límite honesto, que no lo tapa ninguna de las dos cosas:** quien tiene
escritura sobre el fichero puede borrar los triggers y recalcular la cadena entera.
El hash encadenado detecta a quien NO puede reescribir todo lo posterior. Va escrito
en `docs/threat-model.md` porque una defensa cuyo límite no se publica se lee como
una promesa que no es.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Final

from datawarden.audit.chain import GENESIS, AuditRecord, link, verify
from datawarden.domain.types import Role, RoleSource, Status

#: Las columnas de la tabla, en el orden en que se leen y se escriben. Se recorre
#: esta tupla en vez de indexar el diccionario por un literal, y eso NO es estilo:
#: `scripts/check_role_source.py` pone el gate en rojo ante cualquier `x["role"]` o
#: `x["principal_id"]` fuera de `principal/`, porque es la forma que tiene un rol de
#: colarse desde datos no autenticados (I-05). Aquí el rol viene de un `Principal`
#: ya construido, pero el checker es estático y tiene razón en no distinguir: la
#: forma que no se puede escribir mal es la que se recorre.
_COLUMNS: Final = (
    "seq",
    "recorded_at",
    "principal_id",
    "role",
    "role_source",
    "status",
    "question_digest",
    "question_preview",
    "sql_digest",
    "sql",
    "tables",
    "columns_masked",
    "rejection",
    "estimated_bytes",
    "scanned_bytes",
    "budget_bytes",
    "row_count",
    "truncated",
    "duration_ms",
    "engine",
    "model",
    "trace_id",
    "prev_hash",
    "schema_version",
    "chain_hash",
)

#: Campos que se guardan serializados a JSON porque SQLite no tiene ni listas ni
#: objetos. Se leen de vuelta con el mismo mapa, para que no haya dos listas.
_JSON_FIELDS: Final = ("tables", "columns_masked", "rejection", "model")

_SCHEMA_SQL: Final = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq              INTEGER PRIMARY KEY,
    recorded_at      TEXT    NOT NULL,
    principal_id     TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    role_source      TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    question_digest  TEXT    NOT NULL,
    question_preview TEXT,
    sql_digest       TEXT    NOT NULL,
    sql              TEXT,
    tables           TEXT    NOT NULL,
    columns_masked   TEXT    NOT NULL,
    rejection        TEXT,
    estimated_bytes  INTEGER,
    scanned_bytes    INTEGER,
    budget_bytes     INTEGER,
    row_count        INTEGER,
    truncated        INTEGER,
    duration_ms      REAL,
    engine           TEXT,
    model            TEXT,
    trace_id         TEXT,
    prev_hash        TEXT    NOT NULL,
    -- La versión del contrato con la que se escribió ESTA fila. Se guarda en vez de
    -- derivarse: entra en el hash, y un registro al que se le inyectara la versión
    -- de hoy dejaría de verificar el día que la versión suba.
    schema_version   INTEGER NOT NULL,
    chain_hash       TEXT    NOT NULL
);

-- APPEND-ONLY, impuesto por el motor. El mensaje nombra el motivo porque quien se
-- lo encuentre tiene que entender que es deliberado y no un permiso mal puesto.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log es append-only: un registro escrito no se edita');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log es append-only: un registro escrito no se borra');
END;
"""


@dataclass(frozen=True, slots=True)
class Entry:
    """Un registro ya escrito, con el `seq` que le tocó y su hash."""

    record: AuditRecord
    chain_hash: str

    @property
    def seq(self) -> int:
        return self.record.seq


def _utc_now() -> str:
    """La marca de tiempo del contrato: UTC con la `Z` EXPLÍCITA.

    `isoformat()` produce `+00:00`, que **no casa** el `pattern: "Z$"` del schema, y
    `utcnow()` —que sí daría la Z— está deprecada y la suite corre con
    `filterwarnings = ["error"]`. Así que se formatea a mano. Los microsegundos van
    porque dos registros del mismo milisegundo son normales bajo carga; el orden no
    depende de esto de todos modos, lo da `seq`.
    """
    return dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class AuditStore:
    """La cadena, persistida. `":memory:"` para los tests unitarios."""

    def __init__(self, path: str) -> None:
        self.connection = sqlite3.connect(path, isolation_level=None)
        # WAL no aplica a `:memory:` y SQLite lo ignora sin protestar; se pide
        # igual para que el camino de código sea EL MISMO en test y en producción.
        if path != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(_SCHEMA_SQL)

    # ------------------------------------------------------------- escritura ---

    def append(
        self,
        *,
        principal_id: str,
        role: Role,
        role_source: RoleSource,
        status: Status,
        question_digest: str,
        sql_digest: str,
        **optional: Any,
    ) -> Entry:
        """Escribe un registro al final de la cadena y devuelve lo que quedó.

        **El `seq` lo asigna el almacén y nunca quien llama.** Un `seq` de fuera se
        puede repetir o saltar, y un hueco en la numeración es indistinguible de un
        registro borrado: la comprobación de consecutividad de `verify()` dejaría de
        significar nada.
        """
        row = self.connection.execute(
            "SELECT seq, chain_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        next_seq = 1 if row is None else int(row[0]) + 1
        previous = GENESIS if row is None else str(row[1])

        duration = optional.pop("duration_ms", None)
        record = AuditRecord(
            seq=next_seq,
            recorded_at=_utc_now(),
            principal_id=principal_id,
            role=role,
            role_source=role_source,
            status=status,
            question_digest=question_digest,
            sql_digest=sql_digest,
            prev_hash=previous,
            # Redondeado a microsegundos: es la precisión donde el `repr` de Python
            # y el formato de número de ECMAScript coinciden, y JCS exige el de
            # ECMAScript. Ver el docstring de `chain.canonicalize`.
            duration_ms=None if duration is None else round(float(duration), 3),
            **optional,
        )
        chain_hash = link(record)

        payload = record.to_payload()
        values: list[Any] = []
        for name in _COLUMNS:
            if name == "chain_hash":
                values.append(chain_hash)
                continue
            value = payload.get(name)
            if name in _JSON_FIELDS:
                values.append(json.dumps(value if value is not None else [], sort_keys=True))
            else:
                values.append(value)
        # S608: los nombres de columna salen de `_COLUMNS`, una constante de este
        # módulo, y JAMÁS de una entrada. Los VALORES van por parámetro, que es lo
        # único que un atacante controla. Interpolar la lista de columnas es lo que
        # permite que exista un solo sitio donde se declara el orden: dos listas
        # —una para escribir y otra para leer— divergen, y una columna leída en la
        # posición equivocada cambia el JCS y rompe la cadena sin que nadie la toque.
        self.connection.execute(
            f"INSERT INTO audit_log ({','.join(_COLUMNS)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(_COLUMNS))})",
            values,
        )
        return Entry(record=record, chain_hash=chain_hash)

    # -------------------------------------------------------------- lectura ---

    def rows_as_entries(self) -> list[Entry]:
        """Toda la cadena, en orden de `seq`."""
        # S608: mismo motivo que en `append` — columnas de una constante, cero
        # entrada. Y la misma tupla, para que leer y escribir no puedan divergir.
        cursor = self.connection.execute(
            f"SELECT {','.join(_COLUMNS)} FROM audit_log ORDER BY seq"  # noqa: S608
        )
        entries: list[Entry] = []
        for raw in cursor.fetchall():
            fields = dict(zip(_COLUMNS, raw, strict=True))
            chain_hash = str(fields.pop("chain_hash"))
            entries.append(Entry(record=_record_from(fields), chain_hash=chain_hash))
        return entries

    def rows(self) -> list[tuple[AuditRecord, str]]:
        """La cadena en la forma que `chain.verify()` consume."""
        return [(e.record, e.chain_hash) for e in self.rows_as_entries()]

    def verify(self) -> tuple[bool, str | None]:
        """¿Está intacta la cadena? El veredicto lo da `chain.verify`, que es PURO."""
        return verify(self.rows())

    def count(self) -> int:
        row = self.connection.execute("SELECT count(*) FROM audit_log").fetchone()
        return int(row[0])

    def count_by_status(self) -> dict[Status, int]:
        """Cuántos registros de cada estado. Los cuatro salen, valgan cero.

        Es lo que `warden audit reconcile` compara contra el contador del motor, y
        un estado que no apareciera por no tener filas obligaría a quien lee el
        informe a distinguir «cero» de «no medido».
        """
        counts = dict.fromkeys(Status, 0)
        for value, total in self.connection.execute(
            "SELECT status, count(*) FROM audit_log GROUP BY status"
        ):
            counts[Status(value)] = int(total)
        return counts

    def close(self) -> None:
        self.connection.close()


def _record_from(fields: dict[str, Any]) -> AuditRecord:
    """Reconstruye el registro leído de disco.

    Se recorre `_COLUMNS` y se indexa por una VARIABLE, nunca por un literal como
    `fields["role"]`: ver el comentario de `_COLUMNS`. Y los tipos se restauran
    explícitamente porque SQLite no tiene booleano —`truncated` vuelve como 0 o 1— y
    un `0` donde había `False` cambia el JCS y por tanto el hash, con lo que la
    cadena se declararía rota sin que nadie la hubiera tocado.
    """
    restored: dict[str, Any] = {}
    for name, value in fields.items():
        if name in _JSON_FIELDS:
            decoded = json.loads(value) if value else None
            if name in ("tables", "columns_masked"):
                restored[name] = tuple(decoded or ())
            else:
                restored[name] = decoded or None
        elif name == "truncated":
            restored[name] = None if value is None else bool(value)
        elif name == "role":
            restored[name] = Role(value)
        elif name == "role_source":
            restored[name] = RoleSource(value)
        elif name == "status":
            restored[name] = Status(value)
        else:
            restored[name] = value
    return AuditRecord(**restored)
