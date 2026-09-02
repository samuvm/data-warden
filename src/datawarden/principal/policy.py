"""La política de acceso: rol, columna y POSICIÓN EN EL ÁRBOL.

**No es una escala de confianza, es una escala de posición**, y esa es la idea
entera del proyecto. Enmascarar la salida es teatro: si puedes FILTRAR por una
columna, la reconstruyes a base de preguntas aunque nunca veas su valor —el rango
de fechas de nacimiento entre 1930 y 2010 son 29.220 días, así que quince consultas
con distinto literal fijan la fecha exacta de una persona—. Por eso `mask` admite
la columna en proyección directa y la RECHAZA en `WHERE`, `JOIN ON`, `GROUP BY`,
`ORDER BY`, `HAVING`, `QUALIFY`, dentro de una función y como clave de partición de
ventana.

Se carga desde `principal/generated/policy.json`, que compila
`scripts/compile_contracts.py` desde el YAML firmado. El dominio no parsea YAML
—`PyYAML` no está declarada— y además el guard está en el camino crítico de cada
consulta: `G-GUARD-P95` exige p95 <= 25 ms y parsear YAML por consulta es tiempo
tirado en el peor sitio posible.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from datawarden.domain.types import Position, Role


class Level(StrEnum):
    """Los tres niveles. `mask` es el interesante y el que hace serio al proyecto."""

    ALLOW = "allow"
    MASK = "mask"
    DENY = "deny"


#: El YAML firmado nombra las posiciones en español porque lo lee un humano de
#: negocio; el dominio las nombra con el vocabulario de `Position`. La traducción
#: vive aquí, en un sitio, y no repartida por el guard.
_POSITION_ALIASES: Final[dict[str, Position]] = {
    "where": Position.WHERE,
    "join_on": Position.JOIN_ON,
    "group_by": Position.GROUP_BY,
    "order_by": Position.ORDER_BY,
    "having": Position.HAVING,
    "qualify": Position.QUALIFY,
    "argumento_de_funcion": Position.FUNCTION_ARGUMENT,
    "function_argument": Position.FUNCTION_ARGUMENT,
    "clave_de_particion_de_ventana": Position.WINDOW_PARTITION,
    "window_partition": Position.WINDOW_PARTITION,
}


@dataclass(frozen=True, slots=True)
class ColumnPolicy:
    """La fila de una columna en la matriz."""

    column: str
    levels: Mapping[Role, Level]
    #: La clasificación de negocio de la columna, tal cual la firmó la política.
    #: R012 razona sobre ESTO y no sobre una lista escrita en el código: si negocio
    #: reclasifica una columna, la regla cambia con ella y sin tocar Python.
    data_type: str | None = None
    generalized: str | None = None
    transformation: str | None = None
    keep_last_n: int | None = None
    derived_from: tuple[str, ...] = ()
    admin_exception: bool = False
    published_in_catalog: bool = True


@dataclass(frozen=True, slots=True)
class DerivationViolation:
    """Una columna `allow` que deriva de otra que no lo es. Cambio C-5 de la firma.

    `segment_code` contiene `age_band` literalmente: si algún día se restringe
    `age_band`, `segment_code` es un puente que lo rodea. Hoy no hay contradicción;
    esto existe para que siga sin haberla dentro de seis semanas.
    """

    derived: str
    source: str
    role: Role


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """La matriz completa, indexada y lista para consultar en microsegundos."""

    columns: Mapping[str, ColumnPolicy]
    default_level: Level = Level.ALLOW
    forbidden_positions_in_mask: frozenset[Position] = frozenset()
    deterministic_masking: bool = False
    pepper_from: str = ""
    excluded_from_catalog: frozenset[str] = frozenset()
    signed_by: str | None = None
    source_sha256: str = ""
    _restricted: Mapping[Role, frozenset[str]] = field(default_factory=dict, repr=False)

    def column(self, name: str) -> ColumnPolicy:
        """La fila de esa columna, o una fila por defecto si no está en la matriz.

        Devolver una fila y no `None` es deliberado: el que pregunta quiere saber
        qué puede hacer, y obligar a cada llamante a tratar el `None` es obligarle a
        reimplementar el valor por defecto, que es como dos sitios acaban teniendo
        dos valores por defecto distintos.
        """
        found = self.columns.get(name.lower())
        if found is not None:
            return found
        return ColumnPolicy(
            column=name.lower(),
            levels=MappingProxyType(dict.fromkeys(Role, self.default_level)),
        )

    def level_for(self, name: str, role: Role) -> Level:
        return self.column(name).levels[role]

    def is_position_allowed(self, name: str, role: Role, position: Position) -> bool:
        """Si esa columna puede aparecer AHÍ para ese rol.

        `allow` en cualquier posición; `deny` en ninguna, ni siquiera la proyección;
        `mask` solo donde no cierre el canal lateral.
        """
        level = self.level_for(name, role)
        if level is Level.ALLOW:
            return True
        if level is Level.DENY:
            return False
        return position not in self.forbidden_positions_in_mask

    def generalized_for(self, name: str) -> str | None:
        """La columna publicada como salida, si la política declara alguna."""
        return self.column(name).generalized

    def restricted_columns(self, role: Role) -> frozenset[str]:
        """Las columnas que NO son `allow` para ese rol.

        Precalculado al cargar: el guard no puede recorrer 300 columnas por
        consulta y caber en los 25 ms de `G-GUARD-P95`.
        """
        return self._restricted.get(role, frozenset())

    def derivation_violations(self) -> tuple[DerivationViolation, ...]:
        """C-5: toda columna `allow` cuya fuente no lo es para el mismo rol.

        Una columna que no está en la matriz es `allow` por defecto, así que derivar
        de ella no es una violación: no hay nada que rodear.
        """
        violations: list[DerivationViolation] = []
        for name, spec in sorted(self.columns.items()):
            for source in spec.derived_from:
                for role in Role:
                    if spec.levels[role] is Level.ALLOW and self.level_for(
                        source, role
                    ) is not (Level.ALLOW):
                        violations.append(
                            DerivationViolation(derived=name, source=source, role=role)
                        )
        return tuple(violations)

    def undeclared_admin_denials(self) -> tuple[str, ...]:
        """C-2: columnas que admin no ve y que NO lo declaran con `excepcion_admin`.

        El invariante del contrato es «admin lo ve todo» y tiene exactamente una
        excepción firmada. Si la excepción viviera solo en la prosa, esta
        comprobación la marcaría como error y alguien la «arreglaría»
        concediéndosela a admin, que es justo lo contrario de lo que se firmó.
        """
        return tuple(
            name
            for name, spec in sorted(self.columns.items())
            if spec.levels[Role.ADMIN] is not Level.ALLOW and not spec.admin_exception
        )


def policy_from_dict(payload: dict[str, Any]) -> AccessPolicy:
    """Construye la política desde el JSON compilado. Puro: ni disco ni YAML."""
    default = Level(payload.get("default_level", "allow"))
    columns: dict[str, ColumnPolicy] = {}
    for name, spec in payload["columns"].items():
        columns[name.lower()] = ColumnPolicy(
            column=name.lower(),
            levels=MappingProxyType({r: Level(spec["levels"][r.value]) for r in Role}),
            data_type=spec.get("data_type"),
            generalized=spec.get("generalized"),
            transformation=spec.get("transformation"),
            keep_last_n=spec.get("keep_last_n"),
            derived_from=tuple(s.lower() for s in spec.get("derived_from", ())),
            admin_exception=bool(spec.get("admin_exception", False)),
            published_in_catalog=bool(spec.get("published_in_catalog", True)),
        )

    forbidden = frozenset(
        _POSITION_ALIASES[p]
        for p in payload.get("forbidden_positions_in_mask", ())
        if p in _POSITION_ALIASES
    )
    restricted = {
        role: frozenset(n for n, c in columns.items() if c.levels[role] is not Level.ALLOW)
        for role in Role
    }
    return AccessPolicy(
        columns=MappingProxyType(columns),
        default_level=default,
        forbidden_positions_in_mask=forbidden,
        deterministic_masking=bool(payload.get("deterministic_masking", False)),
        pepper_from=str(payload.get("pepper_from", "")),
        excluded_from_catalog=frozenset(
            c.lower() for c in payload.get("excluded_from_catalog", ())
        ),
        signed_by=payload.get("signed_by"),
        source_sha256=str(payload.get("source_sha256", "")),
        _restricted=MappingProxyType(restricted),
    )


def load_policy(path: pathlib.Path) -> AccessPolicy:
    """Carga la política compilada. La única función de este módulo que toca disco."""
    return policy_from_dict(json.loads(path.read_text(encoding="utf-8")))
