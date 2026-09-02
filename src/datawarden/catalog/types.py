"""Tipos del catálogo. Puros, congelados y sin una sola importación de motor.

El catálogo es el anillo 1 y a la vez la entrada del anillo 3: `qualify()` no puede
resolver un alias, expandir un `SELECT *` ni decidir a qué tabla pertenece una
columna sin el esquema. Por eso vive aquí, en un tipo propio, y no como un `dict`
que cada capa interpreta a su manera.

Tres cosas que este módulo NO representa, y es deliberado:

- **La sensibilidad de una columna.** Eso lo dice `docs/spec/policy.yaml`, que lo
  firma negocio. Mezclarlas haría que regenerar el catálogo pudiera cambiar una
  decisión de acceso, que es exactamente lo que no puede pasar.
- **El significado de negocio.** Está en `docs/spec/glossary.yaml`.
- **Los conteos de fila y los bytes.** Cambian con el perfil del dataset y con cada
  regeneración; el esquema no. Mezclarlos haría que el sha del catálogo dependiera
  de cuántas filas se generaron, y `G-CATALOG-FRESH` dejaría de significar nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

#: Familias canónicas de tipo. El nombre exacto del motor se conserva aparte: dos
#: motores escriben `VARCHAR` y `string` para la misma cosa, y una regla del guard
#: que compare nombres de tipo de motor se rompería al cambiar de engine.
FAMILY_INTEGER: Final = "integer"
FAMILY_DECIMAL: Final = "decimal"
FAMILY_FLOAT: Final = "float"
FAMILY_TEXT: Final = "text"
FAMILY_BOOLEAN: Final = "boolean"
FAMILY_DATE: Final = "date"
FAMILY_TIMESTAMP: Final = "timestamp"
FAMILY_TIME: Final = "time"
FAMILY_BLOB: Final = "blob"
FAMILY_OTHER: Final = "other"

FAMILIES: Final = frozenset(
    {
        FAMILY_INTEGER,
        FAMILY_DECIMAL,
        FAMILY_FLOAT,
        FAMILY_TEXT,
        FAMILY_BOOLEAN,
        FAMILY_DATE,
        FAMILY_TIMESTAMP,
        FAMILY_TIME,
        FAMILY_BLOB,
        FAMILY_OTHER,
    }
)

#: Cómo se llama cada tabla según lo que es. `view` no es un detalle: las vistas de
#: este almacén son las que resuelven las trampas declaradas del glosario
#: (`v_attempt_dedup`, `v_payment_intent`), así que el catálogo tiene que decir
#: cuáles son para que el generador prefiera la vista correcta a la tabla cruda.
KIND_TABLE: Final = "table"
KIND_VIEW: Final = "view"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """Una columna del esquema vivo."""

    name: str
    engine_type: str
    family: str
    nullable: bool
    ordinal: int
    #: `False` si `docs/spec/policy.yaml` la excluye del catálogo del anillo 1
    #: (cambio C-3 de la firma de Q-003). Sigue estando aquí porque el esquema
    #: describe lo que EXISTE; publicar es otra decisión.
    published: bool = True
    #: `True` si `docs/spec/catalog-overlay.yaml` la marca obsoleta. El motor no
    #: puede saberlo: es conocimiento de negocio sobre una columna que sigue
    #: existiendo y que ya no hay que usar.
    deprecated: bool = False
    deprecated_reason: str | None = None
    #: Las `tabla_base.columna` de las que sale esta columna. Para una tabla base es
    #: ella misma; para una columna de vista, todas sus fuentes. Es lo que impide
    #: que `v_customer.full_name` —que es `concat(first_name, last_name_1)`— escape
    #: a una política que solo casa por `tabla.columna`.
    derives_from: tuple[str, ...] = ()
    #: `False` cuando sqlglot no pudo seguir el linaje (el caso real es una CTE
    #: recursiva) y `derives_from` contiene el cierre de dependencias en vez del
    #: origen exacto. Se publica: un límite contado vale más que uno escondido.
    lineage_resolved: bool = True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "engine_type": self.engine_type,
            "family": self.family,
            "nullable": self.nullable,
            "ordinal": self.ordinal,
            "published": self.published,
            "deprecated": self.deprecated,
            "derives_from": list(self.derives_from),
            "lineage_resolved": self.lineage_resolved,
        }
        if self.deprecated_reason is not None:
            out["deprecated_reason"] = self.deprecated_reason
        return out


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Una tabla o vista del esquema vivo."""

    name: str
    kind: str
    columns: tuple[ColumnSpec, ...]

    def column(self, name: str) -> ColumnSpec | None:
        """La columna con ese nombre, o `None`. Sin distinguir mayúsculas."""
        lowered = name.lower()
        for col in self.columns:
            if col.name.lower() == lowered:
                return col
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "columns": [c.to_dict() for c in self.columns],
        }


@dataclass(frozen=True, slots=True)
class CatalogSchema:
    """El esquema completo, tal y como está en el motor.

    Es lo que `sqlglot.optimizer.qualify.qualify()` necesita para que el guard deje
    de «buscar» alias: con el árbol cualificado contra este esquema, los alias, las
    CTE anidadas y `SELECT *` ya están resueltos y no hay nada que buscar.
    """

    dialect: str
    tables: tuple[TableSpec, ...]

    def table(self, name: str) -> TableSpec | None:
        lowered = name.lower()
        for table in self.tables:
            if table.name.lower() == lowered:
                return table
        return None

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tables)

    def published(self) -> CatalogSchema:
        """El catálogo del anillo 1: sin las columnas que C-3 excluye.

        Se calcula, no se guarda: el fichero generado describe lo que existe, y lo
        que se publica es una vista de eso. Si fueran dos ficheros, el día que
        alguien regenerase solo uno la política y el catálogo dirían cosas
        distintas y nadie se enteraría.
        """
        return CatalogSchema(
            dialect=self.dialect,
            tables=tuple(
                TableSpec(
                    name=t.name,
                    kind=t.kind,
                    columns=tuple(c for c in t.columns if c.published),
                )
                for t in self.tables
            ),
        )

    def sqlglot_schema(self) -> dict[str, object]:
        """El mapa `{tabla: {columna: tipo}}` que consume `qualify()`.

        Se construye sobre el esquema COMPLETO, no sobre el publicado: si una
        columna excluida no estuviera aquí, `qualify()` no sabría a qué tabla
        pertenece y el guard la vería como una columna desconocida en vez de como
        lo que es. Un rechazo por el motivo equivocado es un acierto por
        casualidad, y aquí además daría un mensaje inútil.
        """
        out: dict[str, object] = {
            t.name: {c.name: c.engine_type for c in t.columns} for t in self.tables
        }
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "dialect": self.dialect,
            "tables": [t.to_dict() for t in self.tables],
        }


def from_dict(payload: dict[str, Any]) -> CatalogSchema:
    """Reconstruye un `CatalogSchema` desde el JSON generado."""
    return CatalogSchema(
        dialect=payload["dialect"],
        tables=tuple(
            TableSpec(
                name=t["name"],
                kind=t["kind"],
                columns=tuple(
                    ColumnSpec(
                        name=c["name"],
                        engine_type=c["engine_type"],
                        family=c["family"],
                        nullable=c["nullable"],
                        ordinal=c["ordinal"],
                        published=c.get("published", True),
                        deprecated=c.get("deprecated", False),
                        deprecated_reason=c.get("deprecated_reason"),
                        derives_from=tuple(c.get("derives_from", ())),
                        lineage_resolved=c.get("lineage_resolved", True),
                    )
                    for c in t["columns"]
                ),
            )
            for t in payload["tables"]
        ),
    )
