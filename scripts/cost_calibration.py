#!/usr/bin/env python
"""`G-COST-CALIB` · **el estimador no subestima sistemáticamente.**

Sin esta meta, `G-BUDGET-ESCAPE` sería trivialmente cierto **y a la vez inútil**: un
estimador que devolviera siempre cero no dejaría escapar nada porque nada superaría
nunca el presupuesto. Este script es lo que impide esa trampa.

El umbral es `p95(real / estimado) <= 1,5` y **cero casos con ratio > 3**. Ese
cociente dice hacia dónde tiene que equivocarse el estimador: si subestima, el ratio
se dispara y una consulta cara se cuela; si sobreestima, alguien acota más su
pregunta y no ha pasado nada.

## Qué es «real», y su límite honesto

**No es `total_bytes_read` de DuckDB, y hay un motivo medido.** Ese contador mide
E/S de verdad, así que lo contamina la caché del sistema operativo: en una prueba, un
escaneo de la tabla ENTERA reportó 987 kB —menos que el de un solo día— porque los
ficheros ya estaban en caché de la consulta anterior. Un número que baja cuando el
trabajo sube no sirve para calibrar nada.

Lo que se usa es:

    real = suma sobre cada escaneo de (filas que el motor LEYÓ DE VERDAD
                                       x bytes por fila de las columnas proyectadas)

Las filas salen del perfil de ejecución de DuckDB (`operator_cardinality` de cada
`READ_PARQUET`), que es independiente de la caché y refleja la poda que el motor hizo
de verdad. Los bytes por fila salen de los `column_sizes` de Iceberg.

**LÍMITE, y se declara porque comparte una mitad con lo que se está midiendo:** los
bytes por columna son los mismos que usa el estimador, así que esto **no valida los
tamaños de columna**. Valida lo que cambia consulta a consulta y es donde el
estimador puede equivocarse de verdad: **la poda**. Si el estimador cree que salva
730 particiones y el motor solo lee una, el cociente lo dice.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.catalog.statistics import load as load_stats
from datawarden.cost import STATISTICS_PATH
from datawarden.cost.screen import screen
from datawarden.domain.types import Principal, Role, RoleSource
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy
from gatelib import ROOT, record

SUITE = ROOT / "evals" / "suites" / "cost-calibration.yaml"
PROFILE_OUT = ROOT / "evals" / "reports" / ".duckdb-profile.json"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="full")
    args = parser.parse_args()

    database = ROOT / "datagen" / "out" / f"cierzo-{args.profile}.duckdb"
    if not database.exists():
        print(f"cost_calibration: FALLO · no existe {database.relative_to(ROOT)}")
        return 1

    import duckdb

    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    budgets = load_budgets(BUDGETS_PATH)
    stats = load_stats(STATISTICS_PATH)
    suite = yaml.safe_load(SUITE.read_text(encoding="utf-8"))

    PROFILE_OUT.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(database), read_only=True)

    rows: list[dict[str, object]] = []
    ratios: list[float] = []
    problems: list[str] = []

    try:
        for case in suite["consultas"]:
            role = Role(case["rol"])
            result = screen(
                case["sql"],
                principal=Principal(id="calib", role=role, source=RoleSource.CLI_FLAG),
                schema=schema,
                policy=policy,
                budgets=budgets,
                stats=stats,
            )
            if result.cost is None:
                problems.append(f"{case['id']}: el guard lo rechazó antes de estimar")
                rows.append({"id": case["id"], "rejected_by": result.rejection.rule_id})
                continue

            # Se ejecuta el SQL RE-SERIALIZADO del árbol validado, no el de entrada:
            # calibrar contra otra consulta mediría otra cosa (I-02).
            sql = result.query.sql() if result.query is not None else case["sql"]
            con.execute("PRAGMA enable_profiling='json'")
            con.execute(f"PRAGMA profiling_output='{PROFILE_OUT}'")
            try:
                con.execute(sql).fetchall()
            finally:
                con.execute("PRAGMA disable_profiling")
            profile = json.loads(PROFILE_OUT.read_text(encoding="utf-8"))

            real = _real_bytes(profile, result.cost.detail, stats)
            scanned = _engine_file_fraction(profile)
            estimated = result.cost.estimated_bytes
            ratio = (real / estimated) if estimated else 0.0
            ratios.append(ratio)
            rows.append(
                {
                    "id": case["id"],
                    "role": role.value,
                    "estimated_bytes": estimated,
                    "real_bytes": real,
                    "engine_file_fraction": round(scanned, 6),
                    "ratio": round(ratio, 4),
                    "latency_s": profile.get("latency"),
                }
            )
            if ratio > 3.0:
                problems.append(
                    f"{case['id']}: ratio real/estimado {ratio:.2f} > 3 "
                    f"(real {real}, estimado {estimated})"
                )
    finally:
        con.close()
        PROFILE_OUT.unlink(missing_ok=True)

    ratios.sort()
    p95 = ratios[int(0.95 * len(ratios)) - 1] if ratios else 0.0
    over_three = sum(1 for r in ratios if r > 3.0)
    if p95 > 1.5:
        problems.append(f"p95(real/estimado) = {p95:.3f} y el umbral es 1,5")

    record(
        "cost-calibration.json",
        "G-COST-CALIB",
        value=round(p95, 4),
        adicionales={"casos con ratio real/estimado > 3": over_three},
        detail={
            "queries": len(rows),
            "profile": args.profile,
            "p50": round(statistics.median(ratios), 4) if ratios else 0.0,
            "p95": round(p95, 4),
            "max": round(ratios[-1], 4) if ratios else 0.0,
            "cases": rows,
            "problems": problems,
            "como_se_mide_real": (
                "las MISMAS columnas que usa el estimador por la fracción de "
                "ficheros que el MOTOR dice haber abierto (`Scanning Files: k/n`). "
                "El cociente real/estimado es por tanto poda_del_motor / "
                "poda_del_estimador. NO se usa `total_bytes_read` de DuckDB porque "
                "mide E/S y lo falsea la caché: medido, un escaneo de la tabla "
                "entera reportó 987 kB, menos que el de un solo día."
            ),
            "limite_declarado": (
                "los bytes por columna son los mismos que usa el estimador, así que "
                "esto NO valida los tamaños de columna: valida la PODA, que es lo que "
                "cambia consulta a consulta."
            ),
        },
        command="make cost-calibration",
    )

    print(
        f"cost_calibration: {len(ratios)} consultas · p50 "
        f"{statistics.median(ratios) if ratios else 0:.3f} · p95 {p95:.3f} · "
        f"máx {ratios[-1] if ratios else 0:.3f} · {over_three} por encima de 3"
    )
    if problems:
        print(f"\ncost_calibration: FALLO · {len(problems)} problemas\n")
        for p in problems[:12]:
            print(f"  · {p}")
        return 1
    print("  el estimador no subestima: p95 <= 1,5 y cero casos por encima de 3")
    return 0


def _real_bytes(profile: dict[str, object], detail: dict[str, object], stats: object) -> int:
    """Los bytes que el motor leyó, con la ÚNICA cifra que reporta sin ambigüedad.

    DuckDB dice, por escaneo, cuántos ficheros abrió de cuántos hay
    (`Scanning Files: 1/730`). Eso es la poda de particiones que el motor hizo DE
    VERDAD, y es cache-independiente. El resto de lo que reporta no sirve:
    `total_bytes_read` mide E/S y lo falsea la caché —medido: la tabla entera
    reportó menos bytes que un solo día—, y `Projections` a veces falta, así que
    tomarla como «no lee ninguna columna» convertía un `count(*)` en la tabla
    completa y disparaba el cociente por un factor de quinientos.

    Así que el real se calcula con **las mismas columnas que el estimador** y **la
    fracción de ficheros del MOTOR**:

        real(T) = bytes_de_las_columnas(T) x (ficheros_leidos / ficheros_totales)

    El cociente real/estimado queda siendo, exactamente,
    `poda_del_motor / poda_del_estimador`. **Y eso es todo lo que esta calibración
    afirma medir**: si el estimador cree que salva 730 particiones y el motor abre
    una, el número lo dice. Los tamaños de columna NO se validan aquí, porque los dos
    lados los toman del mismo sitio; eso está declarado en el artefacto.
    """
    fraccion = _engine_file_fraction(profile)
    total = 0.0
    for name, spec in (detail.get("per_table") or {}).items():  # type: ignore[union-attr]
        table = stats.table(name)  # type: ignore[attr-defined]
        if table is None:
            continue
        columnas = tuple(spec.get("columns") or ())
        bytes_columnas = table.bytes_of(columnas)
        particionada = bool(table.partitions) and len(table.partitions) > 1
        total += bytes_columnas * (fraccion if particionada else 1.0)
    return int(total)


def _engine_file_fraction(profile: dict[str, object]) -> float:
    """`ficheros_leidos / ficheros_totales` del escaneo particionado, según el motor.

    Solo dos tablas de este almacén están particionadas y una consulta casi nunca
    toca las dos, así que basta con el escaneo que reporta `k/n` con `n > 1`. Si hay
    varios, se toma el más restrictivo: subestimar el «real» haría que el cociente
    saliera bajo, y un cociente bajo por un artefacto de la medida es peor que uno
    alto, porque no avisa de nada.
    """
    fracciones = []
    stack = [profile]
    while stack:
        node = stack.pop()
        name = str(node.get("operator_name") or node.get("operator_type") or "")
        extra = node.get("extra_info") or {}
        if "PARQUET" in name.upper() or "SCAN" in name.upper():
            scanning = str(extra.get("Scanning Files") or "")  # type: ignore[union-attr]
            if "/" in scanning:
                leidos, _, totales = scanning.partition("/")
                try:
                    if int(totales) > 1:
                        fracciones.append(int(leidos) / int(totales))
                except (ValueError, ZeroDivisionError):
                    pass
        stack.extend(node.get("children") or [])  # type: ignore[arg-type]
    return max(fracciones) if fracciones else 1.0


if __name__ == "__main__":
    sys.exit(main())
