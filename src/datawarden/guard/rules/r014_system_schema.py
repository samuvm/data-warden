"""R014 · nada de esquemas de sistema.

Parece una regla menor y es de las que más cierran. `docs/spec/policy.yaml` excluye
`dim_merchant.traffic_weight` del catálogo publicado con el cambio C-3, y el
catálogo del anillo 1 no la enseña. Pero el motor sí sabe que existe:

    SELECT column_name FROM information_schema.columns WHERE table_name = 'dim_merchant'

Eso devuelve la columna excluida, y con ella el nombre de todo lo que el catálogo
decidió no publicar. **Leer el catálogo real por debajo del catálogo publicado
convierte una decisión de negocio en una sugerencia.**

Corre antes que R004 —que también rechazaría por «tabla que no está en el
catálogo»— porque el mensaje importa: un rechazo que dice «esa tabla no existe»
sobre una tabla que sí existe enseña a insistir. Este dice lo que pasa y dónde
mirar en su lugar.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.allowlist import SYSTEM_SCHEMAS
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages


class SystemSchemaRule:
    """Ni el esquema de sistema, ni sus tablas, ni cualificadas ni sueltas."""

    rule_id = "R014"
    code = "system_schema_access"
    severity = Severity.SECURITY
    summary = "No se consultan los metadatos del motor, solo el catálogo publicado"
    families: tuple[str, ...] = ("esquema_de_sistema", "enumeracion_de_catalogo")
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        for table in ctx.tree.find_all(exp.Table):
            for part in (table.db, table.catalog, table.name):
                if part and part.lower() in SYSTEM_SCHEMAS:
                    message, suggestion = messages.system_schema(part.lower())
                    return reject(
                        self,
                        message=message,
                        suggestion=suggestion,
                        position=Position.STATEMENT,
                        subject=part.lower(),
                        retryable=False,
                    )
        return PASS


RULE = SystemSchemaRule()
