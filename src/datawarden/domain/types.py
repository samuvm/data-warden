"""Tipos congelados del dominio. Puros, inmutables y sin motor ni transporte.

Son la frontera del proyecto: todo lo que cruza de un anillo al siguiente es uno de
estos, y por eso las garantías viven **en el tipo** y no en quien lo construye.

- Un `RejectionReason` sin sugerencia accionable **no se puede construir** (I-09).
  Si la validación viviera en el sitio que lo crea, el primer camino que se
  olvidara de llamarla produciría un rechazo mudo, y nadie lo notaría hasta que
  `G-RECOVERY` saliera bajo por «culpa del modelo».
- `RoleSource` no tiene ningún valor que signifique «lo dijo el cliente» (I-05).
  El rol viene del proceso servidor, de un `PrincipalToken` acuñado por el
  servidor o de una bandera del CLI. `_meta` y los argumentos de tool son dato.
- `ValidatedQuery` guarda el **árbol**, no la cadena de entrada, y `sql()`
  re-serializa (I-02). Ahí vive la clase entera de ataques por diferencia de
  parser: lo que sqlglot entendió es lo que el motor ejecuta.

`import-linter` impone que este paquete no conozca ni motor, ni transporte, ni
modelo. `sqlglot` sí está permitido y es deliberado: el árbol validado ES el tipo
del dominio, y guardarlo como texto sería exactamente el error que I-02 prohíbe.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

import sqlglot.expressions as exp

#: Los mismos cuatro roles que `docs/spec/policy.yaml`. Un rol de más aquí sería un
#: rol sin matriz de acceso, es decir, un rol sin política que aplicar.
__all__ = [
    "CostEstimate",
    "Position",
    "Principal",
    "RejectionReason",
    "ResultSet",
    "Role",
    "RoleSource",
    "Severity",
    "ValidatedQuery",
    "Verdict",
]

_RULE_ID_RE: Final = re.compile(r"^(R[0-9]{3}|BUDGET|POLICY|INTERNAL)$")
_CODE_RE: Final = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_MIN_TEXT: Final = 10
_MAX_TEXT: Final = 500

#: De dónde puede salir un coste. Un número sin método no es auditable: los
#: metadatos de Iceberg y un `EXPLAIN` no valen lo mismo y no se pueden mezclar en
#: la misma métrica de calibración sin decir cuál produjo cada dato.
COST_METHODS: Final = frozenset({"iceberg", "explain", "hybrid", "fixture"})


class Role(StrEnum):
    """Los cuatro roles de la política firmada."""

    ANALYST = "analyst"
    OPS = "ops"
    FINANCE = "finance"
    ADMIN = "admin"


class RoleSource(StrEnum):
    """De dónde salió el rol. **Aquí NO hay un valor para «lo dijo el cliente».**

    Es I-05 hecho tipo. La spec MCP 2026-07-28 eliminó las sesiones, así que el rol
    no puede venir de la sesión de protocolo; y si pudiera venir de `_meta` o de un
    argumento de tool, cualquiera se autoconcedería `admin`. Un enum sin esos
    valores convierte la promesa en algo que ni siquiera se puede escribir.
    """

    SERVER_PROCESS = "server_process"
    # No es una credencial: es el NOMBRE de una fuente de autoridad. El secreto
    # que firma el handle vive en configuración y no en el dominio.
    PRINCIPAL_TOKEN = "principal_token"  # noqa: S105
    CLI_FLAG = "cli_flag"


class Severity(StrEnum):
    """Qué clase de rechazo es. Distinguirlos cambia qué hay que hacer al respecto.

    Una tasa alta de `POLICY` dice que hay que revisar la matriz con negocio. Una
    tasa alta de `SECURITY` dice algo muy distinto. Meterlos en el mismo contador
    hace que ninguno de los dos se pueda leer.
    """

    SECURITY = "security"
    POLICY = "policy"
    BUDGET = "budget"
    MALFORMED = "malformed"
    INTERNAL = "internal"


class Position(StrEnum):
    """Dónde en el árbol, con vocabulario de SQL y no de sqlglot.

    Es lo que convierte «no puedes usar esa columna» en «no puedes usarla AHÍ», que
    es la diferencia entre la mitad de los rechazos de este proyecto: una columna
    `mask` es legal en la proyección y prohibida en el `WHERE`.
    """

    PROJECTION = "projection"
    WHERE = "where"
    JOIN_ON = "join_on"
    GROUP_BY = "group_by"
    ORDER_BY = "order_by"
    HAVING = "having"
    QUALIFY = "qualify"
    FUNCTION_ARGUMENT = "function_argument"
    WINDOW_PARTITION = "window_partition"
    SUBQUERY = "subquery"
    CTE = "cte"
    STATEMENT = "statement"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Principal:
    """Quién pregunta y con qué autoridad."""

    id: str
    role: Role
    source: RoleSource

    def __post_init__(self) -> None:
        if not self.id.strip():
            message = (
                "principal_id vacío. Sin identificador no hay auditoría, y sin "
                "auditoría no hay no repudio: es el único control que este sistema "
                "tiene sobre el rol admin, que no lleva máscara ninguna."
            )
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class RejectionReason:
    """Un rechazo accionable. Conforme a `docs/spec/rejection.schema.json`.

    No es un error: es una respuesta del sistema, y es la que más veces va a leer
    un modelo. Por eso las restricciones del contrato se comprueban al construirlo
    y no al serializarlo — un rechazo mudo que solo falla al escribirse en la
    auditoría ya ha llegado al cliente.
    """

    rule_id: str
    code: str
    message: str
    suggestion: str
    severity: Severity
    position: Position = Position.UNKNOWN
    subject: str | None = None
    alternative: str | None = None
    retryable: bool = True
    docs: str | None = None

    def __post_init__(self) -> None:
        if not _RULE_ID_RE.match(self.rule_id):
            msg = (
                f"rule_id {self.rule_id!r} no es un identificador del registro. Los "
                "rule_id son un registro cerrado (I-01): RNNN para las reglas del "
                "guard, y BUDGET / POLICY / INTERNAL para los rechazos que no "
                "vienen de una regla. Una cadena libre haría imposible comprobar "
                "que ninguna regla desaparece."
            )
            raise ValueError(msg)
        if not _CODE_RE.match(self.code):
            msg = (
                f"code {self.code!r} no es un identificador estable en minúsculas. "
                "El code agrupa métricas: si 'DeniedColumn' y 'denied_column' "
                "fueran dos códigos distintos, no agruparía nada."
            )
            raise ValueError(msg)
        _require_text("message", self.message)
        _require_text(
            "suggestion",
            self.suggestion,
            extra=(
                "Un rechazo sin salida no redirige el trabajo: lo bloquea, y un "
                "guard que bloquea el trabajo se desactiva en tres semanas (I-09)."
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """La forma del contrato. Los campos ausentes NO se escriben como `null`.

        `additionalProperties: false` y campos opcionales: emitir `null` en vez de
        omitir haría que dos rechazos idénticos tuvieran dos representaciones, y
        estos objetos entran en el hash de la auditoría.
        """
        payload: dict[str, Any] = {
            "rule_id": self.rule_id,
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "severity": self.severity.value,
            "position": self.position.value,
            "retryable": self.retryable,
        }
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.alternative is not None:
            payload["alternative"] = self.alternative
        if self.docs is not None:
            payload["docs"] = self.docs
        return payload


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    """Lo único que un motor acepta. Lleva el ÁRBOL, nunca la cadena de entrada.

    `Engine.execute()` recibe esto y jamás un `str`, y lo comprueba
    `scripts/check_no_raw_sql.py` sobre el AST de `engines/`. La consecuencia
    práctica: si sqlglot entendió mal la consulta, lo que llega a la base de datos
    es lo que sqlglot entendió, no lo que el atacante escribió. Eso elimina por
    construcción la clase entera de ataques por diferencia de parser.
    """

    ast: exp.Expression
    dialect: str
    principal: Principal
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    max_rows: int
    masked_columns: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_rows <= 0:
            msg = (
                f"max_rows={self.max_rows}. El tope de filas es del DOMINIO y no del "
                "motor (I-12): sin él, cada engine aplicaría el suyo y DuckDB y "
                "Athena devolverían resultados distintos para el mismo rol."
            )
            raise ValueError(msg)

    def sql(self) -> str:
        """El SQL que se ejecuta: el árbol re-serializado, SIN COMENTARIOS. I-02.

        `comments=False` no es cosmético y lo encontró un test, no una revisión.
        **sqlglot conserva los comentarios en el árbol y los vuelve a emitir**, así
        que `SELECT /*+ hint */ a FROM t` re-serializado sigue llevando el
        comentario del atacante hasta el motor. Un comentario no es estructura: el
        guard valida la estructura, luego lo que se ejecuta tiene que ser
        exactamente la estructura validada y nada más. Además:

        - algunos motores leen HINTS de optimización dentro de un comentario, que
          es texto que nadie ha validado influyendo en el plan;
        - `sql_digest()` cambiaría con el comentario, y entonces dos consultas
          idénticas producirían dos registros de auditoría distintos.
        """
        return self.ast.sql(dialect=self.dialect, comments=False)

    def sql_digest(self) -> str:
        """sha256 de lo que de verdad corre, para la cadena de auditoría.

        Del SQL re-serializado y nunca de la entrada: si se hasheara la entrada, la
        auditoría certificaría algo distinto de lo que se ejecutó, que es la única
        forma de que un registro de auditoría sea peor que no tenerlo.
        """
        return hashlib.sha256(self.sql().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """Lo que costaría, calculado ANTES de ejecutar."""

    estimated_bytes: int
    estimated_rows: int
    files_scanned: int
    method: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("estimated_bytes", self.estimated_bytes),
            ("estimated_rows", self.estimated_rows),
            ("files_scanned", self.files_scanned),
        ):
            if value < 0:
                msg = f"{name}={value}: una estimación negativa no significa nada"
                raise ValueError(msg)
        if self.method not in COST_METHODS:
            msg = (
                f"method={self.method!r} no está en {sorted(COST_METHODS)}. Un coste "
                "sin método declarado no es auditable: los metadatos de Iceberg y un "
                "EXPLAIN no valen lo mismo y G-COST-CALIB tiene que poder separarlos."
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ResultSet:
    """Filas devueltas, con su forma y con la verdad sobre el recorte."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool

    def __post_init__(self) -> None:
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            msg = (
                f"hay filas que no tienen {width} celdas, una por cada nombre de "
                "columns. Un resultset con filas de anchura variable rompe la "
                "comparación de docs/spec/resultset-equality.md por el sitio más "
                "difícil de depurar."
            )
            raise ValueError(msg)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        """La forma se conoce aunque no haya filas (decisión 8 de resultset-equality)."""
        return len(self.columns)


#: Lo que devuelve el guard: o una consulta validada, o el motivo por el que no.
#: Un `Optional[ValidatedQuery]` habría perdido el motivo, y el motivo es la mitad
#: del valor del sistema.
Verdict = ValidatedQuery | RejectionReason


def _require_text(name: str, value: str, *, extra: str = "") -> None:
    stripped = value.strip()
    if len(stripped) < _MIN_TEXT or len(stripped) > _MAX_TEXT:
        msg = (
            f"{name} tiene {len(stripped)} caracteres y el contrato exige entre "
            f"{_MIN_TEXT} y {_MAX_TEXT}. {extra}"
        ).strip()
        raise ValueError(msg)
