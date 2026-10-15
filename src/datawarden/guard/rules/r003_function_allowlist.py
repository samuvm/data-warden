"""R003: a function sqlglot does not recognise is a function that does not run.

sqlglot builds `exp.Anonymous` for a call it cannot map to a dedicated node: a DuckDB
extension, a UDF, or `read_csv('/etc/passwd')`. That is precisely the set of things whose
behaviour the guard cannot reason about, so it is rejected by default.

`ALLOWED_ANONYMOUS` is deliberately short and each entry is a decision. Adding a
function because one query needed it is how an allowlist turns into a denylist with
extra steps.

R002 cannot do this job, since to it every `Anonymous` is the same node. The name has to
be inspected, and this is the only place in the guard that inspects a function name,
against an allowlist rather than a denylist.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.allowlist import ALLOWED_ANONYMOUS, KNOWN_DANGEROUS
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages


class FunctionAllowlistRule:
    """No unknown function survives the guard."""

    rule_id = "R003"
    code = "function_not_allowed"
    severity = Severity.SECURITY
    summary = "Every function is known and on the allowlist"
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
            # The decision stays here so mutation testing measures it: a function that
            # reads from outside the process is not retryable, an unknown one is. The
            # wording lives in `messages.py`, which is excluded from mutation.
            dangerous = name in KNOWN_DANGEROUS
            retryable = not dangerous
            message, suggestion = messages.function_not_allowed(name, dangerous=dangerous)
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
