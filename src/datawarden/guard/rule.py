"""El protocolo de regla. **Se congela ANTES de escribir la primera regla.**

`docs/PLAN.md` lo pide en ese orden y no es ceremonia: si el protocolo se decide
mientras se escriben las reglas, cada regla acaba con una firma un poco distinta y
la parametrización del corpus —«una regla = un fichero = un test»— deja de ser
posible. Catorce reglas con catorce formas de invocarse son catorce tests.

**Qué es una regla aquí.** Una función pura de `GuardContext` a `RuleResult`. No
lee ficheros, no toca el motor, no conoce el transporte y no lanza excepciones: si
algo va mal dentro, devuelve un rechazo. El único `except Exception` del guard vive
en `validator.validate` (I-04), y esto es lo que hace que esa única red de
seguridad baste.

**Una regla puede REESCRIBIR el árbol.** R006 inyecta el `LIMIT` que falta y R009
expande `SELECT *`. Por eso `RuleResult` lleva un árbol opcional: la alternativa
—que cada regla mutase el árbol en sitio— haría imposible saber qué regla cambió
qué, y el registro de auditoría dejaría de poder explicar la consulta que corrió.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlglot import expressions as exp

from datawarden.catalog.types import CatalogSchema
from datawarden.domain.types import Principal, RejectionReason, Severity
from datawarden.principal.policy import AccessPolicy


@dataclass(frozen=True, slots=True)
class GuardContext:
    """Todo lo que una regla necesita, ya resuelto y ya indexado.

    **El árbol viene YA CUALIFICADO** por `sqlglot.optimizer.qualify.qualify()`
    contra el catálogo generado. Es la decisión que más código ahorra de todo el
    proyecto: con el árbol cualificado, los alias, las CTE anidadas y `SELECT *` ya
    están resueltos, así que ninguna regla tiene que «buscar» a qué tabla pertenece
    una columna. La alternativa —que cada regla resolviera alias por su cuenta— es
    catorce implementaciones de lo mismo y catorce sitios donde equivocarse.
    """

    #: La cadena de entrada. **Solo para diagnóstico y para medir.** JAMÁS se
    #: ejecuta: lo que se ejecuta es `tree.sql(dialect)` (I-02).
    raw_sql: str
    tree: exp.Expression
    schema: CatalogSchema
    policy: AccessPolicy
    principal: Principal
    dialect: str
    max_rows: int
    #: Instante límite del reloj monótono. Un guard que tarda es un guard caído, y
    #: por eso el propio guard tiene fecha de caducidad por consulta.
    deadline: float | None = None
    #: `tabla.columna` -> columnas base de las que sale. Copiado del catálogo para
    #: no recorrerlo por columna: `G-GUARD-P95` son 25 ms para toda la consulta.
    lineage: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)
    #: `id(nodo Column)` -> columnas base, resuelto DENTRO de esta consulta. Es lo
    #: que sigue un alias a través de una CTE o una subconsulta: `c.b` es
    #: `dim_customer.birth_date` con otro nombre, y una política que no lo siguiera
    #: dejaría abierto el canal lateral por predicado con dos líneas de SQL.
    column_sources: dict[int, tuple[str, ...]] = field(default_factory=dict, repr=False)

    def sources_of(self, column: object, table: str, name: str) -> tuple[str, ...]:
        """Las columnas base detrás de ESTE nodo columna.

        Primero el linaje de la consulta —que sigue alias, CTE y subconsultas— y
        solo si no lo tiene, el del catálogo. El orden importa: el de la consulta es
        más específico, y el del catálogo no sabe nada de las relaciones que la
        propia consulta inventa.
        """
        found = self.column_sources.get(id(column))
        if found:
            return found
        return self.base_columns(table, name)

    def base_columns(self, table: str, column: str) -> tuple[str, ...]:
        """Las columnas base detrás de `tabla.columna`, ya resueltas.

        Si no hay linaje —una relación que no está en el catálogo—, se devuelve la
        referencia tal cual. Quien decide qué hacer con una columna desconocida es
        R004, no esto: aquí no se inventa una respuesta segura, se dice la verdad.
        """
        key = f"{table.lower()}.{column.lower()}"
        return self.lineage.get(key, (key,))


@dataclass(frozen=True, slots=True)
class RuleResult:
    """Lo que devuelve una regla: un rechazo, un árbol reescrito, o nada.

    «Nada» es el caso normal y por eso es el valor por defecto: una regla que no
    tiene nada que objetar no construye ningún objeto.
    """

    rejection: RejectionReason | None = None
    #: El árbol reescrito, si la regla reescribió. `None` significa «no he tocado
    #: nada», que es distinto de «lo he dejado igual».
    tree: exp.Expression | None = None
    #: Notas para la auditoría: qué hizo la regla, si hizo algo.
    notes: tuple[str, ...] = ()

    @property
    def rejected(self) -> bool:
        return self.rejection is not None


#: Las dos fases del guard, y por qué son dos.
#:
#: `qualify()` resuelve alias, expande `SELECT *` y cualifica cada columna con su
#: tabla, y eso es lo que hace que once de las catorce reglas no tengan que
#: «buscar» nada. Pero cualificar un `DROP TABLE` no significa nada, y entregarle a
#: `qualify()` un árbol que ni siquiera es una consulta es pedirle que decida algo
#: que no le toca. Por eso las reglas que deciden QUÉ CLASE DE COSA es el árbol
#: corren ANTES, sobre el árbol crudo, y las que razonan sobre columnas corren
#: DESPUÉS, sobre el árbol cualificado.
PRE_QUALIFY: str = "pre"
POST_QUALIFY: str = "post"

#: El resultado limpio, compartido por todas las reglas que no objetan nada. Es un
#: singleton porque se construye una vez por regla y por consulta: catorce objetos
#: por consulta multiplicados por el p95 de 25 ms no son gratis.
PASS = RuleResult()


@runtime_checkable
class Rule(Protocol):
    """Una regla del guard.

    Los cuatro atributos de clase no son metadatos decorativos: `rule_id` es lo que
    `scripts/check_rules_registry.py` vigila para que ninguna regla desaparezca
    jamás (I-01), `code` es lo que agrupa las métricas, `severity` decide si el
    rechazo es un evento de seguridad, y `families` es lo que hace comprobable que
    toda familia de ataque tiene una regla que la para (I-14).
    """

    rule_id: str
    code: str
    severity: Severity
    summary: str
    families: tuple[str, ...]
    phase: str

    def check(self, ctx: GuardContext) -> RuleResult:
        """Decide sobre el árbol. **No lanza. Nunca.**"""
        ...


def reject(
    rule: Rule,
    *,
    message: str,
    suggestion: str,
    position: object = None,
    subject: str | None = None,
    alternative: str | None = None,
    retryable: bool = True,
) -> RuleResult:
    """Construye el rechazo de una regla con sus metadatos ya rellenos.

    Existe para que ninguna regla pueda equivocarse de `rule_id` al rechazar, que es
    el fallo silencioso más caro de este diseño: un caso que se cree cubierto por
    R008 y que en realidad para R002 por accidente. El día que R002 cambie, R008
    tiene un agujero y nadie se entera.
    """
    from datawarden.domain.types import Position

    return RuleResult(
        rejection=RejectionReason(
            rule_id=rule.rule_id,
            code=rule.code,
            message=message,
            suggestion=suggestion,
            severity=rule.severity,
            position=position if isinstance(position, Position) else Position.UNKNOWN,
            subject=subject,
            alternative=alternative,
            retryable=retryable,
            docs=f"docs/spec/rules/{rule.rule_id}.md",
        )
    )
