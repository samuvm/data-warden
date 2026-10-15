"""Cost ring: estimated before execution, never after.

`EXPLAIN ANALYZE` runs the query and Athena reports `DataScannedInBytes` only after
billing, so neither works for a preventive guard. Cost is derived from Iceberg
metadata without reading a row, which makes it engine-independent (DuckDB and Athena).
"""

from __future__ import annotations

import pathlib
from typing import Final

STATISTICS_PATH: Final = (
    pathlib.Path(__file__).resolve().parent.parent / "catalog" / "generated" / "statistics.json"
)

__all__ = ["STATISTICS_PATH"]
