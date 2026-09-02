"""I-13: `tests/unit` no toca disco, ni red, ni DuckDB. Y se IMPONE, no se pide.

Un test unitario que abre una conexión tarda cien veces más y falla por motivos
que no tienen que ver con el código. La invariante existe para que la suite rápida
siga siendo rápida, y aquí se hace cumplir envenenando las dos puertas: el socket y
`duckdb.connect`. Si un test unitario las toca, falla con un mensaje que dice
exactamente qué hacer —moverlo a `tests/integration/`— en vez de ponerse lento.
"""

from __future__ import annotations

import socket
from typing import Any, NoReturn

import pytest


def _no_network(*_args: Any, **_kwargs: Any) -> NoReturn:
    message = (
        "un test de tests/unit ha intentado abrir un socket. I-13 lo prohíbe: la "
        "suite rápida tiene un presupuesto de 20 s y la red no cabe en él. Si el "
        "test necesita red, va a tests/integration/."
    )
    raise RuntimeError(message)


def _no_duckdb(*_args: Any, **_kwargs: Any) -> NoReturn:
    message = (
        "un test de tests/unit ha intentado conectar con DuckDB. I-13 lo prohíbe. "
        "El catálogo se prueba contra un esquema fixture EN MEMORIA; contra el "
        "motor se prueba en tests/integration/."
    )
    raise RuntimeError(message)


@pytest.fixture(autouse=True)
def _sellar_las_puertas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.setattr(duckdb, "connect", _no_duckdb)
