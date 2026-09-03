#!/usr/bin/env python
"""`G-RECOVERY-COV` · toda regla que puede rechazar tiene un caso de recuperación.

**Una regla que rechaza sin caso de recuperación es una regla cuyo mensaje de error
nadie ha comprobado que sea accionable.** Y el mensaje es lo que este proyecto
publica como su tesis: *el valor no está en la tasa de acierto, está en la garantía
sobre el fallo.* Un rechazo que no se puede seguir es un fallo sin garantía.

**No cuenta ficheros: EJECUTA EL GUARD.** Un corpus se puede tener y no sembrar
nada: basta con que una regla cambie, con que el catálogo pierda una columna o con
que sqlglot renombre una clave de `args` —le pasó a R005 el 2026-09-02— para que una
semilla que se creía rechazada pase de largo sin que nadie se entere. Aquí cada
semilla se revalida en cada medida y se exige que dispare LA REGLA QUE DECLARA, no
solo que haya rechazo: un caso que se cree de R008 y que en realidad para R002 por
accidente deja a R008 con un agujero el día que R002 cambie.

**La exención de R009 se COMPRUEBA, no se cree.** `SELECT *` no puede sembrar un
rechazo de R009 de punta a punta porque `qualify()` expande la estrella antes de que
la regla corra. Eso reduciría el denominador, que es la forma barata de sacar un
100 %, así que la exención solo vale si sus tres consultas se ACEPTAN de verdad: es
la prueba de que la estrella se expandió. El día que `qualify()` deje de expandir,
las tres dejarán de aceptarse y esto sale rojo en vez de seguir tapando la regla.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import Principal, Role, RoleSource, ValidatedQuery
from datawarden.guard.registry import BY_ID
from datawarden.guard.validator import validate
from datawarden.principal import POLICY_PATH
from datawarden.principal.policy import load_policy
from gatelib import ROOT, record
from recoverylib import RecoveryCase, read

#: El tope de filas con el que se valida. Es el del rol más amplio: acotarlo más
#: haría que R006 disparara en semillas que están sembrando otra cosa.
MAX_ROWS = 50_000

#: Reglas EXENTAS de tener un rechazo sembrado, con su motivo escrito y con la
#: prueba que lo sostiene. La lista tiene que ser corta y cada entrada tiene que
#: explicarse: una exención sin motivo es un denominador recortado con permiso.
EXEMPT: dict[str, str] = {
    "R009": (
        "defensa en profundidad: `qualify()` expande la estrella contra el catálogo "
        "ANTES de que R009 corra, así que no hay consulta que la haga saltar de punta "
        "a punta. La regla existe para el caso en que `qualify()` NO expanda y "
        "devuelva el árbol con la estrella dentro sin error, y se ejercita a nivel de "
        "regla en tests/unit/guard/cases/R009.yaml. La exención NO se cree: la "
        "sección `expansion_de_estrella` del corpus la comprueba exigiendo que sus "
        "consultas se acepten, que es la prueba de que la estrella se expandió."
    ),
}


def main() -> int:
    corpus = read(ROOT)
    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)

    def verdict(case: RecoveryCase) -> tuple[bool, str]:
        who = Principal(
            id=f"recovery-{case.case_id}",
            role=Role(case.role),
            source=RoleSource.CLI_FLAG,
        )
        result = validate(
            case.sql, principal=who, schema=schema, policy=policy, max_rows=MAX_ROWS
        )
        if isinstance(result, ValidatedQuery):
            return True, "ACEPTADA"
        return False, f"{result.rule_id}/{result.code}"

    problems: list[str] = []
    per_rule: dict[str, int] = {}

    # 1 · toda semilla siembra, y siembra LA REGLA QUE DICE.
    for case in corpus.seeded:
        accepted, got = verdict(case)
        if accepted:
            problems.append(
                f"{case.case_id}: la semilla de {case.rule_id} ya NO rechaza. Un "
                "corpus cuyas semillas no siembran nada es folclore, no evidencia"
            )
            continue
        fired = got.split("/")[0]
        if fired != case.rule_id:
            problems.append(
                f"{case.case_id}: declara {case.rule_id} y dispara {got}. Un caso que "
                f"se cree de {case.rule_id} y que para por otra regla deja a "
                f"{case.rule_id} con un agujero el día que la otra cambie"
            )
            continue
        per_rule[case.rule_id] = per_rule.get(case.rule_id, 0) + 1

    # 2 · la exención se comprueba: sus consultas TIENEN que aceptarse.
    verified_exempt: set[str] = set()
    for rule_id, reason in EXEMPT.items():
        proofs = [c for c in corpus.expanded if c.rule_id == rule_id]
        if not proofs:
            problems.append(
                f"{rule_id} está exenta y no tiene ninguna consulta que lo pruebe. "
                "Una exención en prosa recorta el denominador sin enseñar nada"
            )
            continue
        failed = [(c.case_id, verdict(c)[1]) for c in proofs if not verdict(c)[0]]
        if failed:
            problems.append(
                f"{rule_id}: la exención ya NO es cierta. {failed} no se aceptan, así "
                f"que la estrella no se está expandiendo. El motivo escrito era: {reason}"
            )
            continue
        verified_exempt.add(rule_id)

    # 3 · el porcentaje. El denominador son TODAS las reglas activas.
    active = set(BY_ID)
    covered = set(per_rule) | verified_exempt
    missing = sorted(active - covered)
    for rule_id in missing:
        problems.append(
            f"{rule_id} puede rechazar y no tiene ningún caso de recuperación. Su "
            "mensaje de error no lo ha comprobado nadie"
        )
    percentage = round(100.0 * len(covered) / len(active), 2) if active else 0.0

    record(
        "recovery.json",
        "G-RECOVERY-COV",
        value=percentage,
        detail={
            "active_rules": len(active),
            "covered": sorted(covered),
            "exempt_verified": sorted(verified_exempt),
            "missing": missing,
            "seeded_cases": len(corpus.seeded),
            "cases_per_rule": dict(sorted(per_rule.items())),
            "problems": problems,
        },
        command="python scripts/check_recovery_coverage.py",
    )

    if problems:
        print(f"check_recovery_coverage: FALLO · {len(problems)} problemas")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"check_recovery_coverage: OK · {percentage} % "
        f"({len(covered)}/{len(active)} reglas, {len(corpus.seeded)} rechazos sembrados, "
        f"{len(verified_exempt)} exención comprobada)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
