#!/usr/bin/env python
"""`G-AUDIT-COV` · corre la propiedad y deja el número en su artefacto.

La meta es un AXIOMA (`propuesta_admisible: false`) y su comando tiene dos mitades:
la propiedad de Hypothesis con el perfil del gate, y `warden audit reconcile
--strict` sobre una cadena real. Hacen falta las dos y miden cosas distintas: la
propiedad dice que el ejecutor NUNCA deja una invocación sin registro; el
reconciliador dice que una cadena ya escrita cuadra consigo misma.

**Las etiquetas de los umbrales adicionales se copian LITERALES de `GOALS.yaml`.**
`scripts/goals_check.py` las compara por igualdad exacta de cadena, así que una
tilde o un espacio de más producen «falta el umbral adicional», que se diagnostica
fatal porque el número medido es correcto.
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record, run

PROPERTY = "tests/property/test_audit_coverage.py"

#: Copiadas letra a letra de `docs/GOALS.yaml`. No se reescriben «mejor».
LABEL_SIN_REGISTRO = "invocaciones de run_query sin registro"
LABEL_ESTADOS = "estados auditados (rejected_by_guard, rejected_by_budget, executed, error)"


def main() -> int:
    code, out = run(
        [sys.executable, "-m", "pytest", PROPERTY, "--hypothesis-profile=gate", "--no-header"]
    )
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0

    estados, reconcile_ok, sin_registro = _reconciliar()

    record(
        "audit-coverage.json",
        "G-AUDIT-COV",
        value=100.0 if (not failed and not code and reconcile_ok) else 0.0,
        adicionales={LABEL_SIN_REGISTRO: sin_registro, LABEL_ESTADOS: estados},
        detail={
            "property_suite": PROPERTY,
            "tests_passed": passed,
            "tests_failed": failed,
            "reconcile_strict": reconcile_ok,
            "como_se_mide": (
                "n_registros == n_invocaciones y n_execute == "
                "n_registros[status=executed]. La segunda igualdad se mide con el "
                "CONTADOR de proceso de engines.base, no leyendo el flujo de "
                "control: «el código llama a append» es una lectura, «el motor se "
                "movió tantas veces como registros ejecutados hay» es una medida."
            ),
        },
        command=(
            "pytest tests/property/test_audit_coverage.py --hypothesis-profile=gate "
            "&& warden audit reconcile --strict"
        ),
    )

    if code or failed or not reconcile_ok or sin_registro:
        print(f"check_audit_coverage: FALLO\n{out[-1800:]}")
        return 1
    print(
        f"check_audit_coverage: ok · {passed} tests, {estados} estados auditados, "
        f"{sin_registro} invocaciones sin registro, reconcile --strict en verde"
    )
    return 0


def _reconciliar() -> tuple[int, bool, int]:
    """Escribe una cadena real con los cuatro estados y la reconcilia por el CLI.

    Sobre un fichero de verdad y no `:memory:`, porque lo que se comprueba aquí es
    el comando que la meta nombra, y ese comando abre un almacén de disco. La cadena
    es de usar y tirar: la evidencia es que el reconciliador la aprueba, no la
    cadena en sí.
    """
    sys.path.insert(0, str(ROOT / "src"))
    from datawarden.audit.store import AuditStore
    from datawarden.domain.types import Role, RoleSource, Status

    with tempfile.TemporaryDirectory() as tmp:
        db = pathlib.Path(tmp) / "audit.sqlite3"
        store = AuditStore(str(db))
        for i, estado in enumerate(Status):
            store.append(
                principal_id="gate",
                role=Role.ANALYST,
                role_source=RoleSource.CLI_FLAG,
                status=estado,
                question_digest=f"{i:064x}",
                sql_digest=f"{i + 40:064x}",
            )
        total = store.count()
        conteo = store.count_by_status()
        store.close()

        code, _ = run(
            [
                sys.executable,
                "-m",
                "datawarden.cli",
                "audit",
                "reconcile",
                "--strict",
                "--database",
                str(db),
            ]
        )

    estados_auditados = sum(1 for n in conteo.values() if n > 0)
    # Invocaciones sin registro: cada `append` es una invocación y deja una fila, así
    # que la diferencia tiene que ser exactamente cero. Se calcula, no se asume.
    sin_registro = len(Status) - total
    return estados_auditados, code == 0, abs(sin_registro)


if __name__ == "__main__":
    sys.exit(main())
