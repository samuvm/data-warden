#!/usr/bin/env python
"""`make done MILESTONE=N`: la única definición de "hecho" del proyecto.

Doce condiciones, en el orden de la Definition of Done, parando en la primera que
falla. Escribe el resultado en `.gate/gate-status.json`, que no se edita a mano
(lo escribe este script), y crea el punto de retorno de `.snapshots/`.

Para en el primer fallo y no acumula porque el segundo número deja de significar
nada cuando el primero está mal: una cobertura medida sobre una suite que no pasa
no es una cobertura.

No decide nada: no baja umbrales, no salta pasos, no interpreta. Si a un hito le
falta todavía una pieza, el paso correspondiente falla con el motivo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, run

STATE = ROOT / ".gate"
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
            # Una carpeta de tests que ningún target ejecuta es peor que no tenerla:
            # parece cubierta y no lo está.
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
    """La reserva. No se lee: se ejecuta y se mira el código de salida.

    Mientras no exista, se declara ausente en vez de contar como aprobada: un
    holdout vacío que pasa es exactamente el número inflado que la reserva existe
    para evitar.
    """
    holdout = ROOT / "tests" / "holdout"
    cases = list(holdout.glob("test_*.py"))
    if not cases:
        return {"present": False, "note": "ausente: tests/holdout/ no tiene casos"}
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
    """`G-MUTATION` y `G-MUT-GUARD` bloquean desde MILESTONE=3 y se miden desde el 2."""
    if milestone < 3:
        return {"skipped": True, "reason": "bloquea desde MILESTONE=3 (GOALS.yaml)"}
    code, out = run([PY, "scripts/check_mutation.py"])
    if code:
        raise GateError(f"mutación:\n{out}")
    return {"measured": True}


#: Qué se mide en cada hito y desde cuál. Un `make done` que no ejecutase la medida
#: leería el artefacto de la vez anterior y daría por bueno un número viejo.
MEASUREMENTS: tuple[tuple[int, str, str], ...] = (
    (0, "check_gate_config.py", "I-16 · pyproject.toml y GOALS.yaml"),
    (0, "check_no_raw_sql.py", "I-02 · G-NO-RAW-SQL"),
    (0, "check_contracts.py", "I-17 · G-CONTRACTS-FROZEN"),
    (0, "check_catalog_fresh.py", "I-07 · G-CATALOG-FRESH"),
    (0, "check_secrets.py", "G-SECRETS"),
    (1, "check_resultset_eq.py", "G-RESULTSET-EQ"),
    (2, "check_failclosed.py", "I-04 · un solo except ancho"),
    (2, "check_role_source.py", "I-05 · el rol no sale de un dato"),
    # El ejecutor auditado llegó a saltarse el anillo 4 mientras `G-PII-LEAK` pasaba,
    # porque la suite medía otro camino. Esto impide la reincidencia.
    (4, "check_mask_path.py", "el unico camino al motor pasa por el anillo 4"),
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
    # La recuperación se mide aquí y no solo en `make eval-recovery`: leer el artefacto
    # de la vez anterior no es medir. Puede correr en el gate porque sale de las
    # casetes (determinista, sin modelo); lo que llama al modelo es `make eval-refresh`,
    # que es explícito y no entra en ninguna puerta.
    (6, "eval_recovery.py", "G-RECOVERY · desde casetes, sin modelo"),
    (6, "check_recovery_coverage.py", "G-RECOVERY-COV · toda regla con caso"),
    (7, "mcp_conformance.py", "G-MCP-CONFORM · los 11 puntos de la spec 2026-07-28"),
    # El servidor contestando por stdio. `mcp_conformance` llegó a dar 11/11 mientras
    # `run_query` fallaba: valida la forma de lo publicado, no que el servidor responda.
    (7, "check_mcp_live.py", "el servidor MCP contestando por stdio, cliente real"),
    (7, "check_role_spoof.py", "G-ROLE-SPOOF · axioma, `_meta` es dato y no autoridad"),
    (7, "eval_toolchoice.py", "G-TOOL-CHOICE · desde casetes, sin modelo"),
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
        raise GateError("no existe thresholds.lock. Se genera a mano al sellar GOALS.yaml.")
    expected = THRESHOLDS.read_text(encoding="utf-8").strip().split()[0]
    actual = hashlib.sha256(GOALS.read_bytes()).hexdigest()
    if expected != actual:
        raise GateError(
            "sha256(docs/GOALS.yaml) NO coincide con thresholds.lock.\n"
            f"  esperado {expected}\n  medido   {actual}\n"
            "  Alguien cambió un umbral. GOALS.yaml está sellado por thresholds.lock: "
            "cambiar un umbral exige una decisión explícita y regenerar el lock."
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
                + "\n  Borrar o debilitar un test exige una decisión explícita y documentada."
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
    # Se exige una entrada, no una mención: buscar el número en cualquier parte del
    # fichero aprobaría por una coincidencia en la prosa de otra entrada. Por eso se
    # busca la cabecera de la versión, marcada con `<!-- milestone N -->` (invisible al
    # renderizar).
    cabecera = re.compile(rf"^## \[[^\]]+\].*<!-- milestone {milestone} -->", re.MULTILINE)
    if not cabecera.search(text):
        raise GateError(
            f"CHANGELOG.md no tiene entrada para MILESTONE={milestone} (se busca una "
            f"cabecera `## [x.y.z] · … <!-- milestone {milestone} -->`, no una mención). "
            "Punto 11 de la DoD: un hito cerrado sin entrada es un hito que nadie puede "
            "leer después."
        )
    referenced: set[str] = set()
    missing = []
    documents = sorted((ROOT / "docs").rglob("*.md"))
    ignored = _ignored_by_git(documents)
    for md in documents:
        relative = md.relative_to(ROOT)
        # `docs/CONTRACTS/` son contratos congelados con sus propios ejemplos y
        # `docs/adr/` contiene la plantilla, que cita ADR ficticios. Lo que git ignora
        # son notas locales, no documentación del proyecto.
        if relative.parts[1] in {"CONTRACTS", "adr"} or md.name == "STACK.md" or md in ignored:
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
                # `ADR-002 de <proyecto>` es una referencia a un ADR externo; este gate
                # no puede exigir que exista aquí.
                if match.group(2):
                    continue
                adr = match.group(1)
                referenced.add(adr)
                if not list((ROOT / "docs" / "adr").glob(f"{adr}-*.md")):
                    missing.append(f"{relative}:{lineno} cita ADR-{adr} y no existe")
    # Punto 11 de la DoD: todo ADR referenciado existe.
    if missing:
        raise GateError(
            "hay ADR citados que no existen (punto 11 de la DoD):\n  " + "\n  ".join(missing)
        )
    return {"changelog": "ok", "adr_referenced": len(referenced)}


def _ignored_by_git(paths: list[pathlib.Path]) -> set[pathlib.Path]:
    """Los ficheros de `paths` que git ignora. Sin git disponible, ninguno."""
    if not paths:
        return set()
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=str(ROOT),
            input="\n".join(str(p.relative_to(ROOT)) for p in paths),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    return {ROOT / line for line in result.stdout.splitlines() if line.strip()}


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
        ("Mediciones del hito", lambda: check_measurements(args.milestone)),
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
    """`.gate/rules-registry.json`, que solo escribe el gate (I-01).

    Se escribe solo al cerrar en verde, y por eso vale: el registro guarda el estado
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
    """Lo escribe el gate, no una persona. Es el artefacto de la Definition of Done."""
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
