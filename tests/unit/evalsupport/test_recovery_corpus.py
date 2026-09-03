"""La expansión de las semillas del corpus. `G-RECOVERY`.

Lo que se prueba aquí es la EXPANSIÓN, que es lo que decide qué cadena de SQL acaba
delante del guard. La LECTURA del corpus no está aquí ni en `src/`: vive en
`scripts/recoverylib.py` porque cada caso dice con qué rol se valida, y I-05 prohíbe
que un módulo publicado saque un rol de un diccionario. Se prueba en
`tests/contract/test_recovery_corpus_real.py`, contra el corpus de verdad.

Que las 42 semillas sigan sembrando tampoco se prueba aquí: se MIDE en
`scripts/check_recovery_coverage.py` ejecutando el guard sobre cada una. Un test que
afirmara «esta semilla rechaza» sin ejecutar el guard sería la misma clase de
folclore que este proyecto persigue en las trampas del glosario.
"""

from __future__ import annotations

from datawarden.evalsupport.recovery import BRANCH_MARK, REPEAT_MARK, expand


def test_una_semilla_sin_repeticion_sale_con_los_espacios_normalizados() -> None:
    """El YAML pliega las líneas largas y eso mete saltos de línea en medio del SQL."""
    assert expand("SELECT  1\n  AS n", None) == "SELECT 1 AS n"


def test_un_fragmento_repetido_se_pega_tantas_veces_como_diga() -> None:
    """Es lo que hace que las semillas de R013 pasen de 4.000 nodos sin ser ilegibles.

    Escribirlas literales serían treinta kilobytes de `+ 1` dentro de un fichero que
    alguien tiene que poder leer y revisar.
    """
    sql = expand(f"SELECT 1{REPEAT_MARK} AS n", {"fragmento": " + 1", "veces": 3})

    assert sql == "SELECT 1 + 1 + 1 + 1 AS n"


def test_una_plantilla_de_ramas_se_une_con_su_separador() -> None:
    sql = expand(
        BRANCH_MARK,
        {"plantilla": "SELECT {i} AS n", "union": " UNION ALL ", "veces": 3},
    )

    assert sql == "SELECT 0 AS n UNION ALL SELECT 1 AS n UNION ALL SELECT 2 AS n"


def test_la_expansion_es_determinista() -> None:
    """Dos medidas del mismo corpus tienen que mandarle al guard la misma cadena."""
    repeticion = {"fragmento": " OR x = 1", "veces": 50}

    primera = expand(f"SELECT 1 WHERE x = 1{REPEAT_MARK}", dict(repeticion))
    segunda = expand(f"SELECT 1 WHERE x = 1{REPEAT_MARK}", dict(repeticion))

    assert primera == segunda


def test_cero_repeticiones_deja_la_semilla_sin_el_marcador() -> None:
    """Un caso límite que sí importa: dejar `{repeticion}` dentro rompería el parseo."""
    assert expand(f"SELECT 1{REPEAT_MARK} AS n", {"fragmento": " + 1", "veces": 0}) == (
        "SELECT 1 AS n"
    )
