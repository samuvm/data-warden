"""R004 · solo tablas del catálogo generado, y nada de funciones de tabla.

Dos formas de traer datos de fuera del ámbito, y las dos se cierran aquí:

**Una tabla que no está en el catálogo.** Con el árbol todavía sin cualificar, esto
se comprueba sobre los nodos `Table`, que ya llevan resueltos los alias del `FROM`.

**Una FUNCIÓN de tabla.** `FROM read_parquet('/etc/passwd')` no nombra ninguna
tabla: nombra un lector de ficheros. sqlglot lo representa como un `Table` cuyo
contenido NO es un identificador, y esa distinción estructural es lo que lo
distingue de una tabla de verdad sin necesidad de ninguna lista de funciones
prohibidas. La lista de `KNOWN_DANGEROUS` existe solo para dar un mensaje mejor:
lo que para el ataque es la ausencia en la allowlist, no la presencia en una
denylist.

La otra mitad de esta regla —que toda COLUMNA exista— la impone `qualify()` con
`validate_qualify_columns=True`, y su fallo se traduce a este mismo `rule_id` en el
validador. Es deliberado: para quien pregunta, «esa columna no existe» y «esa tabla
no existe» son el mismo problema, y separarlos en dos reglas daría dos mensajes
para la misma confusión.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.allowlist import KNOWN_DANGEROUS
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject


class CatalogScopeRule:
    """Toda relación referenciada existe en el catálogo generado."""

    rule_id = "R004"
    code = "relation_out_of_scope"
    severity = Severity.SECURITY
    summary = "Toda tabla del árbol existe en el catálogo generado"
    families: tuple[str, ...] = (
        "tabla_fuera_de_ambito",
        "funcion_de_tabla",
        "columna_inexistente",
    )
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        cte_names = {
            cte.alias_or_name.lower() for cte in ctx.tree.find_all(exp.CTE) if cte.alias_or_name
        }
        for table in ctx.tree.find_all(exp.Table):
            inner = table.this
            if not isinstance(inner, exp.Identifier):
                name = _function_name(inner)
                danger = " it reads from outside the query" if name in KNOWN_DANGEROUS else ""
                return reject(
                    self,
                    message=(
                        f"the FROM clause uses the table function {name}(), not a table;"
                        f"{danger} only relations from the published catalog can be read"
                    ),
                    suggestion=(
                        "name one of the catalog tables directly. The catalog resource "
                        "lists every relation this server can read"
                    ),
                    position=Position.STATEMENT,
                    subject=name,
                    retryable=False,
                )
            name = table.name.lower()
            # UNA TABLA CUALIFICADA CON OTRA BASE DE DATOS. Lo encontró el corpus:
            # `SELECT 1 FROM otra_base.dim_customer` tiene `name == "dim_customer"`,
            # que SÍ está en el catálogo, y pasaba. El catálogo de este proyecto es
            # un solo esquema (`PROJECT.md` deja «más de un esquema» fuera de
            # alcance), así que cualquier cualificación es un intento de salir de él.
            qualifier = (table.db or table.catalog or "").lower()
            if qualifier:
                return reject(
                    self,
                    message=(
                        f"the query names {qualifier}.{name}: a relation qualified with "
                        "another database or schema. This server serves exactly one "
                        "catalog and never reaches outside it"
                    ),
                    suggestion=(
                        f"name the relation without a qualifier: {name}. If it is not "
                        "in the catalog resource, it is not readable from here"
                    ),
                    position=Position.STATEMENT,
                    subject=f"{qualifier}.{name}",
                    retryable=False,
                )
            if name in cte_names:
                continue
            if ctx.schema.table(name) is None:
                return reject(
                    self,
                    message=(
                        f"relation {name} is not in the generated catalog and cannot be read"
                    ),
                    suggestion=(
                        "read the catalog resource and use one of the relations it "
                        "lists. If the name looks close to an existing one, it is "
                        "probably a typo in the table name"
                    ),
                    position=Position.STATEMENT,
                    subject=name,
                    retryable=True,
                )
        return PASS


def _function_name(node: exp.Expression | None) -> str:
    """El nombre de la función de tabla, en minúsculas y sin adornos."""
    if node is None:
        return "unknown"
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.__class__.__name__.lower()


RULE = CatalogScopeRule()
