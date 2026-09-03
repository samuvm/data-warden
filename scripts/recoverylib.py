"""Lee el corpus de rechazos sembrados. **Herramienta del constructor, no del paquete.**

Está aquí y no en `src/` por dos motivos distintos y los dos son reglas del proyecto:

**1 · I-05, y es un axioma.** Cada caso dice con qué ROL se valida, y
`check_role_source.py` prohíbe que ningún módulo publicado saque un rol de un
diccionario. Que aquí sea una fixture de evaluación y no una petición de un cliente
es cierto y no cambia nada: «es solo una fixture» es exactamente la excusa que
alguien daría el día que la violación fuese de verdad. `G-ROLE-SPOOF` no se negocia
por comodidad, así que la lectura del rol se muda a donde el invariante no la
prohíbe — que es también donde de verdad pertenece.

**2 · P-002.** PyYAML no está en `[project.dependencies]`: entra transitivamente por
`langgraph` y `detect-secrets`, y un módulo de `src/` que lo importara sería la clase
de dependencia invisible que rompe un despliegue seis meses después.

Lo que sí se queda en `src/datawarden/evalsupport/recovery.py` es la EXPANSIÓN, que
no tiene rol ninguno y decide qué cadena de SQL acaba delante del guard.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass
from typing import Any

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.evalsupport.recovery import CORPUS_PATH, expand

__all__ = ["CORPUS_PATH", "Corpus", "RecoveryCase", "load", "read"]


@dataclass(frozen=True, slots=True)
class RecoveryCase:
    """Un rechazo sembrado: la pregunta, el SQL que lo provoca y quién pregunta.

    `sql` viene YA EXPANDIDO. Nadie aguas abajo tiene que saber que existía una
    plantilla, y así el guard valida exactamente la cadena que se le enseña al
    modelo como intento anterior.
    """

    case_id: str
    rule_id: str
    family: str
    role: str
    question: str
    sql: str


@dataclass(frozen=True, slots=True)
class Corpus:
    """Los sembrados y los de expansión, separados a propósito.

    Miden cosas distintas: `seeded` son rechazos de los que hay que recuperarse y
    entran en el ratio; `expanded` son las consultas que COMPRUEBAN que la exención
    de R009 sigue siendo cierta, y no entran en ningún número. Meterlas en la misma
    lista inflaría el corpus con casos gratis, que es la forma barata de subir una
    métrica sin medir nada.
    """

    seeded: tuple[RecoveryCase, ...]
    expanded: tuple[RecoveryCase, ...]

    def rules_covered(self) -> frozenset[str]:
        return frozenset(case.rule_id for case in self.seeded)


def read(root: pathlib.Path) -> Corpus:
    """Lee el corpus firmado desde el disco."""
    raw = yaml.safe_load((root / CORPUS_PATH).read_text(encoding="utf-8"))
    return load(raw)


def load(raw: dict[str, Any]) -> Corpus:
    """Construye el corpus desde el YAML ya parseado."""
    default_role = str(raw.get("rol_por_defecto", "analyst"))
    return Corpus(
        seeded=tuple(_case(entry, default_role) for entry in raw.get("casos") or []),
        expanded=tuple(
            _case(entry, default_role) for entry in raw.get("expansion_de_estrella") or []
        ),
    )


def _case(entry: dict[str, Any], default_role: str) -> RecoveryCase:
    return RecoveryCase(
        case_id=str(entry["id"]),
        rule_id=str(entry["regla"]),
        family=str(entry["familia"]),
        role=str(entry.get("rol", default_role)),
        question=" ".join(str(entry["pregunta"]).split()),
        sql=expand(str(entry["semilla"]), entry.get("repeticion")),
    )
