"""CLI `warden`.

Vacío a propósito. La fase 0 crea el esqueleto y los cimientos; los comandos
llegan con las piezas que ejecutan, y un CLI que promete subcomandos que no
existen es peor que uno que no promete nada.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada. Devuelve el código de salida del proceso."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in {"-V", "--version"}:
        from datawarden import __version__

        print(__version__)
        return 0
    print("warden: sin subcomandos todavía (fase 0). Usa `--version`.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
