"""La expansión de las semillas del corpus de `G-RECOVERY`. Pura y determinista.

**Aquí vive SOLO la expansión, y el reparto no es estético: lo impuso I-05.** La
lectura del corpus —que incluye con qué ROL se valida cada caso— vive en
`scripts/recoverylib.py`, porque `scripts/check_role_source.py` prohíbe que ningún
módulo publicado saque un rol de un diccionario, y tenía razón: «es solo una fixture
de evaluación» es exactamente la excusa que alguien daría el día que la violación
fuese de verdad. `G-ROLE-SPOOF` es un axioma y no se negocia por comodidad.

Lo que queda aquí es lo que no tiene rol ninguno y sí decide algo: **qué cadena de
SQL acaba delante del guard.** Eso se prueba y se cubre.

**Por qué existe la repetición.** Tres semillas de R013 tienen que superar los 4.000
nodos del árbol para que la regla dispare, y escribirlas literales serían treinta
kilobytes de `+ 1` dentro de un fichero que alguien tiene que poder leer y revisar.
La expansión es declarativa, y el check revalida el SQL YA EXPANDIDO contra el
guard: lo que se comprueba es lo que se manda, no la plantilla.
"""

from __future__ import annotations

from typing import Any, Final

#: El corpus firmado, relativo a la raíz del repositorio. Lo lee `scripts/`.
CORPUS_PATH: Final = "evals/golden/recovery.yaml"

#: Los dos marcadores que la expansión sustituye. Dos formas y ninguna más: cada
#: forma nueva es una manera más de que el SQL medido no sea el SQL leído.
REPEAT_MARK: Final = "{repeticion}"
BRANCH_MARK: Final = "{union_de_ramas}"


def expand(seed: str, repetition: dict[str, Any] | None) -> str:
    """Aplica la repetición declarada, si la hay. Determinista y sin sorpresas."""
    if repetition is None:
        return " ".join(seed.split())
    times = int(repetition["veces"])
    if BRANCH_MARK in seed:
        template = str(repetition["plantilla"])
        joiner = str(repetition["union"])
        branches = [template.replace("{i}", str(index)) for index in range(times)]
        return seed.replace(BRANCH_MARK, joiner.join(branches))
    fragment = str(repetition["fragmento"])
    return seed.replace(REPEAT_MARK, fragment * times)
