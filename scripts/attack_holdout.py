#!/usr/bin/env python
"""`G-WRITE-BLOCK` · la RESERVA. Es el único número publicable de seguridad.

**El agente que escribió el guard no ha visto ni un solo caso de este fichero.** Los
escribió el subagente `qa-adversario` contra la ESPECIFICACIÓN —`docs/spec/policy.yaml`
firmado, el glosario y el catálogo generado— y sin acceso a `src/datawarden/guard/`,
a `attacks/` ni a `tests/unit/guard/cases/`. Es la condición 1 de la respuesta de
Samuel a Q-005, y es lo que convierte «bloqueo del 100 %» en evidencia en vez de en
un examen escrito por el examinando.

**Qué se puede afirmar con quince casos, y qué no.** Con 15/15 en verde el intervalo
de Wilson al 95 % para «bloqueo del 100 %» es aproximadamente **[0,80 - 1,00]**. Eso
es honesto y es mucho mejor que cuarenta casos autoescritos, pero **NO es «100 % de
bloqueo» a secas**, y el README no puede decir eso. Se publica el intervalo. Subir a
30 casos estrecharía el límite inferior a ~0,88: es la mejor compra de credibilidad
por hora que tiene el proyecto.

**Y si el guard falla contra la reserva, se arregla el GUARD.** Reescribir un caso
porque «estaba mal planteado» es la forma exacta en que estos conjuntos se degradan.
El fichero se congela por hash para que esa tentación sea un fallo de gate.

Este script NO imprime el SQL de ningún caso: imprime identificadores y veredictos.
Que el informe del holdout filtre sus casos sería filtrar el examen.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.catalog.statistics import load as load_stats
from datawarden.cost import STATISTICS_PATH
from datawarden.cost.screen import screen
from datawarden.domain.types import (
    Principal,
    RejectionReason,
    Role,
    RoleSource,
)
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy
from gatelib import ROOT, record, wilson

CASES = ROOT / "tests" / "holdout" / "cases.yaml"


def main() -> int:
    if not CASES.exists():
        print(
            "attack_holdout: FALLO · no existe tests/holdout/cases.yaml.\n"
            "  La reserva la escribe el subagente `qa-adversario` (Q-005). Sin ella no\n"
            "  hay número publicable, y contarlo como aprobado sería exactamente el\n"
            "  número inflado que la separación dev/holdout existe para evitar."
        )
        record(
            "attack-holdout.json",
            "G-WRITE-BLOCK",
            value=1,
            adicionales={"casos de holdout superados": 0, "mutantes de AST evaluados": 0},
            detail={"present": False},
            command="make attack-holdout",
        )
        return 1

    raw = CASES.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    data = yaml.safe_load(raw.decode("utf-8"))
    cases = data["casos"]

    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    budgets = load_budgets(BUDGETS_PATH)
    stats = load_stats(STATISTICS_PATH)

    rows: list[dict[str, object]] = []
    passed = 0
    for case in cases:
        role = Role(case["rol"])
        # Se ejercitan los DOS anillos, no solo el guard: la reserva prueba el
        # SISTEMA. Un caso que ataque por el presupuesto tiene que morir en el
        # presupuesto, y con `validate()` a secas ni siquiera se le preguntaría.
        result = screen(
            case["sql"],
            principal=Principal(id="holdout", role=role, source=RoleSource.CLI_FLAG),
            schema=schema,
            policy=policy,
            budgets=budgets,
            stats=stats,
        )
        verdict = result.rejection
        rejected = isinstance(verdict, RejectionReason)
        expected_rejection = bool(case.get("espera_rechazo", True))
        ok = rejected == expected_rejection
        passed += int(ok)
        rows.append(
            {
                "id": case["id"],
                "role": role.value,
                "expected_rejection": expected_rejection,
                "was_rejected": rejected,
                "rule_id": verdict.rule_id if rejected else None,
                "passed": ok,
            }
        )

    total = len(rows)
    low, high = wilson(passed, total)
    mut = _mutants_evaluated()

    record(
        "attack-holdout.json",
        "G-WRITE-BLOCK",
        value=total - passed,
        adicionales={
            "casos de holdout superados": passed,
            "mutantes de AST evaluados": mut,
        },
        detail={
            "total": total,
            "passed": passed,
            "wilson_95": [round(low, 4), round(high, 4)],
            "sha256": digest,
            "cases": rows,
            "como_se_publica": (
                f"bloqueo del holdout {passed}/{total}, intervalo de Wilson 95 % "
                f"[{low:.2f} - {high:.2f}]. NUNCA «100 % de bloqueo» a secas."
            ),
            "autoevaluado": True,
            "por_que_autoevaluado": (
                "Mientras D-09 no esté instalado, el `deny` de lectura sobre "
                "tests/holdout/** es DISCIPLINA y no ejecución, así que el número sale "
                "etiquetado como autoevaluado (condición 3 de Q-005 y matiz de D-06)."
            ),
        },
        command="make attack-holdout && make attack-mut",
    )

    for row in rows:
        if row["passed"]:
            continue
        estado = "lo rechazó" if row["was_rejected"] else "PASÓ EL GUARD"
        print(f"  FALLO {row['id']} (rol {row['role']}): {estado}")

    print(f"\nattack_holdout: {passed}/{total} · Wilson 95 % [{low:.2f} - {high:.2f}]")
    print(f"  sha256 de la reserva: {digest}")
    print(
        "  SE PUBLICA EL INTERVALO, no el punto. Y el número sale ETIQUETADO COMO\n"
        "  AUTOEVALUADO mientras D-09 no esté instalado: el aislamiento del holdout es\n"
        "  hoy disciplina y no ejecución."
    )
    if passed != total:
        print(
            "\n  Un caso que la reserva no pasa se arregla EN EL GUARD. Reescribir el "
            "caso\n  porque «estaba mal planteado» es cómo se degrada un holdout."
        )
        return 1
    return 0


def _mutants_evaluated() -> int:
    from gatelib import read_meta

    meta = read_meta("attack-mut.json", "G-WRITE-BLOCK")
    if meta is None:
        return 0
    return int(meta.get("adicionales", {}).get("mutantes de AST evaluados", 0))


if __name__ == "__main__":
    sys.exit(main())
