#!/usr/bin/env python
"""La tabla de números del README, GENERADA desde los artefactos. Nunca escrita a mano.

**Existe porque el README llegó a ir meses por detrás del código.** Publicaba la mutación
en rojo cuando llevaba dos fases en verde, 427 tests cuando había 820, y decía que dos de
los cinco anillos «todavía no existían» con los cinco funcionando. Ninguna de esas frases
era mentira el día que se escribió; todas lo eran el día que alguien las leyó. Un número
copiado a mano en un documento es un número con fecha de caducidad y sin etiqueta.

Así que la tabla sale de `evals/reports/` —los mismos JSON que lee `goals_check.py`— y
cada fila lleva el comando que la reproduce. Si un artefacto no está, la fila lo dice en
vez de inventar un valor: una casilla con «sin medir» es información; un número viejo
con cara de nuevo, no.

    make readme                                # reescribe la tabla del README
    python scripts/readme_numbers.py --check   # sale 1 si el README no coincide

No entra en el gate a propósito: la latencia oscila entre ejecuciones (0,80 - 0,89 ms de
p95), y un check de igualdad exacta se pondría rojo después de cada `make bench-guard`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Callable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, wilson

README = ROOT / "README.md"
REPORTS = ROOT / "evals" / "reports"
INICIO = "<!-- numeros:inicio · lo genera `make readme`, no se edita a mano -->"
FIN = "<!-- numeros:fin -->"

#: La raya de los intervalos. Con nombre y no literal: es un carácter que se confunde
#: con un guion a simple vista, y aquí separa dos números que no se pueden restar.
RAYA = "\u2013"

Metrica = dict[str, Any]
Fila = tuple[str, str, str, Callable[[], str | None]]


# ------------------------------------------------------------------ lectura ---


def meta(artefacto: str, meta_id: str) -> Metrica | None:
    """Una meta de un artefacto del gate (`{"metas": {...}}`). `None` si no está."""
    try:
        datos = json.loads((REPORTS / artefacto).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    valor = datos.get("metas", {}).get(meta_id)
    return valor if isinstance(valor, dict) else None


def informe(nombre: str) -> Metrica | None:
    """La métrica de un informe conforme a `docs/CONTRACTS/eval-report.schema.json`."""
    try:
        datos = json.loads((REPORTS / nombre).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    metricas = datos.get("metrics") or []
    return metricas[0] if metricas else None


def adicional(m: Metrica, prefijo: str) -> float:
    for clave, valor in (m.get("adicionales") or {}).items():
        if clave.startswith(prefijo):
            return float(valor)
    return 0.0


# ---------------------------------------------------------------- formato ---


def num(valor: float, decimales: int = 2) -> str:
    """Formato español: coma decimal y punto de millares."""
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "·").replace(".", ",").replace("·", ".")


def intervalo(bajo: float, alto: float) -> str:
    return f"Wilson 95 % [{num(bajo)} {RAYA} {num(alto)}]"


def proporcion(m: Metrica) -> str:
    """`0,517 (31/60) · Wilson 95 % [...]` desde un informe con `value`, `n` y `ci95`."""
    aciertos = round(float(m["value"]) * int(m["n"]))
    bajo, alto = m["ci95"]
    return f"**{num(float(m['value']), 3)}** ({aciertos}/{m['n']}) · {intervalo(bajo, alto)}"


def cero(m: Metrica) -> str:
    return f"**{int(m['value'])}**"


def porcentaje(decimales: int) -> Callable[[Metrica], str]:
    return lambda m: f"**{num(float(m['value']), decimales)} %**"


# --------------------------------------------------------------- las filas ---


def de_meta(
    artefacto: str, meta_id: str, formato: Callable[[Metrica], str]
) -> Callable[[], str | None]:
    def valor() -> str | None:
        m = meta(artefacto, meta_id)
        return None if m is None else formato(m)

    return valor


def de_informe(nombre: str) -> Callable[[], str | None]:
    def valor() -> str | None:
        m = informe(nombre)
        return None if m is None else proporcion(m)

    return valor


def holdout(m: Metrica) -> str:
    casos = int(adicional(m, "casos de holdout"))
    bajo, alto = wilson(casos, casos)
    return f"**{int(m['value'])} evasiones** · reserva {casos}/{casos}, {intervalo(bajo, alto)}"


def mutacion_ast(m: Metrica) -> str:
    return f"**{int(m['value'])} evasiones** en {num(adicional(m, 'mutantes'), 0)} mutantes"


def fail_closed(m: Metrica) -> str:
    return f"**{int(m['value'])}** en {num(adicional(m, 'entradas'), 0)} entradas arbitrarias"


def presupuesto(m: Metrica) -> str:
    ms = num(adicional(m, "rechazo"), 1)
    return f"**{int(m['value'])}** · una de 3 GB se rechaza en {ms} ms"


def pii(m: Metrica) -> str:
    return f"**{int(m['value'])}** en proyección, predicado y agregación"


def latencia(m: Metrica) -> str:
    p99 = num(adicional(m, "p99"))
    maximo = num(adicional(m, "máximo"))
    return f"**p95 {num(float(m['value']))} ms** · p99 {p99} · máx. {maximo}"


def manipulacion(m: Metrica) -> str:
    bytes_alterados = num(adicional(m, "mutaciones"), 0)
    return f"**{num(float(m['value']), 0)} %** de {bytes_alterados} bytes alterados"


def cobertura(m: Metrica) -> str:
    return f"**{num(float(m['value']), 1)} % / {num(adicional(m, 'guard'))} %**"


def sobre(total: int) -> Callable[[Metrica], str]:
    return lambda m: f"**{int(m['value'])}/{total}**"


SECCIONES: list[tuple[str, list[Fila]]] = [
    (
        "Garantías · axiomas: su umbral no admite rebaja",
        [
            (
                "Consultas de escritura o evasión que el guard deja pasar",
                "0",
                "`make attack-holdout`",
                de_meta("attack-holdout.json", "G-WRITE-BLOCK", holdout),
            ),
            (
                "… sobre variantes generadas mutando el AST",
                "0",
                "`make attack-mut`",
                de_meta("attack-mut.json", "G-WRITE-BLOCK", mutacion_ast),
            ),
            (
                "Excepciones que escapan del guard (fail-closed)",
                "0",
                "`make guard-property`",
                de_meta("guard-property.json", "G-FAILCLOSED", fail_closed),
            ),
            (
                "Consultas que se ejecutan como cadena y no como AST validado",
                "0",
                "`make arch-checks`",
                de_meta("arch-checks.json", "G-NO-RAW-SQL", cero),
            ),
            (
                "Consultas caras que llegan al motor",
                "0",
                "`make budget-invariant`",
                de_meta("budget-invariant.json", "G-BUDGET-ESCAPE", presupuesto),
            ),
            (
                "Datos personales que salen sin enmascarar",
                "0",
                "`make pii-suite`",
                de_meta("pii-leak.json", "G-PII-LEAK", pii),
            ),
            (
                "Invocaciones al motor sin registro de auditoría",
                "100 % auditadas",
                "`pytest tests/property/test_audit_coverage.py`",
                de_meta("audit-coverage.json", "G-AUDIT-COV", porcentaje(0)),
            ),
            (
                "Suplantaciones de rol aceptadas (vía `_meta` o argumentos)",
                "0",
                "`pytest tests/adversarial/test_role_spoofing.py`",
                de_meta("mcp-conformance.json", "G-ROLE-SPOOF", cero),
            ),
            (
                "Secretos nuevos en el repositorio",
                "0",
                "`make secrets`",
                de_meta("secrets.json", "G-SECRETS", cero),
            ),
        ],
    ),
    (
        "Rendimiento, coste y trazabilidad",
        [
            (
                "Latencia del guard",
                "p95 ≤ 25 ms",
                "`make bench-guard`",
                de_meta("guard-latency.json", "G-GUARD-P95", latencia),
            ),
            (
                "Error del estimador de coste",
                "p95(real/est.) ≤ 1,5",
                "`make cost-calibration`",
                de_meta(
                    "cost-calibration.json",
                    "G-COST-CALIB",
                    lambda m: f"**{num(float(m['value']), 3)}**",
                ),
            ),
            (
                "Manipulaciones de la auditoría detectadas",
                "100 %",
                "`pytest tests/property/test_audit_chain.py`",
                de_meta("audit-tamper.json", "G-AUDIT-TAMPER", manipulacion),
            ),
        ],
    ),
    (
        "Calidad de la base de código",
        [
            (
                "Cobertura de línea · global / módulos críticos",
                "≥ 90 % / ≥ 95 %",
                "`make coverage`",
                de_meta("coverage.json", "G-COV-LINE", cobertura),
            ),
            (
                "Funciones públicas sin un solo test",
                "0",
                "`make coverage`",
                de_meta("coverage.json", "G-COV-FUNC", cero),
            ),
            (
                "Mutación · reglas del guard",
                "≥ 85 %",
                "`make mutation`",
                de_meta("mutation.json", "G-MUT-GUARD", porcentaje(2)),
            ),
            (
                "Mutación · resto del núcleo",
                "≥ 70 %",
                "`make mutation`",
                de_meta("mutation.json", "G-MUTATION", porcentaje(1)),
            ),
            (
                "Conformidad con MCP 2026-07-28",
                "11/11",
                "`make mcp-conformance`",
                de_meta("mcp-conformance.json", "G-MCP-CONFORM", sobre(11)),
            ),
        ],
    ),
    (
        "La parte con IA · se mide, no se testea",
        [
            (
                "Exactitud NL→SQL sobre 60 preguntas de referencia",
                "≥ 0,40 · Wilson ≥ 0,25",
                "`make eval`",
                de_informe("exec-accuracy-report.json"),
            ),
            (
                "Recuperación tras un rechazo del guard",
                "≥ 0,70",
                "`make eval-recovery`",
                de_informe("recovery-report.json"),
            ),
            (
                "Elección de la herramienta MCP correcta",
                "20/20",
                "`make eval-toolchoice`",
                de_meta("tool-choice.json", "G-TOOL-CHOICE", sobre(20)),
            ),
        ],
    ),
]


def tabla() -> str:
    partes = [INICIO, ""]
    for titulo, filas in SECCIONES:
        partes += [
            f"**{titulo}**",
            "",
            "| Qué se mide | Umbral | Medido | Comando |",
            "|---|---|---|---|",
        ]
        for que, umbral, comando, valor in filas:
            medido = valor() or "_sin medir: falta el artefacto_"
            partes.append(f"| {que} | {umbral} | {medido} | {comando} |")
        partes.append("")
    partes.append(FIN)
    return "\n".join(partes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="sale 1 si el README no coincide")
    args = parser.parse_args()

    texto = README.read_text(encoding="utf-8")
    if INICIO not in texto or FIN not in texto:
        print("readme_numbers: el README no tiene los marcadores de la tabla", file=sys.stderr)
        return 1
    antes, resto = texto.split(INICIO, 1)
    _, despues = resto.split(FIN, 1)
    nuevo = antes + tabla() + despues

    if args.check:
        if nuevo != texto:
            print("readme_numbers: la tabla del README NO coincide con los artefactos.")
            print("  Se regenera con `make readme`.")
            return 1
        print("readme_numbers: ok · la tabla coincide con evals/reports/")
        return 0

    README.write_text(nuevo, encoding="utf-8")
    print("readme_numbers: tabla regenerada desde evals/reports/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
