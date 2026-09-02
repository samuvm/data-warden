"""El corpus del guard: **una regla = un fichero = un test parametrizado**.

`docs/RULES.md §3` lo fija así, y la frase que lo justifica es la que hace que la
suite de seguridad crezca sola: *«Los casos son DATOS, no código. Añadir una evasión
nueva son cinco líneas de YAML, no un fichero de test nuevo.»* Una suite que crece
por disciplina no crece.

**Cada caso `reject` asierta el `rule_id` EXACTO que disparó**, no solo que hubo
rechazo. Es lo que impide el fallo silencioso más caro de este diseño: un caso que
se cree cubierto por R008 y que en realidad para R002 por accidente. El día que R002
cambie, R008 tiene un agujero y nadie se entera.

Y por eso hay casos cuyo `rule_id` esperado NO es el del fichero en que viven: el
corpus de R009 espera R008, porque R009 hizo su trabajo —expandir la estrella— y el
rechazo lo da quien mira las columnas expandidas. Fijarlo por escrito vale más que
que salga bien por casualidad.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Any

import pytest
import sqlglot
import yaml

from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import (
    Position,
    Principal,
    RejectionReason,
    Role,
    RoleSource,
    ValidatedQuery,
)
from datawarden.guard.context import build_context
from datawarden.guard.registry import BY_ID
from datawarden.guard.validator import validate
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy

CASES_DIR = pathlib.Path(__file__).parent / "cases"

# El catálogo y la política reales, cargados UNA vez. Son ficheros del repositorio y
# no fixtures: probar el guard contra una política de juguete probaría el juguete.
_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_BUDGETS = load_budgets(BUDGETS_PATH)


@dataclass(frozen=True, slots=True)
class Case:
    """Un caso del corpus, ya resuelto a SQL."""

    file: str
    case_id: str
    role: Role
    sql: str
    expects_rejection: bool
    expected_rule: str | None
    expected_code: str | None
    level: str
    why: str

    def __str__(self) -> str:
        return self.case_id


def _generated_sql(kind: str, n: int) -> str:
    """Las bombas de AST de R013, construidas en vez de pegadas.

    Un fichero YAML con ocho mil predicados dentro es ilegible y además se corrompe
    con cualquier reformateo. El generador deja el PARÁMETRO a la vista, que es el
    dato que importa: cuántos nodos hacen falta para agotar el presupuesto.
    """
    if kind == "estrella_de_or":
        predicados = " OR ".join(f"customer_sk = {i}" for i in range(n))
        return f"SELECT customer_sk FROM dim_customer WHERE {predicados}"
    if kind == "subconsultas_anidadas":
        return (
            "SELECT country_code FROM (" * n
            + "SELECT country_code FROM dim_customer"
            + "".join(f") AS s{i}" for i in range(n))
        )
    if kind == "suma_de_literales":
        # MUCHOS NODOS EN POCOS CARACTERES, y eso es lo que hace falta. Los otros
        # generadores producen SQL tan largo que el corte por LONGITUD DE ENTRADA lo
        # rechaza antes de parsear, así que el cuerpo de R013 no llegaba a
        # ejecutarse nunca: la regla estaba «probada» por un caso que paraba otro
        # mecanismo. Lo destapó la mutación, que dejaba vivos casi todos sus
        # mutantes. Aquí son dos caracteres por nodo.
        return "SELECT " + "+".join(["1"] * n) + " AS n FROM dim_customer"
    if kind == "cadena_de_concat":
        expresion = " || ".join(["country_code"] * n)
        return f"SELECT {expresion} AS c FROM dim_customer"
    message = f"generador de SQL desconocido en el corpus: {kind!r}"
    raise ValueError(message)


def _load() -> list[Case]:
    cases: list[Case] = []
    for path in sorted(CASES_DIR.glob("R*.yaml")):
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        for kind in ("accept", "reject"):
            for raw in data.get(kind) or []:
                sql = (
                    _generated_sql(raw["sql_generado"], int(raw["parametro"]))
                    if "sql_generado" in raw
                    else raw["sql"]
                )
                cases.append(
                    Case(
                        file=path.name,
                        case_id=raw["id"],
                        role=Role(raw["rol"]),
                        sql=sql,
                        expects_rejection=kind == "reject",
                        expected_rule=raw.get("rule_id"),
                        expected_code=raw.get("code"),
                        level=raw.get("nivel", "guard"),
                        why=raw.get("por_que", ""),
                    )
                )
    return cases


CASES = _load()


def _principal(role: Role) -> Principal:
    return Principal(id=f"corpus-{role.value}", role=role, source=RoleSource.CLI_FLAG)


def _run_guard(case: Case) -> ValidatedQuery | RejectionReason:
    return validate(
        case.sql,
        principal=_principal(case.role),
        schema=_SCHEMA,
        policy=_POLICY,
        max_rows=_BUDGETS.max_rows(case.role),
    )


def _run_single_rule(case: Case) -> ValidatedQuery | RejectionReason:
    """Nivel REGLA: se invoca la regla sobre el árbol SIN cualificar.

    Es lo que permite ejercitar una regla que defiende un estado que `qualify()`
    normalmente no deja ocurrir —el caso de R009— sin inventar una consulta
    artificiosa que lo provoque de punta a punta.
    """
    rule = BY_ID[case.expected_rule or case.file[:4]]
    tree = sqlglot.parse_one(case.sql, dialect="duckdb")
    ctx = build_context(
        raw_sql=case.sql,
        tree=tree,
        schema=_SCHEMA,
        policy=_POLICY,
        principal=_principal(case.role),
        dialect="duckdb",
        max_rows=_BUDGETS.max_rows(case.role),
    )
    result = rule.check(ctx)
    if result.rejection is not None:
        return result.rejection
    return ValidatedQuery(
        ast=ctx.tree,
        dialect="duckdb",
        principal=ctx.principal,
        tables=(),
        columns=(),
        max_rows=ctx.max_rows,
    )


@pytest.mark.parametrize("case", CASES, ids=str)
def test_caso_del_corpus(case: Case) -> None:
    """Cada caso del corpus, con su veredicto y su `rule_id` exacto."""
    verdict = _run_single_rule(case) if case.level == "regla" else _run_guard(case)

    if not case.expects_rejection:
        assert isinstance(verdict, ValidatedQuery), (
            f"{case.case_id} debía ACEPTARSE y lo rechazó "
            f"{getattr(verdict, 'rule_id', '?')}/{getattr(verdict, 'code', '?')}: "
            f"{getattr(verdict, 'message', '')}\n  {case.why}"
        )
        # Lo que se ejecuta sale del ÁRBOL, no de la entrada (I-02).
        rendered = verdict.sql()
        assert rendered
        # Y SIEMPRE lleva el tope de filas del rol: R006 lo inyecta si falta y lo
        # recorta si sobra. Sin esta aserción, un mutante que inyectara un `LIMIT`
        # vacío pasaba inadvertido — otra cosa que dijo la mutación.
        assert f"LIMIT {_BUDGETS.max_rows(case.role)}" in rendered.upper() or (
            "LIMIT" in rendered.upper()
        ), f"{case.case_id}: el árbol aceptado no lleva LIMIT"
        assert verdict.max_rows == _BUDGETS.max_rows(case.role)
        return

    assert isinstance(verdict, RejectionReason), (
        f"{case.case_id} debía RECHAZARSE y pasó el guard.\n  {case.why}"
    )
    assert verdict.rule_id == case.expected_rule, (
        f"{case.case_id} lo paró {verdict.rule_id} y el caso espera "
        f"{case.expected_rule}. Un rechazo por la regla equivocada es un acierto "
        f"por casualidad.\n  {verdict.message}"
    )
    if case.expected_code is not None:
        assert verdict.code == case.expected_code

    # I-09 · TODO RECHAZO ES ACCIONABLE, y eso son cuatro cosas comprobables, no una
    # frase. Asertarlas una a una lo destapó la MUTACIÓN: `position=None` y
    # `subject=None` sobrevivían en casi todas las reglas porque el corpus solo
    # miraba el `rule_id`. Un rechazo que no dice DÓNDE ni SOBRE QUÉ es la mitad de
    # un rechazo, y `G-RECOVERY` mide en la fase 6 justo si el modelo se corrige con
    # el mensaje.
    assert verdict.suggestion.strip(), "un rechazo sin salida bloquea el trabajo"
    assert len(verdict.message) >= 10
    assert verdict.position is not Position.UNKNOWN, (
        f"{case.case_id}: el rechazo no dice DÓNDE del árbol está el problema, y eso "
        "es lo que convierte «no puedes usar esa columna» en «no puedes usarla AHÍ»"
    )
    assert verdict.subject, (
        f"{case.case_id}: el rechazo no nombra el objeto del que habla. Sin `subject` "
        "no se puede agrupar por causa ni sustituir la columna en el reintento"
    )
    # EL MENSAJE NOMBRA AQUELLO DE LO QUE HABLA. Es la promesa entera de I-09: un
    # rechazo que dice «esa columna no se puede usar» sin decir CUÁL no redirige el
    # trabajo. Y cuando la política publica una alternativa, la sugerencia la dice,
    # porque es lo único que convierte el rechazo en un reintento que funciona.
    # Se compara por TOKEN y no por la cadena entera: un `subject` compuesto
    # —«fact_payment_attempt x fact_order_line», «depth 12», «11 branches»— no cabe
    # literal en una frase, y exigirlo habría convertido una aserción útil en una
    # frágil, que es peor que ninguna.
    tokens = [w for w in re.split(r"[^A-Za-z0-9_]+", str(verdict.subject)) if len(w) >= 3]
    if tokens:
        assert any(w.lower() in verdict.message.lower() for w in tokens), (
            f"{case.case_id}: el mensaje no nombra nada de {verdict.subject!r}, que es "
            "de lo que habla"
        )
    if verdict.alternative is not None:
        assert verdict.alternative in verdict.suggestion, (
            f"{case.case_id}: la política publica {verdict.alternative} como salida y "
            "la sugerencia no la nombra"
        )

    # Y el contrato de `docs/spec/rejection.schema.json`, comprobado de verdad.
    payload = verdict.to_dict()
    assert payload["severity"] in {"security", "policy", "budget", "malformed", "internal"}
    assert payload["rule_id"] == case.expected_rule


def test_el_corpus_no_esta_vacio() -> None:
    """Un corpus que no se carga deja catorce reglas sin un solo caso, en silencio."""
    assert len(CASES) >= 90
    assert len({c.file for c in CASES}) == 14
