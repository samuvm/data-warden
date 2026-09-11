#!/usr/bin/env python
"""El banco de referencia, VERIFICADO. Antes de que nadie lo firme.

**Lo que este script hace es separar el trabajo que cuesta horas humanas del que no.**

Los **10 de rechazo** no cuestan ninguna: su respuesta correcta es un `rule_id`, el
guard es determinista y sus reglas están escritas. Se ejecuta el guard sobre cada
pregunta y se exige que salte **la regla declarada**, no «alguna» regla — un caso que
se cree de R008 y que para por R002 por accidente deja a R008 sin cubrir el día que
R002 cambie. Es la misma exigencia que `check_rule_coverage.py` hace en el corpus del
guard, y por el mismo motivo.

Los **de ejecución** sí necesitan criterio, pero no todo el criterio es igual de caro:

- Si el caso declara `glosario:`, su SQL **transcribe una entrada FIRMADA** de
  `docs/spec/glossary.yaml`. Aquí se comprueba que esa entrada existe de verdad, para
  que la procedencia no pueda apuntar a una definición inventada. Revisar una
  transcripción cuesta segundos; decidir una definición, veinte minutos.
- Si declara `arbitrio: true`, el glosario no lo cubre y **hace falta Samuel**. Se
  cuentan aparte y se listan, porque sus horas tienen que gastarse ahí y no en
  releer transcripciones.

**Y se ejecuta el SQL de referencia contra el dataset.** Un SQL de referencia que no
corre no es una referencia, y uno que devuelve cero filas casi nunca es lo que se
quería preguntar. Las dos cosas se descubren aquí y no con el banco ya firmado.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.catalog.statistics import load as load_stats
from datawarden.cost import STATISTICS_PATH
from datawarden.cost.screen import screen
from datawarden.domain.types import Principal, Role, RoleSource, ValidatedQuery
from datawarden.guard.validator import validate
from gatelib import ROOT, record

QUESTIONS = ROOT / "evals" / "golden" / "questions.yaml"
GLOSSARY = ROOT / "docs" / "spec" / "glossary.yaml"


#: El perfil lo declara el propio banco. **No se fija aquí.** Tenerlo escrito en dos
#: sitios es como el congelador acaba mirando `full` y el verificador `dev`, que es
#: exactamente lo que pasó el 2026-09-10 y lo que este banco existe para no repetir.
def _database(raw: dict[str, object]) -> pathlib.Path:
    perfil = str(raw.get("perfil", "dev"))
    return ROOT / "datagen" / "out" / f"cierzo-{perfil}.duckdb"


MAX_ROWS = 50_000

#: Lo exige `docs/GOALS.yaml` :: G-EXEC-ACC y lo estratifica `docs/PLAN.md`.
CORPUS_MINIMO = 60
RECHAZOS_MINIMO = 10


def entrada_del_glosario(glosario: dict[str, object], ruta: str) -> bool:
    """Si la entrada FIRMADA que un caso dice transcribir existe de verdad.

    Sin esto, `glosario:` sería una etiqueta decorativa: cualquiera podría escribir el
    SQL que le pareciera y apuntar a una definición que no existe, y la procedencia
    diría «transcrito de lo firmado» sobre algo inventado.
    """
    for parte in ruta.split("+"):
        seccion, _, clave = parte.strip().partition(".")
        bloque = glosario.get(seccion)
        if not isinstance(bloque, dict) or clave not in bloque:
            return False
    return True


def main() -> int:
    raw = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    glosario = yaml.safe_load(GLOSSARY.read_text(encoding="utf-8"))
    rechazos = raw.get("rechazos") or []
    ejecucion = raw.get("ejecucion") or []

    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.principal import BUDGETS_PATH, POLICY_PATH
    from datawarden.principal.budgets import load_budgets
    from datawarden.principal.policy import load_policy

    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    budgets = load_budgets(BUDGETS_PATH)
    stats = load_stats(STATISTICS_PATH)

    problemas: list[str] = []
    verificados = 0

    # 1 · LOS RECHAZOS · gratis, y se exige la regla EXACTA.
    for caso in rechazos:
        who = Principal(
            id=f"banco-{caso['id']}",
            role=Role(caso.get("rol", "analyst")),
            source=RoleSource.CLI_FLAG,
        )
        sql = caso.get("sql_que_lo_provoca")
        if sql is None:
            # Sin SQL sembrado, este caso solo se puede verificar cuando el modelo
            # genere: se cuenta pero no se afirma nada todavía.
            continue
        veredicto = validate(
            str(sql), principal=who, schema=schema, policy=policy, max_rows=MAX_ROWS
        )
        if isinstance(veredicto, ValidatedQuery):
            problemas.append(
                f"{caso['id']}: se esperaba rechazo de {caso['regla']} y se ACEPTÓ"
            )
            continue
        if veredicto.rule_id != caso["regla"]:
            problemas.append(
                f"{caso['id']}: declara {caso['regla']} y dispara {veredicto.rule_id}. "
                "Un caso que para por otra regla deja a la declarada sin cubrir"
            )
            continue
        verificados += 1

    # 2 · LA PROCEDENCIA DEL GLOSARIO no puede apuntar a nada inventado.
    for caso in ejecucion:
        ruta = caso.get("glosario")
        if ruta and not entrada_del_glosario(glosario, str(ruta)):
            problemas.append(
                f"{caso['id']}: dice transcribir «{ruta}» y esa entrada NO está en el "
                "glosario firmado. La procedencia estaría apuntando a una definición "
                "que nadie firmó"
            )

    # 3 · EL SQL DE REFERENCIA TIENE QUE PASAR EL SISTEMA ENTERO PARA SU ROL.
    #
    # Sin esto se puede escribir un caso «de ejecución» cuya referencia el sistema
    # rechazaría: al medir, el modelo acertaría el SQL y aun así fallaría, y el
    # número culparía al modelo de una política que el propio banco incumple.
    #
    # **Y aquí estaba el agujero de P-012: esto llamaba a `validate()`.** El guard es
    # el anillo 3; el presupuesto es el 4. `eval_exec.py` pasa el candidato del modelo
    # por `screen()`, o sea por los DOS, así que un check que solo mira el guard
    # aprueba referencias que la medida rechaza. Pasó exactamente eso: 20 de las 47
    # referencias escritas —todas las que usan las vistas que el glosario firmado
    # manda usar— pasaban este check y las rechazaba el sistema al medir, dejando a
    # `G-EXEC-ACC` un techo de 0,574 que no dependía del modelo.
    #
    # Es la quinta vez en el proyecto que aparece el mismo error de método —medir un
    # camino que no es el que se ejecuta—, después de `G-PII-LEAK`, `G-SECRETS`,
    # `G-MCP-CONFORM` y `check_mcp_live`. Se comprueba por el mismo camino, o no se
    # comprueba.
    for caso in ejecucion:
        sql = caso.get("sql_referencia")
        if not sql:
            continue
        who = Principal(
            id=f"banco-{caso['id']}",
            role=Role(caso.get("rol", "analyst")),
            source=RoleSource.CLI_FLAG,
        )
        resultado = screen(
            str(sql),
            principal=who,
            schema=schema,
            policy=policy,
            budgets=budgets,
            stats=stats,
        )
        if resultado.rejection is not None:
            coste = (
                f" · {resultado.cost.estimated_bytes:,} bytes"
                if resultado.cost is not None
                else ""
            )
            problemas.append(
                f"{caso['id']}: la referencia NO pasa el sistema con rol "
                f"{caso.get('rol')} · {resultado.rejection.rule_id}/"
                f"{resultado.rejection.code}{coste}. "
                "Un caso de ejecución cuya referencia el sistema rechazaría culparía "
                "al modelo de una política que el banco incumple"
            )

    # 4 · Y TIENE QUE CORRER. Uno que no corre no es referencia.
    sin_sql = [c["id"] for c in ejecucion if not c.get("sql_referencia")]
    ejecutables = [c for c in ejecucion if c.get("sql_referencia")]
    vacios: list[str] = []
    database = _database(raw)
    if database.exists() and ejecutables:
        import duckdb

        conexion = duckdb.connect(str(database), read_only=True)
        for caso in ejecutables:
            try:
                filas = conexion.execute(str(caso["sql_referencia"])).fetchall()
            except Exception as fallo:
                problemas.append(f"{caso['id']}: el SQL de referencia no corre · {fallo}")
                continue
            if not filas:
                vacios.append(str(caso["id"]))
        conexion.close()

    arbitrio = [c["id"] for c in ejecucion if c.get("arbitrio")]
    estratos = Counter(str(c.get("estrato", "sin-estrato")) for c in ejecucion)
    total = len(rechazos) + len(ejecucion)

    record(
        "exec-accuracy.json",
        "G-QUESTIONS-BANK",
        value=float(len(problemas)),
        detail={
            "total": total,
            "objetivo": CORPUS_MINIMO,
            "rechazos": len(rechazos),
            "rechazos_verificados_ejecutando_el_guard": verificados,
            "ejecucion": len(ejecucion),
            "necesitan_criterio_de_samuel": arbitrio,
            "sin_sql_de_referencia": sin_sql,
            "referencia_devuelve_cero_filas": vacios,
            "estratos": dict(estratos),
            "perfil": str(raw.get("perfil", "dev")),
            "provenance": raw.get("provenance"),
            "problemas": problemas,
        },
        command="python scripts/check_questions.py",
    )

    print(f"check_questions: {total}/{CORPUS_MINIMO} casos · {len(rechazos)} de rechazo")
    print(f"  rechazos verificados ejecutando el guard : {verificados}/{len(rechazos)}")
    print(f"  necesitan criterio de Samuel             : {len(arbitrio)} {arbitrio}")
    print(f"  sin SQL de referencia todavía            : {len(sin_sql)} {sin_sql}")
    if vacios:
        print(f"  AVISO · referencia con cero filas        : {vacios}")
    print(f"  procedencia                              : {raw.get('provenance')}")
    if raw.get("provenance") != "revisado_humano":
        print(
            "  El banco NO está firmado todavía. Hasta que lo esté, cualquier número "
            "medido contra él es el agente puntuándose a sí mismo."
        )
    if problemas:
        print(f"\ncheck_questions: FALLO · {len(problemas)} problemas")
        for p in problemas:
            print(f"  · {p}")
        return 1
    if total < CORPUS_MINIMO:
        print(
            f"\ncheck_questions: INCOMPLETO · faltan {CORPUS_MINIMO - total} casos. "
            "No es un fallo: es el trabajo que queda."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
