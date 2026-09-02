"""R003 · una función que sqlglot no reconoce es una función que no se ejecuta.

`exp.Anonymous` es lo que sqlglot construye cuando encuentra una llamada a función
que no sabe traducir a un nodo propio: una extensión de DuckDB, una UDF, o
`read_csv('/etc/passwd')`. Es decir, **es exactamente el conjunto de cosas cuyo
comportamiento este guard no puede razonar**, y por eso se rechaza por defecto.

`CLAUDE.md` lo dice en una línea: *`exp.Anonymous` ⇒ rechazo*. La allowlist de
`ALLOWED_ANONYMOUS` es corta a propósito y cada entrada es una decisión: una
función que se añade porque «hacía falta para una consulta» es cómo una allowlist se
convierte en una denylist con más pasos.

R002 no puede hacer este trabajo: para él, todo `Anonymous` es el mismo nodo. Hace
falta mirar el NOMBRE, y ese es el único sitio del guard donde se mira un nombre de
función — con la allowlist delante, no con una lista negra detrás.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.allowlist import ALLOWED_ANONYMOUS, KNOWN_DANGEROUS
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject


class FunctionAllowlistRule:
    """Ninguna función desconocida sobrevive al guard."""

    rule_id = "R003"
    code = "function_not_allowed"
    severity = Severity.SECURITY
    summary = "Toda función es conocida y está en la allowlist"
    families: tuple[str, ...] = (
        "funcion_desconocida",
        "funcion_de_motor",
        "lectura_de_fichero",
    )
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        for node in ctx.tree.find_all(exp.Anonymous):
            name = str(node.this).lower()
            if name in ALLOWED_ANONYMOUS:
                continue
            if name in KNOWN_DANGEROUS:
                message = (
                    f"the function {name}() reads from outside the query — files, the "
                    "network or the engine process — and is never executed here"
                )
                suggestion = (
                    "every answer this server gives comes from the catalog tables. "
                    "Ask the question against them"
                )
                retryable = False
            else:
                message = (
                    f"the function {name}() is not on the allowlist; the guard cannot "
                    "reason about what an unknown function does, so it refuses it"
                )
                suggestion = (
                    "use a standard SQL function. If this one is genuinely needed, it "
                    "has to be added to the allowlist as a decision, with its case"
                )
                retryable = True
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.FUNCTION_ARGUMENT,
                subject=name,
                retryable=retryable,
            )
        return PASS


RULE = FunctionAllowlistRule()
