"""Quién pregunta, con qué autoridad y con qué presupuesto.

**El rol nunca viene de datos no autenticados** (I-05). Es el invariante que la
spec MCP 2026-07-28 vuelve crítico: al eliminar las sesiones, cualquier cosa que el
cliente diga sobre sí mismo es dato transportado y jamás autoridad. Aquí viven las
tres fuentes legítimas —proceso servidor, `PrincipalToken` acuñado por el servidor,
bandera del CLI— y la matriz de acceso que firma negocio.
"""

from __future__ import annotations

import pathlib
from typing import Final

GENERATED_DIR: Final = pathlib.Path(__file__).resolve().parent / "generated"
POLICY_PATH: Final = GENERATED_DIR / "policy.json"
BUDGETS_PATH: Final = GENERATED_DIR / "budgets.json"

__all__ = ["BUDGETS_PATH", "GENERATED_DIR", "POLICY_PATH"]
