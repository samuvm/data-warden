"""Las nueve mutaciones de AST. Zona test-after, cobertura 90 %.

Lo que se prueba aquí NO es que las mutaciones sean «correctas» —una mutación puede
producir SQL que ni siquiera parsea y eso es útil, porque el guard tiene que
rechazarlo igual—, sino las tres propiedades de las que depende el corpus:

1. **Es DETERMINISTA.** La misma semilla da los mismos mutantes. Un contraejemplo
   que no se puede reproducir es una anécdota, no un contraejemplo.
2. **Produce algo distinto.** Una mutación que devuelve la entrada tal cual gasta un
   turno de corpus sin probar nada.
3. **No revienta con nada.** Una mutación que lanza con SQL raro pararía el corpus
   justo en la clase de entrada que más interesa.
"""

from __future__ import annotations

import random

import pytest

from datawarden.evalsupport.mutate import (
    MUTATIONS,
    add_union,
    flip_case,
    inject_comment,
    mutants,
    pad_whitespace,
    push_into_having,
    rename_aliases,
    swap_synonym,
    wrap_in_cte,
    wrap_in_subquery,
)

_SQL = "SELECT c.customer_sk FROM dim_customer AS c WHERE c.country_code = 'ES'"


def _rng() -> random.Random:
    return random.Random(20260902)


def test_las_nueve_mutaciones_estan_registradas() -> None:
    """Si una se cae del registro, el corpus encoge sin que nada avise."""
    assert len(MUTATIONS) == 9


def test_el_corpus_es_determinista() -> None:
    primera = list(mutants(_SQL, rounds=2, seed=1))
    segunda = list(mutants(_SQL, rounds=2, seed=1))
    assert primera == segunda


def test_dos_semillas_dan_corpus_distintos() -> None:
    """Si no, subir el número de semillas no aportaría un solo mutante nuevo."""
    assert list(mutants(_SQL, rounds=1, seed=1)) != list(mutants(_SQL, rounds=1, seed=2))


def test_ninguna_mutacion_devuelve_la_entrada_tal_cual() -> None:
    for _, mutated in mutants(_SQL, rounds=2, seed=7):
        assert mutated != _SQL


def test_un_comentario_se_inyecta_entre_tokens() -> None:
    """La evasión número uno contra un validador que busque palabras."""
    assert "/*" in inject_comment(_SQL, _rng()) or "--" in inject_comment(_SQL, _rng())


def test_las_mayusculas_cambian() -> None:
    assert flip_case(_SQL, _rng()) != _SQL


def test_un_sinonimo_de_dialecto_sustituye_a_su_funcion() -> None:
    assert swap_synonym("SELECT count(*) FROM t", _rng()) != "SELECT count(*) FROM t"


def test_el_espacio_en_blanco_se_estira() -> None:
    assert pad_whitespace(_SQL, _rng()) != _SQL


def test_envolver_en_una_cte_conserva_la_consulta_dentro() -> None:
    """La evasión que esconde una escritura tras un SELECT."""
    assert "WITH" in wrap_in_cte("DELETE FROM t", _rng())
    assert "DELETE FROM t" in wrap_in_cte("DELETE FROM t", _rng())


def test_envolver_en_una_subconsulta() -> None:
    assert wrap_in_subquery(_SQL, _rng()).startswith("SELECT * FROM (")


def test_unir_esconde_el_ataque_en_una_rama() -> None:
    assert "UNION ALL" in add_union("DELETE FROM t", _rng())


def test_renombrar_alias_no_cambia_de_donde_sale_el_dato() -> None:
    mutated = rename_aliases(_SQL, _rng())
    assert "dim_customer" in mutated
    assert " AS c " not in mutated


def test_empujar_el_predicado_a_un_having() -> None:
    mutated = push_into_having(_SQL, _rng())
    assert "HAVING" in mutated.upper()
    assert "WHERE" not in mutated.upper()


def test_una_consulta_sin_where_no_se_puede_empujar_a_having() -> None:
    sin_where = "SELECT customer_sk FROM dim_customer"
    assert push_into_having(sin_where, _rng()) == sin_where


@pytest.mark.parametrize(
    "basura",
    ["", "((((", "SELECT", "ñ", "DROP", "'", "-- solo un comentario", "\x00"],
)
def test_ninguna_mutacion_revienta_con_basura(basura: str) -> None:
    """Es justo la clase de entrada que más interesa mutar, así que no puede parar."""
    for mutation in MUTATIONS:
        mutation(basura, _rng())


def test_las_mutaciones_de_la_segunda_ronda_son_compuestas() -> None:
    """Un comentario dentro de una CTE dentro de un UNION: donde se rompe un parser."""
    rondas = {name.split("@")[1] for name, _ in mutants(_SQL, rounds=2, seed=3)}
    assert rondas == {"0", "1"}
