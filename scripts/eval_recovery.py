#!/usr/bin/env python
"""`G-RECOVERY` · el modelo se corrige solo con el mensaje de rechazo.

**Esto NO es un test: es una evaluación de nivel 4** y necesita un LLM en tiempo de
medida. Por eso `make eval-recovery` corre desde la caché grabada —determinista y
gratis, repetible en cualquier máquina— y `make eval-refresh` es el que vuelve a
llamar al modelo. Mezclarlos sin decirlo daría un número salido de una mitad grabada
y otra generada que nadie podría reproducir.

**QUÉ SE MIDE, dicho con precisión.** No si el modelo acierta a la primera: si SE
CORRIGE con el mensaje del guard. Por eso el rechazo se SIEMBRA. Si el corpus fueran
solo preguntas, el número mezclaría dos cosas —cuántas veces el modelo se equivoca
del modo previsto y cuántas se corrige después—, y la primera varía con el modelo y
no es interesante.

**EL DENOMINADOR SON LOS RECHAZOS REINTENTABLES, y hay que decirlo alto.** De los 42
sembrados, 14 son rechazos que por diseño NO se pueden reformular: un `DELETE` no se
convierte en pregunta reescribiéndolo, ni `information_schema` deja de ser el
esquema del motor. El bucle no los reintenta —darles la oportunidad de fallar
contaminaría la métrica con casos que nadie puede arreglar— así que tampoco pueden
entrar en un ratio de corrección. **No se descartan en silencio:** los 14 se
verifican uno a uno (el bucle tiene que parar en seco y NO gastar una llamada), y el
informe publica los tres números —corpus, recuperables y recuperados— para que nadie
tenga que deducir cuál fue el denominador.

**Se publica el INTERVALO, no el punto.** Con n=28 un 0,75 puntual no significa lo
que parece, y `docs/GOALS.yaml` exige por eso un límite inferior de Wilson.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import (
    Principal,
    RejectionReason,
    Role,
    RoleSource,
    ValidatedQuery,
)
from datawarden.guard.validator import validate
from datawarden.nl2sql.loop import MAX_RETRIES, Attempt, LoopResult, run_loop
from datawarden.nl2sql.prompt import load as load_prompt
from datawarden.nl2sql.providers import (
    CASSETTE_DIR,
    LocalProvider,
    Provider,
    RecordedProvider,
    Request,
    extract_sql,
)
from gatelib import ROOT, record, wilson
from recoverylib import RecoveryCase, read

MODELS_LOCK = ROOT / "models.lock"
MAX_ROWS = 50_000


@dataclass(frozen=True, slots=True)
class Outcome:
    """Lo que pasó con un caso. Con la historia, no solo con el veredicto."""

    case: RecoveryCase
    retryable: bool
    recovered: bool
    model_calls: int
    attempts: tuple[str, ...]
    final: str


def cassette_provenance(directory: pathlib.Path) -> dict[str, object]:
    """De dónde salieron las casetes. **Leído de ellas, nunca del flag de hoy.**

    La reproducción publicaba el modo que traía la invocación y no el que había
    producido las grabaciones: decía «razonador: sí» sobre un número medido sin
    razonador. Un informe que se equivoca en cómo se midió es peor que uno que no lo
    dice, porque invita a comparar dos números que no son comparables.

    Y de paso caza la mezcla: casetes de dos modelos, o del mismo modelo en dos
    modos, dan un número que no es de ninguno de los dos.
    """
    models: set[str] = set()
    modes: set[object] = set()
    count = 0
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        models.add(str(payload.get("model", "desconocido")))
        modes.add(payload.get("thinking"))
        count += 1
    return {"count": count, "models": sorted(models), "modes": sorted(modes, key=str)}


def models_lock(role: str) -> tuple[str, str]:
    """El tag y el digest de un rol de `models.lock`. **Fijado por digest, no por tag.**

    Un tag de Ollama es MÓVIL: `qwen3.5:9b-mlx` puede apuntar a otro peso dentro de
    tres meses y `G-RECOVERY` cambiaría de valor sin que nadie tocara una línea. El
    digest va al informe para que dos números se puedan comparar de verdad.
    """
    for line in MODELS_LOCK.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == role:
            return parts[1], parts[2]
    message = (
        f"models.lock no declara el rol {role!r}. Los modelos NO se inventan "
        "(CLAUDE.md): se fijan en models.lock por digest, y Q-007 ya los fijó."
    )
    raise KeyError(message)


class _Counting:
    """Envuelve al provider para CONTAR las llamadas. El número es parte de la medida.

    Sin contarlas no se puede comprobar que un rechazo no reintentable no gasta ni
    una, que es lo que separa «no se reintenta» de «se reintenta y falla».
    """

    def __init__(self, inner: Provider) -> None:
        self._inner = inner
        self.name = inner.name
        self.calls = 0

    def generate(self, request: Request) -> str:
        self.calls += 1
        return self._inner.generate(request)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="vuelve a llamar al modelo local y regraba las casetes. Sin esto no hay red.",
    )
    parser.add_argument(
        "--model-role",
        default="generador",
        help="rol de models.lock. `generador_dev` mide con el 4b y SALE ETIQUETADO.",
    )
    parser.add_argument(
        "--no-think",
        action="store_true",
        help=(
            "apaga el modo razonador del modelo. VA AL INFORME: medido el 2026-09-03, "
            "la misma consulta sale en 0,3 s sin razonar y en 71 s razonando, así que "
            "dos números con modos distintos no son comparables."
        ),
    )
    args = parser.parse_args()

    corpus = read(ROOT)
    schema = load_generated(SCHEMA_PATH)
    from datawarden.principal import POLICY_PATH
    from datawarden.principal.policy import load_policy

    policy = load_policy(POLICY_PATH)
    prompt = load_prompt("nl2sql")
    retry_prompt = load_prompt("nl2sql-retry")

    tag, digest = models_lock(args.model_role)
    cassettes = RecordedProvider(directory=ROOT / CASSETTE_DIR)

    thinking: object = not args.no_think
    if args.refresh:
        local = LocalProvider(model=tag, think=None if thinking else False)
        source: Provider = _Recording(local, cassettes, tag)
        print(
            f"eval_recovery: REFRESCANDO contra {tag} ({digest}), razonador="
            f"{'sí' if thinking else 'no'}. Esto llama al modelo."
        )
    else:
        source = cassettes

    outcomes: list[Outcome] = []
    problems: list[str] = []

    if not args.refresh:
        # LA PROCEDENCIA SALE DE LAS CASETES. El flag de hoy no midió nada.
        provenance = cassette_provenance(ROOT / CASSETTE_DIR)
        models_used = list(provenance["models"])  # type: ignore[call-overload]
        modes_used = list(provenance["modes"])  # type: ignore[call-overload]
        if len(models_used) > 1:
            problems.append(
                f"las casetes vienen de {models_used}. Mezclar dos modelos da un "
                "número que no es de ninguno de los dos"
            )
        if len(modes_used) > 1:
            problems.append(
                f"las casetes vienen de {len(modes_used)} modos distintos ({modes_used}). "
                "El modo cambia el número: medido el 2026-09-03, la misma consulta "
                "sale en 0,3 s sin razonar y en 71 s razonando"
            )
        if models_used and models_used[0] != tag:
            problems.append(
                f"las casetes son de {models_used[0]} y models.lock pide {tag}. "
                "Regraba con `make eval-refresh`"
            )
        thinking = modes_used[0] if len(modes_used) == 1 else modes_used

    for case in corpus.seeded:
        who = Principal(
            id=f"recovery-{case.case_id}", role=Role(case.role), source=RoleSource.CLI_FLAG
        )

        def check(sql: str, principal: Principal = who) -> ValidatedQuery | RejectionReason:
            return validate(
                sql, principal=principal, schema=schema, policy=policy, max_rows=MAX_ROWS
            )

        seeded = check(case.sql)
        if isinstance(seeded, ValidatedQuery):
            problems.append(
                f"{case.case_id}: la semilla ya no rechaza. `make eval-recovery` mide "
                "corrección, y sin rechazo no hay nada que corregir"
            )
            continue

        counting = _Counting(source)
        try:
            result: LoopResult = run_loop(
                case.question,
                provider=counting,
                validate=check,
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
                seed=Attempt(sql=case.sql, rejection=seeded),
            )
        except KeyError as miss:
            problems.append(f"{case.case_id}: {miss.args[0]}")
            continue

        if result.rejection is not None and result.rejection.rule_id == "INTERNAL":
            # UN FALLO DE MEDIDA NO ES UN FALLO DEL MODELO. Si el proveedor revienta
            # o se pasa del tope de tiempo, el bucle lo convierte —correctamente— en
            # un rechazo `INTERNAL`; contarlo como «no se recuperó» sería medir el
            # reloj y publicarlo como si fuera el modelo.
            problems.append(
                f"{case.case_id}: el ciclo acabó en INTERNAL "
                f"({result.rejection.message}). Eso es un fallo de MEDIDA, no del "
                "modelo, y no puede contar como una recuperación fallida"
            )

        if not seeded.retryable:
            # NO SE DESCARTA EN SILENCIO: se comprueba que el bucle para en seco.
            if counting.calls:
                problems.append(
                    f"{case.case_id}: el rechazo NO es reintentable y aun así se "
                    f"llamó al modelo {counting.calls} veces. Reintentar lo que nadie "
                    "puede arreglar gasta dinero y contamina la métrica"
                )
            if result.accepted:
                problems.append(
                    f"{case.case_id}: un rechazo no reintentable acabó ACEPTADO. "
                    "El bucle tenía que haber parado"
                )

        outcomes.append(
            Outcome(
                case=case,
                retryable=seeded.retryable,
                recovered=result.recovered,
                model_calls=counting.calls,
                attempts=tuple(a.sql for a in result.attempts),
                final="aceptada" if result.accepted else "rechazada",
            )
        )

    recoverable = [o for o in outcomes if o.retryable]
    recovered = [o for o in recoverable if o.recovered]
    total = len(recoverable)
    ratio = round(len(recovered) / total, 4) if total else 0.0
    low, high = wilson(len(recovered), total)

    per_rule: dict[str, dict[str, int]] = {}
    for outcome in recoverable:
        row = per_rule.setdefault(outcome.case.rule_id, {"recuperables": 0, "recuperados": 0})
        row["recuperables"] += 1
        row["recuperados"] += int(outcome.recovered)

    detail = {
        "corpus": len(corpus.seeded),
        "recoverable": total,
        "not_retryable": len(outcomes) - total,
        "recovered": len(recovered),
        "wilson_95": [round(low, 4), round(high, 4)],
        "model": {
            "role": args.model_role,
            "tag": tag,
            "digest": digest,
            "thinking": thinking,
            "temperature": LocalProvider.temperature,
            "seed": LocalProvider.seed,
        },
        "prompts": [
            {"id": prompt.prompt_id, "version": prompt.version, "sha256": prompt.sha256},
            {
                "id": retry_prompt.prompt_id,
                "version": retry_prompt.version,
                "sha256": retry_prompt.sha256,
            },
        ],
        "provider": "local (refrescado)" if args.refresh else "recorded (casetes)",
        "max_retries": MAX_RETRIES,
        "per_rule": dict(sorted(per_rule.items())),
        "problems": problems,
        "cases": [
            {
                "id": o.case.case_id,
                "rule_id": o.case.rule_id,
                "family": o.case.family,
                "role": o.case.role,
                "retryable": o.retryable,
                "recovered": o.recovered,
                "model_calls": o.model_calls,
                "final": o.final,
                "attempts": [a[:400] for a in o.attempts],
            }
            for o in outcomes
        ],
    }

    record(
        "recovery.json",
        "G-RECOVERY",
        value=ratio,
        adicionales={
            "límite inferior del intervalo de Wilson 95 %": round(low, 4),
            "tamaño del corpus de rechazos sembrados": float(len(corpus.seeded)),
        },
        detail=detail,
        command="make eval-recovery",
    )

    print(
        f"\neval_recovery: {len(recovered)}/{total} recuperados · ratio {ratio} · "
        f"Wilson 95 % [{low:.2f} - {high:.2f}]"
    )
    print(
        f"  corpus {len(corpus.seeded)} sembrados · {total} reintentables · "
        f"{len(outcomes) - total} no reintentables (verificados: paran en seco)"
    )
    print(
        f"  modelo {tag} ({digest}) · razonador={'sí' if thinking else 'no'} · "
        f"prompt {prompt.prompt_id} v{prompt.version}"
    )
    if problems:
        print(f"  PROBLEMAS · {len(problems)}")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    return 0


class _Recording:
    """Llama al modelo y graba lo que dijo. Solo lo usa `make eval-refresh`."""

    def __init__(self, inner: LocalProvider, cassettes: RecordedProvider, tag: str) -> None:
        self._inner = inner
        self._cassettes = cassettes
        self._tag = tag
        self.name = "local"

    def generate(self, request: Request) -> str:
        sql = extract_sql(self._inner.generate(request))
        self._cassettes.record(
            request, sql, model=self._tag, thinking=self._inner.think is not False
        )
        print(f"    · grabado {request.cache_key()[:12]} intento {request.attempt}", flush=True)
        return sql


if __name__ == "__main__":
    raise SystemExit(main())
