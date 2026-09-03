"""El corpus de rechazos sembrados, leído del disco. Nivel 3, contrato.

**Por qué es un contrato y no un test unitario.** Toca disco (I-13) y lo que
comprueba no es lógica: es que el artefacto real del repositorio está bien formado y
dice lo que el gate va a dar por supuesto. Es la clase de fallo que ninguna prueba
unitaria ve porque no depende del código, depende de que alguien se haya acordado.

**Lo que NO comprueba, a propósito:** que las semillas sigan rechazando. Eso no se
afirma, se MIDE ejecutando el guard sobre cada una en
`scripts/check_recovery_coverage.py`. Un test que dijera «esta semilla rechaza» sin
ejecutar el guard sería folclore con forma de test.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from recoverylib import Corpus, RecoveryCase, load, read  # noqa: E402

#: `docs/GOALS.yaml`, umbral adicional de `G-RECOVERY`. No se lee de allí a propósito:
#: si alguien bajara el corpus, este test tiene que fallar por su cuenta.
CORPUS_MINIMO = 42


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return read(ROOT)


def test_el_corpus_tiene_al_menos_los_rechazos_que_la_meta_exige(corpus: Corpus) -> None:
    assert len(corpus.seeded) >= CORPUS_MINIMO


def test_ningun_identificador_de_caso_se_repite(corpus: Corpus) -> None:
    """Dos casos con el mismo id harían que el informe pisara uno con el otro."""
    repetidos = [k for k, n in Counter(c.case_id for c in corpus.seeded).items() if n > 1]

    assert repetidos == []


def test_toda_regla_del_registro_menos_la_exenta_tiene_casos(corpus: Corpus) -> None:
    """`G-RECOVERY-COV` exige el 100 %, y aquí se ve de dónde sale el denominador."""
    from datawarden.guard.registry import BY_ID

    faltan = set(BY_ID) - corpus.rules_covered()

    assert faltan == {"R009"}, "R009 es la única exenta, y su exención se comprueba aparte"


def test_toda_familia_declarada_por_un_caso_la_defiende_alguna_regla(corpus: Corpus) -> None:
    """Una familia inventada en el corpus sería un caso que no cubre lo que dice."""
    from datawarden.guard.registry import FAMILIES

    inventadas = {c.family for c in corpus.seeded} - FAMILIES

    assert inventadas == set()


def test_todo_rol_del_corpus_existe(corpus: Corpus) -> None:
    from datawarden.domain.types import Role

    roles = {c.role for c in corpus.seeded} | {c.role for c in corpus.expanded}

    assert roles <= {r.value for r in Role}


def test_los_de_expansion_son_todos_de_la_regla_exenta(corpus: Corpus) -> None:
    """No entran en el ratio: meter otra regla ahí sería sacarla del denominador."""
    assert {c.rule_id for c in corpus.expanded} == {"R009"}


def test_ninguna_pregunta_llega_vacia_ni_plegada(corpus: Corpus) -> None:
    """El YAML pliega las líneas y un salto de línea dentro de la pregunta la parte."""
    for case in corpus.seeded:
        assert case.question.strip()
        assert "\n" not in case.question


def test_las_semillas_de_la_bomba_de_ast_se_expanden_de_verdad(corpus: Corpus) -> None:
    """Si la repetición no se aplicara, R013 no dispararía y el caso no sembraría nada."""
    bombas = [c for c in corpus.seeded if c.rule_id == "R013"]

    assert bombas
    assert all(len(c.sql) > 4_000 for c in bombas)
    assert all("{repeticion}" not in c.sql for c in bombas)


# ------------------------------------------------------- la lectura del mapa ---


def _crudo() -> dict[str, object]:
    return {
        "rol_por_defecto": "analyst",
        "casos": [
            {
                "id": "REC-R008-1",
                "regla": "R008",
                "familia": "columna_denegada",
                "pregunta": "el dni\n  de los clientes",
                "semilla": "SELECT national_id FROM dim_customer LIMIT 10",
            },
            {
                "id": "REC-R012-1",
                "regla": "R012",
                "familia": "agregacion_de_grupo_unico",
                "rol": "admin",
                "pregunta": "cuántos por dni",
                "semilla": "SELECT national_id FROM dim_customer GROUP BY national_id",
            },
        ],
    }


def test_un_caso_sin_rol_hereda_el_del_corpus_y_el_suyo_gana() -> None:
    leido = load(_crudo())

    assert leido.seeded[0].role == "analyst"
    assert leido.seeded[1].role == "admin"


def test_la_pregunta_se_normaliza_porque_el_yaml_la_pliega() -> None:
    assert load(_crudo()).seeded[0].question == "el dni de los clientes"


def test_un_corpus_sin_la_seccion_de_expansion_se_lee_vacio_y_no_revienta() -> None:
    """Que falte una sección lo tiene que decir el CHECK con su número, no un KeyError.

    Un `KeyError` saldría como un fallo de proceso; lo correcto es que la cobertura
    salga por debajo del 100 % y el gate diga qué regla se quedó sin caso.
    """
    leido = load(_crudo())

    assert leido.expanded == ()
    assert isinstance(leido.seeded[0], RecoveryCase)
