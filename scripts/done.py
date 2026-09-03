#!/usr/bin/env python
"""`make done MILESTONE=N`: la ÚNICA definición de «hecho». CONSTITUCION §5.

Doce condiciones, en el orden de la constitución, parando en la primera que falla.
Escribe el resultado en `.claude/state/gate-status.json` —que el agente no edita a
mano: lo escribe el gate, y esto ES el gate— y crea el punto de retorno de
`.snapshots/`.

**Por qué para en el primer fallo y no acumula.** Porque el segundo número deja de
significar nada cuando el primero está mal: una cobertura medida sobre una suite que
no pasa no es una cobertura, es un número. La constitución lo dice en ese orden por
esa razón.

**Lo que NO hace:** decidir. No baja umbrales, no salta pasos, no interpreta. Si una
fase todavía no tiene una pieza, el paso correspondiente falla con el motivo, y eso
es información, no un obstáculo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import shutil
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, run

STATE = ROOT / ".claude" / "state"
GATE_STATUS = STATE / "gate-status.json"
INVENTORY = STATE / "test-inventory.json"
SNAPSHOTS = ROOT / ".snapshots"
GOALS = ROOT / "docs" / "GOALS.yaml"
THRESHOLDS = ROOT / "thresholds.lock"

PY = sys.executable

#: Formas de esquivar la suite. Prohibidas por `docs/RULES.md` y por el mecanismo 3 de
#: anti-gaming: son las cuatro maneras de no correr un test sin que se note.
FORBIDDEN_IN_TESTS = (
    (re.compile(r"@pytest\.mark\.skip\b"), "@pytest.mark.skip"),
    (re.compile(r"@pytest\.mark\.xfail\b"), "@pytest.mark.xfail"),
    (re.compile(r"\bpytest\.skip\("), "pytest.skip("),
    (re.compile(r"\bpytest\.xfail\("), "pytest.xfail("),
)

DEBT = re.compile(r"\b(TODO|FIXME|XXX|NotImplementedError)\b")


class GateError(Exception):
    """Un paso de la DoD que no pasa. Lleva el motivo, no solo el número."""


def step(number: int, title: str) -> None:
    print(f"\n[{number:2d}/13] {title}")


def check_statics() -> dict[str, object]:
    code, out = run([PY, "-m", "ruff", "check", "src", "tests", "scripts", "datagen"])
    if code:
        raise GateError(f"ruff check:\n{out}")
    code, out = run(
        [PY, "-m", "ruff", "format", "--check", "src", "tests", "scripts", "datagen"]
    )
    if code:
        raise GateError(f"ruff format --check:\n{out}")
    code, out = run([PY, "-m", "mypy", "src"])
    if code:
        raise GateError(f"mypy --strict:\n{out}")
    code, out = run([str(pathlib.Path(PY).parent / "lint-imports")])
    if code:
        raise GateError(f"lint-imports (contratos de capas):\n{out}")
    return {"ruff": "ok", "mypy": "ok", "import-linter": "ok"}


def check_suite() -> dict[str, object]:
    paths = [
        p
        for p in (
            "tests/unit",
            "tests/property",
            "tests/contract",
            "tests/integration",
            # `adversarial` ENTRA desde la fase 6. Estaba declarado en el mapa y no lo
            # corría nadie: una carpeta de tests que ningún target ejecuta es peor que
            # no tenerla, porque parece cubierta y no lo está.
            "tests/adversarial",
        )
        if any((ROOT / p).glob("test_*.py"))
    ]
    if not paths:
        raise GateError("no hay ni un fichero de test. Un gate sin suite no es un gate.")
    code, out = run([PY, "-m", "pytest", *paths, "--hypothesis-profile=gate", "-q"])
    if code:
        raise GateError(f"la suite no está verde:\n{out[-4000:]}")
    return {"paths": paths, "profile": "gate"}


def check_holdout() -> dict[str, object]:
    """La reserva. El agente NO la lee; la ejecuta y mira el código de salida.

    Mientras no exista, se declara ausente en vez de contar como aprobada: un
    holdout vacío que pasa es exactamente el número inflado que Q-005 evita.
    """
    holdout = ROOT / "tests" / "holdout"
    cases = list(holdout.glob("test_*.py"))
    if not cases:
        return {"present": False, "note": "todavía no existe: fase 2, subagente qa-adversario"}
    code, out = run([PY, "-m", "pytest", "tests/holdout", "-q"])
    if code:
        raise GateError(f"la reserva NO pasa:\n{out[-3000:]}")
    return {"present": True, "files": len(cases)}


def check_coverage(milestone: int) -> dict[str, object]:
    code, out = run(
        [
            PY,
            "-m",
            "pytest",
            "tests/unit",
            "tests/property",
            "tests/contract",
            "tests/integration",
            "-q",
            "--cov",
            "--cov-context=test",
            "--cov-report=json:evals/reports/coverage-contexts.json",
        ]
    )
    if code:
        raise GateError(f"la medida de cobertura falló:\n{out[-3000:]}")
    code, out = run([PY, "scripts/check_function_coverage.py"])
    if code:
        raise GateError(f"G-COV-FUNC:\n{out}")
    code, out = run([PY, "scripts/check_line_coverage.py"])
    if code:
        raise GateError(f"G-COV-LINE:\n{out}")
    return {"milestone": milestone}


def check_mutation(milestone: int) -> dict[str, object]:
    """`G-MUTATION` y `G-MUT-GUARD` bloquean desde la fase 3, y se miden desde la 2."""
    if milestone < 3:
        return {"skipped": True, "reason": "bloquea desde la fase 3 (GOALS.yaml)"}
    code, out = run([PY, "scripts/check_mutation.py"])
    if code:
        raise GateError(f"mutación:\n{out}")
    return {"measured": True}


#: Qué se MIDE en cada fase, y desde cuándo. Un `make done` que no ejecutase la
#: medida leería el artefacto de la vez anterior y daría por bueno un número viejo:
#: es la forma más silenciosa de que un gate deje de significar algo.
MEASUREMENTS: tuple[tuple[int, str, str], ...] = (
    (0, "check_gate_config.py", "I-16 · pyproject.toml y GOALS.yaml"),
    (0, "check_no_raw_sql.py", "I-02 · G-NO-RAW-SQL"),
    (0, "check_contracts.py", "I-17 · G-CONTRACTS-FROZEN"),
    (0, "check_catalog_fresh.py", "I-07 · G-CATALOG-FRESH"),
    (0, "check_secrets.py", "G-SECRETS"),
    (1, "check_resultset_eq.py", "G-RESULTSET-EQ"),
    (2, "check_failclosed.py", "I-04 · un solo except ancho"),
    (2, "check_role_source.py", "I-05 · el rol no sale de un dato"),
    (2, "check_rule_coverage.py", "RULES §3 · el caso es la unidad"),
    (2, "check_rules_registry.py", "I-01 · ninguna regla desaparece"),
    (2, "check_attack_coverage.py", "I-14 · la matriz sin filas vacías"),
    (2, "check_guard_property.py", "G-FAILCLOSED"),
    (2, "attack_dev.py", "G-WRITE-BLOCK-DEV · higiene, no evidencia"),
    (2, "attack_mut.py", "G-WRITE-BLOCK · mutación de AST"),
    (2, "attack_holdout.py", "G-WRITE-BLOCK · la RESERVA"),
    (2, "bench_guard.py", "G-GUARD-P95"),
    (3, "check_mutation_scope.py", "anti-gaming · lo excluido de la mutación no decide"),
    (3, "check_budget_invariant.py", "G-BUDGET-ESCAPE"),
    (3, "cost_calibration.py", "G-COST-CALIB"),
    (4, "pii_suite.py", "G-PII-LEAK · axioma, tres superficies contra el dataset"),
    (5, "check_audit_coverage.py", "G-AUDIT-COV · axioma, ninguna invocación sin registro"),
    (5, "check_audit_tamper.py", "G-AUDIT-TAMPER · >= 1.000 mutaciones de byte"),
    # La fase 6 se mide AQUÍ y no solo en `make eval-recovery`, por la regla de este
    # paso: leer el artefacto de la vez anterior no es medir. Puede correr en el gate
    # porque sale de las casetes —determinista y gratis, sin modelo—; lo que llama al
    # modelo es `make eval-refresh`, que es explícito y no entra en ninguna puerta.
    (6, "eval_recovery.py", "G-RECOVERY · desde casetes, sin modelo"),
    (6, "check_recovery_coverage.py", "G-RECOVERY-COV · toda regla con caso"),
)


def check_measurements(milestone: int) -> dict[str, object]:
    """Se MIDE ahora. Leer el artefacto de la vez anterior no es medir."""
    ran: list[str] = []
    for since, script, what in MEASUREMENTS:
        if since > milestone:
            continue
        code, out = run([PY, f"scripts/{script}"])
        if code:
            raise GateError(f"{what} ({script}):\n{out[-2500:]}")
        ran.append(script)
    return {"scripts": ran}


def check_goals(milestone: int) -> dict[str, object]:
    code, out = run([PY, "scripts/goals_check.py", "--milestone", str(milestone)])
    print(out.rstrip())
    if code:
        raise GateError("hay metas bloqueantes por debajo de su umbral (ver arriba)")
    return json.loads((ROOT / "evals" / "reports" / "goals-check.json").read_text("utf-8"))


def check_thresholds() -> dict[str, object]:
    if not THRESHOLDS.exists():
        raise GateError("no existe thresholds.lock. Lo genera Samuel, nunca el agente.")
    expected = THRESHOLDS.read_text(encoding="utf-8").strip().split()[0]
    actual = hashlib.sha256(GOALS.read_bytes()).hexdigest()
    if expected != actual:
        raise GateError(
            "sha256(docs/GOALS.yaml) NO coincide con thresholds.lock.\n"
            f"  esperado {expected}\n  medido   {actual}\n"
            "  Alguien cambió un umbral. GOALS.yaml solo lo edita Samuel, y solo él "
            "regenera el lock."
        )
    code, out = run([PY, "scripts/check_gate_config.py"])
    if code:
        raise GateError(f"I-16 · pyproject.toml y GOALS.yaml divergen:\n{out}")
    return {"sha256": actual}


def count_tests() -> dict[str, dict[str, int]]:
    """`{fichero: {n_tests, n_asserts}}`. Mecanismo 1 de anti-gaming."""
    inventory: dict[str, dict[str, int]] = {}
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        inventory[str(path.relative_to(ROOT))] = {
            "n_tests": len(re.findall(r"^\s*def test_", text, flags=re.MULTILINE)),
            "n_asserts": len(re.findall(r"\bassert\b", text)),
        }
    return inventory


def check_inventory() -> dict[str, object]:
    """Ningún fichero pierde tests ni asserts respecto del último cierre verde."""
    current = count_tests()
    totals = {
        "n_tests": sum(v["n_tests"] for v in current.values()),
        "n_asserts": sum(v["n_asserts"] for v in current.values()),
        "files": len(current),
    }
    if INVENTORY.exists():
        previous = json.loads(INVENTORY.read_text(encoding="utf-8"))
        regressions = []
        for name, before in previous.get("files", {}).items():
            after = current.get(name)
            if after is None:
                regressions.append(f"{name}: DESAPARECIÓ ({before['n_tests']} tests)")
                continue
            if after["n_tests"] < before["n_tests"]:
                regressions.append(f"{name}: {before['n_tests']} -> {after['n_tests']} tests")
            if after["n_asserts"] < before["n_asserts"]:
                regressions.append(
                    f"{name}: {before['n_asserts']} -> {after['n_asserts']} asserts"
                )
        if regressions:
            raise GateError(
                "el inventario de tests ha BAJADO desde el último cierre verde:\n  "
                + "\n  ".join(regressions)
                + "\n  Borrar o debilitar un test exige propuesta aprobada (§2.5)."
            )
    return totals


def check_debt() -> dict[str, object]:
    gate = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["gate"]
    limit = int(gate["deuda_max"])
    hits: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if DEBT.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{i}")
    if len(hits) > limit:
        raise GateError(
            f"{len(hits)} marcas de deuda en src/ y el tope es {limit}:\n  " + "\n  ".join(hits)
        )

    skips: list[str] = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern, label in FORBIDDEN_IN_TESTS:
            if pattern.search(text):
                skips.append(f"{path.relative_to(ROOT)}: {label}")
    if skips:
        raise GateError(
            "hay formas de esquivar la suite, y están prohibidas (mecanismo 3):\n  "
            + "\n  ".join(skips)
        )
    return {"debt": len(hits), "limit": limit, "skips": 0}


def check_docs(milestone: int) -> dict[str, object]:
    changelog = ROOT / "CHANGELOG.md"
    if not changelog.exists():
        raise GateError("no existe CHANGELOG.md")
    text = changelog.read_text(encoding="utf-8")
    if f"fase {milestone}" not in text.lower():
        raise GateError(
            f"CHANGELOG.md no tiene entrada para la fase {milestone}. Punto 11 de la "
            "DoD: una fase cerrada sin entrada es una fase que nadie puede leer después."
        )
    referenced: set[str] = set()
    missing = []
    for md in sorted((ROOT / "docs").rglob("*.md")):
        relative = md.relative_to(ROOT)
        # Los documentos COPIADOS de `_comun/` no son de este proyecto y llevan sus
        # propios ejemplos: exigirles que sus ADR existan aquí sería exigir que este
        # repositorio contenga los ADR de otro.
        if relative.parts[1] in {"CONTRACTS", "adr"} or md.name in {
            "CONSTITUCION.md",
            "STACK.md",
        }:
            continue
        text_md = md.read_text(encoding="utf-8")
        if md.name == "JOURNAL.md":
            # La entrada `[EJEMPLO]` del JOURNAL es la referencia de formato y el
            # propio fichero dice que el gate la ignora por ese marcador. Sus ADR
            # son inventados a propósito.
            text_md = re.sub(
                r"^## .*\[EJEMPLO\].*?(?=^## |\Z)", "", text_md, flags=re.MULTILINE | re.DOTALL
            )
        for lineno, line in enumerate(text_md.splitlines(), start=1):
            for match in re.finditer(r"ADR-(\d{3})(\s+de\b)?", line):
                # `ADR-002 de citebound-01` es una referencia CRUZADA a otro
                # proyecto. Este gate no puede exigir que exista aquí.
                if match.group(2):
                    continue
                adr = match.group(1)
                referenced.add(adr)
                if not list((ROOT / "docs" / "adr").glob(f"{adr}-*.md")):
                    missing.append(f"{relative}:{lineno} cita ADR-{adr} y no existe")
    return {"changelog": "ok", "adr_referenced": len(referenced)}


def check_secrets() -> dict[str, object]:
    baseline = ROOT / ".secrets.baseline"
    if not baseline.exists():
        raise GateError("no existe .secrets.baseline. `G-SECRETS` es un AXIOMA.")
    code, out = run([PY, "-m", "detect_secrets", "scan", "--baseline", ".secrets.baseline"])
    if code:
        raise GateError(f"detect-secrets encontró hallazgos NUEVOS:\n{out}")
    return {"baseline": "ok"}


def snapshot(milestone: int, stamp: str) -> str:
    """El punto de retorno que crea `make done`. Sigue existiendo con git al lado."""
    target = SNAPSHOTS / f"milestone-{milestone}-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("src", "tests", "scripts", "docs", "prompts", "attacks", "evals"):
        source = ROOT / name
        if source.exists():
            shutil.copytree(source, target / name, dirs_exist_ok=True)
    for name in ("pyproject.toml", "Makefile", "uv.lock", "thresholds.lock", "models.lock"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, target / name)
    return str(target.relative_to(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--milestone", type=int, required=True)
    parser.add_argument("--no-snapshot", action="store_true")
    args = parser.parse_args()
    stamp = dt.datetime.now(tz=dt.UTC).strftime("%Y%m%dT%H%M%SZ")

    steps = [
        ("Estáticos limpios", check_statics),
        ("Suite completa verde", check_suite),
        ("Reserva verde", check_holdout),
        ("Cobertura por función y de línea", lambda: check_coverage(args.milestone)),
        ("Mutación", lambda: check_mutation(args.milestone)),
        ("Mediciones de la fase", lambda: check_measurements(args.milestone)),
        ("Metas activas", lambda: check_goals(args.milestone)),
        ("Umbrales intactos", check_thresholds),
        ("Inventario de tests", check_inventory),
        ("Deuda y esquivas", check_debt),
        ("Documentación", lambda: check_docs(args.milestone)),
        ("Sin secretos", check_secrets),
    ]

    results: dict[str, object] = {}
    print(f"make done MILESTONE={args.milestone} · {stamp}")
    for i, (title, fn) in enumerate(steps, start=1):
        step(i, title)
        try:
            results[title] = fn()
        except GateError as exc:
            print(f"\n  FALLO: {exc}")
            _write_status(args.milestone, stamp, passed=False, results=results, failed=title)
            print(f"\nmake done MILESTONE={args.milestone}: ROJO en «{title}».")
            return 1
        print("        ok")

    step(13, "Punto de retorno")
    results["snapshot"] = "omitido" if args.no_snapshot else snapshot(args.milestone, stamp)
    print(f"        {results['snapshot']}")

    _write_rules_registry(stamp)
    inventory = count_tests()
    INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY.write_text(
        json.dumps({"stamp": stamp, "files": inventory}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_status(args.milestone, stamp, passed=True, results=results, failed=None)
    print(f"\nmake done MILESTONE={args.milestone}: VERDE")
    return 0


def _write_rules_registry(stamp: str) -> None:
    """`.claude/state/rules-registry.json`, que **lo escribe el gate** (I-01).

    Se escribe SOLO al cerrar en verde, y por eso vale: el registro guarda el estado
    de un cierre que pasó, y el siguiente `make done` compara contra él. Si se
    escribiera en cada ejecución, una regla borrada quedaría registrada como
    inexistente y la comparación no detectaría nada.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_rules_registry import snapshot

    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "rules-registry.json").write_text(
        json.dumps(
            {"stamp": stamp, "written_by": "scripts/done.py", "rules": snapshot()},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_status(
    milestone: int,
    stamp: str,
    *,
    passed: bool,
    results: dict[str, object],
    failed: str | None,
) -> None:
    """Lo escribe el GATE, no el agente. Es el artefacto de la constitución §5."""
    STATE.mkdir(parents=True, exist_ok=True)
    GATE_STATUS.write_text(
        json.dumps(
            {
                "milestone": milestone,
                "passed": passed,
                "failed_step": failed,
                "gate_verde_en": stamp if passed else None,
                "ultima_verificacion": stamp,
                "results": results,
                "written_by": "scripts/done.py",
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
