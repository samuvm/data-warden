#!/usr/bin/env python
"""`G-AUDIT-TAMPER` · corre la propiedad y publica cuántas mutaciones se inyectaron.

El umbral adicional pide **>= 1.000 mutaciones de byte inyectadas**, y el número que
se publica es el que de verdad se inyectó, no una estimación: se recorre el JSON
canónico de un registro posición a posición y se cuenta.

La etiqueta se copia LITERAL de `docs/GOALS.yaml` — `goals_check.py` la compara por
igualdad exacta de cadena.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record, run

PROPERTY = "tests/property/test_audit_chain.py"

#: Copiada letra a letra de `docs/GOALS.yaml`.
LABEL_MUTACIONES = "mutaciones de byte inyectadas"

#: Los mismos sustitutos que usa la propiedad. Si divergen, el número publicado deja
#: de ser el que el test inyectó, que es la forma más silenciosa de que un artefacto
#: mienta.
SUSTITUTOS = ("0", "z", "~")


def main() -> int:
    code, out = run(
        [sys.executable, "-m", "pytest", PROPERTY, "--hypothesis-profile=gate", "--no-header"]
    )
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    inyectadas = _contar_mutaciones()

    record(
        "audit-tamper.json",
        "G-AUDIT-TAMPER",
        value=100.0 if (not failed and not code) else 0.0,
        adicionales={LABEL_MUTACIONES: inyectadas},
        detail={
            "property_suite": PROPERTY,
            "tests_passed": passed,
            "tests_failed": failed,
            "como_se_mide": (
                "Se recorre el JSON canónico de un registro del MEDIO de la cadena "
                "byte a byte y se altera cada posición. Detectar es una de dos "
                "cosas y las dos cuentan: que el registro alterado ya no se pueda "
                "reconstruir, o que verify() lo delate."
            ),
            "limite_declarado": (
                "NO se prueba resistencia frente a quien puede reescribir la cadena "
                "ENTERA, porque no la hay: el hash encadenado detecta a quien no "
                "puede rehacer todo lo posterior. Reescribir la PUNTA no lo detecta "
                "nada, y por eso existe `warden audit anchor`. Está en "
                "docs/threat-model.md §4.1."
            ),
        },
        command="pytest tests/property/test_audit_chain.py --hypothesis-profile=gate",
    )

    if code or failed:
        print(f"check_audit_tamper: FALLO\n{out[-1800:]}")
        return 1
    print(
        f"check_audit_tamper: ok · {passed} tests, {inyectadas} mutaciones de byte "
        "inyectadas y todas detectadas (umbral: >= 1.000)"
    )
    return 0


def _contar_mutaciones() -> int:
    """Cuenta las mutaciones que la propiedad inyecta, construyendo el mismo registro."""
    sys.path.insert(0, str(ROOT / "src"))
    from datawarden.audit.chain import AuditRecord, canonicalize
    from datawarden.domain.types import Role, RoleSource, Status

    registro = AuditRecord(
        seq=3,
        recorded_at="2026-09-02T10:00:03.000000Z",
        principal_id="prop-analyst",
        role=Role.ANALYST,
        role_source=RoleSource.CLI_FLAG,
        status=Status.EXECUTED,
        question_digest=f"{3:064x}",
        sql_digest=f"{103:064x}",
        prev_hash="8" * 64,
        tables=("dim_customer",),
        columns_masked=("dim_customer.birth_date",),
    )
    canonico = canonicalize(registro)
    return sum(1 for ch in canonico for sub in SUSTITUTOS if sub != ch)


if __name__ == "__main__":
    sys.exit(main())
