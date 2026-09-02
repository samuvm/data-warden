"""Anillo 4 · el coste. Se estima ANTES de ejecutar, nunca después.

`EXPLAIN ANALYZE` ejecuta la consulta y Athena solo reporta `DataScannedInBytes`
cuando ya ha cobrado: los dos son inútiles para un guardián preventivo. Aquí el coste
sale de los metadatos de Iceberg, sin leer una sola fila, y por eso mismo sirve igual
para DuckDB y para Athena.
"""

from __future__ import annotations

import pathlib
from typing import Final

STATISTICS_PATH: Final = (
    pathlib.Path(__file__).resolve().parent.parent / "catalog" / "generated" / "statistics.json"
)

__all__ = ["STATISTICS_PATH"]
